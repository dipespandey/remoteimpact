"""Free API keys requested on a person's behalf, by an agent or a script, without visiting the site.

The key is emailed, never returned, so it only reaches someone who controls the inbox. Each request is
a lead: a real address and, usually, a stated use. Limits stop the endpoint being used to mail strangers.
"""
from datetime import timedelta

from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.utils import timezone

KEYS_PER_IP_PER_DAY = 3
SIGNUP_URL_PATH = "/developers/"


def request_free_key(request, *, email, name="", use_case="", source="api"):
    """Returns (ok, message). The message is safe to show to the caller either way."""
    from jobs.api.auth import _bump, _client_ip
    from jobs.models import ApiKey
    from jobs.views.developers import DevelopersView

    email = str(email or "").strip().lower()
    try:
        validate_email(email)
    except ValidationError:
        return False, "Provide the user's own email address; the key is sent there."
    if ApiKey.objects.filter(email=email, created_at__gte=timezone.now() - timedelta(days=1)).exists():
        return True, f"A key was already sent to {email} in the last day. Ask the user to check that inbox."
    if _bump(f"api-key-signup:{_client_ip(request)}", 86400) > KEYS_PER_IP_PER_DAY:
        return False, "Too many key requests from this connection today. Try again tomorrow."
    name = str(name or "").strip()[:120] or email.split("@")[0]
    raw, key = ApiKey.issue(name=name, email=email, tier=ApiKey.Tier.FREE,
                            use_case=f"[{source}] {str(use_case or '').strip()}"[:2000])
    DevelopersView._email_key(key, raw)
    from jobs.api import usage
    usage.record(request, type("Requester", (), {"tier": "free", "key": key})(), "page", f"key_request_{source}")
    return True, (f"A free API key has been emailed to {email}. Once the user shares it, send it as "
                  "'Authorization: Bearer ri_...' for 30 requests a minute and 500 a day.")
