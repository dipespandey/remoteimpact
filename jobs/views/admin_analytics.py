"""Staff analytics page, mounted at /admin/analytics/ behind the admin login."""
from django.contrib import admin
from django.shortcuts import render

from ..services.admin_metrics import RANGES, build_metrics


def analytics(request):
    raw = request.GET.get("days", "30")
    days = int(raw) if raw.isdigit() and int(raw) in RANGES else 30
    metrics = build_metrics(days, refresh=request.GET.get("refresh") == "1")
    return render(request, "admin/analytics.html", {**admin.site.each_context(request), "m": metrics})


def diagnostics(request):
    """Permission-gated, read-only analytics; never display raw event metadata."""
    from django.http import HttpResponseBadRequest, HttpResponseForbidden
    from ..services.product_analytics import RANGES, build_product_analytics, read_scope

    if not request.user.has_perms(('jobs.view_consumerfunnelevent', 'jobs.view_employerfunnelevent')):
        return HttpResponseForbidden('Analytics event-view permissions are required.')
    if request.method not in ('GET', 'HEAD'):
        from django.http import HttpResponseNotAllowed
        return HttpResponseNotAllowed(['GET', 'HEAD'])
    raw = request.GET.get('days', '30')
    days = int(raw) if raw in tuple(str(n) for n in RANGES) else 30
    token = request.GET.get('scope', '')
    scope = read_scope(token)
    if token and scope is None:
        return HttpResponseBadRequest('This activity link is invalid or expired. Open Diagnostics for a fresh link.')
    section = request.GET.get('view', 'journeys' if scope else 'overview')
    if section not in ('overview', 'journeys', 'campaigns', 'errors'):
        section = 'overview'
    report = build_product_analytics(days, family=request.GET.get('family', 'all'),
                                     failures_only=section == 'errors' or request.GET.get('failures') == '1', scope=scope)
    # Identity is an additional privilege beyond event viewing. Respect the
    # registered user admin's object-level permissions, including custom models.
    from django.contrib.auth import get_user_model
    from django.urls import reverse
    User = get_user_model()
    user_admin = admin.site._registry.get(User)
    ids = {row['user_id'] for row in report['journeys'] if row.get('user_id')}
    if scope and scope['kind'] == 'account':
        ids.add(scope['value'])
    identities = {}
    if user_admin and user_admin.has_view_or_change_permission(request):
        for account in User.objects.filter(pk__in=ids):
            if user_admin.has_view_or_change_permission(request, account):
                identities[account.pk] = {
                    'username': account.get_username(),
                    'admin_url': reverse(f'admin:{User._meta.app_label}_{User._meta.model_name}_change', args=[account.pk]),
                }
    for row in report['journeys']:
        row.update(identities.get(row.get('user_id'), {}))
    report['selected_user'] = identities.get(scope['value']) if scope and scope['kind'] == 'account' else None
    response = render(request, 'admin/diagnostics.html', {
        **admin.site.each_context(request), 'a': report, 'scope_token': token, 'section': section,
    })
    response['Cache-Control'] = 'private, no-store'
    response['Referrer-Policy'] = 'no-referrer'
    return response


def outreach(request):
    """Ranked employer outreach queue with ready-to-send drafts (staff only)."""
    from ..models import EmployerOutreach
    from ..services.outreach import outreach_targets

    raw_days = request.GET.get("days", "7")
    days = int(raw_days) if raw_days.isdigit() and int(raw_days) in (7, 14, 30) else 7
    status = request.GET.get("status", "")
    if status not in EmployerOutreach.Status.values:
        status = ""
    sender = request.user.get_full_name() or request.user.get_username()
    rows = outreach_targets(days=days, status=status, sender=sender)
    counts = {value: EmployerOutreach.objects.filter(status=value).count() for value in EmployerOutreach.Status.values}
    return render(request, "admin/outreach.html", {
        **admin.site.each_context(request),
        "rows": rows, "days": days, "status": status, "statuses": EmployerOutreach.Status.choices, "counts": counts,
    })


