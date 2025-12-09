from datetime import timedelta

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.core.mail import send_mail
from django.db import models
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse_lazy
from django.utils import timezone
from django.views.generic import (
    CreateView,
    DeleteView,
    DetailView,
    FormView,
    ListView,
    TemplateView,
    UpdateView,
)

from jobs.models import Organization, UserProfile
from jobs.utils import unique_slug

from .forms import (
    GigApplicationForm,
    GigForm,
    GigInterestForm,
    PortfolioItemForm,
    ReviewForm,
    SubmissionForm,
    TrialFundingForm,
)
from .models import (
    FieldEvidence,
    Gig,
    GigApplication,
    PortfolioItem,
    Review,
    RubricCriterion,
    Submission,
    Trial,
)


class SeekerRequiredMixin(LoginRequiredMixin):
    def dispatch(self, request, *args, **kwargs):
        try:
            profile = request.user.profile
        except UserProfile.DoesNotExist:
            profile = None
        if not profile or profile.account_type != UserProfile.AccountType.SEEKER:
            messages.error(request, "Seeker account required.")
            return redirect("jobs:account")
        return super().dispatch(request, *args, **kwargs)


class EmployerRequiredMixin(LoginRequiredMixin):
    def dispatch(self, request, *args, **kwargs):
        try:
            profile = request.user.profile
        except UserProfile.DoesNotExist:
            profile = None
        if not profile or profile.account_type != UserProfile.AccountType.EMPLOYER:
            messages.error(request, "Employer account required.")
            return redirect("jobs:account")
        if not request.user.organizations.exists():
            messages.error(request, "Join or create an organization first.")
            return redirect("jobs:onboarding_employer")
        self.organization = request.user.organizations.first()
        return super().dispatch(request, *args, **kwargs)


class StaffRequiredMixin(UserPassesTestMixin):
    def test_func(self):
        return self.request.user.is_staff

    def handle_no_permission(self):
        messages.error(self.request, "Staff access only.")
        return redirect("jobs:home")


class GigListView(FormView):
    template_name = "gigs/gig_list.html"
    form_class = GigInterestForm
    success_url = reverse_lazy("gigs:gig_list")

    def form_valid(self, form):
        form.save()
        # HTMX will handle the response - return empty 200
        from django.http import HttpResponse
        return HttpResponse(status=200)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        if "form" not in context:
            context["form"] = self.get_form()
        return context


class GigDetailView(DetailView):
    model = Gig
    template_name = "gigs/gig_detail.html"
    context_object_name = "gig"

    def get_queryset(self):
        qs = Gig.objects.select_related("organization", "category").prefetch_related(
            "rubric"
        )
        user = self.request.user
        if user.is_staff:
            return qs
        return qs.filter(
            models.Q(status=Gig.Status.LIVE)
            | models.Q(organization__members=user)
        )

    def get_object(self, queryset=None):
        obj = super().get_object(queryset)
        try:
            profile = self.request.user.profile
        except UserProfile.DoesNotExist:
            profile = None
        if obj.remote_policy == Gig.RemotePolicy.COUNTRY:
            seeker_country = profile.country if profile else None
            if (
                profile
                and profile.account_type == UserProfile.AccountType.SEEKER
                and seeker_country
                and seeker_country.upper()
                not in [c.upper() for c in obj.eligible_countries]
            ):
                raise Http404()
        return obj

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        user = self.request.user
        can_view_full_brief = False
        if user.is_authenticated:
            if user.is_staff or self.object.organization.members.filter(id=user.id).exists():
                can_view_full_brief = True
            else:
                can_view_full_brief = (
                    GigApplication.objects.filter(
                        gig=self.object,
                        seeker=user,
                        status__in=[
                            GigApplication.Status.TRIAL_FUNDED,
                            GigApplication.Status.TRIAL_SUBMITTED,
                            GigApplication.Status.ACCEPTED,
                        ],
                    ).exists()
                )
        context["rubric"] = self.object.rubric.all()
        context["can_view_full_brief"] = can_view_full_brief
        return context


