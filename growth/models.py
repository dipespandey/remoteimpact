"""Internal operations only. Nothing in this app sends messages or charges buyers."""
import uuid

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator, RegexValidator
from django.db import models


currency_validator = RegexValidator(r"^[a-z]{3}$", "Use a lowercase ISO currency code.")


class RevenueRun(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    account_id = models.CharField(max_length=100)
    livemode = models.BooleanField()
    since = models.DateTimeField()
    until = models.DateTimeField()
    complete = models.BooleanField(default=False)
    summary = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]


class CashMovement(models.Model):
    """Processor balance facts, not funnel events or accounting revenue recognition."""
    account_id = models.CharField(max_length=100)
    livemode = models.BooleanField()
    external_id = models.CharField(max_length=100)
    source_id = models.CharField(max_length=100, blank=True)
    payment_intent_id = models.CharField(max_length=100, blank=True)
    kind = models.CharField(max_length=80)
    currency = models.CharField(max_length=3, validators=[currency_validator])
    amount_minor = models.BigIntegerField()
    fee_minor = models.BigIntegerField()
    net_minor = models.BigIntegerField()
    presentment_currency = models.CharField(max_length=3, blank=True, validators=[currency_validator])
    presentment_amount_minor = models.BigIntegerField(null=True, blank=True,
        help_text="Original charge/refund currency amount, separate from settlement balance and fees.")
    occurred_at = models.DateTimeField(db_index=True)
    available_at = models.DateTimeField(null=True, blank=True)
    scope_verified = models.BooleanField(default=False)
    scope_basis = models.CharField(max_length=120, blank=True)
    product = models.CharField(max_length=40, blank=True)
    last_run = models.ForeignKey(RevenueRun, on_delete=models.PROTECT)

    class Meta:
        constraints = [models.UniqueConstraint(
            fields=["account_id", "livemode", "external_id"], name="growth_cash_processor_identity")]


class OperatingCost(models.Model):
    """One recorded expense or labor valuation; unknown costs stay outside totals."""
    reference = models.CharField(max_length=150, unique=True)
    description = models.CharField(max_length=250)
    date = models.DateField()
    currency = models.CharField(max_length=3, default="usd", validators=[currency_validator])
    amount_minor = models.PositiveBigIntegerField()
    is_owner_labor = models.BooleanField(default=False)
    evidence_note = models.TextField(help_text="Receipt reference or minutes × chosen hourly rate; no credentials.")


class EmployerPilot(models.Model):
    class Stage(models.TextChoices):
        INQUIRY = "inquiry", "Inquiry"
        QUALIFIED = "qualified", "Qualified"
        QUOTED = "quoted", "Quoted"
        PAID = "paid", "Payment verified"
        APPROVED = "approved", "Buyer approved deliverables"
        DELIVERED = "delivered", "Delivered"
        REPORTED = "reported", "Report shared"
        CLOSED = "closed", "Closed"
        CANCELED = "canceled", "Canceled / refunded"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey("jobs.Organization", on_delete=models.PROTECT)
    stage = models.CharField(max_length=20, choices=Stage.choices, default=Stage.INQUIRY)
    independent_buyer = models.BooleanField(default=False, help_text="Staff verified unrelated, non-test buyer.")
    scope = models.TextField(blank=True, help_text="Exact deliverables and channels actually available. No hiring guarantees.")
    terms = models.TextField(blank=True, help_text="Delivery deadline, approval process, cancellation/refund terms.")
    quote_minor = models.PositiveIntegerField(null=True, blank=True, validators=[MinValueValidator(1)])
    currency = models.CharField(max_length=3, default="usd", validators=[currency_validator])
    payment = models.OneToOneField(CashMovement, null=True, blank=True, on_delete=models.PROTECT)
    payment_intent_reference = models.CharField(max_length=100, blank=True,
        help_text="Processor payment-intent ID verified against this buyer's accepted quote. Not an email or funnel ID.")
    buyer_approval = models.TextField(blank=True, help_text="Approval reference and approved scope; no private email body.")
    delivery_evidence = models.TextField(blank=True, help_text="Delivered URLs, dates and available-channel evidence.")
    report_evidence = models.TextField(blank=True, help_text="Report reference and evidence it was shared.")
    fulfillment_minutes = models.PositiveIntegerField(default=0)
    feedback = models.TextField(blank=True)
    next_action_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["payment_intent_reference"],
            condition=~models.Q(payment_intent_reference=""), name="growth_pilot_unique_payment_intent")]

    def __str__(self):
        return f"{self.organization}: {self.get_stage_display()}"


