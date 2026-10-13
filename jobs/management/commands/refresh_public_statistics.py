from django.core.management.base import BaseCommand
from jobs.services.trust_stats import trust_stats


class Command(BaseCommand):
    help = 'Refresh durable public verification statistics outside page requests.'

    def handle(self, *args, **options):
        stats = trust_stats(refresh=True)
        self.stdout.write(self.style.SUCCESS(
            f"Public statistics refreshed at {stats['computed_at'].isoformat()}"
        ))
