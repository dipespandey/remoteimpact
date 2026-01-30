from django.views.generic import TemplateView
from django.contrib.auth.mixins import LoginRequiredMixin
from django.shortcuts import redirect
from django.conf import settings
from django.utils import timezone
from datetime import timedelta
from ..models import UserProfile, SeekerProfile, Referral, JobAlert, TalentInvitation
from ..services.onboarding_service import OnboardingService


class DashboardView(LoginRequiredMixin, TemplateView):
    template_name = "account/dashboard.html"

    def dispatch(self, request, *args, **kwargs):
        # Ensure onboarding is complete
        redirect_url = OnboardingService.get_redirect_for_state(request.user)
        if redirect_url:
            return redirect(redirect_url)
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        user = self.request.user

        # Referral info for all users
        referral = Referral.get_or_create_for_user(user)
        context["referral"] = referral
        context["referral_link"] = f"{settings.SITE_URL}/?ref={referral.code}"
        context["referral_count"] = referral.signups.count()

        # Job alerts count
        context["alert_count"] = JobAlert.objects.filter(user=user, is_active=True).count()

        try:
            profile = user.profile
        except UserProfile.DoesNotExist:
            return context

        if profile.account_type == UserProfile.AccountType.EMPLOYER:
            context["is_employer"] = True
            posted_jobs = list(user.posted_jobs.all().order_by("-posted_at"))
            context["posted_jobs"] = posted_jobs
            context["organization"] = user.organizations.first()

            # Top matching candidates for each active job (attached to job objects)
            try:
                from ..services.vector_search import search_candidates_for_job
                for job in posted_jobs:
                    if job.is_active and job.embedding is not None:
                        try:
                            results = search_candidates_for_job(job, limit=5)
                            job.top_candidates = [
                                {"seeker": s, "score": round(score * 100)}
                                for s, score, _, _, _ in results[:5]
                            ]
                        except Exception:
                            job.top_candidates = []
                    else:
                        job.top_candidates = []
            except (ImportError, Exception):
                for job in posted_jobs:
                    job.top_candidates = []

        elif profile.account_type == UserProfile.AccountType.SEEKER:
            context["is_seeker"] = True
            context["saved_jobs"] = user.saved_jobs.select_related(
                "job", "job__organization"
            )
            context["applications"] = user.applications.select_related(
                "job", "job__organization"
            ).order_by("-applied_at")
            context["profile"] = profile

            # Check for Impact Profile (SeekerProfile)
            try:
                seeker_profile = user.seeker_profile
                context["seeker_profile"] = seeker_profile
                context["has_impact_profile"] = seeker_profile.wizard_completed

                # Talent invitations count
                context["invitation_count"] = TalentInvitation.objects.filter(
                    seeker=seeker_profile
                ).count()
                context["new_invitation_count"] = TalentInvitation.objects.filter(
                    seeker=seeker_profile, status=TalentInvitation.Status.SENT
                ).count()

                # Recent invitations for dashboard display
                context["talent_invitations"] = TalentInvitation.objects.filter(
                    seeker=seeker_profile
                ).select_related("organization", "job").order_by("-sent_at")[:5]

            except SeekerProfile.DoesNotExist:
                context["has_impact_profile"] = False
                context["profile_views_week"] = 0
                context["invitation_count"] = 0
                context["new_invitation_count"] = 0

        return context
