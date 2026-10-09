from django.core.management.base import BaseCommand, CommandError

from jobs.models import Job
from jobs.services.embedding_service import embed_job
from jobs.signals import _job_embedding_hash


class Command(BaseCommand):
    help = 'Generate embeddings for jobs'

    def add_arguments(self, parser):
        parser.add_argument('--batch-size', type=int, default=100)
        parser.add_argument('--force', action='store_true')
        parser.add_argument('--stale', action='store_true', help='Also refresh changed-content embeddings.')
        parser.add_argument('--limit', type=int)

    def handle(self, *args, **options):
        if options['limit'] is not None and options['limit'] < 1:
            raise CommandError('Limit must be positive.')
        qs = Job.objects.filter(is_active=True).select_related('organization', 'category')
        if not options['force'] and not options['stale']:
            qs = qs.filter(embedding__isnull=True)

        total = qs.count()
        self.stdout.write(f'Embedding {total} jobs...')

        updated = 0
        for job in qs.iterator(chunk_size=options['batch_size']):
            content_hash = _job_embedding_hash(job)
            if options['stale'] and not options['force'] and job.embedding is not None and job.embedding_content_hash == content_hash:
                continue
            job.embedding = embed_job(job)
            job.embedding_content_hash = content_hash
            job.save(update_fields=['embedding', 'embedding_content_hash'])
            updated += 1
            if options['limit'] and updated >= options['limit']:
                break

        self.stdout.write(self.style.SUCCESS(f'Done: updated {updated} embeddings.'))
