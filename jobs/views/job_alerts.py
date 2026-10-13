import logging
import hashlib
from datetime import timedelta
from urllib.parse import urlencode

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.signing import BadSignature, TimestampSigner
from django.db import transaction
from django.db.models import Count
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import csrf_exempt
from django.views.generic import CreateView, ListView, TemplateView, UpdateView, View

from ..forms import JobAlertForm
from ..models import (
    AssistantSubscription,
    ConsumerFunnelEvent,
    JobAlert,
    JobAlertDelivery,
)
from ..services.email_service import email_service
from ..services.funnel import record_consumer_event
from ..services.job_service import JobService
from ..services.job_scout_service import JobScoutService
from ..services.payment_service import (
    PRO_PLANS,
    PRO_TRIAL_DAYS,
    PaymentService,
    normalize_pro_plan,
    pro_trial_eligible,
)

logger = logging.getLogger(__name__)
INITIAL_SCOUT_LOOKBACK_DAYS = 30


def _subscription_for(user):
    return AssistantSubscription.get_or_create_for_user(user)


def _save_interpretation(alert, preferences):
    alert.parsed_preferences = preferences
    alert.keywords = ", ".join(
        preferences.get("titles", [])
        + preferences.get("skills", [])
        + preferences.get("include_keywords", [])
    )[:2000]
    alert.save(update_fields=["parsed_preferences", "keywords", "updated_at"])
    alert.categories.set(
        alert.categories.model.objects.filter(
            slug__in=preferences.get("category_slugs", [])
        )
    )


def _empty_because_of_location(alert):
    """True when a Scout's first run found nothing mainly because roles were limited to other countries."""
    event = (
        ConsumerFunnelEvent.objects.filter(alert=alert, event=ConsumerFunnelEvent.Event.INITIAL_MATCHES_EMPTY)
        .order_by("-created_at").first()
    )
    rejections = ((event.metadata or {}).get("matching") or {}).get("hard_rejections") or {} if event else {}
    if not rejections:
        return False
    top = max(rejections, key=rejections.get)
    return top in ("location", "location_rejected", "excluded_location")


def _send_initial_matches(alert, *, source=""):
    diagnostics = {}
    matches = JobScoutService.get_matches(
        alert,
        since=timezone.now() - timedelta(days=INITIAL_SCOUT_LOOKBACK_DAYS),
        limit=5,
        diagnostics=diagnostics,
    )
    if not matches:
        record_consumer_event(
            ConsumerFunnelEvent.Event.INITIAL_MATCHES_EMPTY,
            user=alert.user,
            alert=alert,
            metadata={"frequency": alert.frequency, "source": source, "matching": diagnostics},
        )
        alert._empty_advice = JobScoutService.empty_advice(diagnostics, JobScoutService.preferences_for_alert(alert))
        return matches, False

    sent = email_service.send_job_alert(alert, matches, is_initial=True)
    if sent:
        JobAlertDelivery.objects.bulk_create(
            [
                JobAlertDelivery(
                    alert=alert,
                    job=job,
                    match_score=getattr(job, "scout_score", 0),
                    match_reasons=getattr(job, "scout_reasons", []),
                )
                for job in matches
            ],
            ignore_conflicts=True,
        )
        from jobs.services import job_stats
        for delivered_job in matches:
            job_stats.record(delivered_job.pk, "scout_deliveries", dedupe=False)
        alert.last_sent_at = timezone.now()
        alert.save(update_fields=["last_sent_at"])
    record_consumer_event(
        ConsumerFunnelEvent.Event.INITIAL_EMAIL_SENT
        if sent
        else ConsumerFunnelEvent.Event.INITIAL_EMAIL_FAILED,
        user=alert.user,
        alert=alert,
        metadata={
            "match_count": len(matches),
            "frequency": alert.frequency,
            "source": source,
            "matching": diagnostics,
        },
    )
    return matches, sent


