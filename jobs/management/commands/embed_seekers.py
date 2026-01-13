from django.core.management.base import BaseCommand

from jobs.models import SeekerProfile
from jobs.services.embedding_service import embed_seeker


class Command(BaseCommand):
    help = 'Generate embeddings for seeker profiles'

    def add_arguments(self, parser):
        parser.add_argument('--force', action='store_true')

    def handle(self, *args, **options):
        qs = SeekerProfile.objects.filter(wizard_completed=True)
        if not options['force']:
            qs = qs.filter(embedding__isnull=True)

        total = qs.count()
        self.stdout.write(f'Embedding {total} seekers...')

        for i, seeker in enumerate(qs.iterator()):
            seeker.embedding = embed_seeker(seeker)
            seeker.save(update_fields=['embedding'])
            if (i + 1) % 50 == 0:
                self.stdout.write(f'  {i + 1}/{total}')

        self.stdout.write(self.style.SUCCESS('Done'))
