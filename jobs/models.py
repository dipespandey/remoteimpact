import uuid
from datetime import timedelta

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.postgres.search import SearchVectorField
from django.db import models
from django.urls import reverse
from django.utils import timezone
from pgvector.django import VectorField
from jobs.db_indexes import PostgresOnlyGinIndex, PostgresOnlyHnswIndex


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
    # The employer's own sites, derived and checked by `discover_org_sites` (never a job board or profile page)
    homepage_url = models.TextField(blank=True)
    careers_url = models.TextField(blank=True)
    homepage_source = models.CharField(max_length=20, blank=True)  # website | claim | apply_domain | guessed
    sites_checked_at = models.DateTimeField(null=True, blank=True)
    # Logo fetched from the organization's own site (OrganizationLogo), versioned for cache busting
    logo_version = models.CharField(max_length=16, blank=True)
    logo_checked_at = models.DateTimeField(null=True, blank=True)
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

    # =========================================================================
    # SOURCE TRACKING - Where did this org come from?
    # =========================================================================
    class OrganizationSource(models.TextChoices):
        SCRAPED = "scraped", "Scraped from job boards"
        SIGNUP = "signup", "Direct employer signup"
        CLAIMED = "claimed", "Scraped then claimed by user"
        MANUAL = "manual", "Manually added by staff"

    source = models.CharField(
        max_length=20,
        choices=OrganizationSource.choices,
        default=OrganizationSource.SCRAPED,
        help_text="How this organization was added to the platform",
    )
    source_detail = models.CharField(
        max_length=100,
        blank=True,
        help_text="Specific source e.g., 'climatebase', 'idealist', 'lever'",
    )

    # =========================================================================
    # IMPACT PROFILE - Third-party signals (auto-detected)
    # =========================================================================
    is_80k_recommended = models.BooleanField(
        default=False, help_text="Listed on 80,000 Hours job board"
    )
    is_givewell_top_charity = models.BooleanField(
        default=False, help_text="GiveWell top or standout charity"
    )
    is_bcorp_certified = models.BooleanField(
        default=False, help_text="Certified B Corporation"
    )
    bcorp_score = models.PositiveIntegerField(
        null=True, blank=True, help_text="B Corp overall score (80-200+)"
    )
    bcorp_profile_url = models.URLField(blank=True)

    # =========================================================================
    # IMPACT PROFILE - Self-reported org details
    # =========================================================================
    class OrgType(models.TextChoices):
        NONPROFIT = "nonprofit", "Nonprofit / NGO"
        SOCIAL_ENTERPRISE = "social_enterprise", "Social Enterprise"
        BCORP = "bcorp", "B Corporation"
        GOVERNMENT = "government", "Government / Public Sector"
        FOUNDATION = "foundation", "Foundation"
        RESEARCH = "research", "Research Institution"
        OTHER = "other", "Other"

    class TeamSize(models.TextChoices):
        TINY = "1-10", "1-10"
        SMALL = "11-50", "11-50"
        MEDIUM = "51-200", "51-200"
        LARGE = "201-500", "201-500"
        ENTERPRISE = "500+", "500+"

    class RemoteCulture(models.TextChoices):
        REMOTE_FIRST = "remote_first", "Remote-first"
        HYBRID = "hybrid", "Hybrid"
        OFFICE_FIRST = "office_first", "Office-first"

    organization_type = models.CharField(
        max_length=30, choices=OrgType.choices, blank=True
    )
    founded_year = models.PositiveIntegerField(null=True, blank=True)
    team_size = models.CharField(
        max_length=20, choices=TeamSize.choices, blank=True
    )
    remote_culture = models.CharField(
        max_length=20, choices=RemoteCulture.choices, blank=True
    )

    # Impact transparency
    has_public_impact_report = models.BooleanField(default=False)
    impact_report_url = models.URLField(blank=True)
    has_public_financials = models.BooleanField(default=False)
    financials_url = models.URLField(blank=True)

    # Impact metrics (self-reported)
    impact_metric_name = models.CharField(
        max_length=100, blank=True,
        help_text="e.g., 'Lives saved', 'Trees planted', 'Students taught'"
    )
    impact_metric_value = models.CharField(
        max_length=50, blank=True,
        help_text="e.g., '50,000+', '1.2M', '10,000/year'"
    )
    impact_statement = models.TextField(
        blank=True,
        help_text="Brief statement about your organization's impact (2-3 sentences)"
    )

    # Profile tracking
    impact_profile_completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name

    @property
    def logo_src(self) -> str:
        """Site-relative URL of the logo to show: an uploaded one first, then the one fetched from its site."""
        if self.logo:
            try:
                return self.logo.url
            except ValueError:
                pass
        if self.logo_version and self.slug:
            from django.urls import reverse
            return f"{reverse('jobs:organization_logo', args=[self.slug])}?v={self.logo_version}"
        return ""

    @property
    def impact_profile_completeness(self) -> int:
        """Calculate profile completeness percentage (0-100)."""
        fields = [
            bool(self.description),
            bool(self.website),
            bool(self.logo),
            bool(self.founded_year),
            bool(self.team_size),
            bool(self.organization_type),
            bool(self.impact_statement),
            bool(self.has_public_impact_report or self.has_public_financials),
        ]
        return int(sum(fields) / len(fields) * 100)

    @property
    def has_verified_signals(self) -> bool:
        """Has at least one third-party verification."""
        return any([
            self.is_80k_recommended,
            self.is_givewell_top_charity,
            self.is_bcorp_certified,
        ])

    @property
    def verified_signals_list(self) -> list:
        """Return list of verified signal labels."""
        signals = []
        if self.is_80k_recommended:
            signals.append("Recommended by 80,000 Hours")
        if self.is_givewell_top_charity:
            signals.append("GiveWell Top Charity")
        if self.is_bcorp_certified:
            score_str = f" (Score: {self.bcorp_score})" if self.bcorp_score else ""
            signals.append(f"Certified B Corp{score_str}")
        return signals


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
        default=False, help_text="Receive weekly job digest emails"
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
        WORKABLE = "workable", "Workable"
        RECRUITEE = "recruitee", "Recruitee"
        PERSONIO = "personio", "Personio"
        BAMBOOHR = "bamboohr", "BambooHR"

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

    # Structured fields for filtering (extracted from raw_data or AI)
    EXPERIENCE_LEVEL_CHOICES = [
        ("entry", "Entry Level"),
        ("mid", "Mid Level"),
        ("senior", "Senior"),
        ("executive", "Executive"),
        ("internship", "Internship"),
    ]
    experience_level = models.CharField(
        max_length=50,
        blank=True,
        null=True,
        choices=EXPERIENCE_LEVEL_CHOICES,
        help_text="Experience level required for this job",
    )

    EDUCATION_LEVEL_CHOICES = [
        ("high_school", "High School"),
        ("associate", "Associate"),
        ("bachelor", "Bachelor's"),
        ("master", "Master's"),
        ("phd", "PhD"),
    ]
    education_level = models.CharField(
        max_length=50,
        blank=True,
        null=True,
        choices=EDUCATION_LEVEL_CHOICES,
        help_text="Minimum education level required",
    )

    country = models.CharField(
        max_length=100,
        blank=True,
        null=True,
        help_text="Normalized country name for filtering",
    )

    application_url = models.TextField(
        blank=True,
        default="",
        help_text="Optional external URL to apply for this job",
    )  # TextField for long URLs
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
    custom_questions = models.JSONField(
        default=list,
        blank=True,
        help_text="Custom application questions for on-platform applicants",
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
    last_seen_at = models.DateTimeField(null=True, blank=True, db_index=True)
    quality_score = models.PositiveSmallIntegerField(default=0, db_index=True)
    quality_issues = models.JSONField(default=list, blank=True)

    # Hiring assistant: the role's must-haves, each with the posting text it came from.
    must_haves = models.JSONField(default=list, blank=True)
    # When the employer last changed the listing's content (shown to job seekers as "Updated").
    edited_at = models.DateTimeField(null=True, blank=True)

    # Vector search fields
    embedding = VectorField(dimensions=384, null=True, blank=True)
    embedding_content_hash = models.CharField(max_length=64, blank=True, default="")
    search_vector = SearchVectorField(null=True, blank=True)

    is_active = models.BooleanField(default=True)
    is_featured = models.BooleanField(default=False)
    # Paid placement window. "Featured" on the site means an employer paid.
    featured_until = models.DateTimeField(null=True, blank=True, db_index=True)

    is_paid = models.BooleanField(default=False)
    stripe_checkout_session_id = models.CharField(max_length=255, blank=True, default="", db_index=True)
    stripe_payment_intent = models.CharField(max_length=255, blank=True, default="", db_index=True)
    paid_at = models.DateTimeField(null=True, blank=True)

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
            PostgresOnlyGinIndex(fields=["search_vector"], name="job_search_idx"),
            PostgresOnlyHnswIndex(
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

    PUBLIC_STALE_DAYS = 90
    # Imported listings are refreshed by every feed fetch and by verify_ats_jobs, so one
    # nobody has seen for a month has almost certainly been taken down upstream.
    IMPORTED_UNSEEN_DAYS = 30

    @classmethod
    def availability_filter(cls, now=None, stale_days=None, include_quality=True):
        """Public inventory policy; source sightings indicate freshness, not an open role."""
        from datetime import timedelta

        now = now or timezone.now()
        days = cls.PUBLIC_STALE_DAYS if stale_days is None else stale_days
        cutoff = now - timedelta(days=days)
        seen_cutoff = now - timedelta(days=min(days, cls.IMPORTED_UNSEEN_DAYS))
        manual = models.Q(source=cls.Source.MANUAL)
        fresh = (manual & (models.Q(posted_at__gte=cutoff) | models.Q(last_seen_at__gte=cutoff))) | (
            ~manual & (
                models.Q(last_seen_at__gte=seen_cutoff)
                | (models.Q(last_seen_at__isnull=True) & models.Q(posted_at__gte=cutoff))
            )
        )
        available = models.Q(is_active=True) & (
            models.Q(expires_at__gte=now) | (models.Q(expires_at__isnull=True) & fresh)
        ) & ~models.Q(organization__verification_status=Organization.VerificationStatus.REJECTED)
        if include_quality:
            available &= models.Q(quality_score__gte=55) | models.Q(quality_score=0)
        return available

    def is_expired(self):
        return bool(self.expires_at and timezone.now() > self.expires_at)

    @property
    def is_sponsored(self):
        return bool(self.featured_until and self.featured_until > timezone.now())

    @classmethod
    def sponsored_filter(cls, now=None):
        return models.Q(featured_until__gt=now or timezone.now())

    @property
    def is_available(self):
        from datetime import timedelta

        now = timezone.now()
        cutoff = now - timedelta(days=self.PUBLIC_STALE_DAYS)
        if self.expires_at:
            fresh = self.expires_at >= now
        elif self.source == self.Source.MANUAL:
            fresh = self.posted_at >= cutoff or bool(self.last_seen_at and self.last_seen_at >= cutoff)
        elif self.last_seen_at:
            fresh = self.last_seen_at >= now - timedelta(days=self.IMPORTED_UNSEEN_DAYS)
        else:
            fresh = self.posted_at >= cutoff
        rejected = self.organization.verification_status == Organization.VerificationStatus.REJECTED
        return self.is_active and fresh and not rejected and (self.quality_score == 0 or self.quality_score >= 55)

    @property
    def is_fresh(self):
        """Whether the role was posted within the last seven days."""
        return self.posted_at >= timezone.now() - timedelta(days=7)

    @property
    def is_closing_soon(self):
        """Whether a live role has a deadline in the next seven days."""
        if not self.expires_at:
            return False
        now = timezone.now()
        return now <= self.expires_at <= now + timedelta(days=7)

    @property
    def is_entry_level_friendly(self):
        """
        Returns True if this job is suitable for entry-level candidates
        and career switchers. Checks experience level, years required,
        and keywords in title.
        """
        # Check explicit experience level
        if self.experience_level in ('entry', 'internship'):
            return True
        
        # Check years of experience (0-1 years = entry friendly)
        if hasattr(self, 'years_experience') and self.years_experience is not None:
            if self.years_experience <= 1:
                return True
        
        # Check title for entry-level keywords
        if self.title:
            title_lower = self.title.lower()
            entry_keywords = [
                'junior', 'entry', 'entry-level', 'trainee', 'graduate',
                'intern', 'apprentice', 'associate', 'assistant',
                'coordinator', 'no experience'
            ]
            if any(kw in title_lower for kw in entry_keywords):
                return True
        
        return False


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

    class YearsExperience(models.TextChoices):
        ENTRY = "0-2", "0-2 years"
        MID = "3-5", "3-5 years"
        SENIOR = "6-10", "6-10 years"
        LEAD = "10+", "10+ years"

    job = models.ForeignKey(Job, on_delete=models.CASCADE, related_name="applications")
    applicant = models.ForeignKey(
        get_user_model(), on_delete=models.CASCADE, related_name="applications"
    )
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.APPLIED
    )

    # Contact
    full_name = models.CharField(max_length=200, blank=True)
    email = models.EmailField(blank=True)
    phone = models.CharField(max_length=40, blank=True)
    linkedin_url = models.URLField(blank=True)
    portfolio_url = models.URLField(blank=True, help_text="Portfolio, website, or GitHub")

    # Background
    years_experience = models.CharField(
        max_length=10, choices=YearsExperience.choices, blank=True
    )
    current_location = models.CharField(max_length=200, blank=True)

    # About this role
    available_from = models.DateField(null=True, blank=True, help_text="Earliest start date")
    willing_to_relocate = models.BooleanField(default=False)
    salary_expectation = models.CharField(max_length=120, blank=True)
    why_great_fit = models.TextField(blank=True, help_text="Short pitch for this role")
    custom_question_answers = models.JSONField(
        default=list,
        blank=True,
        help_text="Answers to the job poster's custom application questions",
    )

    # Existing
    cover_letter = models.TextField(blank=True, help_text="Optional cover note")
    resume = models.FileField(
        upload_to="applications/resumes/%Y/%m/",
        blank=True,
        null=True,
        help_text="PDF or DOC resume (max 5 MB)",
    )

    # Hiring assistant: how the application compares with the role's must-haves, each "met"
    # backed by a quote from the applicant's own words. Advisory only; people decide.
    screening = models.JSONField(default=dict, blank=True)
    resume_text = models.TextField(blank=True)

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


class EmployerFunnelEvent(models.Model):
    """Minimal server-side conversion events for the employer journey."""

    class Event(models.TextChoices):
        POST_JOB_VIEWED = "post_job_viewed", "Post job viewed"
        JOB_DRAFT_CREATED = "job_draft_created", "Job draft created"
        CHECKOUT_STARTED = "checkout_started", "Checkout started"
        PAYMENT_COMPLETED = "payment_completed", "Payment completed"
        PAYMENT_CANCELLED = "payment_cancelled", "Payment cancelled"
        CHECKOUT_FAILED = "checkout_failed", "Checkout failed"

        SHORTLIST_VIEWED = "shortlist_viewed", "Candidate shortlist viewed"
        CANDIDATE_REVIEWED = "candidate_reviewed", "Candidate stage updated"

    user = models.ForeignKey(
        User, null=True, blank=True, on_delete=models.SET_NULL, related_name="employer_funnel_events"
    )
    job = models.ForeignKey(
        "Job", null=True, blank=True, on_delete=models.SET_NULL, related_name="funnel_events"
    )
    event = models.CharField(max_length=32, choices=Event.choices)
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [models.Index(fields=["event", "-created_at"]), models.Index(fields=["user", "-created_at"])]


class ConsumerFunnelEvent(models.Model):
    """Server-side conversion events for Remote Impact Pro and Job Scout."""

    class Event(models.TextChoices):
        ACCOUNT_CREATED = "account_created", "Account created"
        SCOUT_LANDED = "scout_landed", "Scout page viewed"
        SCOUT_JOB_VIEWED = "scout_job_viewed", "Scout job viewed"
        SCOUT_PREVIEWED = "scout_previewed", "Scout previewed"
        SCOUT_SIGNUP_STARTED = "scout_signup_started", "Scout signup started"
        SCOUT_CREATED = "scout_created", "Scout created"
        INITIAL_EMAIL_SENT = "initial_email_sent", "Initial email sent"
        INITIAL_EMAIL_FAILED = "initial_email_failed", "Initial email failed"
        INITIAL_MATCHES_EMPTY = "initial_matches_empty", "Initial Scout had no matches"
        UPGRADE_CLICKED = "upgrade_clicked", "Upgrade clicked"
        CHECKOUT_STARTED = "checkout_started", "Checkout started"
        CHECKOUT_CREATED = "checkout_created", "Stripe checkout created"
        CHECKOUT_EXPIRED = "checkout_expired", "Stripe checkout expired"
        CHECKOUT_FAILED = "checkout_failed", "Checkout failed"
        PAYMENT_COMPLETED = "payment_completed", "Payment completed"
        SUBSCRIPTION_CANCELLED = "subscription_cancelled", "Subscription cancelled"
        TRIAL_REMINDER_SENT = "trial_reminder_sent", "Pro trial reminder sent"
        CHECKOUT_RECOVERY_SENT = "checkout_recovery_sent", "Checkout follow-up sent"
        WINBACK_SENT = "winback_sent", "Scout win-back email sent"
        MATCH_RELEVANT = "match_relevant", "Match marked relevant"
        MATCH_NOT_RELEVANT = "match_not_relevant", "Match marked not relevant"
        SHORTLIST_VIEWED = "shortlist_viewed", "Private shortlist viewed"
        INTEREST_SAVED = "interest_saved", "Job interest saved"
        EMPLOYER_OPT_IN = "employer_opt_in", "Candidate opted in to an employer"
        COLLECTION_CREATED = "collection_created", "Job collection shared"
        COLLECTION_VIEWED = "collection_viewed", "Shared job collection viewed"

    user = models.ForeignKey(
        User,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="consumer_funnel_events",
    )
    alert = models.ForeignKey(
        "JobAlert",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="funnel_events",
    )
    event = models.CharField(max_length=32, choices=Event.choices)
    metadata = models.JSONField(default=dict, blank=True)
    dedupe_key = models.CharField(max_length=160, null=True, blank=True, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(fields=["event", "-created_at"]),
            models.Index(fields=["user", "-created_at"]),
        ]


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

    # Work style preference (legacy single choice)
    work_style = models.CharField(
        max_length=20, choices=WorkStyle.choices, blank=True
    )

    # Work styles - multiple selections (new)
    work_styles = models.JSONField(
        default=list, help_text="List of work style values, e.g. ['builder', 'strategist']"
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

    # LinkedIn profile URL
    linkedin_url = models.URLField(
        max_length=200, blank=True,
        help_text="LinkedIn profile URL"
    )

    # Resume (reusable across applications)
    resume = models.FileField(
        upload_to="seekers/resumes/%Y/%m/",
        blank=True,
        null=True,
        help_text="PDF or DOC resume (max 5 MB)",
    )

    # Country of work eligibility
    country_eligibility = models.CharField(
        max_length=100, blank=True,
        help_text="Country where user is eligible to work"
    )

    # -------------------------------------------------------------------------
    # Story & Profile
    # -------------------------------------------------------------------------

    # Legacy field - kept for backward compatibility
    impact_statement = models.TextField(
        max_length=500, blank=True,
        help_text="Legacy: 2-3 sentences on what draws them to impact work"
    )

    # Professional headline (short tagline)
    headline = models.CharField(
        max_length=150, blank=True,
        help_text="Short professional headline, e.g. 'Climate Policy Expert | Former UN Advisor'"
    )

    # Bio (longer description, optional)
    bio = models.TextField(
        blank=True,
        help_text="Professional bio (optional)"
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

    # Read from the uploaded CV: plain text plus grounded facts (each with a quote from the CV).
    resume_text = models.TextField(blank=True)
    cv_facts = models.JSONField(default=dict, blank=True)
    cv_read_at = models.DateTimeField(null=True, blank=True)
    cv_source = models.CharField(max_length=255, blank=True, help_text="File the CV facts were read from")
    embedding = VectorField(dimensions=384, null=True, blank=True)
    embedding_content_hash = models.CharField(max_length=64, blank=True, default="")
    search_vector = SearchVectorField(null=True, blank=True)

    # -------------------------------------------------------------------------
    # Visibility & Search
    # -------------------------------------------------------------------------

    visibility = models.CharField(
        max_length=20, choices=Visibility.choices, default=Visibility.MATCHING
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
            PostgresOnlyGinIndex(fields=["search_vector"], name="seeker_search_idx"),
            PostgresOnlyHnswIndex(
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
        """Calculate and update profile completeness percentage.

        Only counts required fields toward completeness:
        - Impact areas (required)
        - Work style (required)
        - Experience level (required)
        - Skills (required, at least 3)
        - Headline (required)

        Optional fields (bio, assessment, salary, remote preference, etc.)
        do not affect the completeness percentage.
        """
        score = 0
        max_score = 100

        # Impact areas (25 points) - required
        if self.impact_areas.exists():
            score += 25

        # Work style (20 points) - required, check both new and legacy fields
        if self.work_styles or self.work_style:
            score += 20

        # Experience level (20 points) - required
        if self.experience_level:
            score += 20

        # Skills (25 points) - required, need at least 3
        if len(self.skills) >= 3:
            score += 25

        # Headline (10 points) - required
        if self.headline:
            score += 10

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
    stripe_checkout_session_id = models.CharField(max_length=255, blank=True)
    stripe_checkout_expires_at = models.DateTimeField(null=True, blank=True)
    stripe_event_created = models.PositiveBigIntegerField(default=0)
    stripe_status = models.CharField(max_length=24, blank=True)

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
    """Tracks the bundled Remote Impact Pro subscription and assistant usage."""

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
    stripe_checkout_session_id = models.CharField(max_length=255, blank=True)
    stripe_checkout_expires_at = models.DateTimeField(null=True, blank=True)

    # Subscription dates
    subscribed_at = models.DateTimeField(null=True, blank=True)
    expires_at = models.DateTimeField(null=True, blank=True)
    free_job_scout_used_at = models.DateTimeField(null=True, blank=True)

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

    def is_pro_active(self) -> bool:
        """Return whether the bundled Pro entitlement is currently active."""
        if not self.is_subscribed:
            return False
        return not self.expires_at or self.expires_at >= timezone.now()

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


class DripEmailLog(models.Model):
    """Tracks which drip emails have been sent to each user."""

    DRIP_TYPES = [
        ('day2_profile', 'Day 2 – Complete Profile'),
        ('day3_scout', 'Day 3 – AI Job Scout'),
        ('day5_jobs', 'Day 5 – Top Jobs'),
        ('day10_assistant', 'Day 10 – AI Assistant'),
    ]

    user = models.ForeignKey(
        get_user_model(), on_delete=models.CASCADE, related_name='drip_emails'
    )
    drip_type = models.CharField(max_length=30, choices=DRIP_TYPES)
    sent_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('user', 'drip_type')
        ordering = ['-sent_at']

    def __str__(self):
        return f'{self.user.email} – {self.drip_type} ({self.sent_at})'


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


# =============================================================================
# REFERRAL SYSTEM
# =============================================================================


class Referral(models.Model):
    """Tracks referral codes and who referred whom."""

    referrer = models.OneToOneField(
        User, on_delete=models.CASCADE, related_name="referral_profile"
    )
    code = models.CharField(max_length=20, unique=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.referrer.email} ({self.code})"

    @classmethod
    def get_or_create_for_user(cls, user):
        """Get or create a referral code for a user."""
        try:
            return cls.objects.get(referrer=user)
        except cls.DoesNotExist:
            import secrets
            code = secrets.token_urlsafe(8)[:10]
            return cls.objects.create(referrer=user, code=code)

    @property
    def referral_count(self):
        return ReferralSignup.objects.filter(referrer=self).count()


class ReferralSignup(models.Model):
    """Tracks each signup that came through a referral link."""

    referrer = models.ForeignKey(
        Referral, on_delete=models.CASCADE, related_name="signups"
    )
    referred_user = models.OneToOneField(
        User, on_delete=models.CASCADE, related_name="referred_by"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.referred_user.email} referred by {self.referrer.referrer.email}"


# =============================================================================
# JOB ALERTS
# =============================================================================


class JobAlert(models.Model):
    """AI-interpreted job scout with structured matching preferences."""

    class Frequency(models.TextChoices):
        ONCE = "once", "One-time"
        INSTANT = "instant", "As soon as possible"
        DAILY = "daily", "Daily"
        WEEKLY = "weekly", "Weekly"

    user = models.ForeignKey(
        User, on_delete=models.CASCADE, related_name="job_alerts"
    )
    name = models.CharField(max_length=100, blank=True, help_text="Optional label for this alert")
    categories = models.ManyToManyField(Category, blank=True, related_name="alerts")
    keywords = models.TextField(blank=True, help_text="Comma-separated keywords to match")
    intent_text = models.TextField(
        blank=True,
        help_text="The user's natural-language description of the work they want",
    )
    parsed_preferences = models.JSONField(
        default=dict,
        blank=True,
        help_text="Structured criteria interpreted from intent_text",
    )
    frequency = models.CharField(
        max_length=10, choices=Frequency.choices, default=Frequency.ONCE
    )
    is_active = models.BooleanField(default=True)
    last_sent_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        label = self.name or f"Alert #{self.pk}"
        return f"{self.user.email} - {label} ({self.frequency})"

    def get_matching_jobs(self, since=None):
        """Find jobs matching this alert's criteria since a given date."""
        from jobs.services.job_scout_service import JobScoutService

        return JobScoutService.get_matches(self, since=since)


class JobAlertDelivery(models.Model):
    """A job already delivered for a scout, used for reliable deduplication."""

    class Feedback(models.TextChoices):
        RELEVANT = "relevant", "Relevant"
        NOT_RELEVANT = "not_relevant", "Not relevant"

    alert = models.ForeignKey(
        JobAlert, on_delete=models.CASCADE, related_name="deliveries"
    )
    job = models.ForeignKey(
        Job, on_delete=models.CASCADE, related_name="alert_deliveries"
    )
    sent_at = models.DateTimeField(auto_now_add=True)
    match_score = models.PositiveSmallIntegerField(default=0)
    match_reasons = models.JSONField(default=list, blank=True)
    feedback = models.CharField(
        max_length=20, choices=Feedback.choices, blank=True
    )
    feedback_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-sent_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["alert", "job"], name="unique_job_alert_delivery"
            )
        ]
        indexes = [
            models.Index(
                fields=["alert", "-sent_at"],
                name="jobs_jobale_alert_i_9e2095_idx",
            )
        ]

    def __str__(self):
        return f"{self.alert} -> {self.job}"


class ProfileView(models.Model):
    seeker = models.ForeignKey(
        "SeekerProfile", on_delete=models.CASCADE, related_name="profile_views"
    )
    viewer = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="viewed_profiles"
    )
    viewed_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-viewed_at"]
        indexes = [
            models.Index(fields=["seeker", "-viewed_at"], name="jobs_profileview_seeker_viewed"),
        ]

    def __str__(self):
        return f"{self.viewer} viewed {self.seeker} at {self.viewed_at}"


from .shortlist_models import JobInterest, SharedShortlist  # noqa: E402,F401


class JobDailyStat(models.Model):
    """Per-job engagement by day, reported back to employers.

    Views come from a client-side beacon (bots rarely run it); apply clicks from
    the outbound redirect.
    """

    job = models.ForeignKey(Job, on_delete=models.CASCADE, related_name="daily_stats")
    date = models.DateField(db_index=True)
    views = models.PositiveIntegerField(default=0)
    apply_clicks = models.PositiveIntegerField(default=0)
    scout_deliveries = models.PositiveIntegerField(default=0)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["job", "date"], name="unique_job_daily_stat")]
        indexes = [models.Index(fields=["job", "-date"])]

    def __str__(self):
        return f"{self.job_id} {self.date}: {self.views} views, {self.apply_clicks} clicks"


