import uuid

from django.db import models
from django.utils import timezone


class ScoutPreviewTask(models.Model):
    """Durable, deduplicated work for rendering an anonymous Scout preview."""

    class State(models.TextChoices):
        QUEUED = "queued", "Queued"
        RUNNING = "running", "Running"
        SUCCEEDED = "succeeded", "Succeeded"
        FAILED = "failed", "Failed"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    semantic_key = models.CharField(max_length=64, unique=True)
    intent_text = models.CharField(max_length=2000)
    state = models.CharField(max_length=12, choices=State.choices, default=State.QUEUED, db_index=True)
    result = models.JSONField(default=dict, blank=True)
    attempts = models.PositiveSmallIntegerField(default=0)
    available_at = models.DateTimeField(db_index=True)
    lease_until = models.DateTimeField(null=True, blank=True, db_index=True)
    lease_token = models.UUIDField(null=True, blank=True)
    last_error = models.CharField(max_length=240, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        indexes = [models.Index(fields=["state", "available_at", "created_at"], name="scout_task_claim_idx")]


class ScoutPreviewAccess(models.Model):
    """A session grant; possession of a task UUID alone never reveals its brief/results."""

    task = models.ForeignKey(ScoutPreviewTask, on_delete=models.CASCADE, related_name="access_grants")
    session_digest = models.CharField(max_length=64, db_index=True)
    enqueue_count = models.PositiveSmallIntegerField(default=1)
    window_started_at = models.DateTimeField(default=timezone.now, editable=False)
    last_enqueued_at = models.DateTimeField(default=timezone.now, editable=False)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["task", "session_digest"], name="scout_access_task_session_unique")]
        indexes = [models.Index(fields=["session_digest", "created_at"], name="scout_access_session_idx")]


class ScoutPreviewQueueGuard(models.Model):
    """Single-row lock that makes the PostgreSQL global queue bound atomic."""

    key = models.PositiveSmallIntegerField(primary_key=True, default=1, editable=False)
