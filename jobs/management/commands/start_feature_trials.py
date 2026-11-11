"""Feature the most-clicked role at each top employer free for a few days, to show what featuring does.

Dry run unless --apply. Skips employers that already paid, already had a trial, or are featured now.
`feature_trial_report` reads the results once the trials end.
"""
from django.core.management.base import BaseCommand

from jobs.services import feature_trials


class Command(BaseCommand):
    help = "Start free featured trials for top employers' most-clicked roles (dry run unless --apply)."

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true")
        parser.add_argument("--limit", type=int, default=26)
        parser.add_argument("--days", type=int, default=14)
        parser.add_argument("--min-clicks", type=int, default=10)

    def handle(self, *args, **options):
        started = 0
        for job, clicks in feature_trials.candidates(options["limit"], min_clicks=options["min_clicks"]):
            line = f"{job.organization.name}: '{job.title}' ({clicks} clicks across the employer in 30 days)"
            if not options["apply"]:
                self.stdout.write("Would feature " + line)
                started += 1
                continue
            if feature_trials.start(job, options["days"]):
                self.stdout.write("Featured " + line)
                started += 1
        verb = "Started" if options["apply"] else "Dry run: would start"
        self.stdout.write(f"{verb} {started} trial{'s' if started != 1 else ''} of {options['days']} days.")
