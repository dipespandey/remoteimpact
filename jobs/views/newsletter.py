"""Views for newsletter subscription management."""

import json
import re

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.signing import BadSignature, SignatureExpired, TimestampSigner
from django.http import JsonResponse
from django.shortcuts import redirect
from django.utils import timezone
from django.views import View
from django.views.generic import TemplateView

from jobs.models import NewsletterSubscriber


class NewsletterUnsubscribeView(TemplateView):
    """One-click unsubscribe using signed token."""

    template_name = "jobs/newsletter/unsubscribe.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["success"] = False
        context["error"] = None
        return context

    def get(self, request, token):
        """Verify token and show confirmation page."""
        context = self.get_context_data()

        try:
            signer = TimestampSigner(salt="newsletter-unsubscribe")
            # Token valid for 30 days
            user_id = signer.unsign(token, max_age=30 * 24 * 60 * 60)

            from django.contrib.auth import get_user_model

            User = get_user_model()
            user = User.objects.get(id=user_id)

            # Unsubscribe the user
            profile = user.profile
            profile.email_newsletter = False
            profile.email_newsletter_unsubscribed_at = timezone.now()
            profile.save()

            context["success"] = True
            context["email"] = user.email

        except SignatureExpired:
            context["error"] = "This unsubscribe link has expired. Please contact support."
        except BadSignature:
            context["error"] = "Invalid unsubscribe link."
        except Exception:
            context["error"] = "Unable to process unsubscribe request."

        return self.render_to_response(context)


class NewsletterPreferencesView(LoginRequiredMixin, View):
    """Manage newsletter preferences for logged-in users."""

    def post(self, request):
        """Toggle newsletter subscription."""
        profile = request.user.profile
        subscribe = request.POST.get("subscribe") == "true"

        profile.email_newsletter = subscribe
        if not subscribe:
            profile.email_newsletter_unsubscribed_at = timezone.now()
        else:
            profile.email_newsletter_unsubscribed_at = None
        profile.save()

        if subscribe:
            messages.success(request, "You've been subscribed to our weekly job digest.")
        else:
            messages.success(request, "You've been unsubscribed from our weekly job digest.")

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
            "message": "You're in! Watch for our quarterly digest."
        })
