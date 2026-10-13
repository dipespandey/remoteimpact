from django.views.generic import TemplateView, View
from django.contrib import messages
from django.contrib.auth import logout as auth_logout
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db import transaction
from django.db.models import Count, Q, Exists, OuterRef, Window, F
from django.db.models.functions import RowNumber
from django.shortcuts import redirect
from django.conf import settings
from django.utils import timezone
from datetime import timedelta
from ..models import (
    UserProfile,
    SeekerProfile,
    Referral,
    JobAlert,
    TalentInvitation,
    Organization,
    UserPath,
    NewsletterSubscriber,
    AssistantSubscription,
    Job,
)
from ..services.onboarding_service import OnboardingService
from .impact_wizard import profile_scout_url


class DashboardView(LoginRequiredMixin, TemplateView):
    template_name = "account/dashboard.html"

    def dispatch(self, request, *args, **kwargs):
        # LoginRequiredMixin must get the first chance to reject anonymous
        # requests before onboarding touches the profile relation.
        if not request.user.is_authenticated:
            return super().dispatch(request, *args, **kwargs)

        # Ensure onboarding is complete
        redirect_url = OnboardingService.get_redirect_for_state(request.user)
        if redirect_url:
            return redirect(redirect_url)
        return super().dispatch(request, *args, **kwargs)

    @staticmethod
    def _plan_context(organization):
        """Current organization plan, or the one that fits how many roles they have live."""
        from ..models import OrgSubscription
        from ..services import org_plans
        from ..services.job_service import JobService

        if organization is None:
            return {}
        subscription = OrgSubscription.objects.filter(organization=organization).first()
        live = JobService.get_available_jobs().filter(organization=organization).count()
        current = subscription if org_plans.is_active(subscription) else None
        tier = org_plans.recommended_tier(live)
        recommended = org_plans.PLANS.get(tier)
        return {
            "plan_current": current, "plan_current_info": org_plans.PLANS.get(current.tier) if current else None,
            "plan_live_roles": live, "plan_tier": tier, "plan_info": recommended,
            "plan_individual_cost": org_plans.individual_cost_cents(min(live, recommended["role_cap"])) // 100 if recommended else None,
            "plan_price": recommended["price_cents"] // 100 if recommended else None,
        }

    @staticmethod
    def _hiring_overview(posted_jobs):
        """Live roles with their numbers and best-matching people; drafts and closed roles apart."""
        from ..services import job_stats
        from ..services.candidate_suggestions import suggested_candidates_for_jobs
        from ..services.shortlists import visible_interests_for_jobs
        from ..models import Application, SourcingSuggestion
        from ..services.hiring_assistant import listing_checks
        from ..services.unified_matching_service import UnifiedMatchingService

        now = timezone.now()
        live = [j for j in posted_jobs if j.is_paid and j.is_active and j.is_available]
        drafts = [j for j in posted_jobs if not j.is_paid]
        closed = [j for j in posted_jobs if j.is_paid and j not in live]
        stats = job_stats.totals([j.pk for j in live], days=30)
        application_counts = {row["job_id"]: row for row in Application.objects.filter(
            job_id__in=[j.pk for j in posted_jobs]).values("job_id").annotate(
                total=Count("pk"), screened=Count("pk", filter=~Q(screening={}))) }
        interest_counts = dict(visible_interests_for_jobs([j.pk for j in live])
                               .values("job_id").annotate(total=Count("pk")).values_list("job_id", "total"))
        from ..models import TalentInvitation
        already_invited = TalentInvitation.objects.filter(job_id=OuterRef("job_id"), seeker_id=OuterRef("seeker_id"))
        sourced = SourcingSuggestion.objects.filter(job_id__in=[j.pk for j in live]).annotate(
            _invited=Exists(already_invited)).filter(_invited=False).annotate(
                _position=Window(RowNumber(), partition_by=[F("job_id")], order_by=[F("created_at").desc(), F("pk").desc()])
            ).filter(_position__lte=4).select_related("seeker", "seeker__user").defer(
                "draft_message", "seeker__embedding", "seeker__resume_text", "seeker__cv_facts")
        sourced_by_job = {}
        for suggestion in sourced:
            sourced_by_job.setdefault(suggestion.job_id, []).append(suggestion)
        try:
            candidate_suggestions = suggested_candidates_for_jobs([j for j in live if j.pk not in sourced_by_job], limit=4)
        except Exception:
            import logging
            logging.getLogger(__name__).exception("Could not load candidate suggestions")
            candidate_suggestions = {}
        for job in live:
            row = stats.get(job.pk) or {}
            job.views_30d = row.get("views") or 0
            job.apply_clicks_30d = row.get("apply_clicks") or 0
            job.applicant_count = application_counts.get(job.pk, {}).get("total", 0)
            job.interested_count = interest_counts.get(job.pk, 0)
            job.featured_now = bool(job.featured_until and job.featured_until > now)
            job.tips = listing_checks(job)
            job.screened_count = application_counts.get(job.pk, {}).get("screened", 0)
            job.sourced = sourced_by_job.get(job.pk, [])
            try:
                job.matching_data_quality = UnifiedMatchingService._job_data_quality(job)
                job.suggestions = candidate_suggestions.get(job.pk, [])
            except Exception:  # noqa: BLE001 - matching must never break the dashboard
                job.matching_data_quality, job.suggestions = 0, []
        return {
            "live_jobs": live,
            "draft_jobs": drafts,
            "closed_jobs": closed,
            "active_jobs_count": len(live),
            "total_candidates": sum(application_counts.get(j.pk, {}).get("total", 0) for j in live + closed),
            "interested_total": sum(j.interested_count for j in live),
            "views_total": sum(j.views_30d for j in live),
            "apply_clicks_total": sum(j.apply_clicks_30d for j in live),
        }

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        user = self.request.user
        context["delete_account_identifier"] = user.email or user.username

        # Referral info for all users
        referral = Referral.get_or_create_for_user(user)
        context["referral"] = referral
        context["referral_link"] = f"{settings.SITE_URL}/?ref={referral.code}"
        context["referral_count"] = referral.signups.count()

        # Job alerts count
        context["alert_count"] = JobAlert.objects.filter(user=user, is_active=True).count()
        context["pro_subscription"] = AssistantSubscription.get_or_create_for_user(user)

        try:
            profile = user.profile
        except UserProfile.DoesNotExist:
            return context

        if profile.account_type == UserProfile.AccountType.EMPLOYER:
            context["is_employer"] = True
            # Their own posts and drafts, plus roles colleagues at their organization paid for.
            from ..services.employer_access import hiring_jobs

            posted_jobs = list(
                hiring_jobs(user).select_related("organization", "poster", "category")
                .defer("search_vector").order_by("-posted_at")
            )
            context["posted_jobs"] = posted_jobs
            context["organization"] = user.organizations.first()
            context.update(self._hiring_overview(posted_jobs))
            context.update(self._plan_context(context["organization"]))

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
                context["profile_scout_url"] = profile_scout_url(seeker_profile)
                context["show_profile_scout_cta"] = (
                    seeker_profile.wizard_completed
                    and not context["pro_subscription"].is_pro_active()
                    and context["pro_subscription"].free_job_scout_used_at is None
                )
                context["show_profile_pro_cta"] = (
                    seeker_profile.wizard_completed
                    and not context["pro_subscription"].is_pro_active()
                    and context["pro_subscription"].free_job_scout_used_at is not None
                )

                # Talent invitations count
                counts = TalentInvitation.objects.filter(seeker=seeker_profile).aggregate(
                    total=Count("pk"), new=Count("pk", filter=Q(status=TalentInvitation.Status.SENT)))
                context["invitation_count"] = counts["total"]
                context["new_invitation_count"] = counts["new"]

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


