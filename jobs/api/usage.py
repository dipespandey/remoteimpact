"""Count API and MCP calls per day. Never raises: logging must not break the API."""
import logging
import re

from django.db import IntegrityError, transaction
from django.db.models import F
from django.utils import timezone

logger = logging.getLogger(__name__)
FAMILIES = [("claude", "claude"), ("anthropic", "claude"), ("openai", "openai"), ("chatgpt", "openai"), ("cursor", "cursor"),
            ("smithery", "smithery"), ("glama", "glama"), ("pulsemcp", "pulsemcp"), ("curl", "curl"), ("python", "python"),
            ("node", "node"), ("go-http", "go"), ("bot", "bot"), ("mozilla", "browser")]


def client_family(request) -> str:
    agent = request.META.get("HTTP_USER_AGENT", "").lower()
    for needle, family in FAMILIES:
        if needle in agent:
            return family
    return re.sub(r"[^a-z0-9._-]", "", agent.split("/")[0])[:30] or "unknown"


def record(request, caller, surface, endpoint, status=200):
    from jobs.models import ApiUsage

    try:
        key = dict(date=timezone.localdate(), surface=surface, endpoint=str(endpoint)[:60], tier=caller.tier,
                   client=client_family(request), key_prefix=caller.key.prefix if caller.key else "", status=status)
        with transaction.atomic():
            if not ApiUsage.objects.filter(**key).update(count=F("count") + 1):
                try:
                    ApiUsage.objects.create(count=1, **key)
                except IntegrityError:
                    ApiUsage.objects.filter(**key).update(count=F("count") + 1)
    except Exception:  # noqa: BLE001
        logger.exception("API usage logging failed")
