"""Prepare versioned source offsets without model calls or changing public facts."""
from django.core.management.base import BaseCommand,CommandError
from django.db import connection,transaction
from django.db.models import F,Func,JSONField,Value

from jobs.models import Job
from jobs.services.scout_source_profile import KEY,build_profile,source_fields


def store_profile(job,profile):
    # The source match prevents a concurrent importer from receiving stale
    # offsets. JSONB_SET preserves other importer/indexing metadata atomically.
    qs=Job.objects.filter(pk=job.pk,title=job.title,description=job.description,requirements=job.requirements,
        location=job.location,country=job.country)
    if connection.vendor=='postgresql':
        return qs.update(raw_data=Func(F('raw_data'),Value('{'+KEY+'}'),Value(profile,output_field=JSONField()),
            function='jsonb_set',output_field=JSONField()))
    with transaction.atomic():
        current=qs.select_for_update().first()
        if current is None:return 0
        return qs.update(raw_data={**(current.raw_data or {}),KEY:profile})


class Command(BaseCommand):
    help='Prepare source-grounded Scout duty offsets; no LLM, emails or source-field updates.'

    def add_arguments(self,parser):
        parser.add_argument('--limit',type=int,default=5000)
        parser.add_argument('--dry-run',action='store_true')

    def handle(self,*args,**options):
        if not 1<=options['limit']<=10000:raise CommandError('Limit must be 1–10000')
        from jobs.services.job_service import JobService
        qs=JobService.get_available_jobs().only('id','title','description','requirements','location','country','raw_data').order_by('id')
        scanned=prepared=unchanged=conflicts=0
        for job in qs[:options['limit']].iterator(chunk_size=100):
            scanned+=1
            profile=build_profile(source_fields(job))
            if (job.raw_data or {}).get(KEY)==profile:
                unchanged+=1;continue
            if options['dry_run']:prepared+=1
            elif store_profile(job,profile):prepared+=1
            else:conflicts+=1
        self.stdout.write(f'Scout source profiles: scanned={scanned}, prepared={prepared}, unchanged={unchanged}, concurrent_changes={conflicts}, dry_run={options["dry_run"]}')
