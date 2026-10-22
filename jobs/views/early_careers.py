from urllib.parse import urlencode, urlsplit, urlunsplit

from django.conf import settings
from django.contrib import messages
from django.http import Http404, HttpResponseBadRequest, HttpResponseRedirect
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_POST, require_safe
from django.views.decorators.clickjacking import xframe_options_exempt

from earlycareers.models import DirectoryEvent, ProgrammeBookmark
from earlycareers.services import prepare_account, programme, record
from jobs.services.early_careers import FILTERS, opportunities
from jobs.services.seo_text import json_ld


def _return_path(value):
    base = reverse('jobs:early_careers')
    if any(char in value for char in ('\\', '\r', '\n')):
        return base
    try:
        parsed = urlsplit(value[:2000])
    except ValueError:
        return base
    if parsed.scheme or parsed.netloc or parsed.path != base:
        return base
    # Rebuild a local path; the destination never comes from a remote host.
    return urlunsplit(('', '', base, parsed.query, parsed.fragment))


@never_cache
@require_safe
def early_careers(request):
    rows = opportunities()
    bookmarks = dict(ProgrammeBookmark.objects.filter(user=request.user).values_list('slug', 'status')) if request.user.is_authenticated else {}
    selected = {key: request.GET.get(key, '') if request.GET.get(key, '') in choices else '' for key, choices in FILTERS.items()}
    sector = request.GET.get('sector', '')
    sectors = sorted({row['sector'] for row in rows})
    if sector not in sectors:
        sector = ''
    query = request.GET.get('q', '').strip()[:160]
    saved_only = request.GET.get('saved') == '1'
    terms = query.casefold().split()
    for row in rows:
        row['save_status'] = bookmarks.get(row['slug'], '')
    results = [row for row in rows if all(not value or row[key] == value for key, value in selected.items())
        and (not sector or row['sector'] == sector) and (not saved_only or row['slug'] in bookmarks)
        and all(term in ' '.join(str(row.get(key, '')) for key in ('name', 'organization', 'sector', 'location', 'fit', 'requirements')).casefold() for term in terms)]
    results.sort(key=lambda row: (row['funding'] != 'paid', row['audience'] != 'first-step', row['deadline'] or '9999-12-31', row['name'].casefold()))
    groups = [dict(key=key, title=title, items=[r for r in results if r['status'] == key]) for key, title in (
        ('open', 'Applications open'), ('listings', 'Browse current vacancies and projects'),
        ('check', 'Explore the next intake'), ('closed', 'Closed rounds to keep on your radar'))]
    filters = [dict(name=key, label={'kind':'Opportunity', 'audience':'Career stage', 'funding':'Funding', 'mode':'Work format', 'status':'Application status'}[key], choices=choices.items(), value=selected[key]) for key, choices in FILTERS.items()]
    canonical = settings.SITE_URL.rstrip('/') + reverse('jobs:early_careers')
    schema = {'@context':'https://schema.org', '@type':'CollectionPage', 'name':'Early careers in impact', 'url':canonical,
        'description':'Fellowships, internships, mentorships and learning programmes with reviewed eligibility and funding details.',
        'mainEntity':{'@type':'ItemList', 'numberOfItems':len(results), 'itemListElement':[{'@type':'ListItem','position':i,'name':r['name'],'url':canonical+'#'+r['slug']} for i,r in enumerate(results,1)]}}
    record(request, DirectoryEvent.Kind.VISIT, unique=True)
    return render(request, 'jobs/early_careers.html', dict(groups=groups, filters=filters, sectors=sectors, sector=sector,
        total=len(rows), result_count=len(results), query=query, saved_only=saved_only,
        saved_count=sum(r['slug'] in bookmarks for r in rows),
        filtered=bool(query or sector or saved_only or any(selected.values())),
        signup_url=reverse('jobs:early_careers_join'),
        return_path=request.get_full_path(),
        open_paid_remote_internships=sum(r['kind'] == 'internship' and r['funding'] == 'paid'
            and r['mode'] == 'remote' and r['status'] == 'open' for r in rows),
        last_reviewed=max(r['checked_date'] for r in rows), schema_json=json_ld(schema)))


