"""Build a preview for human editorial checking; never send or mark jobs verified."""
import hashlib
import json
import os
from pathlib import Path
from urllib.parse import urlencode

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.template.loader import render_to_string
from django.utils import timezone

from jobs.services.roundup import sample_jobs, issue_week


class Command(BaseCommand):
    help = "Prepare a local six-job editorial draft and source-review manifest. Never sends email."

    def add_arguments(self, parser):
        parser.add_argument("--output-dir", required=True)

    def handle(self, *args, **options):
        path = Path(options["output_dir"])
        try:
            path.mkdir(parents=True, exist_ok=False)
        except OSError as exc:
            raise CommandError(str(exc)) from exc
        os.chmod(path, 0o700)
        picks = []
        for job in sample_jobs():
            picks.append({"title": job.title, "employer": job.organization.name,
                "location_label": job.location, "experience": job.get_experience_level_display(),
                "url": settings.SITE_URL.rstrip("/") + job.get_absolute_url() + "?" + urlencode({"utm_source": "newsletter", "utm_medium": "email", "utm_campaign": "roundup_" + timezone.now().strftime("%Y%m%d")}),
                "employer_source": job.application_url, "job_id": str(job.pk),
                "employer_source_reviewed_at": None, "eligible_countries_review": None,
                "salary_review": None})
        manifest = {"prepared_at": timezone.now().isoformat(), "status": "draft_needs_source_review", "issue_week": issue_week(), "picks": picks,
            "required_before_send": ["Open each employer source and confirm availability, location eligibility and stated pay.",
                "Remove unsuitable, expired and unpaid roles; do not infer eligibility from a Remote label.",
                "Use only the separately confirmed weekly list; reconcile provider suppressions.",
                "Add a working per-recipient unsubscribe link and preview desktop/mobile before one reviewed send."],
            "send_enabled": False}
        raw = json.dumps(manifest, indent=2)
        (path / "review.json").write_text(raw)
        (path / "preview.html").write_text(render_to_string("emails/roundup_draft.html", {"picks": picks}))
        self.stdout.write(json.dumps({"picks": len(picks), "manifest_sha256": hashlib.sha256(raw.encode()).hexdigest(), "status": manifest["status"], "sent": 0}))
