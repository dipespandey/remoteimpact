import csv
import io
import json
from urllib.parse import quote_plus

from django import forms
from django.contrib import admin, messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Sum
from django.http import HttpResponse
from django.shortcuts import redirect
from django.urls import path, reverse
from django.utils import timezone

from jobs.link_models import LinkProspect, LinkProspectEvent
from jobs.services import link_prospecting as service


class NormalizedTextareaField(forms.CharField):
    def to_python(self, value):
        return super().to_python(value).replace("\r\n", "\n").replace("\r", "\n")

    def has_changed(self, initial, data):
        return super().has_changed(self.to_python(initial), data)


class ProspectForm(forms.ModelForm):
    pitch = NormalizedTextareaField(required=False, widget=forms.Textarea)
    fit_reason = NormalizedTextareaField(required=False, widget=forms.Textarea, help_text="Specific benefit for this page's readers. Required before review.")
    notes = NormalizedTextareaField(required=False, widget=forms.Textarea)
    class Meta:
        model = LinkProspect
        fields = "__all__"

    def clean(self):
        data = super().clean()
        status = data.get("status")
        if status in {LinkProspect.Status.APPROVED, LinkProspect.Status.CONTACTED}:
            if not self.instance.pk or LinkProspect.objects.get(pk=self.instance.pk).status != status:
                self.add_error("status", "Use the review or record-contact action to enter this status.")
        if data.get("target_url") and not service.target_is_ours(data["target_url"]):
            self.add_error("target_url", "Choose a Remote Impact page.")
        if status == LinkProspect.Status.PLACED and not data.get("placement_url"):
            self.add_error("placement_url", "Record the page containing the reported placement.")
        return data


class EventInline(admin.TabularInline):
    model = LinkProspectEvent
    extra = 0
    fields = ("created_at", "actor", "action", "detail")
    readonly_fields = fields
    can_delete = False

    def has_add_permission(self, request, obj=None):
        return False

    def has_view_permission(self, request, obj=None):
        return request.user.has_perm("jobs.view_linkprospect") or request.user.has_perm("jobs.change_linkprospect")


