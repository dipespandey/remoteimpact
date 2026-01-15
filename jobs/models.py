import uuid

from django.contrib.auth import get_user_model
from django.contrib.postgres.indexes import GinIndex
from django.contrib.postgres.search import SearchVectorField
from django.db import models
from django.urls import reverse
from django.utils import timezone
from pgvector.django import HnswIndex, VectorField


class Organization(models.Model):
    """Non-profit organization posting jobs"""

    class VerificationStatus(models.TextChoices):
        UNVERIFIED = "unverified", "Unverified"
        PENDING = "pending", "Pending review"
        VERIFIED = "verified", "Verified"
        REJECTED = "rejected", "Rejected"

    name = models.CharField(max_length=255)
    slug = models.SlugField(max_length=255, unique=True)
    description = models.TextField(
        blank=True, help_text="Brief description of the organization"
    )
    website = models.TextField(blank=True)  # TextField to handle long/multi-language URLs
    logo = models.ImageField(upload_to="organizations/", blank=True, null=True)
    members = models.ManyToManyField(
        get_user_model(), related_name="organizations", blank=True
    )
    verification_status = models.CharField(
        max_length=20,
        choices=VerificationStatus.choices,
        default=VerificationStatus.UNVERIFIED,
        help_text="Staff-reviewed verification state",
    )
    verification_notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class UserProfile(models.Model):
    """Extended user profile with account type and bio."""

    class AccountType(models.TextChoices):
        SEEKER = "seeker", "Job Seeker"
        EMPLOYER = "employer", "Employer"

    user = models.OneToOneField(
        get_user_model(), on_delete=models.CASCADE, related_name="profile"
    )
    account_type = models.CharField(
        max_length=20, choices=AccountType.choices, blank=True, null=True
    )

    # Seeker fields
    headline = models.CharField(max_length=200, blank=True)
    bio = models.TextField(blank=True, help_text="Short professional bio")
    linkedin_url = models.URLField(blank=True)
    years_experience = models.PositiveIntegerField(default=0)
    country = models.CharField(max_length=2, blank=True, help_text="ISO country code")

    # Email preferences
    email_newsletter = models.BooleanField(
        default=True, help_text="Receive weekly job digest emails"
    )
    email_newsletter_unsubscribed_at = models.DateTimeField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.user} ({self.account_type})"


class Category(models.Model):
    """Job categories (e.g., Education, Healthcare, Environment)"""

    name = models.CharField(max_length=100, unique=True)
    slug = models.SlugField(max_length=255, unique=True)
    description = models.TextField(blank=True)
    icon = models.CharField(max_length=50, blank=True, help_text="Emoji or icon name")

    class Meta:
        verbose_name_plural = "Categories"
        ordering = ["name"]

    def __str__(self):
        return self.name


