from django.db.models.signals import post_save
from django.dispatch import receiver

from jobs.models import Job, SeekerProfile


@receiver(post_save, sender=Job)
def embed_job_on_save(sender, instance, created, **kwargs):
    if instance.is_active and instance.embedding is None:
        from jobs.services.embedding_service import embed_job
        Job.objects.filter(pk=instance.pk).update(embedding=embed_job(instance))


@receiver(post_save, sender=SeekerProfile)
def embed_seeker_on_save(sender, instance, created, **kwargs):
    if instance.wizard_completed and instance.embedding is None:
        from jobs.services.embedding_service import embed_seeker
        SeekerProfile.objects.filter(pk=instance.pk).update(embedding=embed_seeker(instance))
