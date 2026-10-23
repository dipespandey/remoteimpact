import logging
import hashlib
from datetime import timedelta
from urllib.parse import urlencode, urlsplit, parse_qs

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.cache import cache
from django.core.signing import BadSignature, TimestampSigner
from django.db import transaction
from django.db.models import Count
from django.http import HttpResponse
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.utils import timezone
from django.utils.cache import patch_vary_headers
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import csrf_exempt
from django.views.generic import CreateView, ListView, TemplateView, UpdateView, View

from ..forms import JobAlertForm, ScoutContactForm, ScoutComposerForm
from ..models import (
    AssistantSubscription,
    ConsumerFunnelEvent,
    JobAlert,
    JobAlertDelivery,
    NewsletterSubscriber,
    ScoutContact,
)
from ..services.email_service import email_service
from ..services.funnel import record_consumer_event
from ..services import scout_funnel
from ..services.job_service import JobService
from ..services.job_scout_service import JobScoutService
from ..services.scout_results import initial_result_context, snapshot_matches
from ..services.payment_service import (
    PRO_PLANS,
    PRO_TRIAL_DAYS,
    PaymentService,
    normalize_pro_plan,
    pro_trial_eligible,
)

logger = logging.getLogger(__name__)
INITIAL_SCOUT_LOOKBACK_DAYS = 30
SCOUT_CONTACT_VERIFY_MAX_AGE = 7 * 24 * 60 * 60


def _subscription_for(user):
    return AssistantSubscription.get_or_create_for_user(user)


def _scout_preview_path(flow):
    """Return the latest safe preview URL for a session-bound Scout flow."""
    event = (
        ConsumerFunnelEvent.objects.filter(
            event=ConsumerFunnelEvent.Event.SCOUT_PREVIEWED,
            metadata__scout_flow_id=flow["id"],
        )
        .order_by("-created_at")
        .first()
    )
    if not event:
        return reverse("jobs:home")
    metadata = event.metadata or {}
    intent = str(metadata.get("intent_text") or "").strip()
    if not intent:
        return reverse("jobs:home")
    return f"{reverse('jobs:job_scout_preview')}?{urlencode({'intent': intent, 'source': metadata.get('source') or flow.get('source') or 'scout_preview'})}"


def _contact_verify_url(contact):
    token = TimestampSigner(salt="scout-contact-verify").sign(str(contact.pk))
    path = reverse("jobs:scout_contact_verify", kwargs={"token": token})
    return f"{settings.SITE_URL.rstrip('/')}{path}"


def _confirm_contact_marketing(contact):
    """Promote an explicitly opted-in, verified contact to the newsletter list."""
    if not contact.marketing_opt_in or not contact.verified_at:
        return
    subscriber, _ = NewsletterSubscriber.objects.get_or_create(
        email=contact.email,
        defaults={"source": "scout_contact"},
    )
    subscriber.confirmed = True
    subscriber.confirmed_at = subscriber.confirmed_at or contact.verified_at
    subscriber.unsubscribed = False
    subscriber.unsubscribed_at = None
    subscriber.source = "scout_contact"
    subscriber.save(update_fields=["confirmed", "confirmed_at", "unsubscribed", "unsubscribed_at", "source"])


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


