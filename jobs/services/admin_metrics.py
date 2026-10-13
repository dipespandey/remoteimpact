"""Numbers for the staff analytics page (/admin/analytics/).

Everything is read from our own tables (no Stripe or analytics API calls), so the
page stays fast and works offline from those services. Revenue is reconstructed from
payment events and paid records; Stripe remains the source of truth for money.
"""
from collections import OrderedDict
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.db.models import Count, Max, Q, Sum
from django.db.models.functions import TruncDate
from django.utils import timezone

from jobs.models import (
    AssistantSubscription,
    ConsumerFunnelEvent,
    EmployerFunnelEvent,
    Job,
    JobAlert,
    JobAlertDelivery,
    JobDailyStat,
    JobFeaturePurchase,
    NewsletterSubscriber,
    SeekerProfile,
    TalentInvitation,
)
from jobs.services.payment_service import JOB_POSTING_PRICE_USD_CENTS, PRO_PLANS, PRO_TRIAL_DAYS
from jobs.services.trust_stats import trust_stats

CE = ConsumerFunnelEvent.Event
EE = EmployerFunnelEvent.Event
RANGES = (7, 30, 90)

# The path a seeker takes to paying. Scout page views and account creations are left
# out: most previews start from the homepage composer and most accounts come from other
# sign-up routes, so they are not steps of this sequence.
SEEKER_FUNNEL = [
    (CE.SCOUT_PREVIEWED, "Scout previewed"),
    (CE.SCOUT_CREATED, "Scout created"),
    (CE.INITIAL_EMAIL_SENT, "First matches emailed"),
    (CE.UPGRADE_CLICKED, "Upgrade clicked"),
    ((CE.CHECKOUT_CREATED, CE.CHECKOUT_STARTED), "Checkout started"),  # current flow records CHECKOUT_CREATED
    (CE.PAYMENT_COMPLETED, "Paid or trial started"),
]
EMPLOYER_FUNNEL = [
    (EE.POST_JOB_VIEWED, "Post or feature page viewed"),
    (EE.JOB_DRAFT_CREATED, "Job draft created"),
    (EE.CHECKOUT_STARTED, "Checkout started"),
    (EE.PAYMENT_COMPLETED, "Payment completed"),
    (EE.SHORTLIST_VIEWED, "Shortlist viewed"),
    (EE.CANDIDATE_REVIEWED, "Candidate reviewed"),
]
FRESHNESS_WARN_HOURS = 12
FRESHNESS_CRITICAL_HOURS = 48


def _evaluator_waitlist():
    from gigs.models import EvaluatorInterest
    return EvaluatorInterest.objects.count()


def _delta(current, previous):
    if not previous:
        return None
    return round(100 * (current - previous) / previous)


def _kpi(label, value, previous=None, fmt="int", note=""):
    return {"label": label, "value": value, "previous": previous, "delta": _delta(value, previous)
            if previous is not None else None, "fmt": fmt, "note": note}


def _daily(qs, field, since, days, value=None):
    """Counts (or sums of `value`) per day, zero-filled, oldest first."""
    rows = qs.filter(**{f"{field}__gte": since}).annotate(day=TruncDate(field)).values("day")
    rows = rows.annotate(n=Sum(value) if value else Count("id"))
    by_day = {row["day"]: row["n"] or 0 for row in rows}
    start = timezone.localdate() - timedelta(days=days - 1)
    return [(start + timedelta(days=i), by_day.get(start + timedelta(days=i), 0)) for i in range(days)]


def _series(points, label, color):
    peak = max((v for _, v in points), default=0)
    return {
        "label": label, "color": color, "total": sum(v for _, v in points), "peak": peak,
        "bars": [{"day": d, "value": v, "pct": round(100 * v / peak) if peak else 0} for d, v in points],
    }


def _funnel(model, steps, since, until=None):
    qs = model.objects.filter(created_at__gte=since)
    if until:
        qs = qs.filter(created_at__lt=until)
    counts = dict(qs.values_list("event").annotate(n=Count("id")))
    top = max((sum(counts.get(e, 0) for e in (event if isinstance(event, tuple) else (event,))) for event, _ in steps), default=0)
    out, prev = [], None
    for event, label in steps:
        events = event if isinstance(event, tuple) else (event,)
        n = sum(counts.get(e, 0) for e in events)
        out.append({
            "label": label, "count": n, "pct": round(100 * n / top) if top else 0,
            # Events aren't a strict sequence; a rate above 100% would be misleading.
            "step_rate": round(100 * n / prev) if prev and n <= prev else None,
        })
        prev = n or prev
    return out


