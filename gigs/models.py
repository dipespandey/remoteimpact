from django.contrib.auth import get_user_model
from django.core.validators import MinValueValidator
from django.db import models
from django.urls import reverse
from django.utils import timezone

from jobs.models import Organization, UserProfile
from jobs.utils import unique_slug

User = get_user_model()


class GigCategory(models.Model):
    """Structured gig categories (remote + field)."""

    slug = models.SlugField(unique=True)
    name = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    is_field = models.BooleanField(
        default=False, help_text="True for in-country / field categories"
    )
    rubric_templates = models.JSONField(
        default=list, blank=True, help_text="List of rubric template dicts"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class Gig(models.Model):
    """Short assignments/contract gigs."""

    class RemotePolicy(models.TextChoices):
        ANYWHERE = "anywhere", "Remote anywhere"
        TIMEZONE = "timezone", "Timezone restricted"
        COUNTRY = "country", "Country restricted"

    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        PENDING = "pending", "Pending review"
        LIVE = "live", "Live"
        PAUSED = "paused", "Paused"
        CLOSED = "closed", "Closed"

    organization = models.ForeignKey(
        Organization, on_delete=models.CASCADE, related_name="gigs"
    )
    category = models.ForeignKey(
        GigCategory, on_delete=models.SET_NULL, null=True, related_name="gigs"
    )

    title = models.CharField(max_length=200)
    slug = models.SlugField(unique=True)

    remote_policy = models.CharField(
        max_length=20, choices=RemotePolicy.choices, default=RemotePolicy.ANYWHERE
    )
    eligible_countries = models.JSONField(
        default=list, blank=True, help_text="List of ISO country codes"
    )
    timezone_overlap = models.CharField(max_length=200, blank=True)

    budget_fixed_cents = models.PositiveIntegerField(
        validators=[MinValueValidator(5000)], help_text="Fixed fee in cents (>= $50)"
    )
    currency = models.CharField(max_length=3, default="USD")

    trial_fee_cents = models.PositiveIntegerField(
        validators=[MinValueValidator(5000)], help_text="Trial fee in cents (>= $50)"
    )
    trial_hours_cap = models.DecimalField(
        max_digits=5, decimal_places=2, validators=[MinValueValidator(0.25)]
    )
    trial_due_days = models.PositiveIntegerField(default=3)

    deliverables = models.JSONField(default=list, blank=True)
    definition_of_done = models.TextField()

    brief_redacted = models.TextField()
    brief_full = models.TextField()
    nda_required = models.BooleanField(
        default=False, help_text="Require NDA before full brief"
    )

    requires_field_verification = models.BooleanField(
        default=False, help_text="True for field gigs that need extra verification"
    )

    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.DRAFT
    )
    is_featured = models.BooleanField(default=False)
    published_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-published_at", "-created_at"]
        indexes = [
            models.Index(fields=["status", "-published_at"]),
            models.Index(fields=["remote_policy", "status"]),
        ]

    def __str__(self):
        return f"{self.title} ({self.organization.name})"

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = unique_slug(Gig, self.title)
        super().save(*args, **kwargs)

    def get_absolute_url(self):
        return reverse("gigs:gig_detail", kwargs={"slug": self.slug})

    def visible_to_seeker(self, profile: UserProfile | None):
        if self.remote_policy != Gig.RemotePolicy.COUNTRY:
            return True
        if not profile or not profile.country:
            return False
        return profile.country.upper() in [c.upper() for c in self.eligible_countries]


class RubricCriterion(models.Model):
    """Per-gig rubric items."""

    gig = models.ForeignKey(Gig, on_delete=models.CASCADE, related_name="rubric")
    label = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    weight = models.PositiveIntegerField(default=1)
    max_score = models.PositiveIntegerField(default=5)

    class Meta:
        ordering = ["id"]

    def __str__(self):
        return f"{self.label} ({self.gig.title})"


class PortfolioItem(models.Model):
    """Proof of past work for seekers."""

    class Visibility(models.TextChoices):
        PUBLIC = "public", "Public"
        PRIVATE = "private", "Private"
        APPLICATION_ONLY = "application_only", "Application only"

    seeker = models.ForeignKey(
        User, on_delete=models.CASCADE, related_name="portfolio_items"
    )
    title = models.CharField(max_length=200)
    summary = models.TextField(blank=True)
    links = models.JSONField(default=list, blank=True)
    file = models.FileField(upload_to="portfolio/files/", blank=True, null=True)
    tags = models.JSONField(default=list, blank=True)
    visibility = models.CharField(
        max_length=20, choices=Visibility.choices, default=Visibility.PUBLIC
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.title} ({self.seeker.email})"


