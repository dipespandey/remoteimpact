from django.contrib.postgres.search import SearchVector
from django.core.management.base import BaseCommand

from jobs.models import Job


class Command(BaseCommand):
    help = 'Populate search_vector field for all active jobs'

    def add_arguments(self, parser):
        parser.add_argument('--force', action='store_true',
                            help='Update all jobs, not just those without search_vector')

    def handle(self, *args, **options):
        qs = Job.objects.filter(is_active=True)
        if not options['force']:
            qs = qs.filter(search_vector__isnull=True)

        total = qs.count()
        if total == 0:
            self.stdout.write('No jobs need updating.')
            return

        self.stdout.write(f'Updating search vectors for {total} jobs...')

        # Batch update using Django's SearchVector
        Job.objects.filter(id__in=qs.values_list('id', flat=True)).update(
            search_vector=(
                SearchVector('title', weight='A') +
                SearchVector('description', weight='B') +
                SearchVector('requirements', weight='C') +
                SearchVector('impact', weight='B')
            )
        )

        self.stdout.write(self.style.SUCCESS(f'Updated {total} jobs'))
