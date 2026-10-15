"""Run with manage.py shell; import researched drafts, never approve or send."""
import json, os
from pathlib import Path
from django.db import transaction
from jobs.link_models import LinkProspect, LinkProspectEvent
from jobs.services.link_prospecting import import_prospect

rows=json.loads(Path(os.environ.get('PROSPECT_DRAFTS_PATH',Path(__file__).with_name('editorial-prospects.json'))).read_text())
with transaction.atomic():
    for row in rows:
        # A seeded NIH URL uses title case. Keep that identity instead of creating a case variant.
        existing=LinkProspect.objects.filter(url__iexact=row['url']).first()
        if existing:
            prospect,created=existing,False
        else:
            prospect,created=import_prospect(row,source='Growth pilot editorial research 2026-10-15')
        if prospect.status not in {LinkProspect.Status.RESEARCH,LinkProspect.Status.DRAFT}:
            print('Skipped protected or previously contacted prospect',prospect.pk,prospect.status)
            continue
        if not created and (prospect.pitch or prospect.contacted_at):
            print('Preserved existing work',prospect.pk)
            continue
        for field in ('audience','fit_reason','target_url','priority','contact_email','contact_url','contact_source_url','subject','pitch','notes'):
            setattr(prospect,field,row[field])
        prospect.status=LinkProspect.Status.DRAFT
        prospect.source='Growth pilot editorial research 2026-10-15; source URLs and evidence in marketing/pilots/growth-2026-10-15/'
        prospect.full_clean()
        prospect.save()
        LinkProspectEvent.objects.create(prospect=prospect,action='drafted',detail='Researched draft imported; no approval, message or submission made.')
        print('Draft ready',prospect.pk,prospect.name)
