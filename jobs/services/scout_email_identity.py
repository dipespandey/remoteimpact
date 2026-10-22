"""Human-readable saved-search identity without another model call."""
from django.utils.text import Truncator


def scout_email_label(alert):
    name = " ".join((alert.name or "").split())
    if name.casefold() not in {"", "my job scout", "job scout", "my scout", "unnamed"}:
        return name
    brief = " ".join((alert.intent_text or alert.keywords or "").split())
    return Truncator(brief).chars(90) if brief else f"Scout #{alert.pk}"


def scout_email_schedule(alert):
    return {
        "once": "You requested one matching email for this search.",
        "instant": "Instant alerts are on: we email when this search finds new matches, so you may receive more than one email a day.",
        "daily": "Daily alerts are on for this saved search. We email only when there are new matches.",
        "weekly": "Weekly alerts are on for this saved search. We email only when there are new matches.",
    }.get(alert.frequency, "You saved this search on Remote Impact.")
