"""Before-and-after numbers for free featured trials: the evidence for selling the $49 feature.

Prints unless --send, which emails the owner.
"""
from django.conf import settings
from django.core.management.base import BaseCommand

from jobs.models import FeatureTrial
from jobs.services import feature_trials
from jobs.services.email_service import email_service


def lift(before, during):
    return f"{during / before:.1f}x" if before else ("new" if during else "-")


class Command(BaseCommand):
    help = "Report views and apply clicks for featured trials against the days before (prints unless --send)."

    def add_arguments(self, parser):
        parser.add_argument("--send", action="store_true")

    def handle(self, *args, **options):
        rows, site = feature_trials.results(FeatureTrial.objects.select_related("job__organization"))
        if not rows:
            self.stdout.write("No featured trial has run a full day yet.")
            return
        lines = ["Free featured trials: each role's numbers while featured against the same number of days before.", ""]
        clicks_before = clicks_during = 0
        for row in rows:
            (views_before, before), (views_during, during) = row["before"], row["during"]
            clicks_before += before
            clicks_during += during
            lines.append(f"- {row['trial'].job.organization.name}, {row['trial'].job.title} ({row['days']} days): "
                         f"apply clicks {before} -> {during} ({lift(before, during)}), views {views_before} -> {views_during}")
        lines += ["", f"All trial roles: apply clicks {clicks_before} -> {clicks_during} ({lift(clicks_before, clicks_during)})."]
        if site is not None:
            lines.append(f"Site-wide apply clicks moved {site:.2f}x over the same days, so compare the trial lift with that.")
        lines.append("Apply clicks count people who left Remote Impact for the employer's application page, not total applicants.")
        text = "\n".join(lines)
        if not options["send"]:
            self.stdout.write(text)
            return
        subject = f"Featured trials: apply clicks {clicks_before} -> {clicks_during}"
        import html
        sent = email_service.send_email(settings.OWNER_REPORT_EMAIL, subject, "<pre>" + html.escape(text) + "</pre>", text=text)
        self.stdout.write(("Sent" if sent else "Failed to send") + f": {subject}")
