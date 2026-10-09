from datetime import timedelta

import stripe
from django.conf import settings
from django.core.management.base import BaseCommand
from django.utils import timezone

from jobs.services.payment_service import PaymentService


class Command(BaseCommand):
    help = "Reconcile recent Stripe Checkout outcomes into the consumer funnel"

    def add_arguments(self, parser):
        parser.add_argument("--days", type=int, default=30)

    def handle(self, *args, **options):
        stripe.api_key = settings.STRIPE_SECRET_KEY
        since = int(
            (timezone.now() - timedelta(days=max(1, options["days"]))).timestamp()
        )
        accepted = {
            "checkout.session.completed",
            "checkout.session.async_payment_succeeded",
            "checkout.session.async_payment_failed",
            "checkout.session.expired",
        }
        processed = 0
        for event in stripe.Event.list(created={"gte": since}, limit=100).auto_paging_iter():
            if event.type not in accepted:
                continue
            PaymentService.handle_webhook_event(event)
            processed += 1
        self.stdout.write(self.style.SUCCESS(f"Reconciled {processed} Stripe events"))
