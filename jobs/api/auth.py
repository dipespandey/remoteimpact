"""Who is calling the API, which tier they get, and how much they may call.

Keys come as `Authorization: Bearer ri_...`, `X-Api-Key: ri_...` or `?api_key=`. Without a key the caller
gets the anonymous tier (low limits, by IP), so AI assistants and scripts can try things without signing up.
"""
from dataclasses import dataclass
from datetime import timedelta

from django.core.cache import cache
from django.db.models import F
from django.utils import timezone

TIERS = {
    # The free tiers are for evaluating the data and small non-commercial projects; the catalogue itself
    # (thousands of roles, enriched fields, bulk paging) is the commercial product.
    # per-minute, per-day, max page size, how many pages per query, MCP results per call, enriched fields
    "anonymous": {"per_minute": 10, "per_day": 100, "page_max": 10, "max_pages": 3, "mcp_max": 5, "enriched": False},
    "free": {"per_minute": 30, "per_day": 500, "page_max": 20, "max_pages": 5, "mcp_max": 10, "enriched": False},
    "commercial": {"per_minute": 600, "per_day": 100000, "page_max": 500, "max_pages": 10000, "mcp_max": 25, "enriched": True},
}


@dataclass
class Caller:
    tier: str
    key: object = None  # ApiKey or None
    scope: str = ""     # rate-limit scope

    @property
    def limits(self):
        return TIERS[self.tier]


class RateLimited(Exception):
    def __init__(self, retry_after: int):
        self.retry_after = retry_after


class TierLimit(Exception):
    """The request is valid but beyond what this tier may fetch."""


def _raw_key(request) -> str:
    header = request.META.get("HTTP_AUTHORIZATION", "")
    if header.lower().startswith("bearer "):
        return header[7:].strip()
    return (request.META.get("HTTP_X_API_KEY") or request.GET.get("api_key") or "").strip()


def _client_ip(request) -> str:
    return request.META.get("HTTP_CF_CONNECTING_IP") or request.META.get("REMOTE_ADDR", "0.0.0.0")


def identify(request) -> Caller:
    """Resolve the caller; raises RateLimited when over their limits."""
    from jobs.models import ApiKey

    raw = _raw_key(request)
    key = None
    if raw.startswith("ri_"):
        key = ApiKey.objects.filter(key_hash=ApiKey.hash_key(raw), is_active=True).first()
    if key is not None:
        caller = Caller(tier=key.tier, key=key, scope=f"key:{key.pk}")
    else:
        caller = Caller(tier="anonymous", scope=f"ip:{_client_ip(request)}")
    _count(caller)
    return caller


def _bump(cache_key: str, timeout: int) -> int:
    try:
        cache.add(cache_key, 0, timeout)
        return cache.incr(cache_key)
    except Exception:  # noqa: BLE001 - a cache hiccup must not take the API down
        return 0


def _count(caller: Caller):
    now = timezone.now()
    limits = caller.limits
    minute = _bump(f"api:m:{caller.scope}:{now.strftime('%Y%m%d%H%M')}", 120)
    if minute > limits["per_minute"]:
        raise RateLimited(60 - now.second)
    if caller.key is not None:
        today = now.date()
        from jobs.models import ApiKey

        if caller.key.requests_date != today:
            ApiKey.objects.filter(pk=caller.key.pk).update(requests_today=0, requests_date=today)
            caller.key.requests_today, caller.key.requests_date = 0, today
        ApiKey.objects.filter(pk=caller.key.pk).update(requests_today=F("requests_today") + 1, last_used_at=now)
        used = caller.key.requests_today + 1
    else:
        used = _bump(f"api:d:{caller.scope}:{now.strftime('%Y%m%d')}", 90000)
    if used > limits["per_day"]:
        tomorrow = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        raise RateLimited(int((tomorrow - now).total_seconds()))