class Job(models.Model):
    """Job posting model"""

    class Source(models.TextChoices):
        MANUAL = "manual", "Manual"
        EIGHTY_THOUSAND = "80000hours", "80,000 Hours"
        IDEALIST = "idealist", "Idealist"
        RELIEFWEB = "reliefweb", "ReliefWeb"
        CLIMATEBASE = "climatebase", "Climatebase"
        GREENHOUSE = "greenhouse", "Greenhouse"
        LEVER = "lever", "Lever"
        ASHBY = "ashby", "Ashby"
        PROBABLYGOOD = "probablygood", "Probably Good"

    JOB_TYPE_CHOICES = [
        ("full-time", "Full-time"),
        ("part-time", "Part-time"),
        ("contract", "Contract"),
        ("freelance", "Freelance"),
    ]

    title = models.CharField(max_length=500)
    slug = models.SlugField(max_length=255, unique=True)
    organization = models.ForeignKey(
        Organization, on_delete=models.CASCADE, related_name="jobs"
    )
    category = models.ForeignKey(
        Category, on_delete=models.SET_NULL, null=True, related_name="jobs"
    )

    description = models.TextField(help_text="Full job description")
    requirements = models.TextField(help_text="Job requirements and qualifications")
    location = models.CharField(
        max_length=500, default="Remote", help_text="Always Remote for this board"
    )
    job_type = models.CharField(
        max_length=20, choices=JOB_TYPE_CHOICES, default="full-time"
    )

    application_url = models.TextField(help_text="URL to apply for this job")  # TextField for long URLs
    application_email = models.EmailField(
        blank=True, help_text="Alternative: email to apply"
    )

    salary_min = models.DecimalField(
        max_digits=10, decimal_places=2, null=True, blank=True
    )
    salary_max = models.DecimalField(
        max_digits=10, decimal_places=2, null=True, blank=True
    )
    salary_currency = models.CharField(max_length=3, default="USD")

    # AI-extracted / specific fields
    impact = models.TextField(
        blank=True, help_text="Specific impact the role will drive"
    )
    benefits = models.TextField(
        blank=True, help_text="Benefits, perks, and culture notes"
    )
    company_description = models.TextField(
        blank=True, help_text="AI-extracted company bio if different from Org"
    )
    how_to_apply_text = models.TextField(
        blank=True, help_text="Specific application instructions"
    )

    # Skills extracted from job requirements (for matching)
    skills = models.JSONField(
        default=list, blank=True, help_text="AI-extracted skill slugs from job"
    )

    source = models.CharField(
        max_length=50, choices=Source.choices, default=Source.MANUAL
    )
    external_id = models.CharField(
        max_length=255, blank=True, null=True, help_text="ID from the upstream source"
    )
    raw_data = models.JSONField(default=dict, blank=True)

    # Vector search fields
    embedding = VectorField(dimensions=384, null=True, blank=True)
    search_vector = SearchVectorField(null=True, blank=True)

    is_active = models.BooleanField(default=True)
    is_featured = models.BooleanField(default=False)

    poster = models.ForeignKey(
        get_user_model(),
        on_delete=models.CASCADE,
        related_name="posted_jobs",
        null=True,
        blank=True,
    )

    posted_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)
    expires_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-is_featured", "-posted_at"]
        indexes = [
            models.Index(fields=["is_active", "-posted_at"]),
            models.Index(fields=["category", "is_active"]),
            models.Index(fields=["source", "external_id"]),
            GinIndex(fields=["search_vector"], name="job_search_idx"),
            HnswIndex(
                name="job_embedding_idx",
                fields=["embedding"],
                m=16,
                ef_construction=64,
                opclasses=["vector_cosine_ops"],
            ),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["source", "external_id"],
                name="unique_job_source_external_id",
                condition=models.Q(external_id__isnull=False),
            )
        ]

    def __str__(self):
        return f"{self.title} at {self.organization.name}"

    def get_absolute_url(self):
        return reverse("jobs:job_detail", kwargs={"slug": self.slug})

    def is_expired(self):
        if self.expires_at:
            return timezone.now() > self.expires_at
        return False

    @property
    def is_available(self):
        return self.is_active and not self.is_expired()


class SavedJob(models.Model):
    """Bookmark jobs for authenticated users."""

    user = models.ForeignKey(
        get_user_model(), on_delete=models.CASCADE, related_name="saved_jobs"
    )
    job = models.ForeignKey(Job, on_delete=models.CASCADE, related_name="saves")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ("user", "job")
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.user} saved {self.job}"


class Application(models.Model):
    """Track job applications tracked on platform."""

    class Status(models.TextChoices):
        APPLIED = "applied", "Applied"
        REVIEWED = "reviewed", "Reviewed"
        INTERVIEW = "interview", "Interview"
        OFFER = "offer", "Offer"
        REJECTED = "rejected", "Rejected"

    job = models.ForeignKey(Job, on_delete=models.CASCADE, related_name="applications")
    applicant = models.ForeignKey(
        get_user_model(), on_delete=models.CASCADE, related_name="applications"
    )
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.APPLIED
    )
    cover_letter = models.TextField(blank=True, help_text="Optional cover note")

    # We can add a resume field later if we enforce file uploads,
    # for now we assume they might apply via external link but we track it here.

    applied_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ("job", "applicant")
        ordering = ["-applied_at"]

    def __str__(self):
        return f"{self.applicant} -> {self.job}"


# ============================================================================
# IMPACT STORIES MODELS
# ============================================================================

User = get_user_model()


