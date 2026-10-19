from django.shortcuts import render
from django.urls import reverse
from django.conf import settings

from jobs.services.early_careers import FILTERS, opportunities
from jobs.services.seo_text import json_ld


def early_careers(request):
    rows = opportunities()
    selected = {key: request.GET.get(key, '') if request.GET.get(key, '') in choices else '' for key, choices in FILTERS.items()}
    sector = request.GET.get('sector', '')
    sectors = sorted({row['sector'] for row in rows})
    if sector not in sectors:
        sector = ''
    results = [row for row in rows if all(not value or row[key] == value for key, value in selected.items()) and (not sector or row['sector'] == sector)]
    groups = [dict(key=key, title=title, items=[r for r in results if r['status'] == key]) for key, title in (
        ('open', 'Applications open'), ('listings', 'Browse current vacancies and projects'),
        ('check', 'Explore the next intake'), ('closed', 'Closed rounds to keep on your radar'))]
    filters = [dict(name=key, label={'kind':'Opportunity', 'audience':'Career stage', 'funding':'Funding', 'mode':'Work format', 'status':'Application status'}[key], choices=choices.items(), value=selected[key]) for key, choices in FILTERS.items()]
    canonical = settings.SITE_URL.rstrip('/') + reverse('jobs:early_careers')
    schema = {'@context':'https://schema.org', '@type':'CollectionPage', 'name':'Early careers in impact', 'url':canonical,
              'description':'Fellowships, internships, mentorships and learning programmes with reviewed eligibility and funding details.',
              'mainEntity':{'@type':'ItemList', 'numberOfItems':len(results), 'itemListElement':[{'@type':'ListItem','position':i,'name':r['name'],'url':canonical+'#'+r['slug']} for i,r in enumerate(results,1)]}}
    return render(request, 'jobs/early_careers.html', dict(groups=groups, filters=filters, sectors=sectors, sector=sector,
        total=len(rows), result_count=len(results), filtered=bool(sector or any(selected.values())),
        last_reviewed=max(r['checked_date'] for r in rows), schema_json=json_ld(schema)))
