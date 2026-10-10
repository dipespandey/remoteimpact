"""Employer-facing pages: landing, featured placement and private stats reports."""
import logging
from datetime import timedelta

from django.conf import settings
from django.contrib import messages
from django.core import signing
from django.db.models import Count, Sum
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.utils import timezone
from django.views.generic import TemplateView

from ..models import ConsumerFunnelEvent, EmployerFunnelEvent, Job, JobDailyStat, Organization
from ..services import job_stats
from ..services.funnel import record_employer_event
from ..services.job_service import JobService
from ..services.payment_service import (
    FEATURE_DURATION_DAYS,
    FEATURE_PRICE_USD_CENTS,
    JOB_POSTING_DURATION_DAYS,
    JOB_POSTING_PRICE_USD_CENTS,
    PaymentService,
)

logger = logging.getLogger(__name__)
REPORT_SALT = "employer-report"


def report_token(organization):
    return signing.dumps(organization.pk, salt=REPORT_SALT)


def report_url(organization):
    base = getattr(settings, "SITE_URL", "https://remoteimpact.org")
    return base + reverse("jobs:employer_report", kwargs={"token": report_token(organization)})


def audience_stats():
    """Platform-wide reach numbers shown to employers. Cached by callers' pages."""
    now = timezone.now()
    since = timezone.localdate() - timedelta(days=29)
    totals = JobDailyStat.objects.filter(date__gte=since).aggregate(
        views=Sum("views"), clicks=Sum("apply_clicks"), deliveries=Sum("scout_deliveries")
    )
    return {
        "active_jobs": JobService.get_available_jobs().count(),
        "job_seekers_30d": ConsumerFunnelEvent.objects.filter(
            event=ConsumerFunnelEvent.Event.ACCOUNT_CREATED, created_at__gte=now - timedelta(days=30)
        ).count(),
        "scout_searches_30d": ConsumerFunnelEvent.objects.filter(
            event__in=[ConsumerFunnelEvent.Event.SCOUT_PREVIEWED, ConsumerFunnelEvent.Event.SCOUT_CREATED],
            created_at__gte=now - timedelta(days=30),
        ).count(),
        "views_30d": totals["views"] or 0,
        "apply_clicks_30d": totals["clicks"] or 0,
        "scout_deliveries_30d": totals["deliveries"] or 0,
    }


class EmployerLandingView(TemplateView):
    template_name = "jobs/employers/landing.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update(
            audience=audience_stats(),
            post_price=JOB_POSTING_PRICE_USD_CENTS // 100,
            post_days=JOB_POSTING_DURATION_DAYS,
            feature_price=FEATURE_PRICE_USD_CENTS // 100,
            feature_days=FEATURE_DURATION_DAYS,
        )
        return context


