"""Bounded, read-only end-to-end Scout replay with public source evidence.

Uses an isolated cache and a fixed inventory ID list. It creates no Scouts,
deliveries or analytics events and never sends emails. Output may contain private
briefs: use anonymized inputs and keep reports outside version control.
"""
import hashlib
import json
import os
from pathlib import Path
from time import monotonic
from unittest.mock import patch

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.test import override_settings
from django.utils import timezone

from jobs.services.job_scout_service import JobScoutService
from jobs.services.job_service import JobService
from jobs.services.scout_constraints import plain_job_field
from jobs.services.scout_tiers import tier_for_job


def load_queries(path, split='all', limit=30):
    rows=json.loads(Path(path).read_text())
    if not isinstance(rows,list):raise ValueError('Expected a list of queries')
    seen=set();selected=[]
    for row in rows:
        if not isinstance(row,dict) or not isinstance(row.get('id'),str) or row['id'] in seen:
            raise ValueError('Each query needs a unique string ID')
        seen.add(row['id'])
        if not isinstance(row.get('brief'),str) or not 10<=len(row['brief'])<=2000:
            raise ValueError('Brief must contain 10–2000 characters')
        if split=='all' or row.get('split')==split:selected.append(row)
    return selected[:limit]


def result_evidence(job):
    fields={field:plain_job_field(job,field) for field in ['title','description','requirements']}
    return {'id':job.pk,'organization':job.organization.name,'tier':tier_for_job(job),
            'location':job.location,'country':job.country,'job_type':job.job_type,
            'experience_level':job.experience_level,'salary_min':str(job.salary_min),
            'salary_max':str(job.salary_max),'salary_currency':job.salary_currency,
            'posted_at':str(job.posted_at),'application_url':job.application_url,
            'reasons':getattr(job,'scout_reasons',[]),'checks':getattr(job,'scout_constraint_checks',{}),
            'content_hash':hashlib.sha256(json.dumps(fields,sort_keys=True).encode()).hexdigest(),**fields}


class Command(BaseCommand):
    help='Replay at most 30 anonymized Scout briefs; save timings and review evidence without sending emails.'

    def add_arguments(self, parser):
        parser.add_argument('--queries',default=str(settings.BASE_DIR/'jobs/tests/fixtures/scout_benchmark_queries.json'))
        parser.add_argument('--split',choices=['all','development','holdout','control'],default='all')
        parser.add_argument('--limit',type=int,default=20)
        parser.add_argument('--repeat',type=int,default=2,help='One cold and up to two warm runs per brief.')
        parser.add_argument('--inventory-snapshot',required=True,help='Existing or new JSON list of inventory IDs.')
        parser.add_argument('--output',required=True,help='Private JSONL evidence report.')
        parser.add_argument('--warm-embeddings',action='store_true',help='Preload the configured local embedding model before measuring searches.')

    def handle(self,*args,**options):
        if not 1<=options['limit']<=30 or not 1<=options['repeat']<=3:
            raise CommandError('Limit must be 1–30 and repeat must be 1–3')
        try:queries=load_queries(options['queries'],options['split'],options['limit'])
        except (ValueError,OSError) as exc:raise CommandError(str(exc)) from exc
        snapshot=Path(options['inventory_snapshot'])
        available=JobService.get_available_jobs
        if snapshot.exists():
            ids=json.loads(snapshot.read_text())
            if not isinstance(ids,list) or any(type(pk) is not int for pk in ids):raise CommandError('Invalid inventory snapshot')
        else:
            ids=list(available().values_list('pk',flat=True))
            with os.fdopen(os.open(snapshot,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600),'w') as out:json.dump(ids,out)
        config={'default':{'BACKEND':'django.core.cache.backends.locmem.LocMemCache','LOCATION':'scout-benchmark'}}
        if options.get('warm_embeddings'):
            from jobs.services.embedding_service import load_for_batch
            load_for_batch()
        with os.fdopen(os.open(options['output'],os.O_WRONLY|os.O_CREAT|os.O_TRUNC,0o600),'w') as out, \
             override_settings(CACHES=config), \
             patch.object(JobService,'get_available_jobs',side_effect=lambda *a,**k:available(*a,**k).filter(pk__in=ids)):
            for query in queries:
                for run in range(options['repeat']):
                    started=monotonic();diagnostics={}
                    row={**query,'run':run,'inventory_size':len(ids),'at':str(timezone.now())}
                    from jobs.services import embedding_service
                    row['embedding_model_warmed']=embedding_service._model is not None
                    try:
                        prefs,jobs=JobScoutService.search_intent(query['brief'],limit=3,diagnostics=diagnostics)
                        row.update(preferences=prefs,matches=[result_evidence(j) for j in jobs])
                    except Exception as exc:
                        row['error']=type(exc).__name__
                    row.update(seconds=round(monotonic()-started,3),diagnostics=diagnostics,stages=diagnostics.get('stages',[]))
                    out.write(json.dumps(row,default=str)+'\n');out.flush()
                    self.stdout.write(f"{query['id']} run {run}: {row['seconds']}s, {len(row.get('matches',[]))} results")
