"""Views for newsletter subscription management."""

import json
import logging
import re

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.signing import BadSignature, TimestampSigner
from django.http import HttpResponse, JsonResponse
from django.shortcuts import redirect
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.csrf import csrf_exempt
from django.views.generic import TemplateView

from jobs.models import NewsletterSubscriber

logger = logging.getLogger(__name__)


def unsubscribe_user(user):
    """Stop all marketing email for a user (drips, digests, newsletter).

    Some accounts never got a profile row; creating it here is what makes the opt-out
    stick (the drip sender filters on the profile).
    """
    from jobs.services.newsletter_audience import unsubscribe_newsletters
    if user.email:
        unsubscribe_newsletters(user.email)


@method_decorator(csrf_exempt, name="dispatch")
class NewsletterUnsubscribeView(TemplateView):
    """One-click unsubscribe using a signed token. Links never expire."""

    template_name = "jobs/newsletter/unsubscribe.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["success"] = False
        context["error"] = None
        return context

    def _user_for(self, token):
        from django.contrib.auth import get_user_model

        user_id = TimestampSigner(salt="newsletter-unsubscribe").unsign(token)
        return get_user_model().objects.get(id=user_id)

    def _unsubscribe_subscriber(self, token):
        """Newsletter subscribers without an account carry a signed email instead of a user id."""
        email = TimestampSigner(salt="subscriber-unsubscribe").unsign(token)
        from jobs.services.newsletter_audience import unsubscribe_newsletters
        unsubscribe_newsletters(email)
        return email

    def get(self, request, token):
        context = self.get_context_data()
        try:
            if "@" in token:
                context["email"] = self._unsubscribe_subscriber(token)
            else:
                user = self._user_for(token)
                unsubscribe_user(user)
                context["email"] = user.email
            context["success"] = True
        except BadSignature:
            context["error"] = "This unsubscribe link isn't valid. Reply to any of our emails and we'll remove you."
        except Exception:  # noqa: BLE001 - never leave someone unable to unsubscribe silently
            logger.exception("Newsletter unsubscribe failed")
            context["error"] = "Something went wrong. Reply to any of our emails and we'll remove you right away."
        return self.render_to_response(context)

    def post(self, request, token):
        """RFC 8058 one-click unsubscribe from the mail client."""
        try:
            if "@" in token:
                self._unsubscribe_subscriber(token)
            else:
                unsubscribe_user(self._user_for(token))
        except Exception:  # noqa: BLE001
            logger.exception("One-click unsubscribe failed")
            return HttpResponse(status=400)
        return HttpResponse(status=200)


class NewsletterPreferencesView(LoginRequiredMixin, View):
    """Manage newsletter preferences for logged-in users."""

    def post(self, request):
        """Toggle newsletter subscription."""
        from django.core.exceptions import ValidationError
        from jobs.services.newsletter_audience import subscribe_account, unsubscribe_newsletters
        subscribe = request.POST.get("subscribe") == "true"
        try:
            if subscribe:
                from allauth.account.models import EmailAddress
                if not EmailAddress.objects.filter(user=request.user, email__iexact=request.user.email, verified=True).exists():
                    # Reuse the rate-limited double-opt-in flow. An account alone
                    # does not establish control of an unverified address.
                    from .roundup import RoundupView
                    payload = request.POST.copy()
                    payload.update({"email":request.user.email, "consent":"yes", "source":"account_preferences", "topic":"all"})
                    request.POST = payload
                    return RoundupView().post(request)
                subscribe_account(request.user)
                messages.success(request, "You're subscribed to the weekly job roundup. No completed profile is needed.")
            else:
                unsubscribe_newsletters(request.user.email)
                messages.success(request, "You've been unsubscribed from newsletter emails.")
        except ValidationError:
            messages.error(request, "Add a valid email address to your account before subscribing.")

        return redirect("jobs:account")


class NewsletterSubscribeView(View):
    """Handle anonymous newsletter signups from the footer."""

    def post(self, request):
        """Subscribe an email to the newsletter."""
        # Get email from POST data or JSON body
        if request.content_type == "application/json":
            try:
                data = json.loads(request.body)
                email = data.get("email", "").strip().lower()
            except json.JSONDecodeError:
                return JsonResponse({"success": False, "error": "Invalid request"}, status=400)
        else:
            email = request.POST.get("email", "").strip().lower()

        # Validate email
        if not email:
            return JsonResponse({"success": False, "error": "Email is required"}, status=400)

        # Simple email validation
        email_pattern = r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$"
        if not re.match(email_pattern, email):
            return JsonResponse({"success": False, "error": "Please enter a valid email"}, status=400)

        # Check if already a registered user
        from django.contrib.auth import get_user_model
        User = get_user_model()
        if User.objects.filter(email=email).exists():
            return JsonResponse({
                "success": True,
                "message": "You're already registered! Check your account settings for newsletter preferences."
            })

        # Create or update subscriber
        subscriber, created = NewsletterSubscriber.objects.get_or_create(
            email=email,
            defaults={"source": request.POST.get("source", "footer")}
        )

        if not created:
            if subscriber.unsubscribed:
                # Re-subscribe
                subscriber.unsubscribed = False
                subscriber.unsubscribed_at = None
                subscriber.save()
                return JsonResponse({
                    "success": True,
                    "message": "Welcome back! You've been re-subscribed."
                })
            else:
                return JsonResponse({
                    "success": True,
                    "message": "You're already subscribed!"
                })

        # For now, auto-confirm (no double opt-in)
        # Can add email confirmation later if needed
        subscriber.confirmed = True
        subscriber.confirmed_at = timezone.now()
        subscriber.save()

        return JsonResponse({
            "success": True,
            "message": "You're subscribed to occasional product updates, no more than once a month."
        })
