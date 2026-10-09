"""Staff analytics page, mounted at /admin/analytics/ behind the admin login."""
from django.contrib import admin
from django.shortcuts import render

from ..services.admin_metrics import RANGES, build_metrics


def analytics(request):
    raw = request.GET.get("days", "30")
    days = int(raw) if raw.isdigit() and int(raw) in RANGES else 30
    metrics = build_metrics(days, refresh=request.GET.get("refresh") == "1")
    return render(request, "admin/analytics.html", {**admin.site.each_context(request), "m": metrics})


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