class GigCreateView(EmployerRequiredMixin, FormView):
    template_name = "gigs/gig_form.html"
    form_class = GigForm

    def get_success_url(self):
        return reverse_lazy("gigs:employer_dashboard")

    def form_valid(self, form):
        gig = form.save(commit=False)
        gig.organization = self.organization
        gig.slug = unique_slug(Gig, gig.title)

        # Trigger verification flow
        if (
            self.organization.verification_status
            == Organization.VerificationStatus.UNVERIFIED
        ):
            self.organization.verification_status = (
                Organization.VerificationStatus.PENDING
            )
            self.organization.save(update_fields=["verification_status"])

        if self.request.user.is_staff or self.organization.verification_status == Organization.VerificationStatus.VERIFIED:
            gig.status = Gig.Status.LIVE
            gig.published_at = timezone.now()
        else:
            gig.status = Gig.Status.PENDING
        gig.save()

        # Rubric creation
        gig.rubric.all().delete()
        RubricCriterion.objects.bulk_create(
            [
                RubricCriterion(
                    gig=gig, label=entry["label"], description=entry["description"]
                )
                for entry in getattr(form, "rubric_entries", [])
            ]
        )

        messages.success(
            self.request,
            "Gig saved. It will go live once reviewed." if gig.status != Gig.Status.LIVE else "Gig published.",
        )
        return super().form_valid(form)


class GigApplyView(SeekerRequiredMixin, FormView):
    template_name = "gigs/gig_apply.html"
    form_class = GigApplicationForm

    def dispatch(self, request, *args, **kwargs):
        self.gig = get_object_or_404(Gig, slug=kwargs["slug"])
        try:
            profile = request.user.profile
        except UserProfile.DoesNotExist:
            profile = None
        if self.gig.status != Gig.Status.LIVE:
            raise Http404()
        if not self.gig.visible_to_seeker(profile):
            raise Http404()
        return super().dispatch(request, *args, **kwargs)

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["seeker"] = self.request.user
        return kwargs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["gig"] = self.gig
        return context

    def form_valid(self, form):
        application, created = GigApplication.objects.get_or_create(
            gig=self.gig,
            seeker=self.request.user,
            defaults={"motivation": form.cleaned_data["motivation"]},
        )
        if not created:
            application.motivation = form.cleaned_data["motivation"]
        application.status = GigApplication.Status.SUBMITTED
        application.save()
        application.selected_portfolio_items.set(
            form.cleaned_data["selected_portfolio_items"]
        )

        Trial.objects.get_or_create(
            application=application,
            defaults={
                "fee_cents": self.gig.trial_fee_cents,
                "currency": self.gig.currency,
                "due_at": timezone.now() + timedelta(days=self.gig.trial_due_days),
            },
        )
        messages.success(self.request, "Application submitted.")
        return redirect("gigs:application_detail", pk=application.pk)


class PortfolioListView(SeekerRequiredMixin, ListView):
    model = PortfolioItem
    template_name = "gigs/portfolio_list.html"
    context_object_name = "items"

    def get_queryset(self):
        return PortfolioItem.objects.filter(seeker=self.request.user)


class PortfolioCreateView(SeekerRequiredMixin, CreateView):
    model = PortfolioItem
    form_class = PortfolioItemForm
    template_name = "gigs/portfolio_form.html"
    success_url = reverse_lazy("gigs:portfolio_list")

    def form_valid(self, form):
        form.instance.seeker = self.request.user
        messages.success(self.request, "Portfolio item created.")
        return super().form_valid(form)


class PortfolioUpdateView(SeekerRequiredMixin, UpdateView):
    model = PortfolioItem
    form_class = PortfolioItemForm
    template_name = "gigs/portfolio_form.html"
    success_url = reverse_lazy("gigs:portfolio_list")

    def get_queryset(self):
        return PortfolioItem.objects.filter(seeker=self.request.user)

    def form_valid(self, form):
        messages.success(self.request, "Portfolio item updated.")
        return super().form_valid(form)