class JobSlugRedirect(models.Model):
    """Old job URLs that must keep resolving after a slug changes."""

    old_slug = models.SlugField(max_length=255, unique=True)
    job = models.ForeignKey(Job, on_delete=models.CASCADE, related_name="slug_redirects")
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.old_slug} -> {self.job_id}"


class JobFeaturePurchase(models.Model):
    """A paid 30-day featured placement for one job (also a revenue record)."""

    job = models.ForeignKey(Job, on_delete=models.CASCADE, related_name="feature_purchases")
    stripe_checkout_session_id = models.CharField(max_length=255, unique=True)
    amount_cents = models.PositiveIntegerField(default=0)
    purchaser_email = models.EmailField(blank=True)
    featured_until = models.DateTimeField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"Feature {self.job_id} until {self.featured_until:%Y-%m-%d}"


class EmployerOutreach(models.Model):
    """Staff-tracked sales outreach to an organization (one row per org)."""

    class Status(models.TextChoices):
        NEW = "new", "Not contacted"
        CONTACTED = "contacted", "Contacted"
        REPLIED = "replied", "Replied"
        WON = "won", "Paid"
        NOT_INTERESTED = "not_interested", "Not interested"

    organization = models.OneToOneField(Organization, on_delete=models.CASCADE, related_name="outreach")
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.NEW, db_index=True)
    contact = models.CharField(max_length=254, blank=True, help_text="Email or name of the person contacted")
    notes = models.TextField(blank=True)
    contacted_at = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.organization.name}: {self.get_status_display()}"