class Story(models.Model):
    """First-person impact stories from people working in the sector"""

    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        PUBLISHED = "published", "Published"
        ARCHIVED = "archived", "Archived"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    # Author info
    author_name = models.CharField(max_length=200)
    author_title = models.CharField(max_length=200)
    author_image = models.ImageField(
        upload_to="stories/authors/", blank=True, null=True
    )
    is_verified = models.BooleanField(
        default=False, help_text="Verified by organization"
    )

    # Story content
    content_raw = models.TextField(help_text="Raw story text before formatting")
    content = models.TextField(
        help_text="Formatted story (2-3 sentences, outcome-focused)"
    )

    # Relations
    organization = models.ForeignKey(
        Organization, on_delete=models.CASCADE, related_name="stories"
    )
    categories = models.ManyToManyField(Category, related_name="stories")
    related_jobs = models.ManyToManyField(Job, blank=True, related_name="stories")

    # Metadata
    skills = models.JSONField(default=list, blank=True, help_text="List of skill tags")

    # Engagement
    resonate_count = models.PositiveIntegerField(default=0)
    view_count = models.PositiveIntegerField(default=0)

    # Status
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.DRAFT
    )
    published_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-published_at"]
        verbose_name_plural = "Stories"
        indexes = [
            models.Index(fields=["status", "-published_at"]),
        ]

    def __str__(self):
        return f"{self.author_name} @ {self.organization.name}"

    def increment_resonates(self):
        self.resonate_count = models.F("resonate_count") + 1
        self.save(update_fields=["resonate_count"])
        self.refresh_from_db()

    def increment_views(self):
        self.view_count = models.F("view_count") + 1
        self.save(update_fields=["view_count"])
        self.refresh_from_db()


class StoryResonance(models.Model):
    """Tracks when users resonate with stories"""

    class ResonanceType(models.TextChoices):
        CAUSE = "cause", "The cause/mission"
        WORK = "work", "The type of work"
        OUTCOME = "outcome", "The outcome/impact"
        ORG = "org", "The organization"

    story = models.ForeignKey(
        Story, on_delete=models.CASCADE, related_name="resonances"
    )

    # Can be anonymous (session) or logged-in user
    session_key = models.CharField(max_length=40, blank=True)
    user = models.ForeignKey(User, on_delete=models.CASCADE, null=True, blank=True)

    resonance_type = models.CharField(max_length=20, choices=ResonanceType.choices)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["session_key", "story"]),
            models.Index(fields=["user", "story"]),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["story", "session_key"],
                name="unique_session_story_resonance",
                condition=models.Q(session_key__isnull=False)
                & models.Q(user__isnull=True),
            ),
            models.UniqueConstraint(
                fields=["story", "user"],
                name="unique_user_story_resonance",
                condition=models.Q(user__isnull=False),
            ),
        ]

    def __str__(self):
        identifier = self.user.email if self.user else f"Session {self.session_key[:8]}"
        return f"{identifier} → {self.story.author_name} ({self.resonance_type})"


class Sprint(models.Model):
    """Micro-contribution tasks users can complete"""

    class Difficulty(models.TextChoices):
        BEGINNER = "beginner", "Beginner"
        INTERMEDIATE = "intermediate", "Intermediate"
        ADVANCED = "advanced", "Advanced"

    class TaskType(models.TextChoices):
        DATA_ENTRY = "data_entry", "Data Entry"
        TAGGING = "tagging", "Tagging/Labeling"
        RESEARCH = "research", "Research"
        TRANSLATION = "translation", "Translation"
        REVIEW = "review", "Review/Feedback"
        OTHER = "other", "Other"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    title = models.CharField(max_length=500)
    slug = models.SlugField(max_length=255, unique=True)

    organization = models.ForeignKey(
        Organization, on_delete=models.CASCADE, related_name="sprints"
    )
    categories = models.ManyToManyField(Category, related_name="sprints")

    description = models.TextField()
    instructions = models.TextField(help_text="Step-by-step instructions")

    task_type = models.CharField(max_length=20, choices=TaskType.choices)
    difficulty = models.CharField(
        max_length=20, choices=Difficulty.choices, default=Difficulty.BEGINNER
    )
    time_estimate_minutes = models.PositiveIntegerField(
        default=15, help_text="Estimated time in minutes"
    )

    # External link for the task
    task_url = models.URLField(
        blank=True, help_text="External URL where task is completed"
    )

    # Impact statement
    impact_statement = models.TextField(help_text="Why this matters (shown to users)")

    # Verification
    auto_verify = models.BooleanField(
        default=False, help_text="Automatically verify completions"
    )

    is_active = models.BooleanField(
        default=True,
        help_text="Job is live on the site. Manual jobs are inactive until paid.",
    )
    is_paid = models.BooleanField(
        default=False, help_text="True if payment is confirmed."
    )
    stripe_payment_intent = models.CharField(max_length=255, blank=True, null=True)
    is_featured = models.BooleanField(default=False)
    completion_count = models.PositiveIntegerField(default=0)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.title} @ {self.organization.name}"


