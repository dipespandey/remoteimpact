import logging
import hashlib
import threading

import requests
from django.conf import settings
from django.db import connection, transaction, close_old_connections
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
        profile.cv_facts,
    ])

# IndexNow configuration (the public key comes from settings for both send paths).
INDEXNOW_ENDPOINT = "https://www.bing.com/indexnow"


@receiver(post_save, sender=Job)
def embed_job_on_save(sender, instance, created, **kwargs):
    current_hash = _job_embedding_hash(instance)
    if instance.is_active and (instance.embedding is None or instance.embedding_content_hash != current_hash):
        from jobs.services.embedding_service import embed_job
        try:
            embedding = embed_job(instance)
        except Exception:  # noqa: BLE001 - optional semantic search must not block imports or job saves
            logger.warning("Skipping embedding for job %s; lexical search remains available", instance.pk)
            return
        if embedding is not None:
            Job.objects.filter(pk=instance.pk).update(
                embedding=embedding, embedding_content_hash=current_hash
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


def _ping_indexnow(url: str, job_id=None):
    """Send IndexNow ping to Bing (runs in background thread)."""
    if not settings.INDEXNOW_ENABLED:
        return
    close_old_connections()
    try:
        from jobs.services.search_indexing import pending, fingerprint, acknowledge
        job = Job.objects.select_related('organization').get(pk=job_id) if job_id else None
        if job and (not job.is_available or not pending(job, 'indexnow')):
            return
        signature = fingerprint(job) if job else None
        site_url = getattr(settings, 'SITE_URL', 'https://remoteimpact.org')
        if job and url != site_url.rstrip('/') + job.get_absolute_url():
            return  # A renamed URL will be picked up by the persistent batch.
        key = settings.INDEXNOW_KEY
        key_location = f"{site_url}/{key}.txt"
        response = requests.get(
            INDEXNOW_ENDPOINT,
            params={
                "url": url,
                "key": key,
                "keyLocation": key_location,
            },
            timeout=10,
        )
        if response.status_code in {200, 202}:
            if job:
                acknowledge(job, 'indexnow', signature)
            logger.info(f"IndexNow: Successfully pinged {url}")
        else:
            logger.warning(f"IndexNow: Got status {response.status_code} for {url}")
    except Exception as e:
        logger.error(f"IndexNow: Failed to ping {url}: {e}")
    finally:
        close_old_connections()


@receiver(post_save, sender=Job)
def ping_indexnow_on_job_save(sender, instance, created, **kwargs):
    """Notify Bing when a new public job URL is created."""
    if not settings.INDEXNOW_ENABLED or not created or not instance.is_available:
        return
    try:
        site_url = getattr(settings, 'SITE_URL', 'https://remoteimpact.org')
        job_url = f"{site_url}{instance.get_absolute_url()}"
        # Run in background thread to not block the request
        def start_after_commit():
            thread = threading.Thread(target=_ping_indexnow, args=(job_url, instance.pk))
            thread.daemon = True
            thread.start()
        transaction.on_commit(start_after_commit)
    except Exception as e:
        logger.error(f"IndexNow: Error preparing ping for job {instance.pk}: {e}")


@receiver(post_save, sender=SeekerProfile)
def read_cv_on_upload(sender, instance, created, **kwargs):
    """A new or replaced CV is read in the background, then the profile is re-embedded with what it says."""
    if not instance.resume or instance.resume.name == instance.cv_source:
        return
    from django.db import close_old_connections, transaction

    profile_id, source = instance.pk, instance.resume.name

    def run():
        close_old_connections()
        try:
            from jobs.services.cv_reader import read_profile_cv
            from jobs.services.embedding_service import embed_seeker
            profile = SeekerProfile.objects.get(pk=profile_id)
            read_profile_cv(profile)
            SeekerProfile.objects.filter(pk=profile_id).update(cv_source=source)
            if profile.wizard_completed:
                SeekerProfile.objects.filter(pk=profile_id).update(
                    embedding=embed_seeker(profile), embedding_content_hash=_seeker_embedding_hash(profile))
        except Exception:  # noqa: BLE001 - reading a CV must never break saving a profile
            logger.exception("CV reading failed for profile %s", profile_id)
        finally:
            close_old_connections()

    transaction.on_commit(lambda: threading.Thread(target=run, daemon=True).start())


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
        try:
            from jobs.services.scout_funnel import link_signup
            link_signup(request, user, kwargs.get('sociallogin'))
        except Exception:
            logger.warning('Scout signup attribution unavailable')
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


# ---------------------------------------------------------------------------
# Payment notifications: tell the owner about every payment, and thank job posters.
# ---------------------------------------------------------------------------

from jobs.models import ConsumerFunnelEvent, EmployerFunnelEvent  # noqa: E402


def _owner_email():
    return getattr(settings, "PAYMENT_NOTIFY_EMAIL", "hello@remoteimpact.org")


def _send_payment_notices(subject, lines, poster_job=None):
    from django.db import transaction

    def send():
        from jobs.services.email_service import email_service

        site = getattr(settings, "SITE_URL", "https://remoteimpact.org").rstrip("/")
        text = "\n".join(lines)
        try:
            email_service.send_email(to=_owner_email(), subject=subject, text=text,
                                     html="<p>" + "<br>".join(lines) + "</p>")
        except Exception:  # noqa: BLE001 - a notice must never break a payment
            logger.exception("Owner payment notice failed")
        poster = getattr(poster_job, "poster", None)
        if poster_job is not None and poster is not None and poster.email:
            url = f"{site}{poster_job.get_absolute_url()}"
            body = (f"Hi,\n\nThanks for posting \"{poster_job.title}\" on Remote Impact. Your payment went through "
                    f"and the role is live for 30 days, featured at the top of search:\n{url}\n\n"
                    f"We'll send people with matching profiles your way. Reply to this email if anything needs changing.\n\n"
                    f"Dipesh\nRemote Impact")
            try:
                email_service.send_email(to=poster.email, subject=f"Your job is live: {poster_job.title}",
                                         text=body, html="<p>" + body.replace("\n\n", "</p><p>").replace("\n", "<br>") + "</p>")
            except Exception:  # noqa: BLE001
                logger.exception("Poster payment confirmation failed")

    transaction.on_commit(send)


@receiver(post_save, sender=EmployerFunnelEvent)
def notify_employer_payment(sender, instance, created, **kwargs):
    if not created or instance.event != EmployerFunnelEvent.Event.PAYMENT_COMPLETED:
        return
    job = instance.job
    product = (instance.metadata or {}).get("product", "job post")
    who = getattr(instance.user, "email", "") or "unknown"
    lines = [f"Payment: {product}", f"Job: {getattr(job, 'title', '?')} ({getattr(job.organization, 'name', '') if job else ''})",
             f"Account: {who}"]
    _send_payment_notices(f"New payment: {product} on Remote Impact", lines,
                          poster_job=job if product == "job post" else None)

    # Cached pages (homepage, job list) are kept up to an hour; show the new featured role now.
    from django.core.cache import cache
    from django.db import transaction

    transaction.on_commit(cache.clear)


@receiver(post_save, sender=ConsumerFunnelEvent)
def notify_consumer_payment(sender, instance, created, **kwargs):
    if not created or instance.event != ConsumerFunnelEvent.Event.PAYMENT_COMPLETED:
        return
    meta = instance.metadata or {}
    plan = meta.get("plan", "pro")
    lines = [f"Payment: Pro ({plan}){' trial start' if meta.get('trial') else ''}",
             f"Account: {getattr(instance.user, 'email', '') or 'unknown'}"]
    _send_payment_notices(f"New payment: Pro {plan} on Remote Impact", lines)