class OrganizationClaim(models.Model):
    """A person who verified an email to follow an organization's stats on Remote Impact."""

    organization = models.ForeignKey(Organization, on_delete=models.CASCADE, related_name="claims")
    email = models.EmailField()
    name = models.CharField(max_length=120, blank=True)
    domain_match = models.BooleanField(default=False, help_text="Email domain matches the organization's website")
    monthly_report = models.BooleanField(default=True)
    verified_at = models.DateTimeField(null=True, blank=True)
    last_report_sent_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["organization", "email"], name="unique_org_claim_email")]

    def __str__(self):
        return f"{self.email} -> {self.organization.name}"


class CountryDailyActivity(models.Model):
    """Visitor activity per country per day, from Cloudflare's country header.

    Country only, never anything finer; shown publicly only in aggregate.
    """

    date = models.DateField(db_index=True)
    country = models.CharField(max_length=2)
    views = models.PositiveIntegerField(default=0)
    apply_clicks = models.PositiveIntegerField(default=0)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["date", "country"], name="unique_country_daily_activity")]


class OrganizationLogo(models.Model):
    """A small square PNG of the organization's icon, re-encoded from its own website."""

    organization = models.OneToOneField(Organization, on_delete=models.CASCADE, primary_key=True, related_name="fetched_logo")
    png = models.BinaryField()
    source_url = models.TextField(blank=True)
    fetched_at = models.DateTimeField(auto_now=True)