class SprintCompletion(models.Model):
    """Tracks user sprint progress and completions"""

    class Status(models.TextChoices):
        STARTED = "started", "Started"
        SUBMITTED = "submitted", "Submitted"
        VERIFIED = "verified", "Verified"
        REJECTED = "rejected", "Rejected"

    sprint = models.ForeignKey(
        Sprint, on_delete=models.CASCADE, related_name="completions"
    )
    user = models.ForeignKey(
        User, on_delete=models.CASCADE, related_name="sprint_completions"
    )

    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.STARTED
    )

    submission_notes = models.TextField(blank=True)
    submission_url = models.URLField(blank=True)

    # Impact card text (generated after verification)
    impact_card_text = models.TextField(blank=True)

    started_at = models.DateTimeField(auto_now_add=True)
    submitted_at = models.DateTimeField(null=True, blank=True)
    verified_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-started_at"]
        unique_together = [["sprint", "user"]]

    def __str__(self):
        return f"{self.user.email} → {self.sprint.title} ({self.status})"


class PurposeProfile(models.Model):
    """User's evolving purpose based on engagement"""

    user = models.OneToOneField(
        User, on_delete=models.CASCADE, related_name="purpose_profile"
    )

    # AI-generated purpose statement
    purpose_statement = models.TextField(
        blank=True, help_text="LLM-generated based on resonances"
    )

    # Aggregated data
    top_causes = models.JSONField(
        default=list, help_text="List of {category, count} dicts"
    )
    resonated_skills = models.JSONField(
        default=list, help_text="Skills from resonated stories"
    )

    # Category scores (for matching)
    category_scores = models.JSONField(
        default=dict, help_text="Category slug → score mapping"
    )

    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Purpose Profile: {self.user.email}"


class UserPath(models.Model):
    """Anonymous session tracking before signup"""

    session_key = models.CharField(max_length=40, unique=True)

    # Track engagement before signup
    resonated_stories = models.JSONField(default=list, help_text="List of story UUIDs")
    category_interactions = models.JSONField(
        default=dict, help_text="Category slug → interaction count"
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    # If user signs up, link it
    converted_user = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True
    )
    converted_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return (
            f"Path: {self.session_key[:8]} ({len(self.resonated_stories)} resonances)"
        )


# =============================================================================
# Impact Match - Seeker Profile & Matching Models
# =============================================================================