class GigApplication(models.Model):
    """Applications to gigs with portfolio selection."""

    class Status(models.TextChoices):
        SUBMITTED = "submitted", "Submitted"
        SHORTLISTED = "shortlisted", "Shortlisted"
        TRIAL_OFFERED = "trial_offered", "Trial offered"
        TRIAL_FUNDED = "trial_funded", "Trial funded"
        TRIAL_SUBMITTED = "trial_submitted", "Trial submitted"
        ACCEPTED = "accepted", "Accepted"
        REJECTED = "rejected", "Rejected"
        WITHDRAWN = "withdrawn", "Withdrawn"

    gig = models.ForeignKey(Gig, on_delete=models.CASCADE, related_name="applications")
    seeker = models.ForeignKey(
        User, on_delete=models.CASCADE, related_name="gig_applications"
    )
    motivation = models.TextField()
    selected_portfolio_items = models.ManyToManyField(
        PortfolioItem, related_name="applications", blank=True
    )
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.SUBMITTED
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ["gig", "seeker"]
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.seeker.email} -> {self.gig.title}"

    @property
    def is_owner(self):
        return self.gig.organization.members.filter(id=self.seeker_id).exists()


class Trial(models.Model):
    """Mandatory paid trial metadata."""

    class FundingStatus(models.TextChoices):
        NOT_FUNDED = "not_funded", "Not funded"
        FUNDED = "funded", "Funded"
        RELEASED = "released", "Released"
        REFUNDED = "refunded", "Refunded"

    application = models.OneToOneField(
        GigApplication, on_delete=models.CASCADE, related_name="trial"
    )
    fee_cents = models.PositiveIntegerField(validators=[MinValueValidator(5000)])
    currency = models.CharField(max_length=3, default="USD")
    due_at = models.DateTimeField(null=True, blank=True)
    funding_status = models.CharField(
        max_length=20,
        choices=FundingStatus.choices,
        default=FundingStatus.NOT_FUNDED,
    )
    payment_reference = models.CharField(
        max_length=255, blank=True, help_text="Off-platform payment receipt/reference"
    )
    payment_intent_id = models.CharField(
        max_length=255,
        blank=True,
        null=True,
        help_text="Stripe payment intent for future integration",
    )
    released_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"Trial for {self.application}"


class Submission(models.Model):
    """Trial submissions."""

    trial = models.OneToOneField(
        Trial, on_delete=models.CASCADE, related_name="submission", null=True, blank=True
    )
    application = models.ForeignKey(
        GigApplication, on_delete=models.CASCADE, related_name="submissions"
    )
    artifact_links = models.JSONField(default=list, blank=True)
    artifact_files = models.FileField(
        upload_to="gigs/submissions/", blank=True, null=True
    )
    notes = models.TextField(blank=True)
    submitted_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Submission for {self.application}"


class Review(models.Model):
    """Rubric scoring + decision."""

    class Decision(models.TextChoices):
        PASS = "pass", "Pass"
        FAIL = "fail", "Fail"

    application = models.ForeignKey(
        GigApplication, on_delete=models.CASCADE, related_name="reviews"
    )
    reviewer = models.ForeignKey(
        User, on_delete=models.CASCADE, related_name="gig_reviews"
    )
    scores = models.JSONField(default=dict, blank=True)
    overall_comment = models.TextField(blank=True)
    decision = models.CharField(max_length=10, choices=Decision.choices)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"Review for {self.application}"


class FieldVerification(models.Model):
    """Extra verification for field gigs."""

    class Level(models.TextChoices):
        BASIC = "basic", "Basic"
        ENHANCED = "enhanced", "Enhanced"

    seeker = models.ForeignKey(
        User, on_delete=models.CASCADE, related_name="field_verifications"
    )
    country = models.CharField(max_length=2)
    phone_verified = models.BooleanField(default=False)
    id_doc_verified = models.BooleanField(default=False)
    verification_level = models.CharField(
        max_length=20, choices=Level.choices, default=Level.BASIC
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.seeker.email} {self.country} {self.verification_level}"


class FieldEvidence(models.Model):
    """Evidence for in-country gigs."""

    submission = models.OneToOneField(
        Submission, on_delete=models.CASCADE, related_name="field_evidence"
    )
    geo_photos = models.FileField(
        upload_to="gigs/field_evidence/photos/", blank=True, null=True
    )
    receipts = models.FileField(
        upload_to="gigs/field_evidence/receipts/", blank=True, null=True
    )
    call_logs = models.FileField(
        upload_to="gigs/field_evidence/call_logs/", blank=True, null=True
    )
    witness_contact = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Evidence for {self.submission}"


class GigInterest(models.Model):
    """Store interest submissions for the coming soon page."""

    email = models.EmailField()
    message = models.TextField(blank=True, help_text="Optional message from the user")
    submitted_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-submitted_at"]

    def __str__(self):
        return f"{self.email} - {self.submitted_at.strftime('%Y-%m-%d')}"
