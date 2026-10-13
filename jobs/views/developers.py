"""Jobs API and MCP page: documentation, free keys on the spot, commercial access on request."""
import logging

from django.conf import settings
from django.contrib import messages
from django.core.validators import validate_email
from django.core.exceptions import ValidationError
from django.shortcuts import redirect
from django.template.loader import render_to_string
from django.views.generic import TemplateView

from ..models import ApiKey
from ..services.job_service import JobService

logger = logging.getLogger(__name__)
KEYS_PER_IP_PER_DAY = 3


class DevelopersView(TemplateView):
    template_name = "jobs/developers.html"

    def get(self, request, *args, **kwargs):
        self._log("developers_page")
        return super().get(request, *args, **kwargs)

    def _log(self, endpoint, tier="anonymous"):
        """Page views and sign-ups appear in the API usage dashboard under surface "page" (bots skipped)."""
        from ..api import usage
        from ..services.job_stats import is_probably_bot

        if not is_probably_bot(self.request):
            usage.record(self.request, type("Visitor", (), {"tier": tier, "key": None})(), "page", endpoint)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        site = getattr(settings, "SITE_URL", "https://remoteimpact.org").rstrip("/")
        context.update({
            "site": site, "active_jobs_count": JobService.get_available_jobs().count(),
            "api_url": f"{site}/api/v1/jobs/", "mcp_url": f"{site}/mcp/",
            "new_key": self.request.session.pop("new_api_key", None),
        })
        return context

    def post(self, request):
        kind = request.POST.get("kind", "free")
        name = (request.POST.get("name") or "").strip()[:120]
        email = (request.POST.get("email") or "").strip().lower()
        organization = (request.POST.get("organization") or "").strip()[:160]
        use_case = (request.POST.get("use_case") or "").strip()[:2000]
        if request.POST.get("website"):  # honeypot
            return redirect("jobs:developers")
        try:
            validate_email(email)
        except ValidationError:
            messages.error(request, "Please enter a valid email address.")
            return redirect("jobs:developers")
        if not name:
            messages.error(request, "Please tell us your name.")
            return redirect("jobs:developers")
        ip = request.META.get("HTTP_CF_CONNECTING_IP") or request.META.get("REMOTE_ADDR", "")
        counter = f"api-key-signup:{ip}"
        from ..api.auth import _bump
        if _bump(counter, 86400) > KEYS_PER_IP_PER_DAY:
            messages.error(request, "That's enough keys for today from this connection. Email hello@remoteimpact.org if you need more.")
            return redirect("jobs:developers")
        tier = ApiKey.Tier.COMMERCIAL if kind == "commercial" else ApiKey.Tier.FREE
        raw, key = ApiKey.issue(name=name, email=email, tier=tier, organization=organization, use_case=use_case,
                                is_active=(tier == ApiKey.Tier.FREE))
        self._email_key(key, raw)
        self._log("key_signup_" + tier.value, tier.value)
        if tier == ApiKey.Tier.FREE:
            request.session["new_api_key"] = raw
            messages.success(request, "Your key is ready. It's shown once below and has been emailed to you.")
        else:
            messages.success(request, "Thanks. We've emailed you a key; it switches on once we've confirmed the details with you, usually within a day.")
        return redirect("jobs:developers")

    @staticmethod
    def _email_key(key, raw):
        from ..services.email_service import email_service

        context = {"key": key, "raw": raw, "site": getattr(settings, "SITE_URL", "https://remoteimpact.org").rstrip("/")}
        try:
            email_service.send_email(
                to=key.email, subject="Your Remote Impact API key",
                html=render_to_string("emails/api_key.html", context), text=render_to_string("emails/api_key.txt", context))
            if key.tier == ApiKey.Tier.COMMERCIAL:
                owner = getattr(settings, "PAYMENT_NOTIFY_EMAIL", "hello@remoteimpact.org")
                body = (f"Commercial API access requested.\n\nName: {key.name}\nEmail: {key.email}\nOrganization: {key.organization}\n"
                        f"Use case: {key.use_case}\n\nActivate in admin: {context['site']}/admin/jobs/apikey/{key.pk}/change/")
                email_service.send_email(to=owner, subject=f"API access request: {key.organization or key.name}",
                                         html="<p>" + body.replace("\n", "<br>") + "</p>", text=body)
        except Exception:  # noqa: BLE001 - the key is still shown on the page
            logger.exception("API key email failed")
