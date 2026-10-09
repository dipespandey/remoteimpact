from datetime import timedelta

from django.db.models import Q
from django.utils import timezone

from jobs.models import Job, JobAlertDelivery, JobInterest, SavedJob


def live_jobs():
    return Job.objects.filter(is_active=True, posted_at__lte=timezone.now()).filter(
        Q(quality_score__gte=55) | Q(quality_score=0)
    ).exclude(expires_at__lt=timezone.now())


def candidate_jobs(user):
    return Job.objects.filter(
        Q(pk__in=JobAlertDelivery.objects.filter(alert__user=user).values("job_id"))
        | Q(pk__in=SavedJob.objects.filter(user=user).values("job_id"))
        | Q(pk__in=JobInterest.objects.filter(user=user).values("job_id"))
    )


def visible_interests(job):
    # Old declarations are not represented as a fresh willingness to engage.
    return job.candidate_interests.filter(share_with_employer=True,
        consented_at__gte=timezone.now() - timedelta(days=90)).exclude(status__in=["archived", "not_relevant"])
