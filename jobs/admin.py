from django.contrib import admin
from .models import (
    Job,
    Organization,
    Category,
    Story,
    StoryResonance,
    Sprint,
    SprintCompletion,
    PurposeProfile,
    UserPath,
    UserProfile,
)


@admin.register(Organization)
class OrganizationAdmin(admin.ModelAdmin):
    list_display = ["name", "website", "verification_status", "created_at"]
    prepopulated_fields = {"slug": ("name",)}
    search_fields = ["name", "description"]
    list_filter = ["verification_status", "created_at"]
    actions = ["mark_verified", "mark_rejected"]

    @admin.action(description="Mark selected orgs verified")
    def mark_verified(self, request, queryset):
        queryset.update(verification_status=Organization.VerificationStatus.VERIFIED)

    @admin.action(description="Mark selected orgs rejected")
    def mark_rejected(self, request, queryset):
        queryset.update(verification_status=Organization.VerificationStatus.REJECTED)


@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    list_display = ["name", "icon"]
    prepopulated_fields = {"slug": ("name",)}


@admin.register(Job)
class JobAdmin(admin.ModelAdmin):
    list_display = [
        "title",
        "organization",
        "category",
        "job_type",
        "source",
        "is_active",
        "is_featured",
        "posted_at",
    ]
    list_filter = [
        "is_active",
        "is_featured",
        "job_type",
        "category",
        "source",
        "posted_at",
    ]
    prepopulated_fields = {"slug": ("title",)}
    search_fields = ["title", "description", "organization__name"]
    date_hierarchy = "posted_at"
    fieldsets = (
        (
            "Basic Information",
            {"fields": ("title", "slug", "organization", "category", "job_type")},
        ),
        ("Description", {"fields": ("description", "requirements")}),
        (
            "Application",
            {"fields": ("application_url", "application_email", "location")},
        ),
        (
            "Compensation",
            {
                "fields": ("salary_min", "salary_max", "salary_currency"),
                "classes": ("collapse",),
            },
        ),
        (
            "Status",
            {
                "fields": (
                    "is_active",
                    "is_featured",
                    "expires_at",
                    "source",
                    "external_id",
                )
            },
        ),
        ("Metadata", {"fields": ("raw_data",), "classes": ("collapse",)}),
    )


# ============================================================================
# IMPACT STORIES ADMIN
# ============================================================================


@admin.register(Story)
class StoryAdmin(admin.ModelAdmin):
    list_display = [
        "author_name",
        "organization",
        "status",
        "resonate_count",
        "view_count",
        "published_at",
    ]
    list_filter = ["status", "is_verified", "categories"]
    search_fields = ["author_name", "content", "organization__name"]
    filter_horizontal = ["categories", "related_jobs"]
    readonly_fields = ["resonate_count", "view_count", "created_at", "updated_at"]

    fieldsets = [
        (
            "Author",
            {"fields": ["author_name", "author_title", "author_image", "is_verified"]},
        ),
        ("Content", {"fields": ["content_raw", "content", "skills"]}),
        ("Relations", {"fields": ["organization", "categories", "related_jobs"]}),
        ("Engagement", {"fields": ["resonate_count", "view_count"]}),
        ("Status", {"fields": ["status", "published_at", "created_at", "updated_at"]}),
    ]


@admin.register(StoryResonance)
class StoryResonanceAdmin(admin.ModelAdmin):
    list_display = ["story", "resonance_type", "user", "session_key", "created_at"]
    list_filter = ["resonance_type", "created_at"]
    search_fields = ["story__author_name", "user__email", "session_key"]
    readonly_fields = ["created_at"]


@admin.register(Sprint)
class SprintAdmin(admin.ModelAdmin):
    list_display = [
        "title",
        "organization",
        "task_type",
        "difficulty",
        "time_estimate_minutes",
        "completion_count",
        "is_active",
    ]
    list_filter = ["task_type", "difficulty", "is_active"]
    search_fields = ["title", "organization__name"]
    filter_horizontal = ["categories"]
    prepopulated_fields = {"slug": ["title"]}

    fieldsets = [
        ("Basic", {"fields": ["title", "slug", "organization", "categories"]}),
        ("Content", {"fields": ["description", "instructions", "impact_statement"]}),
        (
            "Details",
            {
                "fields": [
                    "task_type",
                    "difficulty",
                    "time_estimate_minutes",
                    "task_url",
                ]
            },
        ),
        ("Settings", {"fields": ["auto_verify", "is_active", "completion_count"]}),
    ]


@admin.register(SprintCompletion)
class SprintCompletionAdmin(admin.ModelAdmin):
    list_display = ["user", "sprint", "status", "started_at", "verified_at"]
    list_filter = ["status", "started_at"]
    search_fields = ["user__email", "sprint__title"]
    readonly_fields = ["started_at", "submitted_at", "verified_at"]


@admin.register(PurposeProfile)
class PurposeProfileAdmin(admin.ModelAdmin):
    list_display = ["user", "updated_at"]
    search_fields = ["user__email", "purpose_statement"]
    readonly_fields = ["updated_at"]


@admin.register(UserPath)
class UserPathAdmin(admin.ModelAdmin):
    list_display = ["session_key", "created_at", "converted_user", "converted_at"]
    list_filter = ["converted_at"]
    search_fields = ["session_key", "converted_user__email"]
    readonly_fields = ["created_at", "updated_at"]


@admin.register(UserProfile)
class UserProfileAdmin(admin.ModelAdmin):
    list_display = [
        "user",
        "account_type",
        "headline",
        "bio",
        "years_experience",
        "country",
        "created_at",
    ]
    list_filter = ["account_type", "created_at", "country"]
    search_fields = ["user__email", "headline", "bio", "country"]
    readonly_fields = ["created_at", "updated_at"]