@never_cache
@require_safe
def early_careers_access_report(request):
    rows = opportunities()
    total = len(rows)
    count = lambda key, value: sum(row[key] == value for row in rows)
    return render(request, 'jobs/early_careers_access_report.html', {
        'total': total,
        'last_reviewed': max(row['checked_date'] for row in rows),
        'funding_counts': [(label, count('funding', key)) for key, label in (
            ('paid', 'Paid or stipend listed'), ('free', 'Free learning or costs covered'),
            ('unpaid', 'Unpaid placement'), ('fee', 'Fees apply'), ('unknown', 'Funding to confirm'))],
        'format_counts': [(label, count('mode', key)) for key, label in (
            ('remote', 'Fully remote / online'), ('hybrid', 'Remote with required travel'),
            ('onsite', 'In person'), ('mixed', 'Varies by placement'), ('unknown', 'Format to confirm'))],
        'audience_counts': [(label, count('audience', key)) for key, label in (
            ('first-step', 'First career step'), ('research-ready', 'Technical / research preparation'),
            ('career-switcher', 'Changing sectors'))],
        'status_counts': [(label, count('status', key)) for key, label in (
            ('open', 'Applications open'), ('listings', 'Browse vacancies or projects'),
            ('check', 'Check next intake / verify status'), ('closed', 'Closed round'))],
    })


@never_cache
@require_safe
@xframe_options_exempt
def early_careers_embed(request):
    rows = opportunities()
    status_order = {'open': 0, 'listings': 1, 'check': 2}
    items = sorted((row for row in rows if row['status'] in status_order),
                   key=lambda row: (status_order[row['status']], row['deadline'] or '9999-12-31', row['name'].casefold()))[:6]
    return render(request, 'jobs/early_careers_embed.html', {
        'items': items,
        'total': len(rows),
    })


@never_cache
@require_safe
def early_careers_join(request):
    if request.user.is_authenticated:
        return redirect(reverse('jobs:early_careers') + '?saved=1#opportunities')
    placement = request.GET.get('placement')
    placement = placement if placement in {'hero', 'shortlist', 'empty'} else 'directory'
    prepare_account(request, placement=placement)
    return redirect(reverse('account_signup') + '?' + urlencode({'next': reverse('jobs:early_careers') + '#opportunities'}))


@never_cache
@require_POST
def early_careers_save(request, slug):
    item = programme(slug)
    if not item:
        raise Http404('Unknown programme')
    action = request.POST.get('action', 'save')
    if action not in {'save', 'remove', 'applied', 'reset'}:
        return HttpResponseBadRequest('Unknown action')
    target = _return_path(request.POST.get('next', reverse('jobs:early_careers')))
    if not request.user.is_authenticated:
        if action != 'save':
            return HttpResponseBadRequest('Sign in to update a saved programme')
        prepare_account(request, slug=slug, placement='programme_save')
        return redirect(reverse('account_signup') + '?' + urlencode({'next': target.split('#')[0] + '#' + slug}))
    if action == 'remove':
        deleted, _ = ProgrammeBookmark.objects.filter(user=request.user, slug=slug).delete()
        if deleted:
            record(request, DirectoryEvent.Kind.REMOVE, slug=slug)
        messages.success(request, 'Programme removed from your shortlist.')
    else:
        bookmark, created = ProgrammeBookmark.objects.get_or_create(user=request.user, slug=slug)
        if action in {'applied', 'reset'}:
            bookmark.status = ProgrammeBookmark.Status.APPLIED if action == 'applied' else ProgrammeBookmark.Status.SAVED
            bookmark.save(update_fields=['status'])
            if action == 'applied':
                record(request, DirectoryEvent.Kind.APPLIED, slug=slug, unique=True)
        if created:
            record(request, DirectoryEvent.Kind.SAVE, slug=slug)
        messages.success(request, 'Marked as applied.' if action == 'applied' else 'Programme saved to your shortlist.')
    return redirect(target)


@never_cache
@require_safe
def early_careers_outbound(request, slug):
    item = programme(slug)
    if not item:
        raise Http404('Unknown programme')
    record(request, DirectoryEvent.Kind.OUTBOUND, slug=slug, unique=True)
    response = HttpResponseRedirect(item.get('application_url') or item['source_url'])
    response['X-Robots-Tag'] = 'noindex, nofollow'
    response['Referrer-Policy'] = 'origin'
    return response
