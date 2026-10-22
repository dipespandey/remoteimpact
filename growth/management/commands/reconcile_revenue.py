import json
from datetime import timedelta

import stripe
from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from growth.revenue import bounded_list, known_references, movement_row, persist_run, summarize


class Command(BaseCommand):
    help = "Read-only Stripe balance reconciliation; --apply stores local facts, never changes Stripe."

    def add_arguments(self, parser):
        parser.add_argument("--expected-account", required=True)
        parser.add_argument("--mode", choices=["live", "test"], required=True)
        parser.add_argument("--days", type=int, default=28)
        parser.add_argument("--limit", type=int, default=1000)
        parser.add_argument("--apply", action="store_true")

    def handle(self, *args, **options):
        days, limit = options["days"], options["limit"]
        if not 1 <= days <= 366 or not 1 <= limit <= 5000:
            raise CommandError("days must be 1–366 and limit 1–5000.")
        key = settings.STRIPE_SECRET_KEY
        live = options["mode"] == "live"
        if not key.startswith(("sk_live_", "rk_live_") if live else ("sk_test_", "rk_test_")):
            raise CommandError("Configured key mode does not match requested mode; no API request made.")
        until = timezone.now().replace(microsecond=0)
        since = until - timedelta(days=days)
        client = stripe.StripeClient(key, max_network_retries=1, http_client=stripe.RequestsClient(timeout=15))
        try:
            account = client.v1.accounts.retrieve_current()
            if account["id"] != options["expected_account"]:
                raise CommandError("Stripe account mismatch; stopped before reading transactions.")
            references, reference_coverage = known_references(client, livemode=live)
            items, complete = bounded_list(client.v1.balance_transactions, {
                "created": {"gte": int(since.timestamp()), "lt": int(until.timestamp())},
                "expand": ["data.source"]}, limit)
            cache = {}
            rows = [movement_row(item, client, references, cache) for item in items]
            if any(isinstance(item.get("source"), dict) and "livemode" in item["source"]
                   and item["source"]["livemode"] is not live for item in items):
                raise CommandError("Processor object mode mismatch; nothing stored.")
            result = {"account": account["id"], "livemode": live, "since": since.isoformat(),
                      "until": until.isoformat(), "pagination_complete": complete,
                      "reference_coverage": reference_coverage,
                      "dry_run": not options["apply"], **summarize(rows)}
            if options["apply"]:
                if not complete:
                    raise CommandError("Pagination incomplete; raise limit or narrow window before applying.")
                result["run_id"] = str(persist_run(account["id"], live, since, until, rows, complete, reference_coverage).pk)
        except (stripe.StripeError, ValidationError) as exc:
            # Stripe errors may contain sensitive request bodies/URLs. Do not log them.
            raise CommandError(f"Reconciliation stopped ({type(exc).__name__}); no partial snapshot saved.") from None
        self.stdout.write(json.dumps(result, indent=2))
