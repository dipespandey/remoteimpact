"""One weekly audience, retaining legacy provenance without upgrading consent."""
import uuid

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.db import transaction
from django.utils import timezone

from jobs.models import NewsletterSubscriber, RoundupSubscriber, UserProfile


def normalized_email(email):
    email = (email or '').strip().lower()
    validate_email(email)
    if len(email) > 254:
        raise ValidationError('Email too long')
    return email


def weekly_subscription(email):
    from jobs.services.roundup import active_subscribers
    return active_subscribers().filter(email__iexact=email).exists() if email else False


def mirror_weekly_preference(email):
    UserProfile.objects.filter(user__email__iexact=email).update(
        email_newsletter=True, email_newsletter_unsubscribed_at=None)


@transaction.atomic
def subscribe_account(user):
    """A deliberate authenticated preference POST supplies new weekly consent."""
    email = normalized_email(user.email)
    from allauth.account.models import EmailAddress
    if not EmailAddress.objects.filter(user=user, email__iexact=email, verified=True).exists():
        raise ValidationError('Email confirmation is required before weekly delivery.')
    sub = RoundupSubscriber.objects.filter(email__iexact=email).select_for_update().first()
    if sub is None:
        sub = RoundupSubscriber.objects.create(email=email)
    now = timezone.now()
    sub.confirmation_key = uuid.uuid4()  # invalidate outstanding old confirmation links
    sub.consent_basis = 'account_explicit'
    sub.consent_version = 'weekly-account-explicit-2026-10-23'
    sub.consent_at = sub.confirmed_at = now
    sub.unsubscribed_at = None
    sub.source = 'account_preferences'
    sub.save()
    profile, _ = UserProfile.objects.get_or_create(user=user)
    mirror_weekly_preference(email)
    return sub


@transaction.atomic
def unsubscribe_newsletters(email):
    """One opt-out suppresses weekly newsletters and legacy marketing, not Scouts."""
    email = normalized_email(email)
    now = timezone.now()
    sub = RoundupSubscriber.objects.filter(email__iexact=email).select_for_update().first()
    if sub is None:
        sub = RoundupSubscriber.objects.create(email=email, consent_basis='legacy_unknown', source='legacy_unsubscribe')
    sub.unsubscribed_at = now
    sub.confirmation_key = uuid.uuid4()
    sub.save(update_fields=['unsubscribed_at', 'confirmation_key'])
    for user in get_user_model().objects.filter(email__iexact=email):
        profile, _ = UserProfile.objects.get_or_create(user=user)
        profile.email_newsletter = False
        profile.email_newsletter_unsubscribed_at = now
        profile.save(update_fields=['email_newsletter', 'email_newsletter_unsubscribed_at'])
    NewsletterSubscriber.objects.filter(email__iexact=email).update(unsubscribed=True, unsubscribed_at=now)
    return sub


def legacy_records():
    """Private iterator. Never include addresses in command output."""
    for row in UserProfile.objects.filter(email_newsletter=True).select_related('user').iterator():
        yield row.user.email, 'account_preference', 'legacy_account', None
    for row in UserProfile.objects.filter(email_newsletter_unsubscribed_at__isnull=False).select_related('user').iterator():
        yield row.user.email, 'account_preference', 'legacy_account', row.email_newsletter_unsubscribed_at
    for row in NewsletterSubscriber.objects.all().iterator():
        yield row.email, 'monthly_product_updates', 'monthly_only', (row.unsubscribed_at or row.created_at) if row.unsubscribed else None


@transaction.atomic
def reconcile_audience(apply=False):
    """Idempotent import. Explicit newer weekly consent wins over an older opt-out."""
    groups = {}
    invalid = 0
    for raw, origin, basis, stopped in legacy_records():
        try:
            email = normalized_email(raw)
        except ValidationError:
            invalid += 1
            continue
        item = groups.setdefault(email, {'origins': set(), 'basis': basis, 'stop': None})
        item['origins'].add(origin)
        if basis == 'legacy_account':
            item['basis'] = basis
        if stopped and (item['stop'] is None or stopped > item['stop']):
            item['stop'] = stopped
    result = {'mode': 'apply' if apply else 'preview', 'legacy_unique_addresses': len(groups),
              'new_records': 0, 'updated_records': 0, 'suppressed_records': 0, 'invalid_records': invalid,
              'weekly_consent_granted': 0, 'emails_sent': 0}
    for email, item in groups.items():
        sub = RoundupSubscriber.objects.select_for_update().filter(email__iexact=email).first()
        created = sub is None
        if created:
            result['new_records'] += 1
            sub = RoundupSubscriber(email=email, consent_basis=item['basis'], source='legacy_reconciliation',
                                   consent_version='legacy-unverified-2026-10-23')
        origins = sorted(set(sub.legacy_sources) | item['origins'])
        changed = origins != sub.legacy_sources
        sub.legacy_sources = origins
        stop = item['stop']
        if stop and (not sub.confirmed_at or stop >= sub.confirmed_at) and (not sub.unsubscribed_at or stop > sub.unsubscribed_at):
            sub.unsubscribed_at = stop
            sub.confirmation_key = uuid.uuid4()
            result['suppressed_records'] += 1
            changed = True
        if changed and not created:
            result['updated_records'] += 1
        if apply and (created or changed):
            sub.save()
    return result


def link_monthly_record(record):
    """Keep new legacy-footer records visible without granting weekly consent."""
    try:
        email = normalized_email(record.email)
    except ValidationError:
        return
    with transaction.atomic():
        sub = RoundupSubscriber.objects.select_for_update().filter(email__iexact=email).first()
        if sub is None:
            sub = RoundupSubscriber.objects.create(email=email, consent_basis='monthly_only',
                source='legacy_reconciliation', consent_version='monthly-only-2026-10-23')
        sources = sorted(set(sub.legacy_sources) | {'monthly_product_updates'})
        if sources != sub.legacy_sources:
            sub.legacy_sources = sources
            sub.save(update_fields=['legacy_sources'])
        stopped = record.unsubscribed_at or record.created_at
        if record.unsubscribed and (not sub.confirmed_at or stopped >= sub.confirmed_at):
            unsubscribe_newsletters(email)
