import json
from django.core.management.base import BaseCommand

from growth.models import ArticleRevision, RevenueRun
from growth.pilots import pilot_summary
from growth.revenue import cost_summary


class Command(BaseCommand):
    help = "Internal evidence-backed revenue, cost, pilot and editorial readiness report; no network calls."

    def add_arguments(self, parser):
        parser.add_argument("--mode", choices=["live", "test"], default="live")
        parser.add_argument("--account", required=True)

    def handle(self, *args, **options):
        run = RevenueRun.objects.filter(account_id=options["account"], livemode=options["mode"] == "live", complete=True).first()
        result = {"financial_evidence": "missing" if not run else "processor snapshot, scope may be partial",
                  "reconciled_at": run.created_at.isoformat() if run else None,
                  "period": {"since": run.since.isoformat(), "until": run.until.isoformat()} if run else None,
                  "cash": run.summary if run else None,
                  "costs": cost_summary(run.since, run.until) if run else None,
                  "owner_profit": None, "profit_status": "Costs, tax and labor completeness require owner verification.",
                  "employer_pilots": pilot_summary(),
                  "editorial": {state: ArticleRevision.objects.filter(state=state).count() for state in ArticleRevision.State.values}}
        self.stdout.write(json.dumps(result, indent=2))
