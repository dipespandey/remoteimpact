import logging

from django.contrib.postgres.search import SearchVector
from django.db.models.signals import post_save
from django.dispatch import receiver

from jobs.models import Job, SeekerProfile

logger = logging.getLogger(__name__)


@receiver(post_save, sender=Job)
def embed_job_on_save(sender, instance, created, **kwargs):
    if instance.is_active and instance.embedding is None:
        from jobs.services.embedding_service import embed_job
        Job.objects.filter(pk=instance.pk).update(embedding=embed_job(instance))


@receiver(post_save, sender=Job)
def update_job_search_vector(sender, instance, created, **kwargs):
    """Populate full-text search vector for lexical matching."""
    if instance.is_active and instance.search_vector is None:
        Job.objects.filter(pk=instance.pk).update(
            search_vector=(
                SearchVector('title', weight='A') +
                SearchVector('description', weight='B') +
                SearchVector('requirements', weight='C') +
                SearchVector('impact', weight='B')
            )
        )


@receiver(post_save, sender=SeekerProfile)
def embed_seeker_on_save(sender, instance, created, **kwargs):
    if instance.wizard_completed and instance.embedding is None:
        from jobs.services.embedding_service import embed_seeker
        SeekerProfile.objects.filter(pk=instance.pk).update(embedding=embed_seeker(instance))


@receiver(post_save, sender='auth.User')
def send_welcome_email(sender, instance, created, **kwargs):
    """Send welcome email to new users via Resend."""
    if not created:
        return
    try:
        from jobs.services.email_service import email_service
        from django.conf import settings as django_settings
        from django.template.loader import render_to_string

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
        """When a user signs up, check if they came via a referral link."""
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
