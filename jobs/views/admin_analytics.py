"""Staff analytics page, mounted at /admin/analytics/ behind the admin login."""
from django.contrib import admin
from django.shortcuts import render

from ..services.admin_metrics import RANGES, build_metrics


def analytics(request):
    raw = request.GET.get("days", "30")
    days = int(raw) if raw.isdigit() and int(raw) in RANGES else 30
    metrics = build_metrics(days, refresh=request.GET.get("refresh") == "1")
    return render(request, "admin/analytics.html", {**admin.site.each_context(request), "m": metrics})