class JobAlertListView(LoginRequiredMixin, ListView):
    template_name = "jobs/job_alerts.html"
    context_object_name = "alerts"

    def get_queryset(self):
        return (
            JobAlert.objects.filter(user=self.request.user)
            .prefetch_related("categories")
            .annotate(delivery_count=Count("deliveries"))
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        subscription = _subscription_for(self.request.user)
        context["subscription"] = subscription
        context["is_pro"] = subscription.is_pro_active()
        context["free_scout_used"] = (
            not context["is_pro"] and subscription.free_job_scout_used_at is not None
        )
        initial_intent = " ".join(self.request.GET.get("intent", "").split())[:2000]
        landing_source = " ".join(self.request.GET.get("source", "").split())[:80]
        if landing_source:
            record_consumer_event(
                ConsumerFunnelEvent.Event.SCOUT_LANDED,
                user=self.request.user,
                metadata={
                    "source": landing_source,
                    "intent_prefilled": bool(initial_intent),
                },
                dedupe_key=f"scout-landing:{self.request.user.pk}:{landing_source}",
            )
        context["form"] = JobAlertForm(
            user=self.request.user,
            initial={"intent_text": initial_intent} if initial_intent else None,
        )

        created_id = self.request.GET.get("created")
        if created_id:
            created = JobAlert.objects.filter(
                pk=created_id, user=self.request.user
            ).first()
            if created:
                context["created_alert"] = created
                deliveries = created.deliveries.select_related(
                    "job__organization", "job__category"
                ).order_by("-sent_at")[:10]
                preview_jobs = []
                for delivery in deliveries:
                    delivery.job.scout_score = delivery.match_score
                    delivery.job.scout_reasons = delivery.match_reasons
                    delivery.job.scout_delivery_id = delivery.pk
                    delivery.job.scout_feedback = delivery.feedback
                    preview_jobs.append(delivery.job)
                if not preview_jobs:
                    preview_jobs = JobScoutService.get_matches(
                        created,
                        since=timezone.now()
                        - timedelta(days=INITIAL_SCOUT_LOOKBACK_DAYS),
                        limit=5,
                    )
                context["preview_jobs"] = preview_jobs
                if not preview_jobs:
                    context["location_blocked"] = _empty_because_of_location(created)
        return context


class JobScoutPreviewView(TemplateView):
    template_name = "jobs/job_scout_preview.html"

    def get(self, request, *args, **kwargs):
        intent = " ".join(request.GET.get("intent", "").split())[:2000]
        if len(intent) < 20:
            messages.info(request, "Describe the job you want in a little more detail.")
            return redirect("jobs:home")
        self.intent = intent
        return super().get(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        preferences = JobScoutService._fallback_parse(self.intent)
        matches = JobScoutService.get_matches_for_preferences(
            preferences,
            since=timezone.now() - timedelta(days=30),
            limit=3,
        )
        source = " ".join(self.request.GET.get("source", "").split())[:80] or "scout_preview"
        next_path = f"{reverse('jobs:job_alerts')}?{urlencode({'intent': self.intent, 'source': source})}"
        context.update(
            intent=self.intent,
            preferences=preferences,
            preview_jobs=matches,
            provisional=any(getattr(job, "scout_needs_confirmation", False) for job in matches),
            next_path=next_path,
        )
        if not self.request.session.session_key:
            self.request.session.create()
        intent_digest = hashlib.sha256(self.intent.lower().encode("utf-8")).hexdigest()[:16]
        record_consumer_event(
            ConsumerFunnelEvent.Event.SCOUT_PREVIEWED,
            user=self.request.user,
            metadata={
                "match_count": len(matches),
                "intent_text": self.intent[:2000],
                "source": source,
                "authenticated": self.request.user.is_authenticated,
            },
            dedupe_key=f"scout-preview:{self.request.session.session_key}:{intent_digest}",
        )
        return context


class JobScoutSignupStartView(View):
    """Track the preview-to-account handoff before entering allauth."""

    def get(self, request, *args, **kwargs):
        next_path = request.GET.get("next", "").strip()
        # The preview only hands off to the alert flow. Keep this redirect
        # internal so the tracking URL cannot become an open redirect.
        if not next_path.startswith("/alerts/") or next_path.startswith("//"):
            next_path = reverse("jobs:job_alerts")
        source = " ".join(request.GET.get("source", "scout_preview").split())[:80]
        if not request.session.session_key:
            request.session.create()
        handoff_digest = hashlib.sha256(next_path.encode("utf-8")).hexdigest()[:16]
        record_consumer_event(
            ConsumerFunnelEvent.Event.SCOUT_SIGNUP_STARTED,
            metadata={"source": source},
            dedupe_key=f"scout-signup-start:{request.session.session_key}:{handoff_digest}",
        )
        return redirect(
            f"{reverse('account_signup')}?{urlencode({'next': next_path})}"
        )


class JobAlertCreateView(LoginRequiredMixin, CreateView):
    model = JobAlert
    form_class = JobAlertForm
    template_name = "jobs/job_alert_create.html"

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["user"] = self.request.user
        return kwargs

    def form_valid(self, form):
        source = " ".join(self.request.POST.get("source", "").split())[:80]
        form.instance.intent_text = " ".join(form.cleaned_data["intent_text"].split())
        with transaction.atomic():
            subscription = AssistantSubscription.objects.select_for_update().get(
                pk=_subscription_for(self.request.user).pk
            )
            is_pro = subscription.is_pro_active()
            frequency = form.instance.frequency if is_pro else JobAlert.Frequency.ONCE
            existing = JobAlert.objects.filter(
                user=self.request.user,
                intent_text__iexact=form.instance.intent_text,
                frequency=frequency,
                created_at__gte=timezone.now() - timedelta(minutes=10),
            ).first()
            if existing:
                return redirect(f"{reverse('jobs:job_alerts')}?created={existing.pk}")
            limit_reached = (
                JobAlert.objects.filter(user=self.request.user, is_active=True).count() >= 5
                if is_pro else subscription.free_job_scout_used_at is not None
            )
            if limit_reached:
                messages.info(
                    self.request,
                    "The free plan includes one one-time matching email. Upgrade to Pro for recurring alerts and five active scouts."
                    if not is_pro else "You can run up to five active Job Scouts.",
                )
                return redirect("jobs:job_alerts")
            form.instance.user = self.request.user
            form.instance.frequency = frequency
            if not form.instance.name:
                form.instance.name = "My Job Scout"
            self.object = form.save()
        response = redirect(self.get_success_url())
        preferences = JobScoutService.parse_intent(form.instance.intent_text)
        _save_interpretation(self.object, preferences)
        matches, sent = _send_initial_matches(self.object, source=source)
        if not is_pro and matches:
            subscription.free_job_scout_used_at = timezone.now()
            subscription.save(update_fields=["free_job_scout_used_at"])
        record_consumer_event(
            ConsumerFunnelEvent.Event.SCOUT_CREATED,
            user=self.request.user,
            alert=self.object,
            metadata={
                "frequency": self.object.frequency,
                "is_pro": is_pro,
                "match_count": len(matches),
                "email_sent": sent,
                "source": source,
            },
        )
        if not is_pro:
            self.object.is_active = False
            self.object.save(update_fields=["is_active"])

        if matches and sent:
            messages.success(
                self.request,
                f"We emailed you {len(matches)} matching job{'s' if len(matches) != 1 else ''}."
                if not is_pro
                else f"Your scout is active. We emailed you {len(matches)} matching job{'s' if len(matches) != 1 else ''}.",
            )
        elif matches:
            messages.warning(
                self.request,
                "We found matches, but the email could not be sent. You can see them below."
                if not is_pro
                else "Your scout is active and found matches, but the first email could not be sent. You can see them below.",
            )
        else:
            advice = getattr(self.object, "_empty_advice", "")
            messages.info(
                self.request,
                f"We did not find a strong current match. {advice} Your free one-time Scout is still available."
                if not is_pro
                else f"Your scout is active. We will email you when a strong new match appears. {advice}",
            )
        return response

    def get_success_url(self):
        return f"{reverse('jobs:job_alerts')}?created={self.object.pk}"


class JobAlertUpdateView(LoginRequiredMixin, UpdateView):
    model = JobAlert
    form_class = JobAlertForm
    template_name = "jobs/job_alert_edit.html"

    def get_queryset(self):
        return JobAlert.objects.filter(user=self.request.user)

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["user"] = self.request.user
        return kwargs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["is_pro"] = _subscription_for(self.request.user).is_pro_active()
        return context

    def form_valid(self, form):
        subscription = _subscription_for(self.request.user)
        is_pro = subscription.is_pro_active()
        if not is_pro:
            form.instance.frequency = JobAlert.Frequency.ONCE
            form.instance.is_active = False
        response = super().form_valid(form)
        if is_pro and self.object.frequency != JobAlert.Frequency.ONCE:
            self.object.is_active = True
            self.object.save(update_fields=["is_active"])
        preferences = JobScoutService.parse_intent(self.object.intent_text)
        _save_interpretation(self.object, preferences)
        messages.success(self.request, "Your Job Scout has been updated.")
        return response

    def get_success_url(self):
        return reverse("jobs:job_alerts")


class JobAlertDeleteView(LoginRequiredMixin, View):
    def post(self, request, pk):
        alert = get_object_or_404(JobAlert, pk=pk, user=request.user)
        alert.delete()
        messages.success(request, "Job Scout deleted.")
        return redirect("jobs:job_alerts")


class JobAlertToggleView(LoginRequiredMixin, View):
    def post(self, request, pk):
        alert = get_object_or_404(JobAlert, pk=pk, user=request.user)
        subscription = _subscription_for(request.user)
        if not subscription.is_pro_active():
            messages.info(request, "Recurring Job Scouts are included with Pro.")
            return redirect("jobs:job_alerts")
        if not alert.is_active:
            if JobAlert.objects.filter(user=request.user, is_active=True).count() >= 5:
                messages.info(request, "Your current plan has reached its active scout limit.")
                return redirect("jobs:job_alerts")
        alert.is_active = not alert.is_active
        alert.save(update_fields=["is_active"])
        return redirect("jobs:job_alerts")


class JobAlertFeedbackView(LoginRequiredMixin, View):
    def post(self, request, pk):
        delivery = get_object_or_404(
            JobAlertDelivery.objects.select_related("alert"),
            pk=pk,
            alert__user=request.user,
        )
        choice = request.POST.get("feedback", "")
        valid_choices = {
            JobAlertDelivery.Feedback.RELEVANT,
            JobAlertDelivery.Feedback.NOT_RELEVANT,
        }
        if choice not in valid_choices:
            messages.error(request, "Choose whether this job was a useful match.")
            return redirect(f"{reverse('jobs:job_alerts')}?created={delivery.alert_id}")

        delivery.feedback = choice
        delivery.feedback_at = timezone.now()
        delivery.save(update_fields=["feedback", "feedback_at"])
        event = (
            ConsumerFunnelEvent.Event.MATCH_RELEVANT
            if choice == JobAlertDelivery.Feedback.RELEVANT
            else ConsumerFunnelEvent.Event.MATCH_NOT_RELEVANT
        )
        record_consumer_event(
            event,
            user=request.user,
            alert=delivery.alert,
            metadata={
                "delivery_id": delivery.pk,
                "job_id": delivery.job_id,
                "match_score": delivery.match_score,
            },
            dedupe_key=f"scout-feedback-{delivery.pk}-{choice}",
        )
        messages.success(request, "Thanks. This helps us improve Job Scout matches.")
        return redirect(f"{reverse('jobs:job_alerts')}?created={delivery.alert_id}")


@method_decorator(csrf_exempt, name="dispatch")
class JobAlertUnsubscribeView(TemplateView):
    template_name = "jobs/job_alert_unsubscribed.html"

    def post(self, request, token, *args, **kwargs):
        """RFC 8058 one-click unsubscribe from the mail client."""
        try:
            alert_id = TimestampSigner(salt="job-alert-unsubscribe").unsign(token)
            JobAlert.objects.filter(pk=alert_id).update(is_active=False)
        except (BadSignature, ValueError):
            return HttpResponse(status=400)
        return HttpResponse(status=200)

    def get(self, request, token, *args, **kwargs):
        context = {"success": False}
        try:
            alert_id = TimestampSigner(salt="job-alert-unsubscribe").unsign(token)
            alert = JobAlert.objects.get(pk=alert_id)
            alert.is_active = False
            alert.save(update_fields=["is_active"])
            context.update(
                {
                    "success": True,
                    "alert_name": alert.name or "Job Scout",
                    "email": alert.user.email,
                }
            )
        except (BadSignature, JobAlert.DoesNotExist, ValueError):
            context["error"] = "This unsubscribe link is invalid or the Job Scout no longer exists."
        return self.render_to_response(context)


class JobScoutSubscribeView(LoginRequiredMixin, TemplateView):
    template_name = "jobs/pro_checkout_confirm.html"

    @staticmethod
    def _source(request):
        return " ".join(
            (request.POST.get("source") or request.GET.get("source") or "job_scout_page").split()
        )[:80]

    def get(self, request, *args, **kwargs):
        if _subscription_for(request.user).is_pro_active():
            return redirect("jobs:job_alerts")
        source = self._source(request)
        record_consumer_event(
            ConsumerFunnelEvent.Event.UPGRADE_CLICKED,
            user=request.user,
            metadata={"source": source},
            dedupe_key=(
                f"pro-confirmation:{request.user.pk}:{source}:"
                f"{timezone.localdate().isoformat()}"
            ),
        )
        return super().get(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        user = self.request.user
        alerts = JobAlert.objects.filter(user=user)
        deliveries = JobAlertDelivery.objects.filter(alert__user=user).exclude(
            feedback=JobAlertDelivery.Feedback.NOT_RELEVANT
        )
        context.update(
            {
                "source": self._source(self.request),
                "scout_count": alerts.count(),
                "delivered_count": deliveries.count(),
                "recent_matches": deliveries.select_related("job", "job__organization")[:3],
                "fresh_jobs_count": JobService.get_available_jobs().filter(
                    posted_at__gte=timezone.now() - timedelta(days=7)
                ).count(),
                "latest_scout": alerts.order_by("-created_at").first(),
                "checkout_cancelled": self.request.GET.get("cancelled") == "1",
                "plans": PRO_PLANS,
                "trial_days": PRO_TRIAL_DAYS,
                "trial_eligible": pro_trial_eligible(_subscription_for(user)),
            }
        )
        return context

    def post(self, request, *args, **kwargs):
        if _subscription_for(request.user).is_pro_active():
            return redirect("jobs:job_alerts")
        source = self._source(request)
        plan = normalize_pro_plan(request.POST.get("plan"))
        domain_url = request.build_absolute_uri("/").rstrip("/")
        try:
            checkout_session = PaymentService.create_pro_subscription_session(
                request.user,
                domain_url,
                success_path=reverse("jobs:job_scout_subscribe_success"),
                cancel_path=(
                    f"{reverse('jobs:job_scout_subscribe')}?"
                    f"{urlencode({'source': source, 'cancelled': '1'})}"
                ),
                source=source,
                return_session=True,
                plan=plan,
            )
            record_consumer_event(
                ConsumerFunnelEvent.Event.CHECKOUT_CREATED,
                user=request.user,
                metadata={
                    "source": source,
                    "plan": plan,
                    "checkout_session_id": checkout_session.id,
                },
                dedupe_key=f"checkout-created:{checkout_session.id}",
            )
            return redirect(checkout_session.url)
        except Exception:
            logger.exception("Could not start Job Scout Pro checkout")
            record_consumer_event(
                ConsumerFunnelEvent.Event.CHECKOUT_FAILED,
                user=request.user,
                metadata={"source": source},
            )
            messages.error(request, "We could not start checkout. Please try again.")
            return redirect("jobs:job_alerts")


class JobScoutSubscribeSuccessView(LoginRequiredMixin, TemplateView):
    template_name = "jobs/pro_subscribe_success.html"

    def get(self, request, *args, **kwargs):
        session_id = request.GET.get("session_id")
        if session_id:
            success, _ = PaymentService.verify_assistant_subscription(
                session_id, request.user
            )
            if success:
                return super().get(request, *args, **kwargs)
        messages.error(request, "We could not confirm the subscription yet.")
        return redirect("jobs:job_alerts")
