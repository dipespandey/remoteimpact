from django.core.management.base import BaseCommand
from django.utils import timezone

from jobs.models import ApiRateBucket


class Command(BaseCommand):
    help = "Remove expired API rate-limit and key-signup counters."

    def handle(self, *args, **options):
        deleted, _ = ApiRateBucket.objects.filter(expires_at__lte=timezone.now()).delete()
        self.stdout.write(f"Removed {deleted} expired rate counter(s).")
