"""Lightweight engagement tracking that employers can be shown."""
from django.http import Http404, HttpResponse, HttpResponseRedirect
from django.shortcuts import get_object_or_404
from django.views import View
from django.views.decorators.csrf import csrf_exempt
from django.utils.decorators import method_decorator

from ..models import Job
from ..services import job_stats
from ..services.job_quality import safe_application_destination


@method_decorator(csrf_exempt, name="dispatch")
class JobViewBeaconView(View):
    """Counted from a small script after the page is visible; bots rarely run it."""

    def post(self, request, slug):
        job_id = Job.objects.filter(slug=slug).values_list("pk", flat=True).first()
        if job_id and not job_stats.is_probably_bot(request):
            job_stats.record(job_id, "views", request=request)
        return HttpResponse(status=204)


class JobApplyRedirectView(View):
    """Outbound apply link: counts the click, then sends the person to the employer."""

    def get(self, request, slug):
        job = get_object_or_404(Job.objects.only("pk", "slug", "application_url"), slug=slug)
        destination = safe_application_destination(job.application_url)
        if not destination:
            raise Http404("This job has no external application link.")
        if not job_stats.is_probably_bot(request) and job_stats.viewed_today(job.pk, request):
            job_stats.record(job.pk, "apply_clicks", request=request)
        response = HttpResponseRedirect(job_stats.with_referral(destination))
        response["X-Robots-Tag"] = "noindex, nofollow"
        response["Cache-Control"] = "no-store"
        response["Referrer-Policy"] = "origin"
        return response