class AccountDeleteView(LoginRequiredMixin, View):
    """Permanently delete the current user's account and owned user data."""

    def post(self, request, *args, **kwargs):
        user = request.user
        identifier = user.email or user.username
        confirmation = request.POST.get("confirmation", "").strip()

        if confirmation != identifier:
            messages.error(
                request,
                f"Type {identifier} exactly to confirm account deletion.",
            )
            return redirect("jobs:account")

        user_id = user.id
        user_email = user.email
        signup_org_ids = list(
            user.organizations.filter(
                source__in=[
                    Organization.OrganizationSource.SIGNUP,
                    Organization.OrganizationSource.CLAIMED,
                ]
            )
            .annotate(member_count=Count("members"))
            .filter(member_count=1)
            .values_list("id", flat=True)
        )

        with transaction.atomic():
            if user_email:
                NewsletterSubscriber.objects.filter(email__iexact=user_email).delete()
            UserPath.objects.filter(converted_user_id=user_id).delete()
            user.delete()
            if signup_org_ids:
                Organization.objects.filter(
                    id__in=signup_org_ids,
                    members__isnull=True,
                ).delete()

        auth_logout(request)
        messages.success(request, "Your account and related data have been deleted.")
        return redirect("jobs:home")


class UploadCVView(LoginRequiredMixin, View):
    """Upload or replace a seeker's CV; it's read in the background (see signals.read_cv_on_upload)."""

    MAX_BYTES = 5 * 1024 * 1024

    def post(self, request):
        import os

        upload = request.FILES.get("cv")
        if not upload:
            messages.error(request, "Choose a PDF or Word file to upload.")
            return redirect("jobs:account")
        if upload.size > self.MAX_BYTES or os.path.splitext(upload.name)[1].lower() not in (".pdf", ".docx"):
            messages.error(request, "Please upload a PDF or Word (.docx) file under 5 MB.")
            return redirect("jobs:account")
        profile = SeekerProfile.objects.filter(user=request.user).first()
        if profile is None:
            messages.error(request, "Set up your profile first, then add your CV.")
            return redirect("jobs:account")
        profile.resume = upload
        profile.cv_read_at = None
        profile.save()
        messages.success(request, "Thanks. We're reading your CV now; refresh in a minute to see what we found.")
        return redirect("jobs:account")


class RemoveCVView(LoginRequiredMixin, View):
    def post(self, request):
        profile = SeekerProfile.objects.filter(user=request.user).first()
        if profile and profile.resume:
            profile.resume.delete(save=False)
            profile.resume, profile.resume_text, profile.cv_facts = None, "", {}
            profile.cv_read_at, profile.cv_source = None, ""
            profile.save()  # re-embeds the profile without the CV
            messages.success(request, "Your CV and everything we read from it have been removed.")
        return redirect("jobs:account")
