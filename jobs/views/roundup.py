"""Public roundup landing, email verification and private preference links."""
import hashlib
import uuid
from datetime import timedelta

from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.core.signing import BadSignature
from django.core.validators import validate_email
from django.db import transaction
from django.conf import settings
from django.http import HttpResponse, JsonResponse
from django.shortcuts import render
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.cache import never_cache
from django.views.decorators.csrf import csrf_exempt

from jobs.models import RoundupSubscriber
from jobs.services.email_service import email_service
from jobs.services.roundup import CONSENT, TOPICS, active_subscribers, public_url, safe_label, sample_jobs, share_url, subscriber_for, token_for


@method_decorator(never_cache, name="dispatch")
class RoundupView(View):
    def context(self, request, **extra):
        topic = request.POST.get("topic", request.GET.get("topic", "all"))
        if topic not in TOPICS:
            topic = "all"
        return {"topics": TOPICS.items(), "topic": topic, "consent_copy": CONSENT,
                "source": safe_label(request.POST.get("source", request.GET.get("utm_source", request.GET.get("source"))), "newsletter"),
                "campaign": safe_label(request.POST.get("campaign", request.GET.get("utm_campaign"))),
                "ref": request.POST.get("ref", request.GET.get("ref", ""))[:36],
                "sample_jobs": sample_jobs(topic), **extra}

    def get(self, request):
        return render(request, "jobs/newsletter/roundup.html", self.context(request))

    def post(self, request):
        context = self.context(request)
        if request.POST.get("consent") != "yes" and not request.POST.get("website"):
            outcome = "no_consent" if valid_email(request.POST.get("email", "")) else "invalid"
        else:
            outcome = request_weekly(request, request.POST.get("email", ""), context["topic"], context["source"],
                                     context["campaign"], context["ref"])
        if outcome == "requested":
            return render(request, "jobs/newsletter/roundup.html", {**context, "requested": True})
        response = render(request, "jobs/newsletter/roundup.html", {**context, "error": ERRORS[outcome]}, status=STATUS[outcome])
        if outcome == "rate_limited":
            response["Retry-After"] = "3600"
        return response


@method_decorator(never_cache, name="dispatch")
@method_decorator(csrf_exempt, name="dispatch")
class RoundupQuickView(View):
    """One-field signup for prompts on cached pages, which carry no CSRF cookie. Same-origin only."""

    def post(self, request):
        if request.headers.get("Origin") != settings.SITE_URL.rstrip("/"):
            return JsonResponse({"error": "Forbidden"}, status=403)
        if request.POST.get("consent") != "yes":
            return JsonResponse({"error": ERRORS["no_consent"]}, status=400)
        topic = request.POST.get("topic", "all")
        outcome = request_weekly(request, request.POST.get("email", ""), topic if topic in TOPICS else "all",
                                 safe_label(request.POST.get("source"), "prompt"), safe_label(request.POST.get("campaign")), "")
        if outcome == "requested":
            return JsonResponse({"requested": True})
        return JsonResponse({"error": ERRORS[outcome]}, status=STATUS[outcome])


ERRORS = {"invalid": "Enter a valid email address.",
          "no_consent": "Please agree to the weekly email before subscribing.",
          "rate_limited": "Please wait before requesting another confirmation email.",
          "send_failed": "We couldn't send the confirmation. Please try again in 10 minutes."}
STATUS = {"invalid": 400, "no_consent": 400, "rate_limited": 429, "send_failed": 503}


def valid_email(email):
    try:
        validate_email(email)
    except ValidationError:
        return False
    return len(email) <= 254


