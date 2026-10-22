import io

from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from django.views import View
from jobs.services.social_card import render_job_card

from jobs.models import Job


class JobOGImageView(View):
    """Generate a dynamic Open Graph image for a job posting."""

    def get(self, request, slug):
        job = get_object_or_404(Job.objects.select_related('organization'), slug=slug)
        img = render_job_card(job)
        buf = io.BytesIO()
        img.save(buf, format="PNG", optimize=True)
        buf.seek(0)

        response = HttpResponse(buf.getvalue(), content_type="image/png")
        response["Cache-Control"] = "public, max-age=3600, s-maxage=86400"
        return response
