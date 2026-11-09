"""Remote Impact's own numbers, shared by the public index, the weekly roundup and the owner's daily signal.

Apply clicks are counted on Remote Impact only (see job_stats): they measure demand through this
site, not an employer's total applicant pool. Every caller should say so where it shows them.
"""
from dataclasses import dataclass
from datetime import datetime, time, timedelta

from django.db.models import Count, Q, Sum
from django.utils import timezone

from jobs.models import (ConsumerFunnelEvent, Job, JobDailyStat, JobFeaturePurchase, OrganizationClaim,
                         OutreachSend, RoundupSubscriber)
from jobs.services.job_service import JobService

LEVELS = dict(Job.EXPERIENCE_LEVEL_CHOICES)
AI_TITLE = Q(title__iregex=r"(^|[^a-z])(ai|artificial intelligence|machine learning|ml|llm)([^a-z]|$)")


@dataclass(frozen=True)
class Competition:
    label: str
    roles: int
    clicks: int

    @property
    def per_role(self) -> float:
        return self.clicks / self.roles if self.roles else 0.0


def competition(by, days=30, min_roles=20):
    """Apply clicks per open role over the last `days`, grouped by "category" or "level"."""
    field = {"category": "category__name", "level": "experience_level"}[by]
    live = JobService.get_available_jobs()
    roles = {row[field]: row["n"] for row in live.exclude(**{f"{field}__isnull": True}).exclude(**{field: ""})
             .values(field).annotate(n=Count("pk"))}
    since = timezone.localdate() - timedelta(days=days - 1)
    clicks = {row[f"job__{field}"]: row["c"] or 0 for row in JobDailyStat.objects.filter(date__gte=since, job__in=live)
              .values(f"job__{field}").annotate(c=Sum("apply_clicks"))}
    rows = [Competition(LEVELS.get(key, key) if by == "level" else key, n, clicks.get(key, 0))
            for key, n in roles.items() if n >= min_roles]
    return sorted(rows, key=lambda r: -r.per_role)


def ai_and_impact(days=30):
    """Open AI safety and governance roles, plus roles elsewhere whose title names AI."""
    live = JobService.get_available_jobs()
    safety = live.filter(category__slug="ai-safety")
    since = timezone.localdate() - timedelta(days=days - 1)
    safety_clicks = JobDailyStat.objects.filter(date__gte=since, job__in=safety).aggregate(c=Sum("apply_clicks"))["c"] or 0
    return {"safety_roles": safety.count(), "safety_clicks": safety_clicks,
            "ai_titled_elsewhere": live.exclude(category__slug="ai-safety").filter(AI_TITLE).count(),
            "live_roles": live.count()}


def _day_bounds(day):
    zone = timezone.get_current_timezone()
    start = timezone.make_aware(datetime.combine(day, time.min), zone)
    return start, start + timedelta(days=1)


def day_signal(day):
    """Counts for one local calendar day. Money events are counts; Stripe reconciliation owns amounts."""
    start, end = _day_bounds(day)
    in_day = {"gte": start, "lt": end}
    stats = JobDailyStat.objects.filter(date=day).aggregate(v=Sum("views"), c=Sum("apply_clicks"))
    weekly = RoundupSubscriber.objects.filter(consent_basis__in=["weekly_request", "account_explicit"])
    features = JobFeaturePurchase.objects.filter(created_at__gte=start, created_at__lt=end)
    posts = Job.objects.filter(paid_at__gte=start, paid_at__lt=end)
    return {
        "job_views": stats["v"] or 0,
        "apply_clicks": stats["c"] or 0,
        "new_roles": Job.objects.filter(posted_at__gte=start, posted_at__lt=end).count(),
        "accounts": ConsumerFunnelEvent.objects.filter(event="account_created", created_at__gte=start, created_at__lt=end).count(),
        "weekly_requests": weekly.filter(confirmation_requested_at__gte=start, confirmation_requested_at__lt=end).count(),
        "weekly_requests_from_apply_prompt": weekly.filter(source="apply_prompt", confirmation_requested_at__gte=start,
                                                           confirmation_requested_at__lt=end).count(),
        "weekly_confirmed": weekly.filter(**{f"confirmed_at__{k}": v for k, v in in_day.items()}).count(),
        "pro_payments": ConsumerFunnelEvent.objects.filter(event="payment_completed", created_at__gte=start, created_at__lt=end).count(),
        "features_sold": features.count(),
        "posts_sold": posts.count(),
        "claims_verified": OrganizationClaim.objects.filter(verified_at__gte=start, verified_at__lt=end).count(),
        "employer_offers_sent": OutreachSend.objects.filter(campaign__startswith="employer-offer", sent_at__gte=start, sent_at__lt=end).count(),
    }


def weekly_audience():
    from jobs.services.roundup import active_subscribers
    return active_subscribers().count()
