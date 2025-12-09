from django.contrib import admin
from django.utils import timezone

from .models import (
    FieldEvidence,
    FieldVerification,
    Gig,
    GigApplication,
    GigCategory,
    GigInterest,
    PortfolioItem,
    Review,
    RubricCriterion,
    Submission,
    Trial,
)


class RubricCriterionInline(admin.TabularInline):
    model = RubricCriterion
    extra = 1


@admin.register(GigCategory)
class GigCategoryAdmin(admin.ModelAdmin):
    list_display = ["name", "slug", "is_field", "created_at"]
    list_filter = ["is_field"]
    search_fields = ["name", "description"]
    prepopulated_fields = {"slug": ("name",)}


@admin.register(Gig)
class GigAdmin(admin.ModelAdmin):
    list_display = [
        "title",
        "organization",
        "category",
        "remote_policy",
        "status",
        "budget_fixed_cents",
        "trial_fee_cents",
        "is_featured",
        "published_at",
    ]
    list_filter = [
        "status",
        "remote_policy",
        "category",
        "organization",
        "is_featured",
    ]
    search_fields = ["title", "organization__name", "brief_redacted", "brief_full"]
    prepopulated_fields = {"slug": ("title",)}
    inlines = [RubricCriterionInline]
    actions = ["make_live", "close_gig"]

    @admin.action(description="Approve and publish selected gigs")
    def make_live(self, request, queryset):
        queryset.update(status=Gig.Status.LIVE, published_at=timezone.now())

    @admin.action(description="Close selected gigs")
    def close_gig(self, request, queryset):
        queryset.update(status=Gig.Status.CLOSED)


@admin.register(PortfolioItem)
class PortfolioItemAdmin(admin.ModelAdmin):
    list_display = ["title", "seeker", "visibility", "created_at"]
    list_filter = ["visibility", "created_at"]
    search_fields = ["title", "seeker__email", "tags"]


@admin.register(GigApplication)
class GigApplicationAdmin(admin.ModelAdmin):
    list_display = ["gig", "seeker", "status", "created_at"]
    list_filter = ["status", "created_at", "gig__category"]
    search_fields = ["gig__title", "seeker__email", "motivation"]


@admin.register(Trial)
class TrialAdmin(admin.ModelAdmin):
    list_display = ["application", "funding_status", "fee_cents", "due_at"]
    list_filter = ["funding_status"]
    search_fields = ["application__gig__title", "application__seeker__email"]
    actions = ["mark_funded"]

    @admin.action(description="Mark trial funded (manual)")
    def mark_funded(self, request, queryset):
        queryset.update(
            funding_status=Trial.FundingStatus.FUNDED,
            payment_reference="Admin funded",
        )


@admin.register(Submission)
class SubmissionAdmin(admin.ModelAdmin):
    list_display = ["application", "submitted_at"]
    search_fields = ["application__gig__title", "application__seeker__email"]


@admin.register(Review)
class ReviewAdmin(admin.ModelAdmin):
    list_display = ["application", "reviewer", "decision", "created_at"]
    list_filter = ["decision"]
    search_fields = ["application__gig__title", "reviewer__email"]


@admin.register(FieldVerification)
class FieldVerificationAdmin(admin.ModelAdmin):
    list_display = ["seeker", "country", "verification_level", "phone_verified"]
    list_filter = ["verification_level", "country"]
    search_fields = ["seeker__email", "country"]


@admin.register(FieldEvidence)
class FieldEvidenceAdmin(admin.ModelAdmin):
    list_display = ["submission", "witness_contact", "created_at"]


@admin.register(GigInterest)
class GigInterestAdmin(admin.ModelAdmin):
    list_display = ["email", "submitted_at", "has_message"]
    list_filter = ["submitted_at"]
    search_fields = ["email", "message"]
    readonly_fields = ["submitted_at"]
    date_hierarchy = "submitted_at"

    def has_message(self, obj):
        return bool(obj.message)
    has_message.boolean = True
    has_message.short_description = "Has Message"
