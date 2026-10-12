"""Which roles an employer account can manage: its own posts and drafts, plus roles that
colleagues at the same organization have paid for."""
from django.db.models import Q

from jobs.models import Job


def hiring_jobs(user):
    if not user or not user.is_authenticated:
        return Job.objects.none()
    mine = Job.objects.filter(Q(poster=user) | Q(is_paid=True, organization__members=user)).values("pk")
    return Job.objects.filter(pk__in=mine)
