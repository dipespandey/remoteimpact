"""Durable acquisition planning, measurements and bounded automation."""
from django.db import models


class AcquisitionCampaign(models.Model):
    key = models.CharField(max_length=100, unique=True)
    title = models.CharField(max_length=255)
    audience = models.CharField(max_length=12, choices=[('seeker', 'Job seekers'), ('employer', 'Employers')])
    prospect = models.OneToOneField('jobs.LinkProspect', null=True, blank=True, on_delete=models.PROTECT)
    organization = models.OneToOneField('jobs.Organization', null=True, blank=True, on_delete=models.PROTECT)
    source = models.CharField(max_length=100, unique=True)
    destination = models.URLField(max_length=1000, blank=True)
    score = models.PositiveSmallIntegerField(default=0)
    reasons = models.JSONField(default=list)
    evidence = models.JSONField(default=dict)
    draft = models.TextField(blank=True)
    draft_created_at = models.DateTimeField(null=True, blank=True)
    status = models.CharField(max_length=20, default='research', choices=[
        ('research', 'Needs research'), ('review', 'Ready for review'), ('contacted', 'Contacted'),
        ('placed', 'Placement observed'), ('hold', 'On hold'), ('dismissed', 'Dismissed')])
    next_action = models.CharField(max_length=255, blank=True)
    hold_reason = models.CharField(max_length=500, blank=True)
    metrics = models.JSONField(default=dict)
    metrics_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-score', 'id']

    def __str__(self):
        return self.title


class AcquisitionDaily(models.Model):
    campaign = models.ForeignKey(AcquisitionCampaign, on_delete=models.CASCADE, related_name='daily')
    date = models.DateField()
    landing_sessions = models.PositiveIntegerField(default=0)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['campaign', 'date'], name='growth_campaign_day')]


class AcquisitionAutomation(models.Model):
    id = models.PositiveSmallIntegerField(primary_key=True, default=1, editable=False)
    enabled = models.BooleanField(default=True)
    discovery_enabled = models.BooleanField(default=True)
    requested = models.BooleanField(default=False)
    last_started_at = models.DateTimeField(null=True, blank=True)
    lease_until = models.DateTimeField(null=True, blank=True)
    lease_token = models.CharField(max_length=36, blank=True)


class AcquisitionRun(models.Model):
    token = models.CharField(max_length=36, unique=True)
    started_at = models.DateTimeField(auto_now_add=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    status = models.CharField(max_length=20, default='running')
    summary = models.JSONField(default=dict)
    errors = models.JSONField(default=list)

    class Meta:
        ordering = ['-started_at']
