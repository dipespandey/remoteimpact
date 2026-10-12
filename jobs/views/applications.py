from django.views.generic import FormView, ListView, DetailView
from django.contrib.auth.mixins import LoginRequiredMixin
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.contrib import messages
from django.core.mail import send_mail
from django.conf import settings
from django.db import IntegrityError

from ..models import Job, Application, CoverLetter, SeekerProfile
from ..forms import ApplicationForm


def _screen_in_background(application_id):
    import threading

    from django.db import close_old_connections, transaction

    def run():
        close_old_connections()
        try:
            from ..services.hiring_assistant import screen_and_save
            app = Application.objects.select_related("job", "job__organization", "applicant").get(pk=application_id)
            screen_and_save(app)
        finally:
            close_old_connections()

    transaction.on_commit(lambda: threading.Thread(target=run, daemon=True).start())


class ApplicationCreateView(LoginRequiredMixin, FormView):
    """On-platform job application form."""
    template_name = "jobs/apply.html"
    form_class = ApplicationForm

    def dispatch(self, request, *args, **kwargs):
        self.job = get_object_or_404(Job, slug=kwargs["slug"], is_active=True)
        # Check duplicate
        if request.user.is_authenticated:
            if Application.objects.filter(job=self.job, applicant=request.user).exists():
                messages.info(request, "You have already applied to this position.")
                return redirect(self.job.get_absolute_url())
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["job"] = self.job
        ctx["custom_questions"] = self.job.custom_questions or []
        return ctx

    def get_initial(self):
        initial = super().get_initial()
        u = self.request.user
        full_name = " ".join(p for p in [u.first_name, u.last_name] if p).strip()
        initial.update({
            "full_name": full_name or u.username,
            "email": u.email,
        })
        try:
            sp = u.seeker_profile
            if getattr(sp, "location", ""):
                initial["current_location"] = sp.location
        except SeekerProfile.DoesNotExist:
            pass
        # Pre-fill cover letter from CoverLetter model
        cl = CoverLetter.objects.filter(
            seeker__user=u, job=self.job
        ).order_by("-created_at").first()
        if cl:
            initial["cover_letter"] = cl.final_text or cl.generated_text
        return initial

    def get_form(self, form_class=None):
        form = super().get_form(form_class)
        # Show existing resume info
        try:
            sp = self.request.user.seeker_profile
            if sp.resume:
                form.fields["resume"].help_text = (
                    f"Your saved resume: {sp.resume.name.split('/')[-1]}. "
                    "Upload a new one or leave blank to use your saved resume."
                )
        except SeekerProfile.DoesNotExist:
            pass
        return form

    def form_valid(self, form):
        app = form.save(commit=False)
        app.job = self.job
        app.applicant = self.request.user
        custom_answers = []
        for index, question in enumerate(self.job.custom_questions or []):
            answer = (self.request.POST.get(f"custom_question_{index}") or "").strip()
            custom_answers.append({"question": question, "answer": answer})
        app.custom_question_answers = custom_answers

        # Fall back to seeker profile resume if none uploaded
        if not app.resume:
            try:
                sp = self.request.user.seeker_profile
                if sp.resume:
                    app.resume = sp.resume
            except SeekerProfile.DoesNotExist:
                pass

        try:
            app.save()
        except IntegrityError:
            messages.info(self.request, "You have already applied to this position.")
            return redirect(self.job.get_absolute_url())

        # Hiring assistant: read the application against the role's must-haves in the background.
        if self.job.is_paid:
            _screen_in_background(app.pk)

        # Save resume to seeker profile if they uploaded a new one and don't have one
        if form.cleaned_data.get("resume"):
            try:
                sp = self.request.user.seeker_profile
                if not sp.resume:
                    sp.resume = form.cleaned_data["resume"]
                    sp.save(update_fields=["resume"])
            except SeekerProfile.DoesNotExist:
                pass

        # Send confirmation email to applicant
        try:
            send_mail(
                subject=f"Application confirmed: {self.job.title}",
                message=(
                    f"Hi {self.request.user.first_name or self.request.user.email},\n\n"
                    f"Your application for \"{self.job.title}\" at {self.job.organization.name} "
                    f"has been submitted successfully.\n\n"
                    f"You can track your applications at: {settings.SITE_URL}/applications/\n\n"
                    f"Good luck!\nRemote Impact"
                ),
                from_email=settings.DEFAULT_FROM_EMAIL,
                recipient_list=[self.request.user.email],
                fail_silently=True,
            )
        except Exception:
            pass

        # Notify org members
        try:
            org = self.job.organization
            org_emails = list(org.members.values_list("email", flat=True))
            if self.job.poster and self.job.poster.email:
                org_emails.append(self.job.poster.email)
            org_emails = list(set(e for e in org_emails if e))
            if org_emails:
                send_mail(
                    subject=f"New application: {self.job.title}",
                    message=(
                        f"A new application has been received for \"{self.job.title}\" on Remote Impact.\n\n"
                        f"Applicant: {app.full_name or self.request.user.get_full_name()} <{app.email or self.request.user.email}>\n"
                        f"See the application, CV and cover letter (sign in with this email address):\n"
                        f"{settings.SITE_URL}{reverse('jobs:employer_job_applications', kwargs={'slug': self.job.slug})}\n"
                    ),
                    from_email=settings.DEFAULT_FROM_EMAIL,
                    recipient_list=org_emails,
                    fail_silently=True,
                )
        except Exception:
            pass

        messages.success(self.request, f"Application submitted for \"{self.job.title}\"!")
        return redirect("jobs:my_applications")


