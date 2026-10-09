"""Small, deliberately restrictive presentation allowlist for external content."""
from urllib.parse import urlsplit

from bs4 import BeautifulSoup, Comment

ALLOWED_TAGS = {"p", "br", "ul", "ol", "li", "h2", "h3", "h4", "h5", "h6", "strong", "em", "b", "i", "blockquote", "pre", "code", "a", "hr", "div", "span", "table", "thead", "tbody", "tr", "th", "td"}
DROP_CONTENT = {"script", "style", "iframe", "object", "embed", "svg", "math", "template", "noscript", "form", "input", "textarea", "button", "select", "link", "meta", "base"}


def safe_link(value):
    """Allow absolute public-web style links and local paths, never executable URLs."""
    value = str(value or "").strip()
    if not value or any(ord(c) < 33 or ord(c) == 127 for c in value) or "\\" in value:
        return None
    try:
        parsed = urlsplit(value)
        if parsed.scheme in {"http", "https"} and parsed.hostname and not parsed.username and not parsed.password:
            return value
        if not parsed.scheme and not parsed.netloc and value.startswith(("/", "#")) and not value.startswith("//"):
            return value
    except ValueError:
        pass
    return None


def sanitize_html(value):
    soup = BeautifulSoup(str(value), "html.parser")
    for comment in soup.find_all(string=lambda text: isinstance(text, Comment)):
        comment.extract()
    for tag in list(soup.find_all(True)):
        if tag.name is None or tag.parent is None:
            continue  # descendant of an already removed subtree
        name = tag.name.lower()
        if name in DROP_CONTENT:
            tag.decompose()
            continue
        if name == "h1":
            tag.name = name = "h2"
        if name not in ALLOWED_TAGS:
            tag.unwrap()
            continue
        href = safe_link(tag.get("href")) if name == "a" else None
        title = tag.get("title")
        tag.attrs = {}
        if href:
            tag["href"] = href
            tag["rel"] = "nofollow noopener noreferrer"
        if isinstance(title, str):
            tag["title"] = title[:300]
    return str(soup)