class PortfolioDeleteView(SeekerRequiredMixin, DeleteView):
    model = PortfolioItem
    template_name = "gigs/portfolio_confirm_delete.html"
    success_url = reverse_lazy("gigs:portfolio_list")

    def get_queryset(self):
        return PortfolioItem.objects.filter(seeker=self.request.user)

    def delete(self, request, *args, **kwargs):
        messages.success(request, "Portfolio item deleted.")
        return super().delete(request, *args, **kwargs)


class ApplicationDetailView(SeekerRequiredMixin, FormView):
    template_name = "gigs/application_detail.html"
    form_class = SubmissionForm

    def dispatch(self, request, *args, **kwargs):
        self.application = get_object_or_404(
            GigApplication, pk=kwargs["pk"], seeker=request.user
        )
        self.trial = getattr(self.application, "trial", None)
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["application"] = self.application
        context["gig"] = self.application.gig
        context["trial"] = self.trial
        context["submission"] = (
            self.trial.submission if self.trial and self.trial.submission else None
        )
        return context

    def form_valid(self, form):
        if not self.trial or self.trial.funding_status != Trial.FundingStatus.FUNDED:
            messages.error(self.request, "Trial must be funded before submission.")
            return redirect("gigs:application_detail", pk=self.application.pk)

        if self.trial.submission:
            messages.error(self.request, "Submission already uploaded.")
            return redirect("gigs:application_detail", pk=self.application.pk)

        submission = Submission.objects.create(
            trial=self.trial,
            application=self.application,
            artifact_links=form.cleaned_data.get("artifact_links_text", []),
            artifact_files=form.cleaned_data.get("artifact_files"),
            notes=form.cleaned_data.get("notes", ""),
        )

        if (
            form.cleaned_data.get("geo_photos")
            or form.cleaned_data.get("receipts")
            or form.cleaned_data.get("call_logs")
            or form.cleaned_data.get("witness_contact")
        ):
            FieldEvidence.objects.create(
                submission=submission,
                geo_photos=form.cleaned_data.get("geo_photos"),
                receipts=form.cleaned_data.get("receipts"),
                call_logs=form.cleaned_data.get("call_logs"),
                witness_contact=form.cleaned_data.get("witness_contact", ""),
            )

        self.application.status = GigApplication.Status.TRIAL_SUBMITTED
        self.application.save(update_fields=["status", "updated_at"])

        employer_emails = list(
            self.application.gig.organization.members.values_list("email", flat=True)
        )
        if employer_emails:
            send_mail(
                subject="New trial submission received",
                message=f"{self.application.seeker.email} submitted a trial for {self.application.gig.title}.",
                from_email=None,
                recipient_list=employer_emails,
                fail_silently=True,
            )
        messages.success(self.request, "Submission uploaded.")
        return redirect("gigs:application_detail", pk=self.application.pk)


class EmployerDashboardView(EmployerRequiredMixin, TemplateView):
    template_name = "gigs/employer_dashboard.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["organization"] = self.organization
        context["gigs"] = self.organization.gigs.all().order_by("-created_at")
        context["pending_applications"] = GigApplication.objects.filter(
            gig__organization=self.organization
        )[:10]
        return context


class GigApplicationListView(EmployerRequiredMixin, TemplateView):
    template_name = "gigs/employer_gig_applications.html"

    def dispatch(self, request, *args, **kwargs):
        self.gig = get_object_or_404(
            Gig, pk=kwargs["pk"], organization__members=request.user
        )
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["gig"] = self.gig
        context["applications"] = self.gig.applications.select_related("seeker")
        return context


