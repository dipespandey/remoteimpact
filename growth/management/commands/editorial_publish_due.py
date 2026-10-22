import json
from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from growth.editorial import publish_revision
from growth.models import ArticleRevision
from growth.pilots import pilot_summary


class Command(BaseCommand):
    help = "Preview due approved revisions; automatic publication requires an enabled flag and proven pilot gate."

    def add_arguments(self, parser):
        parser.add_argument("--limit", type=int, default=3)
        parser.add_argument("--apply", action="store_true")

    def handle(self, *args, **options):
        if not 1 <= options["limit"] <= 3:
            raise CommandError("limit must be 1–3.")
        if options["apply"] and (not getattr(settings, "GROWTH_EDITORIAL_AUTOPUBLISH_ENABLED", False)
                                  or not pilot_summary()["repeat_demand_gate_passed"]):
            raise CommandError("Automatic publication remains disabled until explicitly enabled and repeat demand is proven. Review/publish individually in admin.")
        now = timezone.now()
        due = ArticleRevision.objects.filter(state="approved", publish_at__lte=now).order_by("publish_at", "pk")[:options["limit"]]
        rows = []
        for revision in due:
            published = False
            if options["apply"]:
                month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
                if ArticleRevision.objects.filter(published_at__gte=month_start).count() >= 12:
                    raise CommandError("Monthly editorial experiment ceiling reached.")
                try:
                    published = publish_revision(revision.pk)
                except ValidationError:
                    rows.append({"revision": str(revision.pk), "published": False, "review_required": True})
                    continue
            rows.append({"revision": str(revision.pk), "published": published})
        self.stdout.write(json.dumps({"dry_run": not options["apply"], "rows": rows,
                                      "external_messages_sent": 0, "paid_model_calls": 0}, indent=2))
