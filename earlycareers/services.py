"""Private programme saves and coarse conversion evidence; no email subscription."""
import hashlib
import logging
from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from jobs.services.early_careers import registry
from jobs.services.job_stats import is_probably_bot, visitor_key
from .models import DirectoryEvent, ProgrammeBookmark

logger = logging.getLogger(__name__)
PENDING_KEY = 'early_career_pending'
INTENT_KEY = 'early_career_intent'


def programme(slug):
    return next((row for row in registry() if row['slug'] == slug), None)


def record(request, event, *, slug='', unique=False, user=None, metadata=None):
    if is_probably_bot(request):
        return
    actor = user or getattr(request, 'user', None)
    actor = actor if getattr(actor, 'is_authenticated', False) else None
    attribution = request.session.get('acquisition_attribution', {})
    safe = {key: str(attribution[key])[:100] for key in ('utm_source', 'utm_medium', 'utm_campaign', 'utm_content') if isinstance(attribution, dict) and key in attribution}
    safe.update(metadata or {})
    key = None
    if unique:
        identity = f'account-created:{actor.pk}' if event == DirectoryEvent.Kind.SIGNUP_COMPLETE and actor else f'{event}:{slug}:{timezone.localdate()}:{visitor_key(request)}'
        key = hashlib.sha256(identity.encode()).hexdigest()
    try:
        with transaction.atomic():
            values = dict(event=event, user=actor, slug=slug, metadata=safe)
            if key:
                DirectoryEvent.objects.get_or_create(dedupe_key=key, defaults=values)
            else:
                DirectoryEvent.objects.create(**values)
    except Exception:
        logger.warning('Early careers event unavailable: %s', event)


def prepare_account(request, *, slug='', placement='directory'):
    request.session[INTENT_KEY] = {'at': timezone.now().isoformat(), 'placement': placement}
    if slug:
        pending = request.session.get(PENDING_KEY, [])
        pending = pending if isinstance(pending, list) else []
        request.session[PENDING_KEY] = list(dict.fromkeys([s for s in [*pending, slug] if isinstance(s, str) and programme(s)]))[-20:]
        request.session['early_career_programme_name'] = programme(slug)['name']
    record(request, DirectoryEvent.Kind.SIGNUP_START, slug=slug, unique=True, metadata={'placement': placement})


def recent_intent(request):
    intent = request.session.get(INTENT_KEY)
    if not isinstance(intent, dict):
        return None
    try:
        entered = timezone.datetime.fromisoformat(intent['at'])
        if timezone.now() - timedelta(days=1) <= entered <= timezone.now():
            return intent
    except (ValueError, TypeError, KeyError):
        pass
    return None


def finish_saves(request, user):
    if not recent_intent(request):
        request.session.pop(PENDING_KEY, None)
        request.session.pop('early_career_programme_name', None)
        request.session.pop(INTENT_KEY, None)
        return
    pending = request.session.get(PENDING_KEY, [])
    if not isinstance(pending, list):
        pending = []
    with transaction.atomic():
        for slug in pending[:20]:
            if not isinstance(slug, str) or not programme(slug):
                continue
            _, created = ProgrammeBookmark.objects.get_or_create(user=user, slug=slug)
            if created:
                record(request, DirectoryEvent.Kind.SAVE, slug=slug, user=user, metadata={'after_account': True})
    request.session.pop(PENDING_KEY, None)
    request.session.pop('early_career_programme_name', None)
    request.session.pop(INTENT_KEY, None)
