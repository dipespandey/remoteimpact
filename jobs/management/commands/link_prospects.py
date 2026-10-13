"""Bounded prospect discovery, imports and verification; never sends outreach."""
import csv
from pathlib import Path
from urllib.parse import urlsplit

from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from jobs.link_models import LinkProspect, LinkProspectEvent
from jobs.services import link_prospecting as service


class Command(BaseCommand):
    help = "Discover, import, seed or check backlink prospects. No email is sent."

    def add_arguments(self, parser):
        mode = parser.add_mutually_exclusive_group(required=True)
        mode.add_argument("--seed", action="store_true")
        mode.add_argument("--import-csv", type=Path)
        mode.add_argument("--query", help="One focused search query via the existing DDGS library.")
        mode.add_argument("--inspect", action="store_true")
        mode.add_argument("--verify", action="store_true")
        parser.add_argument("--sector", choices=LinkProspect.Sector.values, default="impact")
        parser.add_argument("--limit", type=int, default=10)
        parser.add_argument("--id", type=int, help="Inspect or verify one prospect.")
        parser.add_argument("--dry-run", action="store_true", help="Preview imports or discovery without database writes.")

    def handle(self, *args, **options):
        if not 1 <= options["limit"] <= 20:
            raise CommandError("--limit must be between 1 and 20.")
        if options["inspect"] or options["verify"]:
            if options["dry_run"]:
                raise CommandError("Checks write observations; --dry-run is for discovery and imports.")
            rows = LinkProspect.objects.all()
            if options["id"]:
                rows = rows.filter(pk=options["id"])
            elif options["verify"]:
                rows = rows.exclude(placement_url="")
            for prospect in rows[:options["limit"]]:
                try:
                    status = service.inspect_page(prospect, placement=options["verify"])
                    self.stdout.write(f"{prospect.pk} {prospect.name}: {status}")
                except ValidationError as exc:
                    self.stderr.write(str(exc))
            return
        if options["seed"]:
            rows = [{"name": name, "url": url, "sector": sector} for name, url, sector in service.SEEDS]
            source = "Public career resource pages researched 2026-10-13; audience fit needs review"
        elif options["import_csv"]:
            try:
                if options["import_csv"].stat().st_size > 128_000:
                    raise CommandError("CSV must be smaller than 128 KB.")
                with options["import_csv"].open(encoding="utf-8-sig", newline="") as file:
                    rows = list(csv.DictReader(file))
            except (OSError, UnicodeError, csv.Error) as exc:
                raise CommandError(str(exc)) from exc
            if not rows or "url" not in rows[0] or len(rows) > 200:
                raise CommandError("CSV must have a url header and 1–200 rows.")
            source = f"CSV import: {options['import_csv'].name}"
        else:
            from ddgs import DDGS
            if len(options["query"]) > 300:
                raise CommandError("Keep discovery queries under 300 characters.")
            try:
                results = DDGS(timeout=10).text(options["query"], max_results=options["limit"])
                rows = []
                for item in list(results)[:options["limit"]]:
                    url = item.get("href", "")
                    try:
                        canonical = service.canonical_prospect_url(url)
                        parsed = urlsplit(canonical)
                        if (len(canonical) > 1000 or service.target_is_ours(canonical)
                                or (parsed.hostname in {"www.bing.com", "bing.com", "www.google.com", "google.com"}
                                    and parsed.path in {"/aclick", "/url", "/search", "/pagead/aclk"})):
                            continue
                    except ValidationError:
                        continue
                    rows.append({"name": item.get("title", "")[:255], "url": canonical, "sector": options["sector"]})
            except Exception as exc:
                raise CommandError("Search provider failed; retry later or import researched pages from CSV.") from exc
            source = f"DDGS query: {options['query']}"
            if not rows:
                self.stdout.write("No usable results returned after excluding invalid, advertising and self-site URLs; this does not mean no prospects exist.")
                return
        try:
            with transaction.atomic():
                for row in rows:
                    if options["dry_run"]:
                        service.canonical_prospect_url(row.get("url", ""))
                        self.stdout.write(f"Preview: {row.get('name', '')} {row['url']}")
                        continue
                    prospect, created = service.import_prospect(row, source=source)
                    if created:
                        LinkProspectEvent.objects.create(prospect=prospect, action="discovered" if options["query"] else "imported", detail=source)
                    self.stdout.write(f"{'Added' if created else 'Existing'}: {prospect.name} {prospect.url}")
        except ValidationError as exc:
            raise CommandError(str(exc)) from exc