class OutreachSend(models.Model):
    """One campaign email sent to one address, so no one gets the same campaign twice."""

    campaign = models.CharField(max_length=50)
    email = models.EmailField()
    organization = models.ForeignKey("Organization", null=True, blank=True, on_delete=models.SET_NULL)
    sent_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["campaign", "email"], name="outreach_once_per_campaign")]


class SourcingSuggestion(models.Model):
    """A candidate the hiring assistant put in front of an employer for a role, with a drafted invite."""

    job = models.ForeignKey(Job, on_delete=models.CASCADE, related_name="sourcing_suggestions")
    seeker = models.ForeignKey("SeekerProfile", on_delete=models.CASCADE, related_name="sourcing_suggestions")
    reasons = models.JSONField(default=list, blank=True)
    draft_message = models.TextField(blank=True)
    notified_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["job", "seeker"], name="one_suggestion_per_job_seeker")]


class RankingEvent(models.Model):
    """What a visitor was shown and what they did with it, so ranking can be measured on real outcomes.

    One "impression" row per rendered results list (the ordered job ids); "click" rows carry the same
    impression key and the position clicked; "apply" and "save" rows link by visitor and job. No personal data: a hashed visitor key, and the
    user id only for signed-in people. Rows older than 120 days are deleted.
    """

    class Kind(models.TextChoices):
        IMPRESSION = "impression", "Impression"
        CLICK = "click", "Click"
        APPLY = "apply", "Apply"
        SAVE = "save", "Save"

    kind = models.CharField(max_length=12, choices=Kind.choices)
    surface = models.CharField(max_length=24, blank=True)
    visitor = models.CharField(max_length=20, db_index=True)
    user_id = models.IntegerField(null=True, blank=True)
    query = models.CharField(max_length=200, blank=True)
    filters = models.JSONField(default=dict, blank=True)
    job_ids = models.JSONField(default=list, blank=True)
    job_id = models.IntegerField(null=True, blank=True)
    # Random key the browser creates for each rendered results list (works on cached pages too).
    impression_key = models.CharField(max_length=32, blank=True, db_index=True)
    position = models.PositiveSmallIntegerField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        indexes = [models.Index(fields=["kind", "created_at"])]


