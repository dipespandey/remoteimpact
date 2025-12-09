from django.db.models import Q
from django.shortcuts import get_object_or_404
from django.utils import timezone
from ..models import Job, Category, SavedJob, Organization


class JobService:
    @staticmethod
    def get_filtered_jobs(filters: dict):
        """
        Filter jobs based on query parameters.
        """
        jobs = Job.objects.filter(is_active=True).select_related(
            "organization", "category"
        )

        # Category
        if category_slug := filters.get("category"):
            jobs = jobs.filter(category__slug=category_slug)

        # Job Type
        if job_type := filters.get("type"):
            jobs = jobs.filter(job_type=job_type)

        # Organization
        if org_query := filters.get("organization"):
            jobs = jobs.filter(organization__name__icontains=org_query)

        # Location
        if country := filters.get("country"):
            jobs = jobs.filter(location__icontains=country)
        if city := filters.get("city"):
            jobs = jobs.filter(location__icontains=city)

        # Salary
        if salary_min := filters.get("salary_min"):
            try:
                val = float(salary_min)
                jobs = jobs.filter(Q(salary_min__gte=val) | Q(salary_max__gte=val))
            except (TypeError, ValueError):
                pass

        # Text search (description/requirements)
        if expr := filters.get("experience"):
            jobs = jobs.filter(
                Q(description__icontains=expr) | Q(requirements__icontains=expr)
            )
        if edu := filters.get("education"):
            jobs = jobs.filter(
                Q(description__icontains=edu) | Q(requirements__icontains=edu)
            )
        if skill := filters.get("skillset"):
            jobs = jobs.filter(
                Q(description__icontains=skill) | Q(requirements__icontains=skill)
            )

        # Core Search
        if query := filters.get("q"):
            jobs = jobs.filter(
                Q(title__icontains=query)
                | Q(description__icontains=query)
                | Q(requirements__icontains=query)
                | Q(organization__name__icontains=query)
            )

        return jobs.order_by("-is_featured", "-posted_at")

    @staticmethod
    def create_job(data: dict, user=None, organization=None) -> Job:
        """
        Create a job instance from form data.
        """
        # Logic extracted from post_job view
        job = Job(
            title=data.get("title"),
            organization=organization
            or Organization.objects.get_or_create(
                name=data.get("organization_name"),
                defaults={
                    "website": data.get("organization_website"),
                    "description": data.get("organization_description"),
                },
            )[0],
            category=data.get("category"),
            description=data.get("description"),
            requirements=data.get("requirements"),
            location=data.get("location"),
            job_type="full_time",  # Defaulting as per previous view logic, or could be passed
            salary_min=data.get("salary_min"),
            salary_max=data.get("salary_max"),
            salary_currency=data.get("salary_currency", "USD"),
            application_url=data.get("application_url"),
            application_email=data.get("application_email"),
            posted_at=timezone.now(),
        )

        # Handle raw data and extras
        raw_payload = {
            "internal_contact": data.get("contact_email"),
            "start_timeline": data.get("start_timeline"),
            "impact": data.get("impact"),  # was in textarea_placeholders
            "benefits": data.get("benefits"),
            "how_to_apply": data.get("how_to_apply"),
        }
        # Filter empty
        job.raw_data = {k: v for k, v in raw_payload.items() if v}

        if user and user.is_authenticated:
            job.poster = user

        # Default to inactive until paid (payment service handles activation)
        job.is_active = False
        job.is_paid = False

        job.save()
        return job

    @staticmethod
    def toggle_save_job(user, slug):
        job = get_object_or_404(Job, slug=slug)
        saved_job, created = SavedJob.objects.get_or_create(user=user, job=job)
        if not created:
            saved_job.delete()
            return False
        return True
