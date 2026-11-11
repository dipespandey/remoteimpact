"""Free API keys requested on a person's behalf, by an agent or a script, without visiting the site.

The key is emailed, never returned, so it only reaches someone who controls the inbox. Each request is
a lead: a real address and, usually, a stated use. Limits stop the endpoint being used to mail strangers:
browsers are refused (another site could otherwise make its visitors send these), nothing the caller
writes appears in the email, and there are per-connection, per-address and global daily caps.
"""
from datetime import timedelta

from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.utils import timezone

KEYS_PER_IP_PER_DAY = 3
AGENT_KEYS_PER_DAY = 50
EMAIL_NAME = "there"  # "Hi there,": the greeting never carries caller-supplied text
BROWSER_REFUSED = ("Key requests come from agents and scripts, not web pages. "
                   "People can get a key at https://remoteimpact.org/developers/.")


def request_free_key(request, *, email, name="", use_case="", source="api"):
    """Returns (ok, message). The message is safe to show to the caller either way."""
    from jobs.api.auth import _bump, _client_ip
    from jobs.models import ApiKey
    from jobs.views.developers import DevelopersView

    if request.headers.get("Origin"):
        return False, BROWSER_REFUSED
    email = str(email or "").strip().lower()
    try:
        validate_email(email)
    except ValidationError:
        return False, "Provide the user's own email address; the key is sent there."
    # Count before looking the address up, so the reply can't reveal whether it already has a key.
    if _bump(f"api-key-signup:{_client_ip(request)}", 86400) > KEYS_PER_IP_PER_DAY:
        return False, "Too many key requests from this connection today. Try again tomorrow."
    if ApiKey.objects.filter(email__iexact=email, created_at__gte=timezone.now() - timedelta(days=1)).exists():
        return True, f"If {email} can receive email, a key is on its way. Ask the user to check that inbox."
    if _bump("api-key-agent-requests", 86400) > AGENT_KEYS_PER_DAY:
        return False, "Key requests are paused for today. People can get a key at https://remoteimpact.org/developers/."
    detail = " ".join(f"{label}: {str(value).strip()}" for label, value in (("name", name), ("use", use_case)) if value)
    raw, key = ApiKey.issue(name=EMAIL_NAME, email=email, tier=ApiKey.Tier.FREE, use_case=f"[{source}] {detail}"[:2000])
    if not DevelopersView._email_key(key, raw):
        key.delete()  # Never leave a key the person can't receive; they can ask again.
        return False, "The key email couldn't be sent right now. Try again in a few minutes."
    from jobs.api import usage
    usage.record(request, type("Requester", (), {"tier": "free", "key": key})(), "page", f"key_request_{source}")
    return True, (f"If {email} can receive email, a key is on its way. Once the user shares it, send it as "
                  "'Authorization: Bearer ri_...' for 30 requests a minute and 500 a day.")
