"""Editorial partnership research; never sends email."""
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import URLValidator
from django.db import models


def canonical_prospect_url(value):
    value = value.strip()
    URLValidator(schemes=["http", "https"])(value)
    try:
        parsed = urlsplit(value)
        if parsed.username or parsed.password or parsed.port not in (None, 80, 443):
            raise ValueError
        host = parsed.hostname.encode("idna").decode("ascii").lower()
        if ":" in host:
            host = f"[{host}]"
        if parsed.port and parsed.port != (443 if parsed.scheme == "https" else 80):
            host = f"{host}:{parsed.port}"
    except (ValueError, UnicodeError, AttributeError) as exc:
        raise ValidationError("Use a public HTTP(S) page URL on a standard port.") from exc
    return urlunsplit((parsed.scheme.lower(), host, parsed.path or "/", parsed.query, ""))


class LinkProspect(models.Model):
    class Status(models.TextChoices):
        RESEARCH = "research", "Research"
        DRAFT = "draft", "Draft ready"
        APPROVED = "approved", "Reviewed for outreach"
        CONTACTED = "contacted", "Contacted manually"
        REPLIED = "replied", "Replied"
        PLACED = "placed", "Placement reported"
        DECLINED = "declined", "Declined"
        DO_NOT_CONTACT = "do_not_contact", "Do not contact"

    class Sector(models.TextChoices):
        CLIMATE = "climate", "Climate"
        HEALTH = "health", "Global health"
        AI = "ai", "AI safety"
        IMPACT = "impact", "Social impact"

    class Kind(models.TextChoices):
        RESOURCE = "resource", "Career resource page"
        COMMUNITY = "community", "Community / newsletter"
        RESEARCH = "research", "Research / publication"
        DIRECTORY = "directory", "Relevant directory"

    name = models.CharField(max_length=255)
    url = models.URLField(max_length=1000, unique=True, help_text="Exact page that could link to us, not just its homepage.")
    sector = models.CharField(max_length=12, choices=Sector.choices, default=Sector.IMPACT)
    kind = models.CharField(max_length=12, choices=Kind.choices, default=Kind.RESOURCE)
    source = models.CharField(max_length=500, blank=True, help_text="Research query, export filename, or other provenance.")
    audience = models.CharField(max_length=500, blank=True)
    fit_reason = models.TextField(blank=True, help_text="Specific benefit for this page's readers. Required before review.")
    target_url = models.URLField(max_length=1000, blank=True)
    priority = models.PositiveSmallIntegerField(default=2, choices=[(1, "High"), (2, "Medium"), (3, "Low")])
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.RESEARCH, db_index=True)
    contact_name = models.CharField(max_length=255, blank=True)
    contact_email = models.EmailField(blank=True)
    contact_url = models.URLField(max_length=1000, blank=True, help_text="Public contact form or professional contact page.")
    contact_source_url = models.URLField(max_length=1000, blank=True, help_text="Page where this contact was actually found.")
    subject = models.CharField(max_length=255, blank=True)
    pitch = models.TextField(blank=True)
    reviewed_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="reviewed_link_prospects")
    reviewed_at = models.DateTimeField(null=True, blank=True)
    contacted_at = models.DateTimeField(null=True, blank=True)
    follow_up_on = models.DateField(null=True, blank=True)
    placement_url = models.URLField(max_length=1000, blank=True)
    notes = models.TextField(blank=True)
    referral_visits = models.PositiveIntegerField(null=True, blank=True, help_text="Measured visits for this prospect's campaign; blank means not measured.")
    scout_signups = models.PositiveIntegerField(null=True, blank=True, help_text="Measured Scout signups for this prospect's campaign.")
    metrics_since = models.DateField(null=True, blank=True)
    metrics_until = models.DateField(null=True, blank=True)
    metrics_source = models.CharField(max_length=500, blank=True, help_text="Analytics report or export used for these counts.")
    last_checked_at = models.DateTimeField(null=True, blank=True)
    check_status = models.CharField(max_length=80, blank=True)
    page_title = models.CharField(max_length=500, blank=True)
    page_excerpt = models.TextField(blank=True)
    contact_candidates = models.JSONField(default=list, blank=True)
    link_evidence = models.JSONField(default=list, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["priority", "-created_at"]
        verbose_name = "Backlink prospect"

    def __str__(self):
        return self.name

    @property
    def campaign_url(self):
        if not self.target_url or not self.pk:
            return ""
        parsed = urlsplit(self.target_url)
        query = [(k, v) for k, v in parse_qsl(parsed.query, keep_blank_values=True) if not k.lower().startswith("utm_")]
        query.extend([("utm_source", f"link_prospect_{self.pk}"), ("utm_medium", "referral"), ("utm_campaign", "editorial_partnerships")])
        return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, urlencode(query), parsed.fragment))

    def clean(self):
        super().clean()
        if self.url:
            self.url = canonical_prospect_url(self.url)
        if self.referral_visits is not None or self.scout_signups is not None:
            missing = {field: "Record a reporting window and analytics source for measured counts."
                       for field in ("metrics_since", "metrics_until", "metrics_source") if not getattr(self, field)}
            if missing:
                raise ValidationError(missing)
        if self.metrics_since and self.metrics_until and self.metrics_until < self.metrics_since:
            raise ValidationError({"metrics_until": "End date must be on or after the start date."})
        if self.status == self.Status.APPROVED:
            errors = {}
            for field in ("audience", "fit_reason", "target_url", "subject", "pitch", "contact_source_url"):
                if not getattr(self, field).strip():
                    errors[field] = "Required before approving outreach."
            if not (self.contact_email or self.contact_url):
                errors["contact_email"] = "Record an observed email or a public contact page."
            if errors:
                raise ValidationError(errors)


class LinkProspectEvent(models.Model):
    prospect = models.ForeignKey(LinkProspect, on_delete=models.CASCADE, related_name="events")
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL)
    action = models.CharField(max_length=50)
    detail = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return self.action