def _send_initial_matches(alert, *, source="", flow=None):
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
            metadata={
                "frequency": alert.frequency, "source": source, "matching": diagnostics,
                "results_version": 1, "results": [], **scout_funnel.metadata(flow),
            },
        )
        alert._empty_advice = JobScoutService.empty_advice(diagnostics, JobScoutService.preferences_for_alert(alert))
        return matches, False

    results = snapshot_matches(matches)
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
            "results_version": 1,
            "results": results, **scout_funnel.metadata(flow),
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
        flow = None
        if not context['free_scout_used']:
            flow, _ = scout_funnel.start_flow(self.request, initial_intent, landing_source or 'direct')
            scout_funnel.record_stage(self.request, ConsumerFunnelEvent.Event.SCOUT_FORM_VIEWED, flow,
                                     extra={'intent_prefilled': bool(initial_intent)}, once=True)
        context['scout_flow_id'] = flow['id'] if flow else ''
        context['scout_source'] = landing_source
        context['scout_prefilled'] = bool(initial_intent)
        if landing_source:
            record_consumer_event(
                ConsumerFunnelEvent.Event.SCOUT_LANDED,
                user=self.request.user,
                metadata={
                    "source": landing_source,
                    "intent_prefilled": bool(initial_intent), **scout_funnel.metadata(flow),
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
                context.update(initial_result_context(created))
                if not context["free_scout_used"]:
                    context["form"].initial["intent_text"] = created.intent_text
        return context


class JobScoutPreviewView(TemplateView):
    template_name = "jobs/job_scout_preview.html"

    def post(self, request, *args, **kwargs):
        form = ScoutComposerForm(request.POST)
        if not form.is_valid():
            from django.template.response import TemplateResponse
            return TemplateResponse(request, "jobs/scout_composer_error.html", {"form": form}, status=400)
        intent = " ".join(form.cleaned_data["intent"].split())
        if len(intent) < 20:
            form.add_error("intent", "Describe the job you want in a little more detail.")
            from django.template.response import TemplateResponse
            return TemplateResponse(request, "jobs/scout_composer_error.html", {"form": form}, status=400)
        source = form.cleaned_data.get("source") or "scout_preview"
        flow, _ = scout_funnel.start_flow(request, intent, source)
        if form.cleaned_data.get("email"):
            ScoutContactCaptureView().capture(request, flow, form)
        return redirect(f"{reverse('jobs:job_scout_preview')}?{urlencode({'intent': intent, 'source': source, 'scout_flow': flow['id']})}")

    def get(self, request, *args, **kwargs):
        intent = " ".join(request.GET.get("intent", "").split())[:2000]
        if len(intent) < 20:
            messages.info(request, "Describe the job you want in a little more detail.")
            return redirect("jobs:home")
        self.intent = intent
        source = " ".join(request.GET.get('source', '').split())[:80] or 'scout_preview'
        self.scout_flow, changed = scout_funnel.start_flow(request, intent, source,
                                                    personalized=source == 'scout_personalized')
        if source == 'scout_personalized' and changed:
            scout_funnel.record_stage(request, ConsumerFunnelEvent.Event.SCOUT_PERSONALIZED, self.scout_flow,
                                     extra={'brief_changed': changed})
        self.scout_task = None
        self.preview_pending = False
        self.async_preview = bool(getattr(settings, "SCOUT_PREVIEW_ASYNC_ENABLED", False))
        if self.async_preview:
            if not request.session.session_key:
                request.session.create()
            from jobs.services.scout_tasks import PreviewQueueLimited, enqueue_preview, accessible_task, semantic_key

            task_id = request.GET.get("scout_task", "").strip()
            task = accessible_task(task_id, request.session.session_key) if task_id else None
            if task and (task.intent_text != intent or task.semantic_key != semantic_key(intent)):
                task = None
            explicit_retry = request.GET.get("retry") == "1"
            try:
                task, _ = enqueue_preview(
                    intent,
                    request.session.session_key,
                    force_refresh=explicit_retry,
                    retry_failed=explicit_retry,
                )
            except PreviewQueueLimited:
                self.queue_limited = True
                task = None
            if explicit_retry and task is not None:
                # Consume the one-shot retry flag before loading the page. A
                # browser reload or the polling redirect must not enqueue a
                # second attempt.
                clean_query = request.GET.copy()
                clean_query.pop("retry", None)
                return redirect(f"{request.path}?{clean_query.urlencode()}")
            self.preview_pending = bool(task and task.state in (task.State.QUEUED, task.State.RUNNING))
            self.scout_task = task
        response = super().get(request, *args, **kwargs)
        if self.async_preview:
            response["Cache-Control"] = "private, no-store"
            patch_vary_headers(response, ["Cookie"])
        return response

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        workflow_diagnostics = {}
        stale_result = False
        if self.async_preview:
            preferences = {}
            matches = []
            older_jobs = []
            if self.scout_task and self.scout_task.state == self.scout_task.State.SUCCEEDED:
                from jobs.services.scout_tasks import hydrate_result

                result = self.scout_task.result or {}
                preferences = result.get("preferences", {})
                matches, older_jobs, stale = hydrate_result(result)
                if stale:
                    stale_result = True
                    from jobs.services.scout_tasks import PreviewQueueLimited, enqueue_preview

                    try:
                        self.scout_task, _ = enqueue_preview(
                            self.intent, self.request.session.session_key, force_refresh=True
                        )
                        self.preview_pending = self.scout_task.state in (
                            self.scout_task.State.QUEUED, self.scout_task.State.RUNNING
                        )
                    except PreviewQueueLimited:
                        self.queue_limited = True
                        self.scout_task = None
                    preferences, matches, older_jobs = {}, [], []
                    workflow_diagnostics = {}
                    result = None
                if result is not None:
                    workflow_diagnostics = result.get("diagnostics", {})
            elif self.scout_task and self.scout_task.state == self.scout_task.State.FAILED:
                workflow_diagnostics = {"review_incomplete": True}
            if getattr(self, "queue_limited", False):
                workflow_diagnostics = {"review_incomplete": True}
        else:
            preferences, matches = JobScoutService.search_intent(
                self.intent,
                diagnostics=workflow_diagnostics,
                since=timezone.now() - timedelta(days=30),
                limit=3,
            )
            older_jobs = []
            if not matches and not workflow_diagnostics.get('review_incomplete'):
                from jobs.services.scout_alternatives import older_openings
                older_jobs = older_openings(preferences)
        source = " ".join(self.request.GET.get("source", "").split())[:80] or "scout_preview"
        next_path = f"{reverse('jobs:job_alerts')}?{urlencode({'intent': self.intent, 'source': source, 'scout_flow': self.scout_flow['id']})}"
        if self.async_preview and self.preview_pending:
            context["preview_pending"] = True
            context["preview_status_url"] = reverse(
                "jobs:scout_preview_status", kwargs={"task_id": self.scout_task.pk}
            )
        else:
            context["preview_pending"] = False
        context["preview_queue_limited"] = bool(getattr(self, "queue_limited", False))
        context.update(
            intent=self.intent,
            preferences=preferences,
            preview_jobs=matches,
            older_jobs=older_jobs,
            empty_advice=JobScoutService.empty_advice(workflow_diagnostics, preferences),
            review_incomplete=workflow_diagnostics.get("review_incomplete", False),
            provisional=any(getattr(job, "scout_needs_confirmation", False) for job in matches),
            next_path=next_path,
            scout_flow_id=self.scout_flow['id'],
            sample_preview=self.scout_flow['sample'] and not self.scout_flow['personalized'],
            contact_form=ScoutContactForm(initial={"scout_flow": self.scout_flow["id"]}),
        )
        if not self.request.session.session_key:
            self.request.session.create()
        intent_digest = hashlib.sha256(self.intent.lower().encode("utf-8")).hexdigest()[:16]
        result_ready = (
            not self.async_preview
            or (self.scout_task and self.scout_task.state in (self.scout_task.State.SUCCEEDED, self.scout_task.State.FAILED))
        ) and not stale_result
        if result_ready:
            record_consumer_event(
                ConsumerFunnelEvent.Event.SCOUT_PREVIEWED,
                user=self.request.user,
                metadata={
                    "match_count": len(matches),
                    "intent_text": self.intent[:2000],
                    "workflow": workflow_diagnostics,
                    "source": source,
                    "authenticated": self.request.user.is_authenticated, **scout_funnel.metadata(self.scout_flow),
                },
                dedupe_key=f"scout-preview-flow:{self.scout_flow['id']}:{intent_digest}",
            )
        return context


class ScoutPreviewStatusView(View):
    """Session-bound polling endpoint that reveals only coarse task progress."""

    def get(self, request, task_id, *args, **kwargs):
        from jobs.services.scout_tasks import accessible_task

        task = accessible_task(task_id, request.session.session_key)
        if task is None:
            response = JsonResponse({"status": "expired"}, status=404)
            response["Cache-Control"] = "private, no-store"
            patch_vary_headers(response, ["Cookie"])
            return response
        if task.state in (task.State.QUEUED, task.State.RUNNING):
            response = JsonResponse({"status": "processing"}, status=202)
        else:
            response = JsonResponse({"status": "ready" if task.state == task.State.SUCCEEDED else "retry"})
        response["Cache-Control"] = "private, no-store"
        patch_vary_headers(response, ["Cookie"])
        return response


class ScoutContactCaptureView(View):
    """Capture an optional, purpose-limited follow-up preference for a Scout."""

    def post(self, request, *args, **kwargs):
        flow_id = (request.POST.get("scout_flow") or "").strip()
        flow = scout_funnel.get_flow(request, flow_id) if flow_id else None
        if not flow:
            messages.error(request, "This Scout session has expired. Run the search again to leave a contact preference.")
            return redirect("jobs:home")

        form = ScoutContactForm(request.POST)
        if not form.is_valid():
            messages.error(request, "Enter an email and choose how you would like us to use it.")
            return redirect(_scout_preview_path(flow) + "#scout-contact")

        return self.capture(request, flow, form)

    def capture(self, request, flow, form):
        email = form.cleaned_data["email"].lower()
        now = timezone.now()
        contact_consent = bool(form.cleaned_data.get("contact_consent"))
        marketing_opt_in = bool(form.cleaned_data.get("marketing_opt_in"))
        contact, created = ScoutContact.objects.get_or_create(
            scout_flow_id=flow["id"],
            email=email,
            defaults={
                "source": flow.get("source") or "scout_preview",
                "contact_consent": contact_consent,
                "contact_consent_at": now if contact_consent else None,
                "marketing_opt_in": marketing_opt_in,
                "marketing_opt_in_at": now if marketing_opt_in else None,
            },
        )
        if not created:
            updates = []
            if contact.source != (flow.get("source") or "scout_preview"):
                contact.source = flow.get("source") or "scout_preview"
                updates.append("source")
            if contact_consent and not contact.contact_consent:
                contact.contact_consent = True
                contact.contact_consent_at = now
                updates.extend(["contact_consent", "contact_consent_at"])
            if marketing_opt_in and not contact.marketing_opt_in:
                contact.marketing_opt_in = True
                contact.marketing_opt_in_at = now
                updates.extend(["marketing_opt_in", "marketing_opt_in_at"])
            if updates:
                contact.save(update_fields=updates + ["updated_at"])

        verification_sent = bool(contact.verified_at)
        if not contact.verified_at:
            verification_sent = email_service.send_scout_contact_verification(
                contact, _contact_verify_url(contact)
            )
        else:
            _confirm_contact_marketing(contact)

        record_consumer_event(
            ConsumerFunnelEvent.Event.SCOUT_CONTACT_CAPTURED,
            user=request.user,
            metadata={
                "scout_flow_id": flow["id"],
                "scout_source": flow.get("source") or "scout_preview",
                "contact_consent": contact_consent,
                "marketing_opt_in": marketing_opt_in,
                "verification_sent": verification_sent,
            },
        )
        if contact.verified_at:
            messages.success(request, "Your follow-up preference is saved.")
        elif verification_sent:
            messages.success(request, "Check your inbox to confirm your email and follow-up preference.")
        else:
            messages.success(request, "Your follow-up preference is saved. Email confirmation is temporarily unavailable.")
        return redirect(_scout_preview_path(flow) + "#scout-contact")


class ScoutContactVerifyView(View):
    """Verify an anonymous Scout contact and activate any opted-in marketing."""

    def get(self, request, token, *args, **kwargs):
        try:
            contact_id = TimestampSigner(salt="scout-contact-verify").unsign(
                token, max_age=SCOUT_CONTACT_VERIFY_MAX_AGE
            )
            contact = get_object_or_404(ScoutContact, pk=contact_id)
        except BadSignature:
            messages.error(request, "This email confirmation link is invalid or expired.")
            return redirect("jobs:home")

        if not contact.verified_at:
            contact.verified_at = timezone.now()
            contact.save(update_fields=["verified_at", "updated_at"])
            _confirm_contact_marketing(contact)
            record_consumer_event(
                ConsumerFunnelEvent.Event.SCOUT_CONTACT_VERIFIED,
                metadata={"scout_flow_id": contact.scout_flow_id, "source": contact.source},
                dedupe_key=f"scout-contact-verified:{contact.pk}",
            )
        messages.success(request, "Your email is confirmed. We’ll use it only for the preferences you selected.")
        return redirect("jobs:home")


class JobScoutSignupStartView(View):
    """Track the preview-to-account handoff before entering allauth."""

    def get(self, request, *args, **kwargs):
        next_path = request.GET.get("next", "").strip()
        # The preview only hands off to the alert flow. Keep this redirect
        # internal so the tracking URL cannot become an open redirect.
        parsed_next = urlsplit(next_path)
        if parsed_next.scheme or parsed_next.netloc or parsed_next.path != reverse("jobs:job_alerts"):
            next_path = reverse("jobs:job_alerts")
        source = " ".join(request.GET.get("source", "scout_preview").split())[:80]
        token = parse_qs(urlsplit(next_path).query).get('scout_flow', [''])[0]
        flow = scout_funnel.get_flow(request, token) if token else None
        scout_funnel.prepare_signup(request, flow)
        if not request.session.session_key:
            request.session.create()
        handoff_digest = hashlib.sha256(next_path.encode("utf-8")).hexdigest()[:16]
        record_consumer_event(
            ConsumerFunnelEvent.Event.SCOUT_SIGNUP_STARTED,
            metadata={"source": source, **scout_funnel.metadata(flow)},
            dedupe_key=f"scout-signup-flow:{flow['id']}" if flow else f"scout-signup-start:{request.session.session_key}:{handoff_digest}",
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

    def post(self, request, *args, **kwargs):
        intent = ' '.join(request.POST.get('intent_text', '').split())[:2000]
        source = ' '.join(request.POST.get('source', '').split())[:80] or 'direct'
        self.scout_flow, _ = scout_funnel.start_flow(request, intent, source, reuse_completed=True)
        scout_funnel.record_stage(request, ConsumerFunnelEvent.Event.SCOUT_SUBMIT_STARTED, self.scout_flow)
        try:
            return super().post(request, *args, **kwargs)
        except Exception as exc:
            self._submit_result('error', error_type=type(exc).__name__)
            raise

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        subscription = _subscription_for(self.request.user)
        context['is_pro'] = subscription.is_pro_active()
        context['free_scout_used'] = not context['is_pro'] and subscription.free_job_scout_used_at is not None
        if self.request.method == 'GET' and not context['free_scout_used']:
            intent = ' '.join(self.request.GET.get('intent', '').split())[:2000]
            source = ' '.join(self.request.GET.get('source', '').split())[:80] or 'direct'
            self.scout_flow, _ = scout_funnel.start_flow(self.request, intent, source)
            if intent:
                context['form'].initial['intent_text'] = intent
            scout_funnel.record_stage(self.request, ConsumerFunnelEvent.Event.SCOUT_FORM_VIEWED, self.scout_flow,
                                     extra={'intent_prefilled': bool(intent)}, once=True)
        context['scout_flow_id'] = getattr(self, 'scout_flow', {}).get('id', '')
        context['scout_source'] = (self.request.POST.get('source') or self.request.GET.get('source', ''))[:80]
        context['scout_prefilled'] = bool(self.request.POST.get('intent_text') or self.request.GET.get('intent'))
        return context

    def _submit_result(self, outcome, **extra):
        scout_funnel.record_stage(self.request, ConsumerFunnelEvent.Event.SCOUT_SUBMIT_RESULT,
                                 self.scout_flow, extra={'outcome': outcome, **extra})

    def form_invalid(self, form):
        self._submit_result('invalid', invalid_fields=sorted(form.errors))
        response = super().form_invalid(form)
        response.context_data['scout_flow_id'] = self.scout_flow['id']
        return response

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
                self._submit_result('duplicate')
                return redirect(f"{reverse('jobs:job_alerts')}?created={existing.pk}")
            limit_reached = (
                JobAlert.objects.filter(user=self.request.user, is_active=True).count() >= 5
                if is_pro else subscription.free_job_scout_used_at is not None
            )
            if limit_reached:
                self._submit_result('blocked', reason='active_limit' if is_pro else 'free_credit_used')
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
        matches, sent = _send_initial_matches(self.object, source=source, flow=self.scout_flow)
        if not is_pro and matches and sent:
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
                "source": source, **scout_funnel.metadata(self.scout_flow),
            },
        )
        self._submit_result('created', match_count=len(matches), email_sent=sent)
        scout_funnel.complete_flow(self.request, self.scout_flow)
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
                "We found matches, but the email could not be sent. You can see them below. Your free one-time Scout is still available."
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


class JobAlertRetryEmailView(LoginRequiredMixin, View):
    """Explicit owner POST: resend available saved results, never rerun matching."""

    def post(self, request, pk):
        with transaction.atomic():
            # Serialize retries on PostgreSQL. Cache also bounds repeated failures.
            alert = get_object_or_404(JobAlert.objects.select_for_update(), pk=pk, user=request.user)
            target = f"{reverse('jobs:job_alerts')}?created={alert.pk}"
            event = alert.funnel_events.filter(event__in=[
                ConsumerFunnelEvent.Event.INITIAL_EMAIL_FAILED,
                ConsumerFunnelEvent.Event.INITIAL_EMAIL_SENT,
                ConsumerFunnelEvent.Event.INITIAL_MATCHES_EMPTY,
            ]).order_by("-created_at", "-pk").first()
            if not event or event.event != ConsumerFunnelEvent.Event.INITIAL_EMAIL_FAILED:
                messages.info(request, "There is no failed initial email to retry.")
                return redirect(target)
            subscription = AssistantSubscription.objects.select_for_update().get(pk=_subscription_for(request.user).pk)
            if (not subscription.is_pro_active() and subscription.free_job_scout_used_at
                    and ConsumerFunnelEvent.objects.filter(user=request.user,
                        event=ConsumerFunnelEvent.Event.INITIAL_EMAIL_SENT).exclude(alert=alert).exists()):
                messages.info(request, "Your one-time matching email has already been sent for another Scout. These saved matches are still available on this page.")
                return redirect(target)
            matches = initial_result_context(alert)["preview_jobs"]
            if not matches:
                messages.info(request, "Those saved matches are no longer available. Browse current jobs or start an eligible new search.")
                return redirect(target)
            if not cache.add(f"scout-email-retry:{alert.pk}", True, timeout=60):
                messages.info(request, "Please wait a minute before retrying this email.")
                return redirect(target)
            sent = email_service.send_job_alert(alert, matches, is_initial=True)
            if sent:
                JobAlertDelivery.objects.bulk_create([
                    JobAlertDelivery(alert=alert, job=job, match_score=getattr(job, "scout_score", 0),
                                     match_reasons=getattr(job, "scout_reasons", [])) for job in matches
                ], ignore_conflicts=True)
                from jobs.services import job_stats
                for job in matches:
                    job_stats.record(job.pk, "scout_deliveries", dedupe=False)
                alert.last_sent_at = timezone.now()
                alert.save(update_fields=["last_sent_at"])
                if not subscription.is_pro_active() and not subscription.free_job_scout_used_at:
                    subscription.free_job_scout_used_at = timezone.now()
                    subscription.save(update_fields=["free_job_scout_used_at"])
            record_consumer_event(
                ConsumerFunnelEvent.Event.INITIAL_EMAIL_SENT if sent else ConsumerFunnelEvent.Event.INITIAL_EMAIL_FAILED,
                user=request.user, alert=alert,
                metadata={**(event.metadata or {}), "retry": True, "match_count": len(matches),
                          "results_version": 1, "results": snapshot_matches(matches)},
            )
            messages.success(request, "We emailed your saved matches.") if sent else messages.warning(
                request, "The email still could not be sent. Your saved matches remain available below.")
        return redirect(target)


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
                # Selling "keep watching" before a Scout has found anything rarely works; fix the brief first.
                "needs_brief_fix": not deliveries.exists(),
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
