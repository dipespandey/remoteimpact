"""
Django admin configuration for Hire for Mission models.
Add to jobs/admin.py: from .admin_hire_for_mission import *
"""
from django.contrib import admin
from django.utils.html import format_html
from .models import ScreeningSession, ScreeningCandidate, TeamFeedback


@admin.register(ScreeningSession)
class ScreeningSessionAdmin(admin.ModelAdmin):
    list_display = ("job_title", "status_badge", "applicant_count", "qualified_count", "created_at")
    list_filter = ("status", "created_at", "job__organization")
    search_fields = ("job__title", "job__organization__name")
    readonly_fields = ("id", "created_at", "updated_at", "closed_at")
    
    fieldsets = (
        ("Job Information", {
            "fields": ("id", "job", "status"),
        }),
        ("Scoring Configuration", {
            "fields": ("skills_weight", "mission_weight", "culture_weight"),
            "description": "Weights should sum to 100 for proper calculation.",
        }),
        ("Screening Questions", {
            "fields": ("screening_questions",),
        }),
        ("Timestamps", {
            "fields": ("created_at", "updated_at", "closed_at"),
            "classes": ("collapse",),
        }),
    )
    
    def job_title(self, obj):
        return obj.job.title
    job_title.short_description = "Job"
    
    def status_badge(self, obj):
        colors = {
            "draft": "gray",
            "active": "green",
            "review": "orange",
            "closed": "red",
        }
        color = colors.get(obj.status, "gray")
        return format_html(
            '<span style="color: {}; font-weight: bold;">{}</span>',
            color,
            obj.get_status_display(),
        )
    status_badge.short_description = "Status"
    
    def applicant_count(self, obj):
        return obj.get_total_applicants()
    applicant_count.short_description = "Total Applicants"
    
    def qualified_count(self, obj):
        return obj.get_qualified_count()
    qualified_count.short_description = "Qualified (≥70)"


@admin.register(ScreeningCandidate)
class ScreeningCandidateAdmin(admin.ModelAdmin):
    list_display = (
        "candidate_name",
        "job_title",
        "combined_score_display",
        "team_recommendation_badge",
        "has_concerns_badge",
        "created_at",
    )
    list_filter = (
        "session__job__title",
        "team_recommendation",
        "has_concerns",
        "combined_score",
        "created_at",
    )
    search_fields = (
        "application__applicant__email",
        "application__applicant__first_name",
        "application__applicant__last_name",
        "session__job__title",
    )
    readonly_fields = (
        "id",
        "created_at",
        "updated_at",
        "scored_at",
        "score_breakdown",
    )
    
    fieldsets = (
        ("Candidate Information", {
            "fields": ("id", "session", "application"),
        }),
        ("AI Scores", {
            "fields": (
                "skills_score",
                "mission_score",
                "culture_score",
                "combined_score",
                "score_breakdown",
            ),
        }),
        ("Scoring Details", {
            "fields": ("reasoning", "scored_at"),
            "classes": ("collapse",),
        }),
        ("Team Feedback", {
            "fields": (
                "team_recommendation",
                "team_consensus_score",
                "has_concerns",
                "concerns_text",
            ),
        }),
        ("Candidate Responses", {
            "fields": ("screening_answers",),
            "classes": ("collapse",),
        }),
        ("Ranking", {
            "fields": ("ranked_position",),
        }),
        ("Timestamps", {
            "fields": ("created_at", "updated_at"),
            "classes": ("collapse",),
        }),
    )
    
    def candidate_name(self, obj):
        return obj.application.applicant.get_full_name() or obj.application.applicant.email
    candidate_name.short_description = "Candidate"
    
    def job_title(self, obj):
        return obj.session.job.title
    job_title.short_description = "Job"
    
    def combined_score_display(self, obj):
        if obj.combined_score >= 80:
            color = "green"
        elif obj.combined_score >= 60:
            color = "orange"
        else:
            color = "red"
        return format_html(
            '<span style="color: {}; font-weight: bold; font-size: 16px;">{}/100</span>',
            color,
            obj.combined_score,
        )
    combined_score_display.short_description = "Score"
    
    def team_recommendation_badge(self, obj):
        colors = {
            "strong_yes": "darkgreen",
            "yes": "green",
            "maybe": "orange",
            "no": "red",
            "strong_no": "darkred",
            "pending": "gray",
        }
        color = colors.get(obj.team_recommendation, "gray")
        return format_html(
            '<span style="color: {}; font-weight: bold;">{}</span>',
            color,
            obj.get_team_recommendation_display(),
        )
    team_recommendation_badge.short_description = "Recommendation"
    
    def has_concerns_badge(self, obj):
        if obj.has_concerns:
            return format_html(
                '<span style="color: orange; font-weight: bold;">⚠️ Yes</span>'
            )
        return format_html(
            '<span style="color: green; font-weight: bold;">✓ No</span>'
        )
    has_concerns_badge.short_description = "Concerns"
    
    def score_breakdown(self, obj):
        return f"Skills: {obj.skills_score} | Mission: {obj.mission_score} | Culture: {obj.culture_score}"
    score_breakdown.short_description = "Score Breakdown"


@admin.register(TeamFeedback)
class TeamFeedbackAdmin(admin.ModelAdmin):
    list_display = (
        "candidate_name",
        "reviewer_name",
        "rating_stars",
        "recommendation_badge",
        "reviewed_at",
    )
    list_filter = (
        "rating",
        "recommendation",
        "reviewed_at",
        "candidate__session__job",
    )
    search_fields = (
        "candidate__application__applicant__email",
        "reviewer__email",
        "comment",
    )
    readonly_fields = ("id", "reviewed_at", "updated_at")
    
    fieldsets = (
        ("Feedback Details", {
            "fields": ("candidate", "reviewer"),
        }),
        ("Review", {
            "fields": ("rating", "comment", "recommendation"),
        }),
        ("Timestamps", {
            "fields": ("reviewed_at", "updated_at"),
            "classes": ("collapse",),
        }),
    )
    
    def candidate_name(self, obj):
        return obj.candidate.application.applicant.get_full_name() or obj.candidate.application.applicant.email
    candidate_name.short_description = "Candidate"
    
    def reviewer_name(self, obj):
        return obj.reviewer.get_full_name() or obj.reviewer.email
    reviewer_name.short_description = "Reviewer"
    
    def rating_stars(self, obj):
        stars = "★" * obj.rating + "☆" * (5 - obj.rating)
        colors = {
            1: "red",
            2: "orange",
            3: "goldenrod",
            4: "lightgreen",
            5: "green",
        }
        color = colors.get(obj.rating, "gray")
        return format_html(
            '<span style="color: {}; font-weight: bold;">{} ({}/5)</span>',
            color,
            stars,
            obj.rating,
        )
    rating_stars.short_description = "Rating"
    
    def recommendation_badge(self, obj):
        colors = {
            "strong_yes": "darkgreen",
            "yes": "green",
            "maybe": "orange",
            "no": "red",
            "strong_no": "darkred",
            "pending": "gray",
        }
        color = colors.get(obj.recommendation, "gray")
        return format_html(
            '<span style="color: {}; font-weight: bold;">{}</span>',
            color,
            obj.get_recommendation_display(),
        )
    recommendation_badge.short_description = "Recommendation"
