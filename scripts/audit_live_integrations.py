"""Read-only Stripe/Resend checks; no checkout, payment, or email is created."""

import json
from urllib.parse import urlsplit

import requests
import stripe
from django.conf import settings

report = {}
stripe.api_key = settings.STRIPE_SECRET_KEY
stripe.max_network_retries = 1
try:
    account = stripe.Account.retrieve()
    report["stripe"] = {
        "live_key": stripe.api_key.startswith("sk_live_"),
        "charges_enabled": account.get("charges_enabled"),
        "payouts_enabled": account.get("payouts_enabled"),
        "currently_due_requirements": list((account.get("requirements") or {}).get("currently_due", [])),
        "webhook_secret_configured": bool(settings.STRIPE_WEBHOOK_SECRET),
    }
    endpoints = stripe.WebhookEndpoint.list(limit=100)
    report["stripe"]["site_webhooks"] = [
        {"url": item.url, "status": item.status, "events": list(item.enabled_events)}
        for item in endpoints.data
        if urlsplit(item.url).hostname == "remoteimpact.org"
    ]
except Exception as exc:
    report["stripe_error"] = type(exc).__name__

try:
    response = requests.get("https://api.resend.com/domains", headers={
        "Authorization": "Bearer " + settings.RESEND_API_KEY}, timeout=15)
    report["email"] = {"domain_api_status": response.status_code}
    if response.ok:
        report["email"]["domains"] = [{"name": item["name"], "status": item["status"]}
                                       for item in response.json().get("data", [])]
    report["email"]["from_address"] = settings.DEFAULT_FROM_EMAIL
except Exception as exc:
    report["email_error"] = type(exc).__name__

response = requests.post("https://remoteimpact.org/webhook/stripe/", data=b"{}",
                         headers={"Stripe-Signature": "invalid", "Content-Type": "application/json"}, timeout=15)
report["unsigned_webhook_rejected"] = response.status_code == 400
print("INTEGRATION_REPORT=" + json.dumps(report), flush=True)