class SeekerProfile(models.Model):
    """Extended profile for job seekers with matching data.

    This is the core profile used for AI-powered job matching.
    Built through the onboarding wizard.
    """

    class WorkStyle(models.TextChoices):
        BUILDER = "builder", "Building things (engineering, product, design)"
        STRATEGIST = "strategist", "Moving ideas (strategy, communications, policy)"
        OPERATOR = "operator", "Running operations (ops, finance, HR, admin)"
        DIRECT = "direct", "Direct service (program delivery, field work)"
        RESEARCHER = "researcher", "Research & analysis"

    class ExperienceLevel(models.TextChoices):
        EARLY = "early", "Early career (0-2 years)"
        MID = "mid", "Mid-level (3-6 years)"
        SENIOR = "senior", "Senior (7-12 years)"
        LEADERSHIP = "leadership", "Leadership (12+ years)"
        CAREER_CHANGER = "career_changer", "Career changer"

    class RemotePreference(models.TextChoices):
        REMOTE = "remote", "Fully remote"
        HYBRID = "hybrid", "Hybrid"
        ONSITE = "onsite", "On-site"
        FLEXIBLE = "flexible", "Flexible"

    class Visibility(models.TextChoices):
        PUBLIC = "public", "Public - visible to all orgs"
        MATCHING = "matching", "Visible to matching orgs only"
        HIDDEN = "hidden", "Hidden from talent search"

    user = models.OneToOneField(
        User, on_delete=models.CASCADE, related_name="seeker_profile"
    )

    # -------------------------------------------------------------------------
    # Core profile data (from wizard)
    # -------------------------------------------------------------------------

    # Impact areas they care about (M2M to existing Category)
    impact_areas = models.ManyToManyField(
        Category, blank=True, related_name="interested_seekers"
    )

    # Work style preference
    work_style = models.CharField(
        max_length=20, choices=WorkStyle.choices, blank=True
    )

    # Experience level
    experience_level = models.CharField(
        max_length=20, choices=ExperienceLevel.choices, blank=True
    )

    # Skills (JSON array of skill slugs from taxonomy)
    skills = models.JSONField(
        default=list, help_text="List of skill slugs, e.g. ['python', 'data-analysis']"
    )

    # -------------------------------------------------------------------------
    # Preferences
    # -------------------------------------------------------------------------

    remote_preference = models.CharField(
        max_length=20, choices=RemotePreference.choices, blank=True
    )

    # Location preferences - list of country codes or "anywhere"
    location_preferences = models.JSONField(
        default=list, help_text="ISO country codes or regions, e.g. ['US', 'GB', 'europe']"
    )

    salary_min = models.PositiveIntegerField(
        null=True, blank=True, help_text="Minimum expected salary (USD)"
    )
    salary_max = models.PositiveIntegerField(
        null=True, blank=True, help_text="Maximum expected salary (USD)"
    )

    # Job types interested in
    job_types = models.JSONField(
        default=list, help_text="e.g. ['full-time', 'contract', 'part-time']"
    )

    # -------------------------------------------------------------------------
    # Story & Motivation
    # -------------------------------------------------------------------------

    impact_statement = models.TextField(
        max_length=500, blank=True,
        help_text="2-3 sentences on what draws them to impact work"
    )

    # -------------------------------------------------------------------------
    # Optional Assessment
    # -------------------------------------------------------------------------

    assessment_answers = models.JSONField(
        default=dict,
        help_text="Answers to optional assessment questions, e.g. {'time_horizon': 'future', 'org_size': 'startup'}"
    )

    # -------------------------------------------------------------------------
    # Vector Search
    # -------------------------------------------------------------------------

    embedding = VectorField(dimensions=384, null=True, blank=True)
    search_vector = SearchVectorField(null=True, blank=True)

    # -------------------------------------------------------------------------
    # Visibility & Search
    # -------------------------------------------------------------------------

    visibility = models.CharField(
        max_length=20, choices=Visibility.choices, default=Visibility.PUBLIC
    )
    is_actively_looking = models.BooleanField(
        default=True, help_text="Currently seeking opportunities"
    )

    # -------------------------------------------------------------------------
    # Wizard Progress
    # -------------------------------------------------------------------------

    wizard_completed = models.BooleanField(default=False)
    wizard_step = models.PositiveIntegerField(
        default=0, help_text="Current step in onboarding wizard (0 = not started)"
    )
    profile_completeness = models.PositiveIntegerField(
        default=0, help_text="Profile completeness percentage 0-100"
    )

    # -------------------------------------------------------------------------
    # Metadata
    # -------------------------------------------------------------------------

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Seeker Profile"
        verbose_name_plural = "Seeker Profiles"
        indexes = [
            GinIndex(fields=["search_vector"], name="seeker_search_idx"),
            HnswIndex(
                name="seeker_embedding_idx",
                fields=["embedding"],
                m=16,
                ef_construction=64,
                opclasses=["vector_cosine_ops"],
            ),
        ]

    def __str__(self):
        return f"Seeker: {self.user.email}"

    def calculate_completeness(self):
        """Calculate and update profile completeness percentage."""
        score = 0
        max_score = 100

        # Impact areas (20 points)
        if self.impact_areas.exists():
            score += 20

        # Work style (15 points)
        if self.work_style:
            score += 15

        # Experience level (15 points)
        if self.experience_level:
            score += 15

        # Skills (20 points) - need at least 3
        if len(self.skills) >= 3:
            score += 20
        elif len(self.skills) >= 1:
            score += 10

        # Remote preference (5 points)
        if self.remote_preference:
            score += 5

        # Salary expectations (5 points)
        if self.salary_min or self.salary_max:
            score += 5

        # Impact statement (15 points)
        if self.impact_statement and len(self.impact_statement) >= 50:
            score += 15
        elif self.impact_statement:
            score += 7

        # Assessment (5 points bonus)
        if self.assessment_answers:
            score += 5

        self.profile_completeness = min(score, max_score)
        return self.profile_completeness


