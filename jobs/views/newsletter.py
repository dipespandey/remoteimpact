"""Views for newsletter subscription management."""

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.signing import BadSignature, SignatureExpired, TimestampSigner
from django.shortcuts import redirect
from django.utils import timezone
from django.views import View
from django.views.generic import TemplateView


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
