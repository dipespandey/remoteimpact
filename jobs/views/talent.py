"""Views for the Talent Directory / Marketplace."""

import logging

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.paginator import Paginator
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.views import View
from django.views.generic import ListView, DetailView, TemplateView

from ..services.employer_access import hiring_jobs
from ..models import (
    SeekerProfile,
    Category,
    Job,
    TalentInvitation,
    UserProfile,
    Organization,
)

logger = logging.getLogger(__name__)


class TalentDirectoryView(LoginRequiredMixin, ListView):
    """Talent directory — employer-only for now."""

    template_name = "jobs/talent/directory.html"
    context_object_name = "seekers"
    paginate_by = 20

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated:
            try:
                if request.user.profile.account_type != UserProfile.AccountType.EMPLOYER and not request.user.is_staff:
                    raise Http404
            except UserProfile.DoesNotExist:
                raise Http404
        return super().dispatch(request, *args, **kwargs)

    def get_queryset(self):
        qs = SeekerProfile.objects.filter(
            visibility="public",
            is_actively_looking=True,
            wizard_completed=True,
        ).select_related("user").prefetch_related("impact_areas").defer("embedding", "search_vector", "resume_text", "cv_facts")

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


class TalentProfileView(LoginRequiredMixin, DetailView):
    """Seeker profile page — employer-only for now."""

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated:
            try:
                if request.user.profile.account_type != UserProfile.AccountType.EMPLOYER and not request.user.is_staff:
                    raise Http404
            except UserProfile.DoesNotExist:
                raise Http404
        return super().dispatch(request, *args, **kwargs)

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
    """Invite a seeker to apply for one of your jobs.

    Public profiles can be invited to any of your active roles. People who chose
    "visible to matching orgs only" can be invited only to a paid posting, which is
    where they are suggested as a match.
    """

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return super().dispatch(request, *args, **kwargs)
        try:
            is_employer = request.user.profile.account_type == UserProfile.AccountType.EMPLOYER
        except UserProfile.DoesNotExist:
            is_employer = False
        has_paid_post = hiring_jobs(request.user).filter(is_paid=True).exists()
        if not (is_employer or has_paid_post or request.user.is_staff):
            raise Http404
        return super().dispatch(request, *args, **kwargs)

    @staticmethod
    def _seeker(user_id):
        return get_object_or_404(
            SeekerProfile.objects.select_related("user"),
            user_id=user_id,
            visibility__in=[SeekerProfile.Visibility.PUBLIC, SeekerProfile.Visibility.MATCHING],
            is_actively_looking=True,
            wizard_completed=True,
        )

    @staticmethod
    def _organization(user):
        org = user.organizations.first()
        if org:
            return org
        latest = hiring_jobs(user).select_related("organization").order_by("-posted_at").first()
        return latest.organization if latest else None

    @staticmethod
    def _jobs(user, seeker):
        jobs = hiring_jobs(user).filter(is_active=True)
        if seeker.visibility != SeekerProfile.Visibility.PUBLIC:
            jobs = jobs.filter(is_paid=True)
        return jobs.order_by("-posted_at")

    @staticmethod
    def _back(seeker, job=None):
        if seeker.visibility == SeekerProfile.Visibility.PUBLIC:
            return redirect("jobs:talent_profile", user_id=seeker.user_id)
        if job:
            return redirect("jobs:employer_shortlist", slug=job.slug)
        return redirect("jobs:account")

    def get(self, request, user_id):
        seeker = self._seeker(user_id)
        org = self._organization(request.user)
        if not org:
            messages.error(request, "You need an organization to invite candidates.")
            return self._back(seeker)
        existing = TalentInvitation.objects.filter(seeker=seeker, organization=org).values_list("job_id", flat=True)
        selected = request.GET.get("job", "")
        draft = ""
        suggestion_id = request.GET.get("suggestion", "")
        if suggestion_id.isdigit():
            from ..models import SourcingSuggestion
            found = SourcingSuggestion.objects.filter(
                pk=int(suggestion_id), seeker=seeker, job__in=self._jobs(request.user, seeker)).first()
            draft = found.draft_message if found else ""
        elif request.GET.get("draft") and selected.isdigit():
            job = self._jobs(request.user, seeker).filter(pk=int(selected)).select_related("organization").first()
            if job:
                from ..services.hiring_assistant import draft_invite, sourcing_evidence
                draft = draft_invite(job, sourcing_evidence(job, seeker))
        return render(request, "jobs/talent/invite.html", {
            "draft_message": draft,
            "seeker": seeker,
            "is_public": seeker.visibility == SeekerProfile.Visibility.PUBLIC,
            "organization": org,
            "active_jobs": self._jobs(request.user, seeker),
            "existing_invitations": set(existing),
            "selected_job_id": int(selected) if selected.isdigit() else None,
        })

    def post(self, request, user_id):
        seeker = self._seeker(user_id)
        org = self._organization(request.user)
        if not org:
            messages.error(request, "You need an organization to invite candidates.")
            return self._back(seeker)

        job_id = request.POST.get("job_id")
        message_text = request.POST.get("message", "").strip()
        if not job_id or not message_text:
            messages.error(request, "Please select a job and write a message.")
            return redirect("jobs:talent_invite", user_id=user_id)

        job = get_object_or_404(self._jobs(request.user, seeker), id=job_id)
        if TalentInvitation.objects.filter(job=job, seeker=seeker).exists():
            messages.warning(request, "You've already invited this candidate for this role.")
            return self._back(seeker, job)

        TalentInvitation.objects.create(organization=org, job=job, seeker=seeker, message=message_text)
        # The TalentInvitation post_save signal sends the (escaped, templated)
        # invitation email; sending here too emailed candidates twice.
        name = seeker.user.first_name if seeker.visibility == SeekerProfile.Visibility.PUBLIC else ""
        messages.success(request, f"Invitation sent{' to ' + name if name else ''} for {job.title}.")
        return self._back(seeker, job)
