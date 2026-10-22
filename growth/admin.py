from django.contrib import admin, messages
from django.core.exceptions import ValidationError

from .editorial import approve_revision, publish_revision
from .models import (ArticleRevision, CashMovement, EditorialOpportunity, EmployerPilot,
                     OperatingCost, PilotActivity, RevenueRun)
from .pilots import STEPS, transition_pilot


class ImmutableEvidenceAdmin(admin.ModelAdmin):
    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(RevenueRun)
class RevenueRunAdmin(ImmutableEvidenceAdmin):
    list_display = ("created_at", "account_id", "livemode", "complete", "since", "until")
    list_filter = ("livemode", "complete")


@admin.register(CashMovement)
class CashMovementAdmin(ImmutableEvidenceAdmin):
    list_display = ("occurred_at", "product", "kind", "currency", "amount_minor", "fee_minor", "livemode", "scope_verified")
    list_filter = ("livemode", "scope_verified", "kind", "currency")
    search_fields = ("external_id", "source_id", "payment_intent_id")


@admin.register(OperatingCost)
class OperatingCostAdmin(admin.ModelAdmin):
    list_display = ("date", "description", "currency", "amount_minor", "is_owner_labor")
    list_filter = ("currency", "is_owner_labor")


class PilotActivityInline(admin.TabularInline):
    model = PilotActivity
    extra = 0
    readonly_fields = ("stage", "note", "actor", "created_at")
    can_delete = False

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(EmployerPilot)
class EmployerPilotAdmin(admin.ModelAdmin):
    list_display = ("organization", "stage", "independent_buyer", "quote_minor", "currency", "next_action_at")
    list_filter = ("stage", "independent_buyer")
    search_fields = ("organization__name",)
    readonly_fields = ("stage", "created_at", "updated_at")
    raw_id_fields = ("organization", "payment")
    inlines = (PilotActivityInline,)
    actions = ("advance", "cancel")

    def _transition(self, request, queryset, cancel=False):
        for pilot in queryset:
            target = "canceled" if cancel else STEPS[min(STEPS.index(pilot.stage) + 1, len(STEPS) - 1)] if pilot.stage in STEPS else ""
            try:
                transition_pilot(pilot.pk, target, actor=request.user,
                                 note="Staff reviewed the saved evidence and explicitly advanced this pilot in admin.")
            except ValidationError as exc:
                self.message_user(request, f"{pilot.organization}: {'; '.join(exc.messages)}", messages.ERROR)

    @admin.action(description="Advance one stage after reviewing saved evidence (never sends or charges)")
    def advance(self, request, queryset):
        self._transition(request, queryset)

    @admin.action(description="Close as canceled (does NOT issue a processor refund)")
    def cancel(self, request, queryset):
        self._transition(request, queryset, cancel=True)


@admin.register(EditorialOpportunity)
class EditorialOpportunityAdmin(admin.ModelAdmin):
    list_display = ("title", "audience", "decision", "destination_path")
    list_filter = ("audience", "decision")
    search_fields = ("title", "intent")


@admin.register(ArticleRevision)
class ArticleRevisionAdmin(admin.ModelAdmin):
    list_display = ("title", "state", "publish_at", "approved_by", "published_at")
    list_filter = ("state", "sponsored")
    readonly_fields = ("state", "approval_hash", "approved_by", "approved_at", "published_at", "published_hash", "evidence_snapshot")
    raw_id_fields = ("post", "opportunity")
    actions = ("approve", "publish", "reject")

    def get_readonly_fields(self, request, obj=None):
        if obj and obj.state == ArticleRevision.State.PUBLISHED:
            return [field.name for field in obj._meta.fields]
        return self.readonly_fields

    def _run(self, request, queryset, operation):
        for revision in queryset:
            try:
                operation(revision)
            except ValidationError as exc:
                self.message_user(request, f"{revision.title}: {'; '.join(exc.messages)}", messages.ERROR)

    @admin.action(description="Approve selected reviewed revisions and their schedule")
    def approve(self, request, queryset):
        self._run(request, queryset, lambda row: approve_revision(row.pk, actor=request.user))

    @admin.action(description="Publish selected approved, due revisions NOW (public action)")
    def publish(self, request, queryset):
        self._run(request, queryset, lambda row: publish_revision(row.pk))

    @admin.action(description="Reject unpublished revisions")
    def reject(self, request, queryset):
        queryset.exclude(state=ArticleRevision.State.PUBLISHED).update(
            state=ArticleRevision.State.REJECTED, approval_hash="", approved_by=None, approved_at=None)
