"""Bounded, proxy-free HTTP fetches with validated and pinned destinations.

The original hostname remains the HTTP Host and TLS verification/SNI name;
the TCP connection uses only the IP validated before that request.
"""
import ipaddress
import socket
import threading
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
from dataclasses import dataclass, field
from urllib.parse import urljoin, urlsplit

import urllib3
from urllib3.connection import HTTPConnection, HTTPSConnection


_DNS_EXECUTOR = ThreadPoolExecutor(max_workers=4, thread_name_prefix="public-fetch-dns")
_DNS_SLOTS = threading.BoundedSemaphore(4)
_NAT64_NETWORKS = (ipaddress.ip_network("64:ff9b::/96"), ipaddress.ip_network("64:ff9b:1::/48"))


class UnsafeURL(ValueError):
    pass


@dataclass
class FetchResult:
    status: str
    status_code: int = 0
    text: str = ""
    headers: dict = field(default_factory=dict)


def resolve_public_url(url, *, dns_timeout=5):
    if (
        not isinstance(url, str) or len(url) > 4096
        or any(ord(c) <= 32 or ord(c) == 127 for c in url) or "\\" in url
    ):
        raise UnsafeURL("Malformed URL")
    try:
        parsed = urlsplit(url)
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
    except ValueError as exc:
        raise UnsafeURL("Malformed URL") from exc
    if (
        parsed.scheme not in {"http", "https"} or not parsed.hostname
        or parsed.username is not None or parsed.password is not None
        or port not in {80, 443}
    ):
        raise UnsafeURL("Only HTTP(S) on standard ports is supported")
    hostname = parsed.hostname.rstrip(".").encode("idna").decode("ascii")
    try:
        if not _DNS_SLOTS.acquire(blocking=False):
            raise UnsafeURL("DNS capacity exhausted")
        try:
            future = _DNS_EXECUTOR.submit(socket.getaddrinfo, hostname, port, type=socket.SOCK_STREAM)
        except BaseException:
            _DNS_SLOTS.release()
            raise
        future.add_done_callback(lambda _: _DNS_SLOTS.release())
        addresses = future.result(timeout=dns_timeout)
    except (OSError, UnicodeError, FutureTimeout) as exc:
        raise UnsafeURL("Unresolvable destination") from exc
    ips = []
    for address in addresses:
        try:
            ip = ipaddress.ip_address(address[4][0])
        except ValueError as exc:
            raise UnsafeURL("Invalid address") from exc
        # Fail the entire destination if ANY answer is non-public. Explicit
        # mapped/transition rejection avoids embedded private IPv4 surprises.
        if (
            not ip.is_global or ip.is_reserved or ip.is_multicast
            or getattr(ip, "is_site_local", False) or getattr(ip, "ipv4_mapped", None)
            or getattr(ip, "sixtofour", None) or getattr(ip, "teredo", None)
            or (ip.version == 6 and any(ip in network for network in _NAT64_NETWORKS))
        ):
            raise UnsafeURL("Non-public destination")
        ips.append(str(ip))
    if not ips:
        raise UnsafeURL("No destination")
    return parsed, hostname, port, ips[0]


def _request_pinned(parsed, hostname, port, ip, headers, remaining):
    base = HTTPSConnection if parsed.scheme == "https" else HTTPConnection

    class PinnedConnection(base):
        def _new_conn(self):
            # Numeric sockaddr bypasses a second DNS resolution entirely.
            family = socket.AF_INET6 if ":" in ip else socket.AF_INET
            connection = socket.socket(family, socket.SOCK_STREAM)
            connection.settimeout(self.timeout)
            try:
                connection.connect((ip, port))
                return connection
            except BaseException:
                connection.close()
                raise

    pool_type = urllib3.HTTPSConnectionPool if parsed.scheme == "https" else urllib3.HTTPConnectionPool
    pool = pool_type(
        hostname, port,
        timeout=urllib3.Timeout(total=remaining, connect=min(5, remaining), read=min(5, remaining)),
        maxsize=1,
    )
    pool.ConnectionCls = PinnedConnection
    target = parsed.path or "/"
    if parsed.query:
        target += "?" + parsed.query
    try:
        response = pool.urlopen("GET", target, headers=headers, redirect=False, retries=False, preload_content=False, decode_content=False)
        return pool, response
    except BaseException:
        pool.close()
        raise


def fetch_public_html(url, *, headers=None, max_bytes=1_000_000, total_timeout=15, max_redirects=3):
    """Return only bounded HTML; every redirect gets fresh validation/pinning."""
    deadline = time.monotonic() + total_timeout
    request_headers = {"User-Agent": "RemoteImpactBot/1.0", "Accept": "text/html,application/xhtml+xml", **(headers or {}), "Accept-Encoding": "identity"}
    for hop in range(max_redirects + 1):
        pool = response = None
        try:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return FetchResult("request_failed")
            parsed, hostname, port, ip = resolve_public_url(url, dns_timeout=min(5, remaining))
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return FetchResult("request_failed")
            pool, response = _request_pinned(parsed, hostname, port, ip, request_headers, remaining)
            status = response.status
            response_headers = dict(response.headers)
            if status in {301, 302, 303, 307, 308}:
                location = response.headers.get("Location")
                if not location:
                    return FetchResult(f"http_{status}", status)
                if hop == max_redirects:
                    return FetchResult("too_many_redirects")
                url = urljoin(url, location)
                continue
            if status != 200:
                return FetchResult(f"http_{status}", status, headers=response_headers)
            if response.headers.get("Content-Type", "").split(";", 1)[0].strip().lower() not in {"text/html", "application/xhtml+xml"}:
                return FetchResult("non_html", status)
            if response.headers.get("Content-Encoding", "identity").lower() not in {"identity", ""}:
                return FetchResult("unsupported_encoding", status)
            length = response.headers.get("Content-Length")
            if length and (not length.isdigit() or int(length) > max_bytes):
                return FetchResult("page_too_large", status)
            chunks, size = [], 0
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return FetchResult("request_failed")
                # Keep each read inside the remaining wall-clock budget.
                if response.connection and response.connection.sock:
                    response.connection.sock.settimeout(min(5, remaining))
                chunk = response.read1(min(65536, max_bytes + 1 - size), decode_content=False)
                if not chunk:
                    break
                size += len(chunk)
                if size > max_bytes:
                    return FetchResult("page_too_large", status)
                chunks.append(chunk)
            return FetchResult("ok", status, b"".join(chunks).decode("utf-8", errors="replace"), response_headers)
        except (UnsafeURL, UnicodeError):
            return FetchResult("unsafe_or_unresolvable_url")
        except (OSError, urllib3.exceptions.HTTPError, ValueError):
            return FetchResult("request_failed")
        finally:
            if response is not None:
                response.close()
            if pool is not None:
                pool.close()
    return FetchResult("too_many_redirects")