class JobMatch(models.Model):
    """Cached match scores between seekers and jobs.

    Pre-computed for performance. Updated when seeker profile
    or job changes.
    """

    seeker = models.ForeignKey(
        SeekerProfile, on_delete=models.CASCADE, related_name="matches"
    )
    job = models.ForeignKey(
        "Job", on_delete=models.CASCADE, related_name="seeker_matches"
    )

    # Overall match score (0-100)
    score = models.PositiveIntegerField(db_index=True)

    # Score breakdown by factor
    breakdown = models.JSONField(
        default=dict,
        help_text="{'impact': 85, 'skills': 70, 'experience': 100, 'location': 80, 'salary': 60}"
    )

    # Human-readable match reasons
    reasons = models.JSONField(
        default=list,
        help_text="['Climate focus matches', '8 of 10 required skills', ...]"
    )

    # Skill gaps identified
    gaps = models.JSONField(
        default=list,
        help_text="['Terraform', 'AWS', ...] - skills job wants that seeker lacks"
    )

    computed_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ["seeker", "job"]
        indexes = [
            models.Index(fields=["seeker", "-score"]),
            models.Index(fields=["job", "-score"]),
        ]
        ordering = ["-score"]

    def __str__(self):
        return f"{self.seeker.user.email} ↔ {self.job.title}: {self.score}%"


class TalentInvitation(models.Model):
    """Tracks org invitations to candidates to apply for a job."""

    class Status(models.TextChoices):
        SENT = "sent", "Sent"
        VIEWED = "viewed", "Viewed"
        APPLIED = "applied", "Applied"
        DECLINED = "declined", "Declined"

    organization = models.ForeignKey(
        Organization, on_delete=models.CASCADE, related_name="talent_invitations"
    )
    job = models.ForeignKey(
        "Job", on_delete=models.CASCADE, related_name="talent_invitations"
    )
    seeker = models.ForeignKey(
        SeekerProfile, on_delete=models.CASCADE, related_name="invitations_received"
    )

    # Personalized message from org
    message = models.TextField()

    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.SENT
    )

    sent_at = models.DateTimeField(auto_now_add=True)
    viewed_at = models.DateTimeField(null=True, blank=True)
    responded_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        unique_together = ["job", "seeker"]
        ordering = ["-sent_at"]

    def __str__(self):
        return f"Invite: {self.organization.name} → {self.seeker.user.email} for {self.job.title}"


class OrgSubscription(models.Model):
    """Organization subscription tier for premium features."""

    class Tier(models.TextChoices):
        FREE = "free", "Free"
        PRO = "pro", "Pro ($149/mo)"
        GROWTH = "growth", "Growth ($299/mo)"
        ENTERPRISE = "enterprise", "Enterprise"

    organization = models.OneToOneField(
        Organization, on_delete=models.CASCADE, related_name="subscription"
    )

    tier = models.CharField(
        max_length=20, choices=Tier.choices, default=Tier.FREE
    )

    # Stripe integration
    stripe_subscription_id = models.CharField(max_length=100, blank=True)
    stripe_customer_id = models.CharField(max_length=100, blank=True)

    # Usage tracking
    invites_used_this_month = models.PositiveIntegerField(default=0)
    invites_reset_at = models.DateTimeField(null=True, blank=True)

    started_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = "Organization Subscription"
        verbose_name_plural = "Organization Subscriptions"

    def __str__(self):
        return f"{self.organization.name}: {self.tier}"

    @property
    def invite_limit(self):
        """Monthly invite limit based on tier."""
        limits = {
            self.Tier.FREE: 0,
            self.Tier.PRO: 10,
            self.Tier.GROWTH: 50,
            self.Tier.ENTERPRISE: 999,
        }
        return limits.get(self.tier, 0)

    @property
    def invites_remaining(self):
        return max(0, self.invite_limit - self.invites_used_this_month)

    def can_use_talent_search(self):
        return self.tier in [self.Tier.PRO, self.Tier.GROWTH, self.Tier.ENTERPRISE]

    def can_send_invites(self):
        return self.tier in [self.Tier.GROWTH, self.Tier.ENTERPRISE]


