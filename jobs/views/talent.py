"""Views for the Talent Directory / Marketplace."""

import logging

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.paginator import Paginator
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.views import View
from django.views.generic import ListView, DetailView, TemplateView

from ..models import (
    SeekerProfile,
    Category,
    Job,
    TalentInvitation,
    UserProfile,
    Organization,
)
from ..services.email_service import EmailService

logger = logging.getLogger(__name__)


class TalentDirectoryView(LoginRequiredMixin, ListView):
    """Talent directory — hidden from public for now (login required)."""

    template_name = "jobs/talent/directory.html"
    context_object_name = "seekers"
    paginate_by = 20

    def get_queryset(self):
        qs = SeekerProfile.objects.filter(
            visibility="public",
            is_actively_looking=True,
            wizard_completed=True,
        ).select_related("user").prefetch_related("impact_areas")

        # Filters
        impact_area = self.request.GET.get("impact_area")
        if impact_area:
            qs = qs.filter(impact_areas__slug=impact_area)

        experience = self.request.GET.get("experience")
        if experience:
            qs = qs.filter(experience_level=experience)

        work_style = self.request.GET.get("work_style")
        if work_style:
            from django.db.models import Q
            qs = qs.filter(Q(work_style=work_style) | Q(work_styles__contains=[work_style]))

        skill = self.request.GET.get("skill")
        if skill:
            qs = qs.filter(skills__contains=[skill])

        return qs.order_by("-updated_at")

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["categories"] = Category.objects.all().order_by("name")
        ctx["experience_choices"] = SeekerProfile.ExperienceLevel.choices
        ctx["work_style_choices"] = SeekerProfile.WorkStyle.choices
        ctx["current_filters"] = {
            "impact_area": self.request.GET.get("impact_area", ""),
            "experience": self.request.GET.get("experience", ""),
            "work_style": self.request.GET.get("work_style", ""),
            "skill": self.request.GET.get("skill", ""),
        }
        return ctx


class TalentProfileView(DetailView):
    """Public seeker profile page."""

    template_name = "jobs/talent/profile.html"
    context_object_name = "seeker"

    def get_object(self):
        user_id = self.kwargs["user_id"]
        seeker = get_object_or_404(
            SeekerProfile.objects.select_related("user").prefetch_related("impact_areas"),
            user_id=user_id,
            visibility="public",
            is_actively_looking=True,
            wizard_completed=True,
        )
        return seeker

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        seeker = self.object
        user = self.request.user

        ctx["is_org_member"] = False
        ctx["is_owner"] = False
        if user.is_authenticated:
            if user.id == seeker.user_id:
                ctx["is_owner"] = True
            if user.organizations.exists():
                ctx["is_org_member"] = True

        ws_dict = dict(SeekerProfile.WorkStyle.choices)
        if seeker.work_styles:
            ctx["work_style_labels"] = [ws_dict.get(ws, ws) for ws in seeker.work_styles]
        elif seeker.work_style:
            ctx["work_style_labels"] = [ws_dict.get(seeker.work_style, seeker.work_style)]
        else:
            ctx["work_style_labels"] = []

        exp_dict = dict(SeekerProfile.ExperienceLevel.choices)
        ctx["experience_label"] = exp_dict.get(seeker.experience_level, seeker.experience_level)

        rp_dict = dict(SeekerProfile.RemotePreference.choices)
        ctx["remote_label"] = rp_dict.get(seeker.remote_preference, seeker.remote_preference)

        return ctx


class TalentInviteView(LoginRequiredMixin, View):
    """Invite a seeker to apply for one of your jobs."""

    def get(self, request, user_id):
        seeker = get_object_or_404(
            SeekerProfile.objects.select_related("user"),
            user_id=user_id,
            visibility="public",
        )
        org = request.user.organizations.first()
        if not org:
            messages.error(request, "You need an organization to invite candidates.")
            return redirect("jobs:talent_profile", user_id=user_id)

        active_jobs = Job.objects.filter(
            poster=request.user, is_active=True
        ).order_by("-posted_at")

        existing = TalentInvitation.objects.filter(
            seeker=seeker, organization=org
        ).values_list("job_id", flat=True)

        return render(request, "jobs/talent/invite.html", {
            "seeker": seeker,
            "organization": org,
            "active_jobs": active_jobs,
            "existing_invitations": set(existing),
        })

    def post(self, request, user_id):
        seeker = get_object_or_404(
            SeekerProfile.objects.select_related("user"),
            user_id=user_id,
            visibility="public",
        )
        org = request.user.organizations.first()
        if not org:
            messages.error(request, "You need an organization to invite candidates.")
            return redirect("jobs:talent_profile", user_id=user_id)

        job_id = request.POST.get("job_id")
        message_text = request.POST.get("message", "").strip()

        if not job_id or not message_text:
            messages.error(request, "Please select a job and write a message.")
            return redirect("jobs:talent_invite", user_id=user_id)

        job = get_object_or_404(Job, id=job_id, poster=request.user, is_active=True)

        if TalentInvitation.objects.filter(job=job, seeker=seeker).exists():
            messages.warning(request, "You've already invited this candidate for this role.")
            return redirect("jobs:talent_profile", user_id=user_id)

        TalentInvitation.objects.create(
            organization=org,
            job=job,
            seeker=seeker,
            message=message_text,
        )

        # Send email
        try:
            email_service = EmailService()
            site_url = getattr(settings, "SITE_URL", "https://remoteimpact.org")
            job_url = f"{site_url}{job.get_absolute_url()}"
            subject = f"{org.name} thinks you'd be a great fit for {job.title}"
            html = f"""
            <div style="font-family: sans-serif; max-width: 600px; margin: 0 auto;">
                <h2 style="color: #1a202c;">You've been invited to apply!</h2>
                <p><strong>{org.name}</strong> thinks you'd be a great fit for <strong>{job.title}</strong>.</p>
                <div style="background: #f7fafc; padding: 16px; border-radius: 8px; margin: 16px 0;">
                    <p style="margin: 0; color: #4a5568;"><em>"{message_text}"</em></p>
                </div>
                <p><a href="{job_url}" style="display: inline-block; background: #8AD425; color: #1a202c; padding: 12px 24px; border-radius: 24px; text-decoration: none; font-weight: bold;">View Job &amp; Apply</a></p>
                <p style="color: #a0aec0; font-size: 12px; margin-top: 24px;">You received this because your profile is public on Remote Impact.</p>
            </div>
            """
            email_service.send_email(to=seeker.user.email, subject=subject, html=html)
        except Exception as e:
            logger.error(f"Failed to send invitation email: {e}")

        messages.success(request, f"Invitation sent to {seeker.user.first_name or 'candidate'} for {job.title}!")
        return redirect("jobs:talent_profile", user_id=user_id)
