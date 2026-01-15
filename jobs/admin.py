from django.contrib import admin
from django.utils.html import format_html
from .models import (
    Job,
    Organization,
    Category,
    Story,
    StoryResonance,
    UserProfile,
    SavedJob,
    SeekerProfile,
    JobMatch,
    AssistantSubscription,
    AssistantGeneration,
    NewsletterSubscriber,
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


@admin.register(UserProfile)
class UserProfileAdmin(admin.ModelAdmin):
    list_display = [
        "user",
        "account_type",
        "headline",
        "email_newsletter",
        "created_at",
    ]
    list_filter = ["account_type", "email_newsletter", "created_at"]
    search_fields = ["user__email", "headline"]
    readonly_fields = ["created_at", "updated_at"]


# ============================================================================
# SAVED JOBS
# ============================================================================


@admin.register(SavedJob)
class SavedJobAdmin(admin.ModelAdmin):
    list_display = ["user", "job", "created_at"]
    list_filter = ["created_at"]
    search_fields = ["user__email", "job__title", "job__organization__name"]
    readonly_fields = ["created_at"]
    raw_id_fields = ["user", "job"]
    date_hierarchy = "created_at"


# ============================================================================
# SEEKER PROFILES & JOB MATCHING (Impact Profiles)
# ============================================================================


@admin.register(SeekerProfile)
class SeekerProfileAdmin(admin.ModelAdmin):
    list_display = [
        "user",
        "work_style",
        "experience_level",
        "remote_preference",
        "is_actively_looking",
        "profile_completeness",
        "wizard_completed",
        "created_at",
    ]
    list_filter = [
        "work_style",
        "experience_level",
        "remote_preference",
        "is_actively_looking",
        "visibility",
        "wizard_completed",
        "created_at",
    ]
    search_fields = ["user__email", "impact_statement"]
    readonly_fields = ["created_at", "updated_at", "profile_completeness"]
    filter_horizontal = ["impact_areas"]
    raw_id_fields = ["user"]

    fieldsets = [
        ("User", {"fields": ["user", "visibility", "is_actively_looking"]}),
        (
            "Profile",
            {
                "fields": [
                    "work_style",
                    "experience_level",
                    "skills",
                    "impact_areas",
                ]
            },
        ),
        (
            "Preferences",
            {
                "fields": [
                    "remote_preference",
                    "location_preferences",
                    "salary_min",
                    "salary_max",
                    "job_types",
                ]
            },
        ),
        ("Impact Statement", {"fields": ["impact_statement"]}),
        (
            "Wizard Progress",
            {
                "fields": [
                    "wizard_completed",
                    "wizard_step",
                    "profile_completeness",
                ]
            },
        ),
        ("Dates", {"fields": ["created_at", "updated_at"]}),
    ]


@admin.register(JobMatch)
class JobMatchAdmin(admin.ModelAdmin):
    list_display = ["seeker_email", "job", "score", "computed_at"]
    list_filter = ["score", "computed_at"]
    search_fields = ["seeker__user__email", "job__title", "job__organization__name"]
    readonly_fields = ["computed_at", "breakdown", "reasons", "gaps"]
    raw_id_fields = ["seeker", "job"]
    ordering = ["-score"]

    def seeker_email(self, obj):
        return obj.seeker.user.email
    seeker_email.short_description = "Seeker"
    seeker_email.admin_order_field = "seeker__user__email"

    fieldsets = [
        ("Match", {"fields": ["seeker", "job", "score"]}),
        (
            "Details",
            {
                "fields": ["breakdown", "reasons", "gaps"],
                "classes": ["collapse"],
            },
        ),
        ("Metadata", {"fields": ["computed_at"]}),
    ]


# ============================================================================
# AI ASSISTANT
# ============================================================================


@admin.register(AssistantSubscription)
class AssistantSubscriptionAdmin(admin.ModelAdmin):
    list_display = [
        "user",
        "is_subscribed",
        "free_uses_remaining",
        "total_uses",
        "subscribed_at",
        "expires_at",
    ]
    list_filter = ["is_subscribed", "subscribed_at", "expires_at"]
    search_fields = ["user__email", "stripe_subscription_id", "stripe_customer_id"]
    readonly_fields = ["created_at", "updated_at", "total_uses"]
    raw_id_fields = ["user"]

    fieldsets = [
        ("User", {"fields": ["user"]}),
        (
            "Usage",
            {"fields": ["free_uses_remaining", "total_uses"]},
        ),
        (
            "Subscription",
            {
                "fields": [
                    "is_subscribed",
                    "subscribed_at",
                    "expires_at",
                ]
            },
        ),
        (
            "Stripe",
            {
                "fields": ["stripe_subscription_id", "stripe_customer_id"],
                "classes": ["collapse"],
            },
        ),
        ("Dates", {"fields": ["created_at", "updated_at"]}),
    ]


@admin.register(AssistantGeneration)
class AssistantGenerationAdmin(admin.ModelAdmin):
    list_display = [
        "user",
        "generation_type",
        "preview_content",
        "created_at",
    ]
    list_filter = ["generation_type", "created_at"]
    search_fields = ["user__email", "job_url", "generated_content"]
    readonly_fields = ["created_at"]
    raw_id_fields = ["user"]
    date_hierarchy = "created_at"

    def preview_content(self, obj):
        return obj.preview
    preview_content.short_description = "Preview"

    fieldsets = [
        ("Generation", {"fields": ["user", "generation_type"]}),
        (
            "Input",
            {
                "fields": ["job_url", "job_description", "user_highlights"],
                "classes": ["collapse"],
            },
        ),
        ("Output", {"fields": ["generated_content"]}),
        ("Dates", {"fields": ["created_at"]}),
    ]


# ============================================================================
# NEWSLETTER
# ============================================================================


@admin.register(NewsletterSubscriber)
class NewsletterSubscriberAdmin(admin.ModelAdmin):
    list_display = ["email", "confirmed", "unsubscribed", "source", "created_at"]
    list_filter = ["confirmed", "unsubscribed", "source", "created_at"]
    search_fields = ["email"]
    readonly_fields = ["created_at", "confirmed_at", "unsubscribed_at"]
    date_hierarchy = "created_at"
    ordering = ["-created_at"]
