"""Organization logos from their own websites: the icon they publish for home screens and tabs.

Only raster icons (never SVG) are used, and every one is decoded and re-encoded as a 128px PNG, so what
we store and serve is plain pixels from our own domain.
"""
import io
import re
from html.parser import HTMLParser
from urllib.parse import urljoin

from jobs.services.safe_fetch import fetch_public_html, fetch_public_image

SIZE = 128
MIN_SIDE = 48
MAX_PIXELS = 20_000_000  # bytes are capped separately; this guards decompression bombs


class _IconLinks(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links = []

    def handle_starttag(self, tag, attrs):
        if tag != "link":
            return
        attrs = {k.lower(): (v or "") for k, v in attrs}
        rel = attrs.get("rel", "").lower().split()
        href = attrs.get("href", "").strip()
        if not href or href.startswith("data:") or href.lower().split("?")[0].endswith(".svg"):
            return
        if "apple-touch-icon" in rel or "apple-touch-icon-precomposed" in rel:
            self.links.append((0, -_largest(attrs.get("sizes", "180x180")), href))
        elif "icon" in rel:
            self.links.append((1, -_largest(attrs.get("sizes", "")), href))


def _largest(sizes: str) -> int:
    values = [int(a) for a, _ in re.findall(r"(\d+)x(\d+)", sizes or "")]
    return max(values, default=0)


def icon_candidates(html: str, page_url: str) -> list[str]:
    """Icon URLs to try, best first: apple-touch icons, then sized icons, then the conventional paths."""
    parser = _IconLinks()
    try:
        parser.feed(html or "")
    except Exception:  # noqa: BLE001 - a broken page just yields fewer candidates
        pass
    urls = [urljoin(page_url, href) for _, _, href in sorted(parser.links)]
    urls += [urljoin(page_url, "/apple-touch-icon.png"), urljoin(page_url, "/favicon.ico")]
    return [u for u in dict.fromkeys(urls) if u.startswith(("http://", "https://"))][:6]


def to_square_png(data: bytes, max_ratio: float = 1.25) -> bytes | None:
    """Re-encode an icon as a 128px square PNG (padded when slightly wide), or None if it isn't usable."""
    from PIL import Image

    try:
        image = Image.open(io.BytesIO(data))
        if image.format == "ICO":
            sizes = sorted(image.info.get("sizes") or [image.size], key=lambda s: s[0] * s[1])
            image.size = sizes[-1]
        width, height = image.size
        if width * height > MAX_PIXELS or min(width, height) < MIN_SIDE // 2 or max(width, height) < MIN_SIDE \
                or not 1 / max_ratio <= width / height <= max_ratio:
            return None
        image.load()
        image = image.convert("RGBA")
    except Exception:  # noqa: BLE001 - anything Pillow can't read is not a logo
        return None
    image.thumbnail((SIZE, SIZE), Image.LANCZOS)
    canvas = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    canvas.paste(image, ((SIZE - image.width) // 2, (SIZE - image.height) // 2))
    out = io.BytesIO()
    canvas.save(out, format="PNG", optimize=True)
    return out.getvalue()


def fetch_logo(website: str) -> tuple[bytes, str] | None:
    """(png, source_url) for the best icon a website publishes, or None."""
    page = fetch_public_html(website, max_bytes=800_000, total_timeout=12)
    if page.status != "ok":
        return None
    for url in icon_candidates(page.text, website):
        result = fetch_public_image(url)
        if result.status != "ok":
            continue
        png = to_square_png(result.content)
        if png:
            return png, url
    return None


# Applicant-tracking boards that show the employer's own logo on their board page
BOARD_HOSTS = ("greenhouse.io", "bamboohr.com", "lever.co")
_BOARD_LOGO = re.compile(r"<img\b[^>]*>", re.I)


def board_root(application_url: str) -> str:
    """The employer's board page on an ATS (job-boards.greenhouse.io/<org>, <org>.bamboohr.com, jobs.lever.co/<org>)."""
    from urllib.parse import urlparse

    parsed = urlparse(application_url or "")
    host = parsed.netloc.lower()
    if not any(host == h or host.endswith("." + h) for h in BOARD_HOSTS):
        return ""
    if host.endswith("bamboohr.com"):
        return f"https://{host}/"
    first = next((part for part in parsed.path.split("/") if part), "")
    return f"https://{host}/{first}" if first else ""


def board_logo_url(html: str, page_url: str) -> str:
    """The employer logo on an ATS board page (never the ATS's own logo)."""
    for tag in _BOARD_LOGO.findall(html or ""):
        src = re.search(r"""\bsrc\s*=\s*["']([^"']+)""", tag)
        if not src:
            continue
        url = src.group(1)
        lowered = (tag + url).lower()
        if url.lower().split("?")[0].endswith(".svg") or "lever-logo" in lowered or "greenhouse-logo" in lowered:
            continue
        if "logo" in lowered or "/logos/" in url:
            return urljoin(page_url, url)
    return ""


def fetch_board_logo(application_url: str) -> tuple[bytes, str] | None:
    root = board_root(application_url)
    if not root:
        return None
    page = fetch_public_html(root, max_bytes=1_500_000, total_timeout=12)
    if page.status != "ok":
        return None
    url = board_logo_url(page.text, root)
    if not url:
        return None
    result = fetch_public_image(url, max_bytes=600_000)
    if result.status != "ok":
        return None
    png = to_square_png(result.content, max_ratio=2.0)
    return (png, url) if png else None
