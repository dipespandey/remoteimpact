"""Validated transport identity and mandatory staff second-factor checks."""
import ipaddress
import time
from urllib.parse import urlencode

from django.conf import settings
from django.shortcuts import redirect
from django.urls import reverse


def client_ip(request):
    return getattr(request, "client_ip", request.META.get("REMOTE_ADDR", "0.0.0.0"))


class TrustedProxyMiddleware:
    """Only the loopback proxy may supply our overwritten origin identity header."""
    def __init__(self, get_response):
        self.get_response = get_response
        self.networks = [ipaddress.ip_network(n) for n in settings.TRUSTED_PROXY_CIDRS]

    def __call__(self, request):
        peer = request.META.get("REMOTE_ADDR", "")
        chosen = peer
        try:
            trusted = any(ipaddress.ip_address(peer) in n for n in self.networks)
            supplied = request.META.get("HTTP_X_REMOTEIMPACT_CLIENT_IP", "")
            if trusted and supplied:
                chosen = str(ipaddress.ip_address(supplied))
        except ValueError:
            pass
        request.client_ip = chosen or "0.0.0.0"
        request.META["REMOTE_ADDR"] = request.client_ip
        # Legacy consumers must not see a forged Cloudflare or forwarding header.
        request.META.pop("HTTP_CF_CONNECTING_IP", None)
        request.META.pop("HTTP_X_FORWARDED_FOR", None)
        request.META.pop("HTTP_X_REMOTEIMPACT_CLIENT_IP", None)
        return self.get_response(request)


class StaffMFAMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = getattr(request, "user", None)
        if (settings.ADMIN_REQUIRE_MFA and request.path.startswith("/admin/")
                and user and user.is_authenticated and user.is_staff):
            from allauth.account.authentication import get_authentication_records
            from allauth.account.models import EmailAddress
            from allauth.mfa.models import Authenticator
            auths = Authenticator.objects.filter(user=user)
            if not auths.filter(type=Authenticator.Type.TOTP).exists():
                target = "account_email" if EmailAddress.objects.filter(user=user, verified=False).exists() else "mfa_activate_totp"
                return redirect(reverse(target))
            recent_ids = [r.get("id") for r in get_authentication_records(request)
                          if r.get("method") == "mfa" and isinstance(r.get("at"), (int, float))
                          and time.time() - settings.ADMIN_MFA_MAX_AGE <= r["at"] <= time.time() + 30]
            if not auths.filter(pk__in=recent_ids).exists():
                return redirect(reverse("mfa_reauthenticate") + "?" + urlencode({"next": request.get_full_path()}))
        return self.get_response(request)