class ApiKey(models.Model):
    """Access to the Jobs API and MCP server. Only a hash of the key is stored; the key is shown once."""

    class Tier(models.TextChoices):
        FREE = "free", "Free (non-commercial, attribution)"
        COMMERCIAL = "commercial", "Commercial"

    name = models.CharField(max_length=120)
    email = models.EmailField()
    organization = models.CharField(max_length=160, blank=True)
    use_case = models.TextField(blank=True)
    tier = models.CharField(max_length=12, choices=Tier.choices, default=Tier.FREE)
    key_hash = models.CharField(max_length=64, unique=True)
    prefix = models.CharField(max_length=12, help_text="First characters of the key, for display")
    is_active = models.BooleanField(default=True)
    requests_today = models.PositiveIntegerField(default=0)
    requests_date = models.DateField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    last_used_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.prefix}… ({self.get_tier_display()}) {self.email}"

    @staticmethod
    def hash_key(raw: str) -> str:
        import hashlib

        return hashlib.sha256(raw.encode()).hexdigest()

    @classmethod
    def issue(cls, *, name, email, tier=Tier.FREE, organization="", use_case="", is_active=True):
        """Create a key; returns (raw_key, ApiKey). The raw key is not recoverable later."""
        import secrets

        raw = "ri_" + secrets.token_urlsafe(30)
        obj = cls.objects.create(name=name, email=email, tier=tier, organization=organization, use_case=use_case,
                                 is_active=is_active, key_hash=cls.hash_key(raw), prefix=raw[:10])
        return raw, obj


class ApiRateBucket(models.Model):
    """Atomic shared rate counters; scopes are hashed and expired buckets are pruned hourly."""

    key = models.CharField(max_length=64, primary_key=True)
    count = models.PositiveIntegerField(default=0)
    expires_at = models.DateTimeField(db_index=True)


class ApiUsage(models.Model):
    """Daily call counts for the Jobs API and MCP server, by surface, endpoint or tool, tier and client."""

    date = models.DateField(db_index=True)
    surface = models.CharField(max_length=8)  # rest | mcp
    endpoint = models.CharField(max_length=60)  # jobs, job, areas, ... or the MCP tool / method
    tier = models.CharField(max_length=12)
    client = models.CharField(max_length=40, blank=True)  # coarse user-agent family
    key_prefix = models.CharField(max_length=12, blank=True)
    status = models.PositiveSmallIntegerField(default=200)
    count = models.PositiveIntegerField(default=0)

    class Meta:
        constraints = [models.UniqueConstraint(
            fields=["date", "surface", "endpoint", "tier", "client", "key_prefix", "status"], name="api_usage_daily")]
