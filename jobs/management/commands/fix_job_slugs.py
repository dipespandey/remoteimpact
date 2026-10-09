"""Give jobs a real slug once their organization is known, keeping old URLs working.

Jobs imported before their organization was identified got slugs such as
"program-manager-unknown-organization". Fixing the organization later never
updated the slug. This regenerates it and records a permanent redirect.

Usage:
    python manage.py fix_job_slugs            # dry run
    python manage.py fix_job_slugs --apply
"""
from django.core.management.base import BaseCommand
from django.db import transaction

from jobs.models import Job, JobSlugRedirect
from jobs.utils import unique_slug


class Command(BaseCommand):
    help = "Regenerate 'unknown-organization' job slugs and record redirects."

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true")

    def handle(self, *args, **options):
        jobs = (
            Job.objects.filter(slug__contains="unknown-organization")
            .exclude(organization__name__iexact="Unknown Organization")
            .select_related("organization")
        )
        count = 0
        for job in jobs:
            new_slug = unique_slug(Job, f"{job.title}-{job.organization.name}")
            self.stdout.write(f"{job.slug} -> {new_slug}")
            count += 1
            if options["apply"]:
                with transaction.atomic():
                    JobSlugRedirect.objects.get_or_create(old_slug=job.slug, defaults={"job": job})
                    Job.objects.filter(pk=job.pk).update(slug=new_slug)
        self.stdout.write(self.style.SUCCESS(f"{count} slugs {'updated' if options['apply'] else 'to update (dry run)'}"))