class EmployerApplicationReviewView(EmployerRequiredMixin, TemplateView):
    template_name = "gigs/employer_application_review.html"

    def dispatch(self, request, *args, **kwargs):
        self.application = get_object_or_404(
            GigApplication,
            pk=kwargs["pk"],
            gig__organization__members=request.user,
        )
        self.trial = getattr(self.application, "trial", None)
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["application"] = self.application
        context["gig"] = self.application.gig
        context["trial"] = self.trial
        context["funding_form"] = TrialFundingForm(instance=self.trial)
        context["review_form"] = ReviewForm(
            criteria=list(self.application.gig.rubric.all())
        )
        return context

    def post(self, request, *args, **kwargs):
        action = request.POST.get("action")
        if action == "fund":
            return self._handle_fund(request)
        if action == "review":
            return self._handle_review(request)
        messages.error(request, "Unknown action.")
        return redirect("gigs:employer_application", pk=self.application.pk)

    def _handle_fund(self, request):
        if not self.trial:
            self.trial = Trial.objects.create(
                application=self.application,
                fee_cents=self.application.gig.trial_fee_cents,
                currency=self.application.gig.currency,
                due_at=timezone.now() + timedelta(days=self.application.gig.trial_due_days),
            )
        form = TrialFundingForm(request.POST, instance=self.trial)
        if form.is_valid():
            trial = form.save(commit=False)
            trial.funding_status = Trial.FundingStatus.FUNDED
            trial.save(update_fields=["payment_reference", "funding_status"])

            self.application.status = GigApplication.Status.TRIAL_FUNDED
            self.application.save(update_fields=["status", "updated_at"])

            send_mail(
                subject=f"Trial funded for {self.application.gig.title}",
                message="Your trial has been funded. Full brief is now unlocked.",
                from_email=None,
                recipient_list=[self.application.seeker.email],
                fail_silently=True,
            )
            messages.success(request, "Marked as funded and seeker notified.")
        else:
            messages.error(request, "Funding reference required.")
        return redirect("gigs:employer_application", pk=self.application.pk)

    def _handle_review(self, request):
        form = ReviewForm(
            request.POST, criteria=list(self.application.gig.rubric.all())
        )
        if form.is_valid():
            Review.objects.create(
                application=self.application,
                reviewer=request.user,
                scores=form.cleaned_scores(),
                overall_comment=form.cleaned_data.get("overall_comment", ""),
                decision=form.cleaned_data["decision"],
            )
            if form.cleaned_data["decision"] == Review.Decision.PASS:
                self.application.status = GigApplication.Status.ACCEPTED
                if self.trial and self.trial.funding_status == Trial.FundingStatus.FUNDED:
                    self.trial.funding_status = Trial.FundingStatus.RELEASED
                    self.trial.released_at = timezone.now()
                    self.trial.save(update_fields=["funding_status", "released_at"])
            else:
                self.application.status = GigApplication.Status.REJECTED
            self.application.save(update_fields=["status", "updated_at"])
            messages.success(request, "Review saved.")
        else:
            messages.error(request, "Unable to save review.")
        return redirect("gigs:employer_application", pk=self.application.pk)


class StaffQueueView(StaffRequiredMixin, TemplateView):
    template_name = "gigs/staff_queue.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["orgs_pending"] = Organization.objects.filter(
            verification_status=Organization.VerificationStatus.PENDING
        )
        context["gigs_pending"] = Gig.objects.filter(status=Gig.Status.PENDING)
        return context

    def post(self, request, *args, **kwargs):
        action = request.POST.get("action")
        if action == "verify_org":
            org = get_object_or_404(Organization, pk=request.POST.get("org_id"))
            org.verification_status = Organization.VerificationStatus.VERIFIED
            org.save(update_fields=["verification_status"])
            messages.success(request, f"Organization {org.name} verified.")
        elif action == "reject_org":
            org = get_object_or_404(Organization, pk=request.POST.get("org_id"))
            org.verification_status = Organization.VerificationStatus.REJECTED
            org.save(update_fields=["verification_status"])
            messages.success(request, f"Organization {org.name} rejected.")
        elif action == "approve_gig":
            gig = get_object_or_404(Gig, pk=request.POST.get("gig_id"))
            gig.status = Gig.Status.LIVE
            gig.published_at = timezone.now()
            gig.save(update_fields=["status", "published_at"])
            messages.success(request, f"Gig {gig.title} approved.")
        elif action == "close_gig":
            gig = get_object_or_404(Gig, pk=request.POST.get("gig_id"))
            gig.status = Gig.Status.CLOSED
            gig.save(update_fields=["status"])
            messages.success(request, f"Gig {gig.title} closed.")
        else:
            messages.error(request, "Unknown action.")
        return redirect("gigs:staff_queue")
