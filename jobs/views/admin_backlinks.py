"""Dedicated staff backlink queue, alongside the employer outreach page."""
from urllib.parse import quote, urlencode

from django import forms
from django.contrib import admin, messages
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Count, Q, Sum
from django.http import HttpResponseNotAllowed
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone

from jobs.link_admin import ProspectForm
from jobs.link_models import LinkProspect, LinkProspectEvent
from jobs.services import link_prospecting as service


class CreateProspectForm(forms.ModelForm):
    class Meta:
        model = LinkProspect
        fields = ("name", "url", "sector", "kind", "priority", "source")


class WorkspaceForm(ProspectForm):
    class Meta(ProspectForm.Meta):
        fields = ("name", "url", "sector", "kind", "priority", "status", "source", "audience", "fit_reason", "target_url",
                  "contact_name", "contact_email", "contact_url", "contact_source_url", "subject", "pitch",
                  "follow_up_on", "placement_url", "notes", "referral_visits", "scout_signups", "metrics_since", "metrics_until", "metrics_source")
        widgets = {"follow_up_on": forms.DateInput(attrs={"type": "date"}),
                   "metrics_since": forms.DateInput(attrs={"type": "date"}), "metrics_until": forms.DateInput(attrs={"type": "date"})}


GROUPS = (
    ("Research and audience fit", ("name", "url", "sector", "kind", "priority", "status", "source", "audience", "fit_reason", "target_url")),
    ("Public contact", ("contact_name", "contact_email", "contact_url", "contact_source_url")),
    ("Edit pitch", ("subject", "pitch")),
    ("Follow-up and placement", ("follow_up_on", "placement_url", "notes")),
    ("Measured results", ("referral_visits", "scout_signups", "metrics_since", "metrics_until", "metrics_source")),
)


def filters(values, *, prefix=""):
    value = lambda key: values.get(prefix + key, "")
    return {"q": value("q").strip()[:200],
            "status": value("status") if value("status") in LinkProspect.Status.values else "",
            "sector": value("sector") if value("sector") in LinkProspect.Sector.values else "",
            "due": "1" if value("due") == "1" else ""}


def return_url(request, pk=None):
    params = {key: value for key, value in filters(request.POST, prefix="_filter_").items() if value}
    page = request.POST.get("page", "")
    if page.isdigit() and len(page) < 6:
        params["page"] = page
    return reverse("admin_backlinks") + ("?" + urlencode(params) if params else "") + (f"#prospect-{pk}" if pk else "")