class MyApplicationsView(LoginRequiredMixin, ListView):
    """Seeker application tracking dashboard."""
    template_name = "jobs/my_applications.html"
    context_object_name = "applications"
    paginate_by = 20

    def get_queryset(self):
        return Application.objects.filter(
            applicant=self.request.user
        ).select_related("job", "job__organization", "job__category").order_by("-applied_at")


class EmployerJobApplicationsView(LoginRequiredMixin, ListView):
    """Employer view of applicants for a specific job."""
    template_name = "jobs/employer_applications.html"
    context_object_name = "applications"
    paginate_by = 20

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return self.handle_no_permission()
        self.job = get_object_or_404(Job, slug=kwargs["slug"])
        # Allow superusers, the job's poster, or any org member.
        user = request.user
        is_authorized = (
            user.is_superuser
            or (self.job.poster_id and self.job.poster_id == user.pk)
            or self.job.organization.members.filter(pk=user.pk).exists()
        )
        if not is_authorized:
            messages.error(request, "You don't have permission to view these applications.")
            return redirect("jobs:account")
        return super().dispatch(request, *args, **kwargs)

    def get_queryset(self):
        return Application.objects.filter(
            job=self.job
        ).select_related("applicant", "applicant__profile").order_by("-applied_at")

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["job"] = self.job
        return ctx


def _send_private_file(field, download_name):
    """Stream an uploaded file from storage. Never public: callers check who may see it."""
    import mimetypes
    import os

    from django.http import FileResponse, Http404

    if not field:
        raise Http404
    try:
        handle = field.open("rb")
    except (FileNotFoundError, OSError):
        raise Http404("This file is no longer available.")
    content_type = mimetypes.guess_type(field.name)[0] or "application/octet-stream"
    response = FileResponse(handle, content_type=content_type, as_attachment=False,
                            filename=download_name + os.path.splitext(field.name)[1].lower())
    response["Cache-Control"] = "private, no-store"
    response["X-Robots-Tag"] = "noindex, nofollow"
    response["X-Content-Type-Options"] = "nosniff"
    return response


def application_cv(request, pk):
    """An applicant's CV: only for people hiring for that role, the applicant, and staff."""
    from django.contrib.auth.views import redirect_to_login
    from django.http import Http404

    if not request.user.is_authenticated:
        return redirect_to_login(request.get_full_path())
    app = get_object_or_404(Application.objects.select_related("job", "applicant"), pk=pk)
    from ..services.employer_access import hiring_jobs
    allowed = (request.user.is_staff or app.applicant_id == request.user.pk
               or hiring_jobs(request.user).filter(pk=app.job_id, is_paid=True).exists()
               or (app.job.poster_id == request.user.pk))
    if not allowed:
        raise Http404
    name = (app.full_name or "applicant").strip().replace(" ", "-")[:60] or "applicant"
    return _send_private_file(app.resume, f"CV-{name}")


def my_cv(request):
    """The signed-in seeker's own uploaded CV."""
    from django.contrib.auth.views import redirect_to_login

    if not request.user.is_authenticated:
        return redirect_to_login(request.get_full_path())
    profile = get_object_or_404(SeekerProfile, user=request.user)
    return _send_private_file(profile.resume, "my-CV")
