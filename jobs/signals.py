import logging
import hashlib
import threading

import requests
from django.conf import settings
from django.db import connection
from django.db.models.signals import post_save
from django.dispatch import receiver

from jobs.models import Job, SeekerProfile
from jobs.search import weighted_job_search_vector

logger = logging.getLogger(__name__)


def _embedding_hash(parts):
    value = "\x1f".join(str(part or "").strip().lower() for part in parts)
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _job_embedding_hash(job):
    return _embedding_hash([
        job.title,
        job.organization.name if job.organization_id and job.organization else "",
        job.category.name if job.category_id and job.category else "",
        job.description,
        job.requirements,
        job.impact,
        job.skills,
        job.job_type,
        job.experience_level,
        job.education_level,
    ])


def _seeker_embedding_hash(profile):
    return _embedding_hash([
        profile.headline,
        profile.bio,
        profile.skills,
        profile.impact_areas,
        profile.work_styles,
        profile.experience_level,
    ])

# IndexNow configuration
INDEXNOW_KEY = "db25ddbb333d413289a18c8820c32ca4"
INDEXNOW_ENDPOINT = "https://www.bing.com/indexnow"


@receiver(post_save, sender=Job)
def embed_job_on_save(sender, instance, created, **kwargs):
    current_hash = _job_embedding_hash(instance)
    if instance.is_active and (instance.embedding is None or instance.embedding_content_hash != current_hash):
        from jobs.services.embedding_service import embed_job
        Job.objects.filter(pk=instance.pk).update(
            embedding=embed_job(instance), embedding_content_hash=current_hash
        )


@receiver(post_save, sender=Job)
def update_job_search_vector(sender, instance, created, **kwargs):
    """Populate full-text search vector for lexical matching."""
    if connection.vendor != "postgresql":
        return

    qs = Job.objects.filter(pk=instance.pk)
    if instance.is_active:
        qs.update(search_vector=weighted_job_search_vector())
    else:
        qs.update(search_vector=None)


def _ping_indexnow(url: str):
    """Send IndexNow ping to Bing (runs in background thread)."""
    try:
        site_url = getattr(settings, 'SITE_URL', 'https://remoteimpact.org')
        key_location = f"{site_url}/{INDEXNOW_KEY}.txt"
        response = requests.get(
            INDEXNOW_ENDPOINT,
            params={
                "url": url,
                "key": INDEXNOW_KEY,
                "keyLocation": key_location,
            },
            timeout=10,
        )
        if response.status_code == 200:
            logger.info(f"IndexNow: Successfully pinged {url}")
        else:
            logger.warning(f"IndexNow: Got status {response.status_code} for {url}")
    except Exception as e:
        logger.error(f"IndexNow: Failed to ping {url}: {e}")


@receiver(post_save, sender=Job)
def ping_indexnow_on_job_save(sender, instance, created, **kwargs):
    """Notify Bing when a new public job URL is created."""
    if not created or not instance.is_active:
        return
    try:
        site_url = getattr(settings, 'SITE_URL', 'https://remoteimpact.org')
        job_url = f"{site_url}{instance.get_absolute_url()}"
        # Run in background thread to not block the request
        thread = threading.Thread(target=_ping_indexnow, args=(job_url,))
        thread.daemon = True
        thread.start()
    except Exception as e:
        logger.error(f"IndexNow: Error preparing ping for job {instance.pk}: {e}")


@receiver(post_save, sender=SeekerProfile)
def embed_seeker_on_save(sender, instance, created, **kwargs):
    current_hash = _seeker_embedding_hash(instance)
    if instance.wizard_completed and (instance.embedding is None or instance.embedding_content_hash != current_hash):
        from jobs.services.embedding_service import embed_seeker
        SeekerProfile.objects.filter(pk=instance.pk).update(
            embedding=embed_seeker(instance), embedding_content_hash=current_hash
        )


@receiver(post_save, sender='auth.User')
def send_welcome_email(sender, instance, created, **kwargs):
    """Send welcome email to new users via Resend."""
    if not created:
        return
    try:
        from jobs.models import ConsumerFunnelEvent
        from jobs.services.funnel import record_consumer_event
        from jobs.services.email_service import email_service
        from django.conf import settings as django_settings
        from django.template.loader import render_to_string

        record_consumer_event(
            ConsumerFunnelEvent.Event.ACCOUNT_CREATED,
            user=instance,
            metadata={"has_email": bool(instance.email)},
            dedupe_key=f"account-created:{instance.pk}",
        )
        site_url = getattr(django_settings, 'SITE_URL', 'https://remoteimpact.org')
        context = {'user': instance, 'site_url': site_url}
        html = render_to_string('emails/welcome.html', context)
        text = render_to_string('emails/welcome.txt', context)
        email_service.send_email(
            to=instance.email,
            subject='Welcome to Remote Impact!',
            html=html,
            text=text,
        )
        logger.info(f'Welcome email sent to {instance.email}')
    except Exception as e:
        logger.error(f'Failed to send welcome email to {instance.email}: {e}')




from jobs.models import TalentInvitation

@receiver(post_save, sender=TalentInvitation)
def send_invitation_email(sender, instance, created, **kwargs):
    """Send email when a talent invitation is created."""
    if not created:
        return
    try:
        from jobs.services.email_service import email_service
        email_service.send_talent_invitation_notification(instance)
        logger.info(f"Invitation email sent to {instance.seeker.user.email} for {instance.job.title}")
    except Exception as e:
        logger.error(f"Failed to send invitation email: {e}")

# =============================================================================
# REFERRAL TRACKING
# =============================================================================

try:
    from allauth.account.signals import user_signed_up

    @receiver(user_signed_up)
    def track_referral_on_signup(request, user, **kwargs):
        """Record referral and campaign context without storing personal referrers."""
        attribution = request.session.get("acquisition_attribution", {})
        if attribution:
            try:
                from jobs.models import ConsumerFunnelEvent

                event, _ = ConsumerFunnelEvent.objects.get_or_create(
                    dedupe_key=f"account-created:{user.pk}",
                    defaults={
                        "event": ConsumerFunnelEvent.Event.ACCOUNT_CREATED,
                        "user": user,
                        "metadata": {"has_email": bool(user.email)},
                    },
                )
                metadata = dict(event.metadata or {})
                metadata.update({
                    key: value
                    for key, value in attribution.items()
                    if key in ("utm_source", "utm_medium", "utm_campaign", "utm_content", "source")
                })
                event.metadata = metadata
                event.save(update_fields=["metadata"])
            except Exception as e:
                logger.warning("Error recording signup attribution: %s", e)

        referral_code = request.session.pop("referral_code", None)
        if not referral_code:
            return
        try:
            from jobs.models import Referral, ReferralSignup
            referral = Referral.objects.get(code=referral_code)
            # Don't let users refer themselves
            if referral.referrer == user:
                return
            ReferralSignup.objects.get_or_create(
                referrer=referral,
                referred_user=user,
            )
            logger.info(f"Referral tracked: {user.email} referred by {referral.referrer.email}")
        except Referral.DoesNotExist:
            logger.warning(f"Invalid referral code: {referral_code}")
        except Exception as e:
            logger.error(f"Error tracking referral: {e}")
except ImportError:
    pass