def workspace(request, *, bound_form=None, bound_pk=None, create_form=None):
    registry = admin.site._registry[LinkProspect]
    if not registry.has_view_or_change_permission(request):
        raise PermissionDenied
    if request.method != "GET" and bound_form is None and create_form is None:
        return HttpResponseNotAllowed(["GET"])
    values = request.POST if request.method == "POST" else request.GET
    selected = filters(values, prefix="_filter_" if request.method == "POST" else "")
    prospects = LinkProspect.objects.all()
    if selected["q"]:
        prospects = prospects.filter(Q(name__icontains=selected["q"]) | Q(url__icontains=selected["q"]) | Q(audience__icontains=selected["q"]))
    if selected["status"]:
        prospects = prospects.filter(status=selected["status"])
    if selected["sector"]:
        prospects = prospects.filter(sector=selected["sector"])
    if selected["due"]:
        prospects = prospects.filter(status__in=["contacted", "replied"], follow_up_on__lte=timezone.localdate())
    page = Paginator(prospects, 20).get_page(values.get("page"))
    rows = []
    for prospect in page:
        form = bound_form if prospect.pk == bound_pk else WorkspaceForm(instance=prospect, auto_id=f"prospect-{prospect.pk}-%s")
        mailto = ""
        if prospect.status == "approved" and prospect.reviewed_at and prospect.contact_email:
            mailto = f"mailto:{quote(prospect.contact_email, safe='@')}?" + urlencode({"subject": prospect.subject, "body": prospect.pitch}, quote_via=quote)
        rows.append({"prospect": prospect, "form": form, "groups": [(title, [form[field] for field in fields]) for title, fields in GROUPS], "mailto": mailto})
    counts = {item["status"]: item["total"] for item in LinkProspect.objects.values("status").annotate(total=Count("id"))}
    pipeline = LinkProspect.objects.aggregate(total=Count("id"), drafts=Count("id", filter=Q(status="draft")),
        reviewed=Count("id", filter=Q(status="approved")),
        due=Count("id", filter=Q(status__in=["contacted", "replied"], follow_up_on__lte=timezone.localdate())),
        verified=Count("id", filter=Q(check_status="target_link_found")), visits=Sum("referral_visits"), signups=Sum("scout_signups"))
    status_links = [{"label": label, "value": value, "count": counts.get(value, 0),
                     "query": urlencode({**selected, "status": value})} for value, label in LinkProspect.Status.choices]
    queries = [{"sector": sector, "query": query, "url": "https://www.google.com/search?" + urlencode({"q": query})}
               for sector, queries in service.QUERIES.items() for query in queries]
    context = {**admin.site.each_context(request), "rows": rows, "page": page, "selected": selected,
               "pipeline": pipeline, "status_links": status_links, "all_query": urlencode({**selected, "status": ""}),
               "page_query": urlencode(selected), "sectors": LinkProspect.Sector.choices, "research_queries": queries,
               "can_change": registry.has_change_permission(request), "can_add": registry.has_add_permission(request),
               "create_form": create_form or CreateProspectForm(initial={"priority": 2, "sector": selected["sector"] or "impact", "kind": "resource"})}
    return render(request, "admin/backlinks.html", context)


def update(request, pk):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    registry = admin.site._registry[LinkProspect]
    if not registry.has_change_permission(request):
        raise PermissionDenied
    operation = request.POST.get("operation", "save")
    if operation not in {"save", "inspect", "draft", "approve", "contact", "verify"}:
        messages.error(request, "Unknown workflow action.")
        return redirect(return_url(request, pk))
    with transaction.atomic():
        prospect = get_object_or_404(LinkProspect.objects.select_for_update(), pk=pk)
        form = WorkspaceForm(request.POST, instance=prospect, auto_id=f"prospect-{pk}-%s")
        if not form.is_valid():
            messages.error(request, "Correct the highlighted fields; your changes have not been saved.")
            return workspace(request, bound_form=form, bound_pk=pk)
        obj = form.save(commit=False)
        registry.save_model(request, obj, form, change=True)
        if operation == "save":
            messages.success(request, f"Saved {obj.name}.")
        else:
            registry._apply(request, LinkProspect.objects.filter(pk=pk), operation)
    return redirect(return_url(request, pk))


def create(request):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    if not admin.site._registry[LinkProspect].has_add_permission(request):
        raise PermissionDenied
    form = CreateProspectForm(request.POST)
    if not form.is_valid():
        return workspace(request, create_form=form)
    with transaction.atomic():
        prospect = form.save()
        LinkProspectEvent.objects.create(prospect=prospect, actor=request.user, action="created", detail="Added from backlink workspace")
    messages.success(request, f"Added {prospect.name} to research.")
    return redirect(return_url(request, prospect.pk))


def seed(request):
    response = admin.site._registry[LinkProspect].seed(request)
    return redirect("admin_backlinks") if response.status_code == 302 else response


def import_csv(request):
    response = admin.site._registry[LinkProspect].import_csv(request)
    return redirect("admin_backlinks") if response.status_code == 302 else response


def export_csv(request):
    registry = admin.site._registry[LinkProspect]
    if not registry.has_view_or_change_permission(request):
        raise PermissionDenied
    return registry.export_csv(request, LinkProspect.objects.all())
