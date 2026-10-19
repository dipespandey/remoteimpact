from django.conf import settings
from django.db import models


class ProgrammeBookmark(models.Model):
    class Status(models.TextChoices):
        SAVED = 'saved', 'Saved'
        APPLIED = 'applied', 'Applied'

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='programme_bookmarks')
    slug = models.SlugField(max_length=100)
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.SAVED)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['user', 'slug'], name='unique_user_programme')]


class DirectoryEvent(models.Model):
    class Kind(models.TextChoices):
        VISIT = 'visit', 'Directory visit'
        SIGNUP_START = 'signup_start', 'Account handoff'
        SIGNUP_COMPLETE = 'signup_complete', 'Account created'
        SAVE = 'save', 'Programme saved'
        REMOVE = 'remove', 'Programme removed'
        APPLIED = 'applied', 'Marked applied'
        OUTBOUND = 'outbound', 'Official source opened'

    event = models.CharField(max_length=24, choices=Kind.choices)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL)
    slug = models.SlugField(max_length=100, blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    dedupe_key = models.CharField(max_length=96, null=True, blank=True, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [models.Index(fields=['event', '-created_at'], name='early_event_date_idx')]
