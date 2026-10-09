"""Remind trialing Pro subscribers a few days before their first charge.

Runs daily. It does not depend on the Stripe ``trial_will_end`` webhook being
enabled: candidates come from local entitlements and Stripe is asked for the
real trial state before anything is sent.

Usage:
    python manage.py send_pro_trial_reminders
    python manage.py send_pro_trial_reminders --dry-run
"""
from datetime import datetime, timedelta, timezone as dt_timezone

import stripe
from django.conf import settings
from django.core.management.base import BaseCommand
from django.utils import timezone

from jobs.models import AssistantSubscription
from jobs.services.payment_service import PaymentService


class Command(BaseCommand):
    help = "Email trialing Pro subscribers before the trial converts to a paid plan."

    def add_arguments(self, parser):
        parser.add_argument("--days", type=int, default=3, help="Remind when the trial ends within this many days.")
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args, **options):
        stripe.api_key = settings.STRIPE_SECRET_KEY
        now = timezone.now()
        candidates = (
            AssistantSubscription.objects.filter(
                is_subscribed=True,
                expires_at__gt=now,
                expires_at__lte=now + timedelta(days=options["days"]),
            )
            .exclude(stripe_subscription_id="")
            .select_related("user")
        )
        sent = 0
        for subscription in candidates:
            try:
                remote = stripe.Subscription.retrieve(subscription.stripe_subscription_id)
            except stripe.error.StripeError as exc:
                self.stderr.write(f"Could not load {subscription.stripe_subscription_id}: {exc}")
                continue
            if remote.get("status") != "trialing" or not remote.get("trial_end"):
                continue
            trial_end = datetime.fromtimestamp(remote["trial_end"], tz=dt_timezone.utc)
            if trial_end > now + timedelta(days=options["days"]):
                continue
            if options["dry_run"]:
                self.stdout.write(f"Would remind {subscription.user.email} (trial ends {trial_end:%Y-%m-%d})")
                continue
            if PaymentService.send_trial_reminder(subscription, trial_end, stripe_subscription_id=remote["id"]):
                sent += 1
        self.stdout.write(self.style.SUCCESS(f"Trial reminders sent: {sent}"))