class CoverLetter(models.Model):
    """AI-generated cover letters for job applications."""

    seeker = models.ForeignKey(
        SeekerProfile, on_delete=models.CASCADE, related_name="cover_letters"
    )
    job = models.ForeignKey(
        "Job", on_delete=models.CASCADE, related_name="cover_letters"
    )

    # AI-generated text
    generated_text = models.TextField()

    # Final text after user edits (if any)
    final_text = models.TextField(blank=True)

    # Whether it was used in an application
    used_in_application = models.BooleanField(default=False)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"Cover letter: {self.seeker.user.email} → {self.job.title}"


class AssistantSubscription(models.Model):
    """Tracks user subscription and usage for the Applicant Assistant (cover letter writer)."""

    user = models.OneToOneField(
        get_user_model(), on_delete=models.CASCADE, related_name="assistant_subscription"
    )

    # Usage tracking
    free_uses_remaining = models.PositiveIntegerField(default=3)
    total_uses = models.PositiveIntegerField(default=0)

    # Subscription status
    is_subscribed = models.BooleanField(default=False)
    stripe_subscription_id = models.CharField(max_length=100, blank=True)
    stripe_customer_id = models.CharField(max_length=100, blank=True)

    # Subscription dates
    subscribed_at = models.DateTimeField(null=True, blank=True)
    expires_at = models.DateTimeField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Assistant Subscription"
        verbose_name_plural = "Assistant Subscriptions"

    def __str__(self):
        status = "Pro" if self.is_subscribed else f"Free ({self.free_uses_remaining} left)"
        return f"{self.user.email}: {status}"

    def can_use_assistant(self) -> bool:
        """Check if user can use the assistant."""
        if self.is_subscribed:
            # Check if subscription is still active
            if self.expires_at and self.expires_at < timezone.now():
                return False
            return True
        return self.free_uses_remaining > 0

    def record_usage(self):
        """Record a usage of the assistant."""
        self.total_uses += 1
        if not self.is_subscribed and self.free_uses_remaining > 0:
            self.free_uses_remaining -= 1
        self.save()

    @classmethod
    def get_or_create_for_user(cls, user):
        """Get or create subscription record for a user."""
        subscription, _ = cls.objects.get_or_create(user=user)
        return subscription


class AssistantGeneration(models.Model):
    """Stores past generations from the Applicant Assistant."""

    class GenerationType(models.TextChoices):
        COVER_LETTER = "cover_letter", "Cover Letter"
        INTERVIEW_PREP = "interview_prep", "Interview Prep"

    user = models.ForeignKey(
        get_user_model(), on_delete=models.CASCADE, related_name="assistant_generations"
    )
    generation_type = models.CharField(
        max_length=20,
        choices=GenerationType.choices,
        default=GenerationType.COVER_LETTER,
    )

    # Input data
    job_url = models.URLField(blank=True)
    job_description = models.TextField(blank=True)
    user_highlights = models.TextField(blank=True)

    # Output
    generated_content = models.TextField()

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Assistant Generation"
        verbose_name_plural = "Assistant Generations"
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.get_generation_type_display()} - {self.user.email} - {self.created_at.strftime('%Y-%m-%d %H:%M')}"

    @property
    def preview(self):
        """Return first 100 chars of generated content."""
        return self.generated_content[:100] + "..." if len(self.generated_content) > 100 else self.generated_content


class NewsletterSubscriber(models.Model):
    """Anonymous newsletter subscribers (not yet registered users)."""

    email = models.EmailField(unique=True)
    confirmed = models.BooleanField(default=False)
    confirmed_at = models.DateTimeField(null=True, blank=True)
    unsubscribed = models.BooleanField(default=False)
    unsubscribed_at = models.DateTimeField(null=True, blank=True)
    source = models.CharField(
        max_length=50,
        default="footer",
        help_text="Where they signed up (footer, homepage, etc.)",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        status = "confirmed" if self.confirmed else "unconfirmed"
        if self.unsubscribed:
            status = "unsubscribed"
        return f"{self.email} ({status})"
