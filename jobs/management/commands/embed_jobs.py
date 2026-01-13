from django.core.management.base import BaseCommand

from jobs.models import Job
from jobs.services.embedding_service import embed_job


class Command(BaseCommand):
    help = 'Generate embeddings for jobs'

    def add_arguments(self, parser):
        parser.add_argument('--batch-size', type=int, default=100)
        parser.add_argument('--force', action='store_true')

    def handle(self, *args, **options):
        qs = Job.objects.filter(is_active=True)
        if not options['force']:
            qs = qs.filter(embedding__isnull=True)

        total = qs.count()
        self.stdout.write(f'Embedding {total} jobs...')

        for i, job in enumerate(qs.iterator(chunk_size=options['batch_size'])):
            job.embedding = embed_job(job)
            job.save(update_fields=['embedding'])
            if (i + 1) % 100 == 0:
                self.stdout.write(f'  {i + 1}/{total}')

        self.stdout.write(self.style.SUCCESS('Done'))
