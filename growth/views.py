from django import forms
from django.contrib import admin, messages
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.db.models import Count
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST
from django.views.decorators.cache import never_cache
from .models import AcquisitionAutomation, AcquisitionCampaign, AcquisitionRun

PERMISSIONS = ('growth.view_acquisitioncampaign', 'jobs.view_linkprospect',
               'jobs.view_consumerfunnelevent', 'jobs.view_employerfunnelevent', 'growth.view_cashmovement')


def access(request, change=False):
    if not request.user.has_perms(PERMISSIONS) or (change and not request.user.has_perm('growth.change_acquisitioncampaign')):
        raise PermissionDenied


class DraftForm(forms.ModelForm):
    class Meta:
        model = AcquisitionCampaign
        fields = ('draft',)
        widgets = {'draft': forms.Textarea(attrs={'rows': 18})}


@never_cache
def workspace(request, pk=None):
    access(request)
    config = AcquisitionAutomation.objects.filter(pk=1).first()
    rows = AcquisitionCampaign.objects.select_related('prospect', 'organization')
    family = request.GET.get('audience', '')
    if family in ('seeker', 'employer'):
        rows = rows.filter(audience=family)
    selected = get_object_or_404(rows, pk=pk) if pk else None
    counts = dict(AcquisitionCampaign.objects.values_list('status').annotate(n=Count('id')))
    top = list(rows.exclude(status__in=['hold', 'dismissed', 'contacted', 'placed'])[:3])
    campaigns = list(rows[:100])
    funnel = []
    for audience, label in [('seeker', 'Job seekers'), ('employer', 'Employers')]:
        subset = [r for r in campaigns if r.audience == audience and r.metrics_at]
        keys = [('landing_sessions', 'Landing session-days'), ('accounts', 'Accounts'), ('scouts', 'Scouts')] if audience == 'seeker' else [('landing_sessions', 'Landing session-days'), ('checkouts', 'Checkout events')]
        bars = []
        for key, name in keys:
            observed = [r.metrics[key] for r in subset if r.metrics.get(key) is not None]
            bars.append({'label': name, 'value': sum(observed) if observed else None,
                         'partial': bool(observed) and len(observed) < len(subset)})
        maximum = max([b['value'] or 0 for b in bars] + [1])
        for bar in bars:
            bar['percent'] = round((bar['value'] or 0)*100/maximum)
        funnel.append({'title': label, 'bars': bars, 'measured': bool(subset)})
    runs = list(AcquisitionRun.objects.all()[:8])
    discovery = next((r.summary.get('discovery') for r in runs if r.summary.get('discovery')), None)
    context = {**admin.site.each_context(request), 'title': 'Growth', 'campaigns': campaigns,
               'selected': selected, 'draft_form': DraftForm(instance=selected) if selected else None,
               'counts': counts, 'top': top, 'funnel': funnel, 'audience': family,
               'runs': runs, 'discovery': discovery, 'config': config,
               'can_change': request.user.has_perm('growth.change_acquisitioncampaign'),
               'can_control': request.user.has_perm('growth.change_acquisitionautomation'),
               'has_more': rows.count() > 100}
    return render(request, 'admin/growth.html', context)


@require_POST
@transaction.atomic
def control(request):
    access(request)
    if not request.user.has_perm('growth.change_acquisitionautomation'):
        raise PermissionDenied
    AcquisitionAutomation.objects.get_or_create(pk=1)
    config = AcquisitionAutomation.objects.select_for_update().get(pk=1)
    action = request.POST.get('action')
    if action == 'refresh':
        config.requested = True
        messages.success(request, 'Refresh requested. The scheduled worker checks every 15 minutes while automation is enabled.')
    elif action == 'pause':
        config.enabled = False
        messages.success(request, 'Automation paused. An in-progress check can finish; no new steps will start.')
    elif action == 'resume':
        config.enabled, config.requested = True, True
        messages.success(request, 'Daily research and measurement enabled.')
    elif action == 'discovery':
        config.discovery_enabled = not config.discovery_enabled
    else:
        return redirect('admin_growth')
    config.save()
    return redirect('admin_growth')


@require_POST
@transaction.atomic
def update(request, pk):
    access(request, change=True)
    campaign = get_object_or_404(AcquisitionCampaign.objects.select_for_update(), pk=pk)
    action = request.POST.get('action')
    if action == 'draft':
        form = DraftForm(request.POST, instance=campaign)
        if form.is_valid():
            form.save()
            messages.success(request, 'Draft saved. Publishing remains in the existing reviewed channel workflow.')
        else:
            messages.error(request, 'Draft could not be saved.')
    elif action in ('hold', 'dismiss'):
        campaign.status = 'hold' if action == 'hold' else 'dismissed'
        campaign.hold_reason = request.POST.get('reason', '').strip()[:500] or 'Paused by staff.'
        campaign.save()
        messages.success(request, 'Campaign removed from automatic preparation.')
    elif action == 'resume':
        campaign.status, campaign.hold_reason = 'research', ''
        campaign.save()
        messages.success(request, 'Campaign returned to research. Source opt-outs and channel holds still apply.')
    return redirect('admin_growth_detail', pk=pk)
