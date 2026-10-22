"""Weekly acquisition primitives; deliberately independent of Scout consent."""
import re
from zoneinfo import ZoneInfo
from urllib.parse import urlencode

from django.conf import settings
from django.core.signing import TimestampSigner
from django.urls import reverse
from django.utils import timezone

from jobs.models import Job, RoundupSubscriber

TOPICS = {"all": "All impact areas", "climate": "Climate", "health": "Global health", "ai": "AI safety", "early": "Early careers"}
CATEGORY = {"climate": "climate-environment", "health": "global-health", "ai": "ai-safety"}
CONSENT = "One free email a week with remote impact job picks. Unsubscribe anytime."


def safe_label(value, fallback=""):
    """Campaign labels only: reject URLs, email addresses and free-form briefs."""
    return value if isinstance(value, str) and re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,63}", value) else fallback


def active_subscribers():
    return RoundupSubscriber.objects.filter(confirmed_at__isnull=False, unsubscribed_at__isnull=True)


def token_for(subscriber, purpose):
    return TimestampSigner(salt=f"roundup-{purpose}").sign(str(subscriber.pk if purpose == "unsubscribe" else subscriber.confirmation_key))


def subscriber_for(token, purpose):
    kwargs = {"max_age": 48 * 3600} if purpose == "confirm" else {}
    key = TimestampSigner(salt=f"roundup-{purpose}").unsign(token, **kwargs)
    return RoundupSubscriber.objects.get(**({"pk": key} if purpose == "unsubscribe" else {"confirmation_key": key}))


def public_url(name, **kwargs):
    return settings.SITE_URL.rstrip("/") + reverse(name, kwargs=kwargs)


def share_url(subscriber):
    return public_url("jobs:roundup") + "?" + urlencode({"ref": str(subscriber.referral_code), "utm_source": "reader", "utm_medium": "referral", "utm_campaign": "weekly_roundup"})


def sample_jobs(topic="all", limit=6):
    """A catalogue preview, never a claim of independent employer verification."""
    qs = Job.objects.filter(Job.availability_filter()).exclude(application_url="").select_related("organization", "category")
    if topic == "early":
        qs = qs.filter(experience_level__in=["entry", "internship"])
    if topic in CATEGORY:
        qs = qs.filter(category__slug=CATEGORY[topic])
    # Do not let one employer fill the whole sample; never call unknown pay paid.
    result, employers = [], set()
    for job in qs.order_by("-posted_at", "pk")[:80]:
        if job.organization_id in employers:
            continue
        result.append(job)
        employers.add(job.organization_id)
        if len(result) == limit:
            break
    return result


def issue_week():
    return timezone.now().astimezone(ZoneInfo("Europe/Oslo")).strftime("%G-W%V")