def request_weekly(request, email, topic, source, campaign, ref):
    """Record a weekly signup request and email its confirmation link.

    Returns "requested", "invalid", "rate_limited" or "send_failed". Nobody receives the
    roundup until they confirm, and the public result never reveals whether an address exists.
    """
    email = email.strip().lower()
    if not valid_email(email):
        return "invalid"
    if request.POST.get("website"):
        return "requested"
    # Shared cache protects mail volume; hash keys so addresses/IPs aren't retained there.
    ip = request.META.get("HTTP_CF_CONNECTING_IP") or request.META.get("REMOTE_ADDR", "")
    key = "roundup-ip:" + hashlib.sha256(ip.encode()).hexdigest()
    cache.add(key, 0, 3600)
    try:
        if cache.incr(key) > 10:
            return "rate_limited"
    except ValueError:
        return "rate_limited"
    now = timezone.now()
    with transaction.atomic():
        sub, created = RoundupSubscriber.objects.get_or_create(email=email, defaults={"topic": topic, "source": source, "campaign": campaign})
        sub = RoundupSubscriber.objects.select_for_update().get(pk=sub.pk)
        if sub.confirmed_at and not sub.unsubscribed_at:
            return "requested"
        if sub.confirmation_requested_at and sub.confirmation_requested_at > now - timedelta(minutes=10):
            return "requested"
        if sub.unsubscribed_at:
            sub.confirmation_key = uuid.uuid4()  # Old links cannot renew consent.
        sub.topic, sub.source, sub.campaign = topic, source, campaign
        sub.consent_at, sub.confirmation_requested_at = now, now
        sub.consent_basis = "weekly_request"
        sub.consent_version = "weekly-roundup-2026-10-23"
        try:
            referrer = active_subscribers().filter(referral_code=uuid.UUID(ref)).exclude(pk=sub.pk).first()
        except (ValueError, AttributeError):
            referrer = None
        if created or sub.unsubscribed_at:
            sub.referred_by = referrer
        sub.save()
    confirm = public_url("jobs:roundup_confirm", token=token_for(sub, "confirm"))
    stop = public_url("jobs:roundup_unsubscribe", token=token_for(sub, "unsubscribe"))
    from django.template.loader import render_to_string
    html = render_to_string("emails/roundup_confirm.html", {"confirm_url": confirm, "stop_url": stop})
    accepted = email_service.send_email(email, "Confirm your Remote Impact weekly roundup", html,
        text=f"Confirm one weekly job roundup: {confirm}\nIf you didn't request this, ignore it. Stop requests: {stop}", unsubscribe_url=stop)
    return "requested" if accepted else "send_failed"


class PrivateLinkView(View):
    def dispatch(self, request, *args, **kwargs):
        response = super().dispatch(request, *args, **kwargs)
        response["Referrer-Policy"] = "same-origin"
        response["X-Robots-Tag"] = "noindex, nofollow"
        return response


@method_decorator(never_cache, name="dispatch")
class RoundupConfirmView(PrivateLinkView):
    def get(self, request, token):
        try:
            subscriber_for(token, "confirm")
        except (BadSignature, RoundupSubscriber.DoesNotExist, ValidationError, ValueError):
            return render(request, "jobs/newsletter/roundup_status.html", {"invalid": True}, status=400)
        # GET is scanner-safe: only a deliberate POST confirms consent.
        return render(request, "jobs/newsletter/roundup_status.html", {"confirm": True})

    @transaction.atomic
    def post(self, request, token):
        try:
            sub = subscriber_for(token, "confirm")
        except (BadSignature, RoundupSubscriber.DoesNotExist, ValidationError, ValueError):
            return render(request, "jobs/newsletter/roundup_status.html", {"invalid": True}, status=400)
        confirmed_at = sub.confirmed_at if sub.confirmed_at and not sub.unsubscribed_at else timezone.now()
        changed = RoundupSubscriber.objects.filter(pk=sub.pk, confirmation_key=sub.confirmation_key).update(confirmed_at=confirmed_at, unsubscribed_at=None, consent_basis="weekly_request")
        if not changed:
            return render(request, "jobs/newsletter/roundup_status.html", {"invalid": True}, status=400)
        from jobs.services.newsletter_audience import mirror_weekly_preference
        mirror_weekly_preference(sub.email)
        return render(request, "jobs/newsletter/roundup_status.html", {"confirmed": True, "share_url": share_url(sub)})


@method_decorator(never_cache, name="dispatch")
@method_decorator(csrf_exempt, name="dispatch")
class RoundupUnsubscribeView(PrivateLinkView):
    def get(self, request, token):
        return render(request, "jobs/newsletter/roundup_status.html", {"unsubscribe": True})

    def post(self, request, token):
        try:
            sub = subscriber_for(token, "unsubscribe")
        except (BadSignature, RoundupSubscriber.DoesNotExist, ValidationError, ValueError):
            return HttpResponse("Invalid unsubscribe link", status=400)
        from jobs.services.newsletter_audience import unsubscribe_newsletters
        unsubscribe_newsletters(sub.email)
        return render(request, "jobs/newsletter/roundup_status.html", {"unsubscribed": True})