class FeatureJobView(TemplateView):
    """Feature an existing listing (imported or posted) for 30 days."""

    template_name = "jobs/employers/feature_job.html"

    def dispatch(self, request, *args, **kwargs):
        self.job = get_object_or_404(
            Job.objects.select_related("organization", "category"), slug=kwargs["slug"]
        )
        return super().dispatch(request, *args, **kwargs)

    def get(self, request, *args, **kwargs):
        if not JobService.get_available_jobs().filter(pk=self.job.pk).exists():
            messages.error(request, "This listing is no longer open, so it can't be featured.")
            return redirect(self.job.get_absolute_url())
        # Crawlers follow the "Feature it" link on every job page; don't count them as employers.
        if not job_stats.is_probably_bot(request):
            record_employer_event(
                EmployerFunnelEvent.Event.POST_JOB_VIEWED, user=request.user, job=self.job,
                metadata={"page": "feature", "source": request.GET.get("source", "")[:80]},
            )
        return super().get(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        stats = job_stats.totals([self.job.pk]).get(self.job.pk, {})
        context.update(
            job=self.job,
            stats=stats,
            price=FEATURE_PRICE_USD_CENTS // 100,
            days=FEATURE_DURATION_DAYS,
            cancelled=self.request.GET.get("cancelled") == "1",
            source=self.request.GET.get("source", "")[:80],
        )
        return context

    def post(self, request, *args, **kwargs):
        if not JobService.get_available_jobs().filter(pk=self.job.pk).exists():
            raise Http404("Listing closed")
        domain_url = request.build_absolute_uri("/").rstrip("/")
        try:
            session = PaymentService.create_feature_checkout_session(
                self.job, domain_url, user=request.user, source=request.POST.get("source", "")
            )
        except Exception:  # noqa: BLE001 - show a friendly error, keep details in logs
            logger.exception("Could not start feature checkout for job %s", self.job.pk)
            record_employer_event(EmployerFunnelEvent.Event.CHECKOUT_FAILED, user=request.user, job=self.job,
                                  metadata={"product": "feature"})
            messages.error(request, "We couldn't start checkout. Please try again in a minute.")
            return redirect(reverse("jobs:feature_job", kwargs={"slug": self.job.slug}))
        record_employer_event(EmployerFunnelEvent.Event.CHECKOUT_STARTED, user=request.user, job=self.job,
                              metadata={"product": "feature"})
        return redirect(session.url)


class FeatureJobSuccessView(TemplateView):
    template_name = "jobs/employers/feature_success.html"

    def get(self, request, slug):
        self.job = get_object_or_404(Job.objects.select_related("organization"), slug=slug)
        session_id = request.GET.get("session_id", "")
        self.purchase = PaymentService.verify_feature_session(session_id, self.job) if session_id else None
        if not self.purchase:
            messages.error(request, "We couldn't confirm the payment yet. If you were charged, it will appear within a few minutes.")
            return redirect(self.job.get_absolute_url())
        return super().get(request, slug=slug)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update(job=self.job, purchase=self.purchase, report_url=report_url(self.job.organization))
        return context


class EmployerReportView(TemplateView):
    """Private, link-shared stats for one organization's live listings."""

    template_name = "jobs/employers/report.html"

    def get(self, request, token):
        try:
            org_id = signing.loads(token, salt=REPORT_SALT, max_age=60 * 60 * 24 * 120)
        except signing.BadSignature:
            raise Http404("This report link is invalid or has expired.")
        self.organization = get_object_or_404(Organization, pk=org_id)
        return super().get(request, token=token)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        jobs = list(
            JobService.get_available_jobs()
            .filter(organization=self.organization)
            .select_related("category")
            .order_by("-posted_at")[:50]
        )
        totals = job_stats.totals([job.pk for job in jobs])
        rows = []
        for job in jobs:
            row = totals.get(job.pk, {})
            rows.append({
                "job": job,
                "views": row.get("views") or 0,
                "apply_clicks": row.get("apply_clicks") or 0,
                "scout_deliveries": row.get("scout_deliveries") or 0,
            })
        rows.sort(key=lambda r: (r["views"] + 3 * r["apply_clicks"]), reverse=True)
        context.update(
            organization=self.organization,
            rows=rows,
            total_views=sum(r["views"] for r in rows),
            total_clicks=sum(r["apply_clicks"] for r in rows),
            total_deliveries=sum(r["scout_deliveries"] for r in rows),
            feature_price=FEATURE_PRICE_USD_CENTS // 100,
            feature_days=FEATURE_DURATION_DAYS,
            audience=audience_stats(),
            claim_url=(reverse("jobs:claim_organization", kwargs={"slug": self.organization.slug})
                       if self.organization.slug else ""),
        )
        return context


CLAIM_SALT = "org-claim"


def _domain(value):
    from urllib.parse import urlparse
    host = urlparse(value if "//" in (value or "") else f"//{value or ''}").netloc.lower()
    return host[4:] if host.startswith("www.") else host


class ClaimOrganizationView(TemplateView):
    """An employer verifies a work email to follow their organization's stats."""

    template_name = "jobs/employers/claim.html"

    def dispatch(self, request, *args, **kwargs):
        self.organization = get_object_or_404(Organization, slug=kwargs["slug"])
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update(organization=self.organization, sent=self.request.GET.get("sent") == "1")
        return context

    def post(self, request, *args, **kwargs):
        from django.core.validators import validate_email
        from django.core.exceptions import ValidationError
        from ..models import OrganizationClaim
        from ..services.email_service import email_service

        email = (request.POST.get("email") or "").strip().lower()
        name = (request.POST.get("name") or "").strip()[:120]
        try:
            validate_email(email)
        except ValidationError:
            messages.error(request, "Enter a valid work email.")
            return redirect(request.path)
        site_domain = _domain(self.organization.website)
        claim, _ = OrganizationClaim.objects.get_or_create(organization=self.organization, email=email)
        claim.name = name or claim.name
        claim.domain_match = bool(site_domain) and email.split("@", 1)[1] in (site_domain, f"mail.{site_domain}")
        claim.save()
        token = signing.dumps(claim.pk, salt=CLAIM_SALT)
        base = getattr(settings, "SITE_URL", "https://remoteimpact.org")
        verify_url = base + reverse("jobs:claim_verify", kwargs={"token": token})
        email_service.send_claim_verification(claim, verify_url)
        return redirect(f"{request.path}?sent=1")


class ClaimVerifyView(TemplateView):
    def get(self, request, token):
        from ..models import OrganizationClaim
        try:
            claim_id = signing.loads(token, salt=CLAIM_SALT, max_age=60 * 60 * 24 * 7)
        except signing.BadSignature:
            messages.error(request, "This link is invalid or has expired. Request a new one.")
            return redirect("jobs:employers")
        claim = get_object_or_404(OrganizationClaim.objects.select_related("organization"), pk=claim_id)
        if not claim.verified_at:
            claim.verified_at = timezone.now()
            claim.save(update_fields=["verified_at"])
        messages.success(request, "Email confirmed. We'll send you a short monthly summary of the applicants we send you.")
        return redirect("jobs:employer_report", token=report_token(claim.organization))
