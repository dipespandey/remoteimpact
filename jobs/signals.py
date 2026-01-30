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