def outreach_update(request, org_id):
    """Record contact, status and notes for one organization."""
    from django.http import HttpResponseNotAllowed
    from django.shortcuts import get_object_or_404, redirect
    from django.utils import timezone

    from ..models import EmployerOutreach, Organization

    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    org = get_object_or_404(Organization, pk=org_id)
    record, _ = EmployerOutreach.objects.get_or_create(organization=org)
    status = request.POST.get("status", record.status)
    if status in EmployerOutreach.Status.values:
        if status == EmployerOutreach.Status.CONTACTED and not record.contacted_at:
            record.contacted_at = timezone.now()
        record.status = status
    if "contact" in request.POST:
        record.contact = request.POST["contact"].strip()[:254]
    if "notes" in request.POST:
        record.notes = request.POST["notes"].strip()
    record.save()
    return redirect(request.POST.get("next") or "admin_outreach")


def api_usage(request):
    """Jobs API and MCP usage: calls by day, surface, endpoint/tool, tier, client and key (staff only)."""
    from datetime import timedelta

    from django.db.models import Count, Sum
    from django.utils import timezone

    from ..models import ApiKey, ApiUsage

    raw = request.GET.get("days", "14")
    days = int(raw) if raw.isdigit() and int(raw) in (7, 14, 30, 90) else 14
    since = timezone.localdate() - timedelta(days=days - 1)
    everything = ApiUsage.objects.filter(date__gte=since)
    rows = everything.exclude(surface="page")  # API and MCP calls only; page views are counted separately
    page_rows = everything.filter(surface="page")

    def top(field, limit=10):
        return [{"label": r[field] or "-", "n": r["n"]} for r in rows.values(field).annotate(n=Sum("count")).order_by("-n")[:limit]]

    per_day = {r["date"]: r["n"] for r in rows.values("date").annotate(n=Sum("count"))}
    series = [{"date": since + timedelta(days=i), "n": per_day.get(since + timedelta(days=i), 0)} for i in range(days)]
    biggest = max([d["n"] for d in series] + [1])
    for d in series:
        d["pct"] = round(d["n"] * 100 / biggest)
    keys = list(ApiKey.objects.order_by("-last_used_at", "-created_at")[:25])
    by_key = {r["key_prefix"]: r["n"] for r in rows.exclude(key_prefix="").values("key_prefix").annotate(n=Sum("count"))}
    for key in keys:
        key.period_calls = by_key.get(key.prefix, 0)
    total = rows.aggregate(n=Sum("count"))["n"] or 0
    limited = rows.filter(status__in=(403, 429)).aggregate(n=Sum("count"))["n"] or 0
    page_views = page_rows.filter(endpoint="developers_page").aggregate(n=Sum("count"))["n"] or 0
    signups = page_rows.filter(endpoint__startswith="key_signup_").aggregate(n=Sum("count"))["n"] or 0
    page_per_day = {r["date"]: r["n"] for r in page_rows.filter(endpoint="developers_page").values("date").annotate(n=Sum("count"))}
    page_biggest = max(list(page_per_day.values()) + [1])
    for d in series:
        d["views"] = page_per_day.get(d["date"], 0)
        d["views_pct"] = round(d["views"] * 100 / page_biggest)
    return render(request, "admin/api_usage.html", {
        **admin.site.each_context(request), "days": days, "ranges": (7, 14, 30, 90), "total": total, "limited": limited, "page_views": page_views, "signups": signups,
        "series": series, "surfaces": top("surface"), "endpoints": top("endpoint"), "tiers": top("tier"), "clients": top("client"),
        "statuses": top("status"), "keys": keys, "key_counts": {"all": ApiKey.objects.count(), "active": ApiKey.objects.filter(is_active=True).count(),
                                                                 "commercial_pending": ApiKey.objects.filter(tier="commercial", is_active=False).count()},
    })