def _revenue_cents(since, until):
    postings = Job.objects.filter(is_paid=True, paid_at__gte=since, paid_at__lt=until).count()
    features = JobFeaturePurchase.objects.filter(created_at__gte=since, created_at__lt=until).aggregate(
        s=Sum("amount_cents"))["s"] or 0
    pro = 0
    for meta in ConsumerFunnelEvent.objects.filter(
        event=CE.PAYMENT_COMPLETED, created_at__gte=since, created_at__lt=until
    ).values_list("metadata", flat=True):
        meta = meta or {}
        if meta.get("trial"):
            continue
        plan = meta.get("plan") or "monthly"
        pro += PRO_PLANS.get(plan, PRO_PLANS["monthly"])["unit_amount"]
    return {
        "total": postings * JOB_POSTING_PRICE_USD_CENTS + features + pro,
        "postings": postings * JOB_POSTING_PRICE_USD_CENTS, "postings_count": postings,
        "features": features, "pro": pro,
    }


def build_metrics(days=30, refresh=False):
    days = days if days in RANGES else 30
    key = f"admin_metrics:v1:{days}"
    cached = None if refresh else cache.get(key)
    if cached:
        return cached

    now = timezone.now()
    since = now - timedelta(days=days)
    prev_since = since - timedelta(days=days)
    User = get_user_model()

    revenue = _revenue_cents(since, now)
    revenue_prev = _revenue_cents(prev_since, since)
    signups = User.objects.filter(date_joined__gte=since).count()
    signups_prev = User.objects.filter(date_joined__gte=prev_since, date_joined__lt=since).count()

    active_pro = AssistantSubscription.objects.filter(is_subscribed=True).filter(
        Q(expires_at__isnull=True) | Q(expires_at__gte=now))
    pro_billed = active_pro.exclude(stripe_subscription_id="").count()
    trials = ConsumerFunnelEvent.objects.filter(
        event=CE.PAYMENT_COMPLETED, metadata__trial=True,
        created_at__gte=now - timedelta(days=PRO_TRIAL_DAYS),
    ).count()

    scouts_active = JobAlert.objects.filter(is_active=True).exclude(frequency=JobAlert.Frequency.ONCE).count()
    scouts_created = ConsumerFunnelEvent.objects.filter(event=CE.SCOUT_CREATED, created_at__gte=since).count()
    scouts_empty = ConsumerFunnelEvent.objects.filter(event=CE.INITIAL_MATCHES_EMPTY, created_at__gte=since).count()

    deliveries = JobAlertDelivery.objects.filter(sent_at__gte=since)
    feedback = dict(deliveries.exclude(feedback="").values_list("feedback").annotate(n=Count("id")))
    relevant = feedback.get(JobAlertDelivery.Feedback.RELEVANT, 0)
    not_relevant = feedback.get(JobAlertDelivery.Feedback.NOT_RELEVANT, 0)

    stats_qs = JobDailyStat.objects.filter(date__gte=timezone.localdate() - timedelta(days=days - 1))
    engagement = stats_qs.aggregate(views=Sum("views"), clicks=Sum("apply_clicks"), deliveries=Sum("scout_deliveries"))
    views, clicks = engagement["views"] or 0, engagement["clicks"] or 0
    tracking_since = JobDailyStat.objects.order_by("date").values_list("date", flat=True).first()
    top_jobs = list(
        stats_qs.values("job_id", "job__title", "job__slug", "job__organization__name")
        .annotate(views=Sum("views"), clicks=Sum("apply_clicks"))
        .order_by("-clicks", "-views")[:10]
    )

    # Staff diagnostics may initialize a missing snapshot; public pages never do.
    trust = trust_stats() or trust_stats(refresh=True)
    live = Job.objects.filter(Job.availability_filter())
    live_by_source = dict(live.values_list("source").annotate(n=Count("id")))
    source_labels = dict(Job.Source.choices)
    freshness = []
    for row in Job.objects.exclude(source=Job.Source.MANUAL).values("source").annotate(last=Max("last_seen_at")).order_by("source"):
        hours = (now - row["last"]).total_seconds() / 3600 if row["last"] else None
        status = "critical" if hours is None or hours > FRESHNESS_CRITICAL_HOURS else "warning" if hours > FRESHNESS_WARN_HOURS else "good"
        freshness.append({
            "source": source_labels.get(row["source"], row["source"]), "last": row["last"], "hours": hours,
            "status": status, "live": live_by_source.get(row["source"], 0),
        })
    freshness.sort(key=lambda r: -r["live"])
    categories = list(live.values("category__name").annotate(n=Count("id")).order_by("-n")[:8])
    top_cat = categories[0]["n"] if categories else 0
    for row in categories:
        row["pct"] = round(100 * row["n"] / top_cat) if top_cat else 0

    visible_pool = SeekerProfile.objects.filter(
        wizard_completed=True, is_actively_looking=True,
        visibility__in=[SeekerProfile.Visibility.PUBLIC, SeekerProfile.Visibility.MATCHING],
    ).count()

    metrics = {
        "days": days, "ranges": RANGES, "generated_at": now,
        "kpis": [
            _kpi("Revenue", revenue["total"] / 100, revenue_prev["total"] / 100, fmt="money",
                 note="Postings, features and Pro payments recorded here; trials excluded"),
            _kpi("Paying Pro", pro_billed, note=f"{active_pro.count()} with Pro access, {trials} in trial now"),
            _kpi("New accounts", signups, signups_prev),
            _kpi("Scouts created", scouts_created,
                 ConsumerFunnelEvent.objects.filter(event=CE.SCOUT_CREATED, created_at__gte=prev_since,
                                                    created_at__lt=since).count(),
                 note=f"{scouts_active} recurring Scouts active"),
            _kpi("Apply clicks", clicks, None, note=f"{views:,} job page views"
                 + (f", tracked since {tracking_since:%b %-d}" if tracking_since else "")),
            _kpi("Live jobs", trust["live"], note=f"{trust['confirmed_share']}% confirmed on employer boards"),
        ],
        "revenue": {k: (v / 100 if k != "postings_count" else v) for k, v in revenue.items()},
        "seeker_funnel": _funnel(ConsumerFunnelEvent, SEEKER_FUNNEL, since),
        "employer_funnel": _funnel(EmployerFunnelEvent, EMPLOYER_FUNNEL, since),
        "series": [
            _series(_daily(User.objects.all(), "date_joined", since, days), "New accounts", "var(--rec-1)"),
            _series(_daily(ConsumerFunnelEvent.objects.filter(event=CE.SCOUT_CREATED), "created_at", since, days),
                    "Scouts created", "var(--rec-3)"),
            _series(_daily(JobDailyStat.objects.all(), "date", since, days, value="views"), "Job page views", "var(--rec-7)"),
            _series(_daily(JobDailyStat.objects.all(), "date", since, days, value="apply_clicks"), "Apply clicks", "var(--rec-2)"),
        ],
        "scout": {
            "created": scouts_created, "empty": scouts_empty,
            "empty_rate": round(100 * scouts_empty / scouts_created) if scouts_created else None,
            "active": scouts_active, "deliveries": deliveries.count(),
            "relevant": relevant, "not_relevant": not_relevant,
            "relevance": round(100 * relevant / (relevant + not_relevant)) if relevant + not_relevant else None,
            "ctr": round(100 * clicks / views, 1) if views else None,
        },
        "inventory": {
            "trust": trust, "freshness": freshness, "categories": categories,
            "new_jobs": live.filter(posted_at__gte=since).count(),
            "paid_live": live.filter(source=Job.Source.MANUAL, is_paid=True).count(),
            "featured_live": live.filter(Job.sponsored_filter()).count(),
        },
        "top_jobs": top_jobs,
        "tracking_since": tracking_since,
        "audience": OrderedDict([
            ("Talent pool (visible, looking)", visible_pool),
            ("Employer invitations sent", TalentInvitation.objects.filter(sent_at__gte=since).count()),
            ("Candidates opted in to employers", ConsumerFunnelEvent.objects.filter(event=CE.EMPLOYER_OPT_IN, created_at__gte=since).count()),
            ("Newsletter subscribers", NewsletterSubscriber.objects.filter(confirmed=True, unsubscribed=False).count()),
            ("New newsletter signups", NewsletterSubscriber.objects.filter(created_at__gte=since).count()),
            ("AI evaluator waitlist", _evaluator_waitlist()),
            ("Total accounts", User.objects.count()),
        ]),
    }
    cache.set(key, metrics, 5 * 60)
    return metrics
