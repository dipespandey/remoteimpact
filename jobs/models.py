import uuid

from django.contrib.auth import get_user_model
from django.db import models
from django.urls import reverse
from django.utils import timezone


class Organization(models.Model):
    """Non-profit organization posting jobs"""

    class VerificationStatus(models.TextChoices):
        UNVERIFIED = "unverified", "Unverified"
        PENDING = "pending", "Pending review"
        VERIFIED = "verified", "Verified"
        REJECTED = "rejected", "Rejected"

    name = models.CharField(max_length=200)
    slug = models.SlugField(unique=True)
    description = models.TextField(
        blank=True, help_text="Brief description of the organization"
    )
    website = models.URLField(blank=True)
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

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.user} ({self.account_type})"


class Category(models.Model):
    """Job categories (e.g., Education, Healthcare, Environment)"""

    name = models.CharField(max_length=100, unique=True)
    slug = models.SlugField(unique=True)
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

    title = models.CharField(max_length=200)
    slug = models.SlugField(unique=True)
    organization = models.ForeignKey(
        Organization, on_delete=models.CASCADE, related_name="jobs"
    )
    category = models.ForeignKey(
        Category, on_delete=models.SET_NULL, null=True, related_name="jobs"
    )

    description = models.TextField(help_text="Full job description")
    requirements = models.TextField(help_text="Job requirements and qualifications")
    location = models.CharField(
        max_length=200, default="Remote", help_text="Always Remote for this board"
    )
    job_type = models.CharField(
        max_length=20, choices=JOB_TYPE_CHOICES, default="full-time"
    )

    application_url = models.URLField(help_text="URL to apply for this job")
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

    source = models.CharField(
        max_length=50, choices=Source.choices, default=Source.MANUAL
    )
    external_id = models.CharField(
        max_length=255, blank=True, null=True, help_text="ID from the upstream source"
    )
    raw_data = models.JSONField(default=dict, blank=True)

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

    title = models.CharField(max_length=200)
    slug = models.SlugField(unique=True)

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
