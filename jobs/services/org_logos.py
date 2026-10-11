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
MAX_PIXELS = 4_000_000


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


def to_square_png(data: bytes) -> bytes | None:
    """Re-encode an icon as a 128px square PNG, or None if it isn't a usable square icon."""
    from PIL import Image

    try:
        image = Image.open(io.BytesIO(data))
        if image.format == "ICO":
            sizes = sorted(image.info.get("sizes") or [image.size], key=lambda s: s[0] * s[1])
            image.size = sizes[-1]
        width, height = image.size
        if width * height > MAX_PIXELS or min(width, height) < MIN_SIDE or not 0.8 <= width / height <= 1.25:
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