class PilotActivity(models.Model):
    pilot = models.ForeignKey(EmployerPilot, on_delete=models.CASCADE, related_name="activities")
    stage = models.CharField(max_length=20, choices=EmployerPilot.Stage.choices)
    note = models.TextField()
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL)
    created_at = models.DateTimeField(auto_now_add=True)


class EditorialOpportunity(models.Model):
    class Audience(models.TextChoices):
        EMPLOYER = "employer", "Employer"
        SEEKER = "seeker", "Job seeker"
        COMMUNITY = "community", "Community"

    key = models.SlugField(max_length=120, unique=True)
    title = models.CharField(max_length=300)
    audience = models.CharField(max_length=20, choices=Audience.choices)
    intent = models.TextField()
    destination_path = models.CharField(max_length=300, help_text="Existing internal destination; not a speculative new URL.")
    evidence = models.JSONField(default=list, help_text="List of source URL, captured_at and factual note objects.")
    coverage_notes = models.TextField(blank=True)
    decision = models.CharField(max_length=20, choices=[("review", "Needs review"), ("accepted", "Accepted"), ("deferred", "Deferred")], default="review")
    review_note = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.title


class ArticleRevision(models.Model):
    class State(models.TextChoices):
        DRAFT = "draft", "Draft"
        APPROVED = "approved", "Approved and scheduled"
        PUBLISHED = "published", "Published"
        REJECTED = "rejected", "Rejected"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    opportunity = models.ForeignKey(EditorialOpportunity, on_delete=models.PROTECT, related_name="revisions")
    post = models.ForeignKey("blog.BlogPost", on_delete=models.PROTECT, related_name="editorial_revisions")
    title = models.CharField(max_length=300)
    excerpt = models.TextField(max_length=500)
    body = models.TextField(help_text="Sanitized allowlisted HTML on save.")
    sponsored = models.BooleanField(default=False)
    state = models.CharField(max_length=20, choices=State.choices, default=State.DRAFT)
    approval_hash = models.CharField(max_length=64, blank=True)
    approved_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL)
    approved_at = models.DateTimeField(null=True, blank=True)
    publish_at = models.DateTimeField(null=True, blank=True)
    published_at = models.DateTimeField(null=True, blank=True)
    published_hash = models.CharField(max_length=64, blank=True)
    evidence_snapshot = models.JSONField(default=dict, blank=True, help_text="Evidence and destination frozen at approval.")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def clean(self):
        from .editorial import validate_evidence, validate_destination
        if not self.opportunity_id:
            return  # Field validation supplies the required-relation error in admin.
        validate_evidence(self.opportunity.evidence)
        validate_destination(self.opportunity.destination_path)

    def save(self, *args, **kwargs):
        from .editorial import revision_hash, sanitize_html
        self.body = sanitize_html(self.body, sponsored=self.sponsored)
        previous = type(self).objects.filter(pk=self.pk).first()
        if previous and previous.state == self.State.PUBLISHED:
            # Published revisions are an audit record. Edit through a new revision.
            if revision_hash(previous) != revision_hash(self):
                raise ValidationError("Published revisions are immutable; create a new revision.")
        if self.state == self.State.APPROVED and self.approval_hash != revision_hash(self):
            self.state = self.State.DRAFT
            self.approval_hash = ""
            self.approved_by = None
            self.approved_at = None
            if kwargs.get("update_fields") is not None:
                kwargs["update_fields"] = set(kwargs["update_fields"]) | {
                    "state", "approval_hash", "approved_by", "approved_at"}
        super().save(*args, **kwargs)

from .acquisition_models import AcquisitionAutomation, AcquisitionCampaign, AcquisitionDaily, AcquisitionRun  # noqa: E402,F401