@admin.register(LinkProspect)
class LinkProspectAdmin(admin.ModelAdmin):
    form = ProspectForm
    change_list_template = "admin/jobs/linkprospect/change_list.html"
    change_form_template = "admin/jobs/linkprospect/change_form.html"
    list_display = ("name", "sector", "kind", "priority", "status", "follow_up_on", "check_status", "last_checked_at")
    list_filter = ("status", "sector", "kind", "priority", "check_status")
    search_fields = ("name", "url", "audience", "fit_reason", "contact_email", "notes")
    list_per_page = 40
    readonly_fields = ("campaign_url", "reviewed_by", "reviewed_at", "contacted_at", "last_checked_at", "check_status",
                       "page_title", "page_excerpt", "contact_candidates", "link_evidence", "created_at", "updated_at")
    inlines = (EventInline,)
    actions = ("inspect_prospects", "draft_pitches", "approve_pitches", "record_contacts", "verify_placements", "export_csv")
    fieldsets = (
        ("Prospect", {"fields": ("name", "url", "sector", "kind", "priority", "status", "source")}),
        ("Audience fit", {"fields": ("audience", "fit_reason", "target_url", "campaign_url")}),
        ("Public contact", {"fields": ("contact_name", "contact_email", "contact_url", "contact_source_url", "contact_candidates")}),
        ("Pitch", {"fields": ("subject", "pitch", "reviewed_by", "reviewed_at")}),
        ("Outreach and placement", {"fields": ("contacted_at", "follow_up_on", "placement_url", "notes")}),
        ("Measured results", {"fields": ("referral_visits", "scout_signups", "metrics_since", "metrics_until", "metrics_source")}),
        ("Last page inspection", {"fields": ("last_checked_at", "check_status", "page_title", "page_excerpt", "link_evidence"), "classes": ("collapse",)}),
        ("Record", {"fields": ("created_at", "updated_at"), "classes": ("collapse",)}),
    )

    def get_urls(self):
        return [
            path("import/", self.admin_site.admin_view(self.import_csv), name="jobs_linkprospect_import"),
            path("seed/", self.admin_site.admin_view(self.seed), name="jobs_linkprospect_seed"),
            path("<int:pk>/workflow/<str:operation>/", self.admin_site.admin_view(self.workflow), name="jobs_linkprospect_workflow"),
        ] + super().get_urls()

    def changelist_view(self, request, extra_context=None):
        all_rows = LinkProspect.objects.all()
        stats = {"total": all_rows.count(), "drafts": all_rows.filter(status="draft").count(),
                 "reviewed": all_rows.filter(status="approved").count(),
                 "due": all_rows.filter(follow_up_on__lte=timezone.localdate(), status__in=["contacted", "replied"]).count(),
                 "verified": all_rows.filter(check_status="target_link_found").count()}
        stats.update(all_rows.aggregate(visits=Sum("referral_visits"), signups=Sum("scout_signups")))
        queries = [{"sector": sector, "query": query, "url": "https://www.google.com/search?q=" + quote_plus(query)}
                   for sector, queries in service.QUERIES.items() for query in queries]
        return super().changelist_view(request, {**(extra_context or {}), "pipeline": stats, "research_queries": queries})

    def save_model(self, request, obj, form, change):
        material = {"url", "audience", "fit_reason", "target_url", "contact_name", "contact_email", "contact_url",
                    "contact_source_url", "subject", "pitch", "name", "sector", "kind"}
        old = LinkProspect.objects.get(pk=obj.pk) if change else None
        if old and {"url", "sector"}.intersection(form.changed_data):
            obj.research_assessment = {}
            obj.last_checked_at = None
        if old and material.intersection(form.changed_data):
            obj.reviewed_at = None
            obj.reviewed_by = None
            if obj.status == LinkProspect.Status.APPROVED:
                obj.status = LinkProspect.Status.DRAFT
                self.message_user(request, "Draft changed; review it again before outreach.", messages.WARNING)
        super().save_model(request, obj, form, change)
        if not change or form.changed_data:
            LinkProspectEvent.objects.create(prospect=obj, actor=request.user, action="edited" if change else "created",
                                             detail=", ".join(form.changed_data))
        if {"referral_visits", "scout_signups", "metrics_since", "metrics_until", "metrics_source"}.intersection(form.changed_data):
            LinkProspectEvent.objects.create(prospect=obj, actor=request.user, action="metrics_recorded", detail=json.dumps({
                "visits": obj.referral_visits, "scout_signups": obj.scout_signups,
                "since": str(obj.metrics_since or ""), "until": str(obj.metrics_until or ""), "source": obj.metrics_source,
            }))

    def response_change(self, request, obj):
        if request.POST.get("_workflow"):
            self._apply(request, LinkProspect.objects.filter(pk=obj.pk), request.POST["_workflow"])
            return redirect(reverse("admin:jobs_linkprospect_change", args=[obj.pk]))
        return super().response_change(request, obj)

    def _apply(self, request, queryset, operation):
        if not self.has_change_permission(request):
            raise PermissionDenied
        limit = 3 if operation in {"inspect", "verify"} else 10
        if queryset.count() > limit:
            self.message_user(request, f"Select at most {limit} prospects per action.", messages.ERROR)
            return
        for prospect in queryset:
            try:
                with transaction.atomic():
                    prospect = LinkProspect.objects.select_for_update().get(pk=prospect.pk)
                    if operation == "inspect":
                        status = service.inspect_page(prospect, actor=request.user)
                        self.message_user(request, f"{prospect.name}: {status}")
                    elif operation == "draft":
                        service.generate_pitch(prospect, sender=request.user.get_full_name() or request.user.get_username(), actor=request.user)
                    elif operation == "approve":
                        service.approve(prospect, request.user)
                    elif operation == "contact":
                        service.record_contact(prospect, request.user)
                    elif operation == "verify":
                        status = service.inspect_page(prospect, placement=True, actor=request.user)
                        self.message_user(request, f"{prospect.name}: {status}")
                    else:
                        raise ValidationError("Unknown workflow action.")
            except ValidationError as exc:
                self.message_user(request, f"{prospect.name}: {'; '.join(exc.messages)}", messages.ERROR)
                continue
            if operation not in {"inspect", "verify"}:
                self.message_user(request, f"{prospect.name}: {operation} recorded. No message was sent.")

    def workflow(self, request, pk, operation):
        if request.method != "POST":
            return HttpResponse(status=405)
        prospect = self.get_object(request, pk)
        if not prospect:
            return HttpResponse(status=404)
        if not self.has_change_permission(request, prospect):
            raise PermissionDenied
        self._apply(request, LinkProspect.objects.filter(pk=pk), operation)
        return redirect(reverse("admin:jobs_linkprospect_change", args=[pk]))

    @admin.action(description="Inspect page and find public contact paths", permissions=["change"])
    def inspect_prospects(self, request, queryset):
        self._apply(request, queryset, "inspect")

    @admin.action(description="Generate personalised draft pitches", permissions=["change"])
    def draft_pitches(self, request, queryset):
        self._apply(request, queryset, "draft")

    @admin.action(description="Approve current drafts after review", permissions=["change"])
    def approve_pitches(self, request, queryset):
        self._apply(request, queryset, "approve")

    @admin.action(description="Record that reviewed pitches were sent manually", permissions=["change"])
    def record_contacts(self, request, queryset):
        self._apply(request, queryset, "contact")

    @admin.action(description="Verify reported placement links", permissions=["change"])
    def verify_placements(self, request, queryset):
        self._apply(request, queryset, "verify")

    @admin.action(description="Export selected prospects and drafts as CSV", permissions=["view"])
    def export_csv(self, request, queryset):
        response = HttpResponse(content_type="text/csv; charset=utf-8")
        response["Content-Disposition"] = 'attachment; filename="remote-impact-link-prospects.csv"'
        writer = csv.writer(response)
        fields = ("name", "url", "sector", "kind", "priority", "status", "source", "audience", "fit_reason",
                  "target_url", "campaign_url", "contact_email", "contact_url", "contact_source_url", "subject", "pitch",
                  "follow_up_on", "placement_url", "check_status", "referral_visits", "scout_signups", "metrics_since", "metrics_until", "metrics_source")
        writer.writerow(fields)
        for prospect in queryset.iterator():
            values = [str(getattr(prospect, field)) if getattr(prospect, field) is not None else "" for field in fields]
            writer.writerow(["'" + value if value.lstrip().startswith(("=", "+", "-", "@")) else value for value in values])
        return response

    def import_csv(self, request):
        if request.method != "POST":
            return HttpResponse(status=405)
        if not self.has_add_permission(request):
            raise PermissionDenied
        upload = request.FILES.get("file")
        try:
            if not upload or upload.size > 128_000:
                raise ValidationError("Choose a CSV smaller than 128 KB.")
            rows = list(csv.DictReader(io.StringIO(upload.read().decode("utf-8-sig"))))
            if len(rows) > 200 or not rows or "url" not in rows[0]:
                raise ValidationError("Include a url header and 1–200 rows.")
            created = 0
            with transaction.atomic():
                for row in rows:
                    prospect, new = service.import_prospect(row, source=f"CSV import: {upload.name}"[:500])
                    if new:
                        created += 1
                        LinkProspectEvent.objects.create(prospect=prospect, actor=request.user, action="imported")
            self.message_user(request, f"Added {created} prospects; skipped {len(rows) - created} existing pages.")
        except (ValidationError, UnicodeError, csv.Error) as exc:
            self.message_user(request, str(exc), messages.ERROR)
        return redirect("admin:jobs_linkprospect_changelist")

    def seed(self, request):
        if request.method != "POST":
            return HttpResponse(status=405)
        if not self.has_add_permission(request):
            raise PermissionDenied
        count = 0
        for name, url, sector in service.SEEDS:
            prospect, new = service.import_prospect({"name": name, "url": url, "sector": sector}, source="Public career resource pages researched 2026-10-13; audience fit needs review")
            if new:
                count += 1
                LinkProspectEvent.objects.create(prospect=prospect, actor=request.user, action="seeded")
        self.message_user(request, f"Added {count} starting prospects. Inspect each page and assess fit before outreach.")
        return redirect("admin:jobs_linkprospect_changelist")
