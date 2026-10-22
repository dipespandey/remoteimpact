"""Public roundup landing, email verification and private preference links."""
import hashlib
import uuid
from datetime import timedelta

from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.core.signing import BadSignature
from django.core.validators import validate_email
from django.db import transaction
from django.http import HttpResponse
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
        email = request.POST.get("email", "").strip().lower()
        try:
            validate_email(email)
            if len(email) > 254:
                raise ValidationError("Email too long")
        except ValidationError:
            return render(request, "jobs/newsletter/roundup.html", {**context, "error": "Enter a valid email address."}, status=400)
        if request.POST.get("consent") != "yes":
            return render(request, "jobs/newsletter/roundup.html", {**context, "error": "Please agree to the weekly email before subscribing."}, status=400)
        if request.POST.get("website"):
            return render(request, "jobs/newsletter/roundup.html", {**context, "requested": True})
        # Shared cache protects mail volume; hash keys so addresses/IPs aren't retained there.
        ip = request.META.get("HTTP_CF_CONNECTING_IP") or request.META.get("REMOTE_ADDR", "")
        key = "roundup-ip:" + hashlib.sha256(ip.encode()).hexdigest()
        cache.add(key, 0, 3600)
        try:
            limited = cache.incr(key) > 10
        except ValueError:
            limited = True
        if limited:
            response = render(request, "jobs/newsletter/roundup.html", {**context, "error": "Please wait before requesting another confirmation email."}, status=429)
            response["Retry-After"] = "3600"
            return response
        now = timezone.now()
        with transaction.atomic():
            sub, created = RoundupSubscriber.objects.get_or_create(email=email, defaults={"topic": context["topic"], "source": context["source"], "campaign": context["campaign"]})
            sub = RoundupSubscriber.objects.select_for_update().get(pk=sub.pk)
            if sub.confirmed_at and not sub.unsubscribed_at:
                # No preference changes or account-existence disclosure from a public form.
                return render(request, "jobs/newsletter/roundup.html", {**context, "requested": True})
            if sub.confirmation_requested_at and sub.confirmation_requested_at > now - timedelta(minutes=10):
                return render(request, "jobs/newsletter/roundup.html", {**context, "requested": True})
            if sub.unsubscribed_at:
                sub.confirmation_key = uuid.uuid4()  # Old links cannot renew consent.
            sub.topic, sub.source, sub.campaign = context["topic"], context["source"], context["campaign"]
            sub.consent_at, sub.confirmation_requested_at = now, now
            try:
                ref = active_subscribers().filter(referral_code=uuid.UUID(context["ref"])).exclude(pk=sub.pk).first()
            except (ValueError, AttributeError):
                ref = None
            if created or sub.unsubscribed_at:
                sub.referred_by = ref
            sub.save()
        confirm = public_url("jobs:roundup_confirm", token=token_for(sub, "confirm"))
        stop = public_url("jobs:roundup_unsubscribe", token=token_for(sub, "unsubscribe"))
        # No broadcast is triggered here. A confirmation is the only requested email.
        from django.template.loader import render_to_string
        html = render_to_string("emails/roundup_confirm.html", {"confirm_url": confirm, "stop_url": stop})
        accepted = email_service.send_email(email, "Confirm your Remote Impact weekly roundup", html,
            text=f"Confirm one weekly job roundup: {confirm}\nIf you didn't request this, ignore it. Stop requests: {stop}", unsubscribe_url=stop)
        if not accepted:
            return render(request, "jobs/newsletter/roundup.html", {**context, "error": "We couldn't send the confirmation. Please try again in 10 minutes."}, status=503)
        return render(request, "jobs/newsletter/roundup.html", {**context, "requested": True})



class PrivateLinkView(View):
    def dispatch(self, request, *args, **kwargs):
        response = super().dispatch(request, *args, **kwargs)
        response["Referrer-Policy"] = "no-referrer"
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

    def post(self, request, token):
        try:
            sub = subscriber_for(token, "confirm")
        except (BadSignature, RoundupSubscriber.DoesNotExist, ValidationError, ValueError):
            return render(request, "jobs/newsletter/roundup_status.html", {"invalid": True}, status=400)
        confirmed_at = sub.confirmed_at if sub.confirmed_at and not sub.unsubscribed_at else timezone.now()
        changed = RoundupSubscriber.objects.filter(pk=sub.pk, confirmation_key=sub.confirmation_key).update(confirmed_at=confirmed_at, unsubscribed_at=None)
        if not changed:
            return render(request, "jobs/newsletter/roundup_status.html", {"invalid": True}, status=400)
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
        RoundupSubscriber.objects.filter(pk=sub.pk).update(
            unsubscribed_at=timezone.now(), confirmation_key=uuid.uuid4())
        return render(request, "jobs/newsletter/roundup_status.html", {"unsubscribed": True})
