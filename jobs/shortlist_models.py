"""Candidate-owned progress and explicitly consented employer introductions."""

import uuid

from django.conf import settings
from django.db import models


class JobInterest(models.Model):
    class Status(models.TextChoices):
        SAVED = "saved", "Interested"
        APPLIED = "applied", "Applied"
        INTERVIEW = "interview", "Interviewing"
        OFFER = "offer", "Offer received"
        ARCHIVED = "archived", "Archived"
        NOT_RELEVANT = "not_relevant", "Not for me"

    class Stage(models.TextChoices):
        NEW = "new", "Not reviewed"
        REVIEWING = "reviewing", "Reviewing"
        CONTACTED = "contacted", "Contacted"
        CLOSED = "closed", "Closed"

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="job_interests")
    job = models.ForeignKey("jobs.Job", on_delete=models.CASCADE, related_name="candidate_interests")
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.SAVED)
    share_with_employer = models.BooleanField(default=False)
    consented_at = models.DateTimeField(null=True, blank=True)
    # Only these candidate-entered fields and their account email are shared.
    full_name = models.CharField(max_length=150, blank=True)
    headline = models.CharField(max_length=200, blank=True)
    evidence = models.TextField(blank=True, max_length=2000)
    portfolio_url = models.URLField(blank=True)
    eligibility = models.CharField(max_length=300, blank=True)
    availability = models.CharField(max_length=150, blank=True)
    employer_stage = models.CharField(max_length=20, choices=Stage.choices, default=Stage.NEW)
    employer_notified_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["user", "job"], name="unique_candidate_job_interest")]
        indexes = [models.Index(fields=["user", "-updated_at"]), models.Index(fields=["job", "share_with_employer"])]


class SharedShortlist(models.Model):
    token = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="shared_shortlists")
    jobs = models.ManyToManyField("jobs.Job", related_name="shared_shortlists")
    revoked = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
