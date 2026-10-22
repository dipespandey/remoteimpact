"""Explicit, bounded prepare -> stage -> publish rollout for new imports only."""
import asyncio
import hashlib
import json
import math
from pathlib import Path
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.core.serializers.json import DjangoJSONEncoder
from django.db import transaction, IntegrityError
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from jobs.models import Job
from jobs.services.importers.common import _upsert_job, is_duplicate_job, ExistingJobError
from jobs.services.job_facts import source_description, reusable_facts
from jobs.services.extraction_contract import CONTRACT_HASH
from jobs.services.llm_parser import JobParser


def snapshot(job):
    fields = ('id', 'title', 'description', 'requirements', 'impact', 'skills', 'job_type',
              'experience_level', 'education_level', 'country', 'location', 'organization_id', 'category_id')
    row = {field: getattr(job, field) for field in fields}
    row['organization_name'] = job.organization.name
    row['category_name'] = job.category.name if job.category_id else ''
    row['fingerprint'] = hashlib.sha256(json.dumps(row, sort_keys=True).encode()).hexdigest()
    return row


def exists(payload):
    return (Job.objects.filter(source=payload.get('source'), external_id=payload.get('external_id')).exists()
            or is_duplicate_job(payload.get('application_url', ''), payload.get('source', ''),
                                payload.get('title'), payload.get('organization_name')))


def check_facts(payload):
    facts = (payload.get('raw_data') or {}).get('job_facts')
    return reusable_facts(facts, payload.get('title', ''), payload.get('organization_name', ''),
                          source_description(payload), settings.DEEPSEEK_MODEL, CONTRACT_HASH)


class Command(BaseCommand):
    help = 'Explicit canary only: prepare up to five new jobs, stage inactive, then publish reviewed embeddings.'

    def add_arguments(self, parser):
        parser.add_argument('--input', required=True)
        parser.add_argument('--output', required=True)
        parser.add_argument('--limit', type=int, default=3)
        modes = parser.add_mutually_exclusive_group()
        modes.add_argument('--apply', action='store_true', help='Stage reviewed extraction output as inactive new jobs.')
        modes.add_argument('--publish', action='store_true', help='Publish staged jobs with locally generated embeddings.')

    def handle(self, *args, **options):
        limit = options['limit']
        if not 1 <= limit <= 5:
            raise CommandError('Canary limit must be between 1 and 5.')
        data = json.loads(Path(options['input']).read_text())
        if options['publish']:
            result = self.publish(data, limit)
        elif options['apply']:
            result = self.apply(data, limit)
        else:
            payloads = data if isinstance(data, list) else data.get('payloads', [])
            payloads = [payload for payload in payloads if not exists(payload)][:limit]
            if not payloads:
                raise CommandError('No new listings available for the canary.')
            results = asyncio.run(JobParser(optimized=True).parse_batch(payloads, batch_size=1))
            result = {'phase': 'prepared', 'items': [{'payload': payload, 'eligible': check_facts(payload)} for payload in results]}
        Path(options['output']).write_text(json.dumps(result, cls=DjangoJSONEncoder, indent=2))
        self.stdout.write(json.dumps({'phase': result['phase'], 'count': len(result.get('jobs', result.get('items', [])))}))

    def apply(self, data, limit):
        if data.get('phase') != 'prepared' or len(data.get('items', [])) > limit:
            raise CommandError('Expected a bounded, prepared canary.')
        run_id = hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()
        jobs, skipped = [], []
        for item in data['items']:
            payload = dict(item['payload'])
            if not payload.get('external_id') or not check_facts(payload) or exists(payload):
                skipped.append(payload.get('external_id'))
                continue
            for field in ('posted_at', 'expires_at'):
                if isinstance(payload.get(field), str):
                    value = parse_datetime(payload[field])
                    if value is None:
                        raise CommandError('Invalid source date.')
                    payload[field] = value
            raw = dict(payload.get('raw_data') or {})
            raw['extraction_canary'] = {'phase': 'staged', 'run_id': run_id, 'at': timezone.now().isoformat()}
            payload.update(is_active=False, raw_data=raw)
            try:
                with transaction.atomic():
                    job, created = _upsert_job(payload, create_only=True)
                    if not created:
                        raise ExistingJobError('Canary can only create jobs.')
                    jobs.append(snapshot(job))
            except (ExistingJobError, IntegrityError):
                skipped.append(payload.get('external_id'))
        # A reviewer may have corrected the original model output. Keep shared
        # reuse aligned with the persisted, reviewed facts rather than the draft.
        from asgiref.sync import async_to_sync
        from jobs.services.extraction_cache import set_facts
        created_ids = {row['id'] for row in jobs}
        if created_ids:
            for facts in Job.objects.filter(pk__in=created_ids).values_list('raw_data__job_facts', flat=True):
                if isinstance(facts, dict) and facts.get('cache_key'):
                    async_to_sync(set_facts)(facts['cache_key'], facts)
        return {'phase': 'staged', 'model': settings.EMBEDDING_MODEL, 'jobs': jobs, 'skipped': skipped}

    def publish(self, data, limit):
        if data.get('model') != settings.EMBEDDING_MODEL or len(data.get('jobs', [])) > limit:
            raise CommandError('Embedding model or canary size mismatch.')
        ids = [row.get('id') for row in data.get('jobs', [])]
        if not ids or len(set(ids)) != len(ids):
            raise CommandError('Expected unique staged job IDs.')
        published = []
        with transaction.atomic():
            for row in data['jobs']:
                job = Job.objects.select_for_update(of=("self",)).select_related('organization', 'category').get(pk=row['id'])
                meta = dict((job.raw_data or {}).get('extraction_canary') or {})
                if job.is_active or meta.get('phase') != 'staged' or snapshot(job)['fingerprint'] != row.get('fingerprint'):
                    raise CommandError('Staged listing changed; publication refused.')
                if (job.raw_data or {}).get('job_facts', {}).get('unresolved_fields'):
                    raise CommandError('Unresolved extraction must not be published.')
                if job.quality_score < 55 or not job.application_url or (job.expires_at and job.expires_at <= timezone.now()):
                    raise CommandError('Staged job does not pass publication checks.')
                vector = row.get('embedding')
                dimension = Job._meta.get_field('embedding').dimensions
                if not isinstance(vector, list) or len(vector) != dimension or any(
                    isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) for value in vector
                ):
                    raise CommandError('Expected a finite embedding vector of the configured dimension.')
                from jobs.signals import _job_embedding_hash
                raw = dict(job.raw_data)
                raw['extraction_canary'] = dict(meta, phase='published', published_at=timezone.now().isoformat())
                # Models are generated off-host; this transaction loads no ML runtime.
                Job.objects.filter(pk=job.pk).update(is_active=True, embedding=vector,
                                                     embedding_content_hash=_job_embedding_hash(job), raw_data=raw)
                published.append({'id': job.pk, 'url': job.get_absolute_url()})
        return {'phase': 'published', 'jobs': published}
