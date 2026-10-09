import re

from django.db import connection
import math
from datetime import timedelta

from django.db.models import Q, F, Value, Case, When, FloatField, IntegerField
from django.db.models.functions import Greatest, Coalesce
from django.shortcuts import get_object_or_404
from django.utils import timezone
from pgvector.django import CosineDistance
from ..models import Job, Category, SavedJob, Organization
from ..utils import unique_slug


class JobService:
    PUBLIC_STALE_DAYS = 90

    @staticmethod
    def get_available_jobs(stale_days=None):
        """Return the shared publishable set; a source sighting is not verification."""
        return Job.objects.filter(Job.availability_filter(stale_days=stale_days))

    @staticmethod
    def get_filtered_jobs(filters):
        """
        Filter jobs based on query parameters.
        Supports both single values and multiple values (multiselect).

        Args:
            filters: QueryDict or dict-like object with filter parameters
        """
        now = timezone.now()
        jobs = JobService.get_available_jobs().select_related(
            "organization", "category"
        )

        # Category - support multiple selections
        # Use getlist if available (QueryDict), otherwise get single value
        if hasattr(filters, "getlist"):
            category_slugs = filters.getlist("category")
        else:
            category_slugs = [filters.get("category")] if filters.get("category") else []
        category_slugs = [c for c in category_slugs if c]  # Remove empty values
        if category_slugs:
            jobs = jobs.filter(category__slug__in=category_slugs)

        # Job Type - support multiple selections
        if hasattr(filters, "getlist"):
            job_types = filters.getlist("type")
        else:
            job_types = [filters.get("type")] if filters.get("type") else []
        job_types = [t for t in job_types if t]
        if job_types:
            jobs = jobs.filter(job_type__in=job_types)

        # Application path - direct applications are jobs posted on Remote Impact.
        if filters.get("direct_apply"):
            jobs = jobs.filter(source=Job.Source.MANUAL)

        # Organization - support multiple selections
        if hasattr(filters, "getlist"):
            org_queries = filters.getlist("organization")
        else:
            org_queries = [filters.get("organization")] if filters.get("organization") else []
        org_queries = [o for o in org_queries if o]
        if org_queries:
            org_q = Q()
            for org in org_queries:
                org_q |= Q(organization__name__icontains=org)
            jobs = jobs.filter(org_q)

        # Country - support multiple selections (uses new country field + location fallback)
        if hasattr(filters, "getlist"):
            countries = filters.getlist("country")
        else:
            countries = [filters.get("country")] if filters.get("country") else []
        countries = [c for c in countries if c]
        if countries:
            from .location_normalizer import country_filter_aliases

            country_q = Q()
            for country in countries:
                for alias in country_filter_aliases(country):
                    country_q |= Q(country__iexact=alias)
                    # Two-letter codes such as "in" and "at" are ordinary
                    # words too; use those only against the structured field.
                    if len(alias) > 2 or alias.lower() in {"us", "uk"}:
                        country_q |= Q(location__iregex=rf"(^|[^a-z0-9_]){re.escape(alias)}([^a-z0-9_]|$)")
            jobs = jobs.filter(country_q)

        # "Has salary listed" filter - only show jobs with actual salary data
        if filters.get("has_salary"):
            jobs = jobs.filter(
                Q(salary_min__isnull=False, salary_min__gt=0) |
                Q(salary_max__isnull=False, salary_max__gt=0)
            )

        # Salary range filter - a job matches if its salary range OVERLAPS with the user's filter range
        # Overlap logic: job_max >= filter_min AND job_min <= filter_max
        filter_salary_min = filters.get("salary_min")
        filter_salary_max = filters.get("salary_max")

        if filter_salary_min:
            try:
                val = float(filter_salary_min)
                # Job's upper bound must be >= user's lower bound (for overlap)
                # Use salary_max if available, otherwise fall back to salary_min
                if math.isfinite(val) and val > 0:
                    jobs = JobService._annual_salary_jobs(jobs).filter(salary_currency="USD").filter(
                        Q(salary_max__gte=val) |
                        Q(salary_max__isnull=True, salary_min__gte=val)
                    )
            except (TypeError, ValueError):
                pass

        if filter_salary_max:
            try:
                val = float(filter_salary_max)
                # Job's lower bound must be <= user's upper bound (for overlap)
                # Use salary_min if available, otherwise fall back to salary_max
                # The slider's upper endpoint is labelled "$1M+", not a cap.
                if math.isfinite(val) and 0 < val < 1000000:
                    jobs = JobService._annual_salary_jobs(jobs).filter(salary_currency="USD").filter(
                        Q(salary_min__lte=val) |
                        Q(salary_min__isnull=True, salary_max__lte=val)
                    )
            except (TypeError, ValueError):
                pass

        # Experience level - use new field with multiselect support
        if hasattr(filters, "getlist"):
            experience_levels = filters.getlist("experience")
        else:
            experience_levels = [filters.get("experience")] if filters.get("experience") else []
        experience_levels = [e for e in experience_levels if e]
        if experience_levels:
            # Map display values to field values
            level_map = {
                "Entry Level": "entry",
                "Mid Level": "mid",
                "Senior": "senior",
                "Executive": "executive",
                "Internship": "internship",
            }
            mapped_levels = [level_map.get(e, e.lower()) for e in experience_levels]
            jobs = jobs.filter(experience_level__in=mapped_levels)

        # Education level - use new field with multiselect support
        if hasattr(filters, "getlist"):
            education_levels = filters.getlist("education")
        else:
            education_levels = [filters.get("education")] if filters.get("education") else []
        education_levels = [e for e in education_levels if e]
        if education_levels:
            # Map display values to field values
            edu_map = {
                "High School": "high_school",
                "Associate": "associate",
                "Bachelor's": "bachelor",
                "Master's": "master",
                "PhD": "phd",
            }
            mapped_edu = [edu_map.get(e, e.lower()) for e in education_levels]
            jobs = jobs.filter(education_level__in=mapped_edu)

        # Skills filter - filter jobs that have ANY of the selected skills
        if hasattr(filters, "getlist"):
            skills = filters.getlist("skill")
        else:
            skills = [filters.get("skill")] if filters.get("skill") else []
        skills = [s for s in skills if s]
        if skills:
            skill_q = Q()
            for skill in skills:
                skill_q |= Q(skills__contains=[skill])
            jobs = jobs.filter(skill_q)

        # Skill search (text-based, keep backward compatibility)
        if skill := filters.get("skillset"):
            jobs = jobs.filter(
                Q(description__icontains=skill) | Q(requirements__icontains=skill)
            )

        # Date posted filter
        if posted := filters.get("posted"):
            try:
                days = int(posted)
                if days == 29:
                    # "More than 4 weeks"
                    jobs = jobs.filter(posted_at__lt=now - timedelta(days=28))
                elif days == 7:
                    # "Less than 1 week"
                    jobs = jobs.filter(posted_at__gte=now - timedelta(days=7))
                elif days == 14:
                    # "1-2 weeks"
                    jobs = jobs.filter(
                        posted_at__gte=now - timedelta(days=14),
                        posted_at__lt=now - timedelta(days=7),
                    )
                elif days == 28:
                    # "2-4 weeks"
                    jobs = jobs.filter(
                        posted_at__gte=now - timedelta(days=28),
                        posted_at__lt=now - timedelta(days=14),
                    )
            except (TypeError, ValueError):
                pass

        # Enhanced Search with ranking
        query = filters.get("q")
        sort = filters.get("sort", "")
        
        if query:
            query = query.strip()
            jobs = JobService._apply_smart_search(jobs, query, sort=sort)
            # Return early - ordering is handled by search
            return jobs

        # The public feed is a chronological feed. Personalization belongs on
        # My Matches, and featured status must not make stale roles look new.
        return JobService._order_jobs(jobs, sort, interleave=True)

    @staticmethod
    def _annual_salary_jobs(jobs):
        from django.db.models.fields.json import KeyTextTransform
        from django.db.models import Value, TextField
        return jobs.annotate(
            _source_pay_period=Coalesce(KeyTextTransform('salaryPeriod','raw_data'),Value('YEAR'),output_field=TextField()),
            _stored_pay_period=Coalesce(KeyTextTransform('salary_period','raw_data'),Value('year'),output_field=TextField()),
        ).exclude(_source_pay_period__in=['HOUR','DAY','WEEK','hour','day','week']).exclude(_stored_pay_period__in=['hour','day','week'])

    @staticmethod
    def _order_jobs(queryset, sort, interleave=False):
        if sort == "salary-high":
            return queryset.order_by(
                Coalesce("salary_max", "salary_min").desc(nulls_last=True),
                "-posted_at", "-id",
            )
        if sort == "salary-low":
            return queryset.order_by(
                Coalesce("salary_min", "salary_max").asc(nulls_last=True),
                "-posted_at", "-id",
            )
        if not interleave:
            return queryset.order_by("-posted_at", "-id")
        # Browsing: newest first, but within a day interleave organizations (each org's newest role,
        # then each org's second, ...) so one employer posting 40 roles at once doesn't
        # bury everyone else. Days stay in order, so "Newest" still means newest.
        from django.db.models import F, Window
        from django.db.models.functions import RowNumber, TruncDate
        return queryset.annotate(
            _day=TruncDate("posted_at"),
            _org_turn=Window(
                expression=RowNumber(),
                partition_by=[F("organization_id"), TruncDate("posted_at")],
                order_by=[F("posted_at").desc(), F("id").desc()],
            ),
        ).order_by("-_day", "_org_turn", "-posted_at", "-id")

    MIN_STRONG_SEARCH_RESULTS = 10
    SOFT_SKILL_STEMS = {
        "communication", "teamwork", "leadership", "collaboration", "presentation", "writing",
        "problem-solving", "organization", "organisation", "time-management", "negotiation",
        "stakeholder", "interpersonal", "adaptability", "creativity", "attention",
    }
    SEARCH_NOISE_WORDS = {
        "remote", "job", "jobs", "role", "roles", "position", "positions", "opportunity",
        "opportunities", "career", "careers", "hiring", "vacancy", "vacancies", "work", "openings",
    }
    SEARCH_SYNONYMS = {
        "ai": ["ai", "artificial intelligence"],
        "ml": ["ml", "machine learning"],
        "intern": ["intern", "internship"],
        "comms": ["comms", "communications"],
        "ops": ["ops", "operations"],
        "m&e": ["m&e", "monitoring and evaluation", "mel", "meal"],
        "mel": ["mel", "monitoring, evaluation", "monitoring and evaluation"],
        "dev": ["dev", "developer", "development"],
        "pm": ["pm", "project manager", "product manager", "program manager", "programme manager"],
        "program": ["program", "programme"],
        "programme": ["programme", "program"],
        "hr": ["hr", "human resources", "people"],
        "esg": ["esg", "environmental, social"],
    }

    @staticmethod
    def _word_q(field, text):
        """Whole-word, case-insensitive match (plural allowed), with a few synonyms.

        Plain substring matching made "AI" match "Maintenance" and "intern"
        match "International".
        """
        variants = JobService.SEARCH_SYNONYMS.get(text.lower(), [text])
        condition = Q()
        for variant in variants:
            words = [re.escape(part) for part in variant.split()]
            body = r"[^A-Za-z0-9]+".join(words)
            pattern = rf"(^|[^A-Za-z0-9]){body}(s|es)?([^A-Za-z0-9]|$)"
            condition |= Q(**{f"{field}__iregex": pattern})
        return condition

    @staticmethod
    def _apply_smart_search(queryset, query, sort=""):
        """
        Precise search using PostgreSQL full-text search plus title intent.
        Uses raw SQL for ts_rank because Django's SearchRank has a bug
        with SearchVectorField (converts tsvector to text, losing weights).
        
        Args:
            queryset: Base queryset to search
            query: Search query string
            sort: Sort order - "relevance" for FTS rank, otherwise newest first
        """
        from django.db.models.expressions import RawSQL

        all_words = [
            word
            for word in re.findall(r"(?i)\bm&e\b|[A-Za-z0-9][A-Za-z0-9+#.:-]*", query)
            if len(word) > 1
        ]
        # Every listing is remote; words like "remote" or "jobs" carry no intent.
        query_words = [w for w in all_words if w.lower() not in JobService.SEARCH_NOISE_WORDS] or all_words
        phrase = " ".join(query_words) or query
        title_phrase_q = JobService._word_q("title", phrase)
        title_all_words_q = title_phrase_q
        if query_words:
            title_all_words_q = Q()
            for word in query_words:
                title_all_words_q &= JobService._word_q("title", word)
        organization_q = (
            JobService._word_q("organization__name", query.strip()) if len(query.strip()) >= 3 else Q(pk__in=[])
        )

        # Annotate with proper FTS rank using raw SQL and title buckets.
        queryset = queryset.annotate(
            fts_rank=RawSQL(
                "ts_rank(search_vector, websearch_to_tsquery('english', %s))",
                [query]
            ),
            title_phrase_match=Case(
                When(title_phrase_q, then=Value(1)),
                default=Value(0),
                output_field=IntegerField(),
            ),
            title_all_words_match=Case(
                When(title_all_words_q, then=Value(1)),
                default=Value(0),
                output_field=IntegerField(),
            ),
        )
        
        is_short_query = len(query_words) <= 3
        is_multi_word_role_query = 1 < len(query_words) <= 3
        
        if is_multi_word_role_query or not is_short_query:
            # Role searches like "software engineer" must describe the title.
            # Body-only FTS matches otherwise let adjacent roles such as sales
            # engineers outrank the role the user explicitly requested.
            strict = queryset.filter(title_phrase_q | title_all_words_q | organization_q)
            if connection.vendor == "postgresql" and not strict.exists():
                # Topic searches ("climate policy") rarely appear verbatim in
                # titles; rather than show nothing, require every word in the
                # listing text.
                from django.contrib.postgres.search import SearchQuery
                strict = queryset.filter(
                    Q(search_vector=SearchQuery(phrase, search_type="plain", config="english")) | organization_q
                )
            queryset = strict
        else:
            # Single words like "communications" or "python" appear somewhere in most
            # descriptions, so a body match alone says little. Prefer roles whose title,
            # skills, area or organization carry the word; fall back to body text only
            # when that leaves too few results to be useful.
            stem = re.sub(r"(?:es|s)$", "", phrase.lower()) if len(phrase) > 4 else phrase.lower()
            skill_q = Q(pk__in=[])
            if stem not in JobService.SOFT_SKILL_STEMS:
                # Soft skills ("communication") are tagged on most roles and mean nothing here.
                skill_q = Q(skills__icontains=f'"{stem}') | Q(skills__icontains=f'-{stem}')
            strong_q = title_phrase_q | organization_q | skill_q | Q(category__name__icontains=stem)
            strong = queryset.filter(strong_q)
            if strong.count() >= JobService.MIN_STRONG_SEARCH_RESULTS:
                queryset = strong
            else:
                body_q = (
                    Q(fts_rank__gt=0.01) if connection.vendor == "postgresql"
                    else JobService._word_q("description", phrase)  # local SQLite has no FTS
                )
                queryset = queryset.filter(body_q | strong_q)
        
        if sort == "relevance":
            return queryset.order_by(
                "-title_phrase_match",
                "-title_all_words_match",
                "-fts_rank",
                "-posted_at",
                "-id",
            )

        # "Newest" is the default visible selection, so it must be strictly
        # chronological across every matching result, not just within hidden
        # relevance buckets.
        return JobService._order_jobs(queryset, sort)

    @staticmethod
    def create_job(data: dict, user=None, organization=None) -> Job:
        """
        Create a job instance from form data.
        """
        if organization is None:
            org_name = (data.get("organization_name") or "").strip()
            organization = JobService._organization_for_poster(org_name, data, user)

        job = Job(
            title=data.get("title"),
            organization=organization,
            category=data.get("category"),
            description=data.get("description"),
            requirements=data.get("requirements"),
            location=data.get("location"),
            job_type=data.get("job_type") or "full-time",
            salary_min=data.get("salary_min"),
            salary_max=data.get("salary_max"),
            salary_currency=data.get("salary_currency") or ("USD" if (data.get("salary_min") or data.get("salary_max")) else ""),
            application_url=data.get("application_url") or "",
            application_email=data.get("application_email") or "",
            custom_questions=data.get("custom_questions") or [],
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

        job.slug = unique_slug(Job, f"{job.title}-{job.organization.name}")

        job.save()
        return job

    @staticmethod
    def _organization_for_poster(org_name, data, user):
        """Reuse an organization only when it has no owners or the poster is one.

        Otherwise anyone could post under, and edit, another organization's
        profile. A new organization made by a poster lists them as a member.
        """
        is_user = bool(user and getattr(user, "is_authenticated", False))
        for org in Organization.objects.filter(name=org_name).order_by("id"):
            members = org.members.all()
            if not members.exists() or (is_user and members.filter(pk=user.pk).exists()):
                return org
        organization = Organization.objects.create(
            name=org_name,
            slug=unique_slug(Organization, org_name),
            website=data.get("organization_website") or "",
            description=data.get("organization_description") or "",
        )
        if is_user:
            organization.members.add(user)
        return organization

    @staticmethod
    def update_job(job: Job, data: dict) -> Job:
        """Apply form data to an existing draft Job. Caller is responsible for ownership checks."""
        org_name = (data.get("organization_name") or "").strip()
        if org_name and org_name != job.organization.name:
            job.organization = JobService._organization_for_poster(org_name, data, job.poster)
        else:
            # Fill gaps only. A poster must never rewrite an organization
            # profile that other listings (often imported) rely on.
            org = job.organization
            changed = False
            if not org.website and data.get("organization_website"):
                org.website = data["organization_website"]; changed = True
            if not org.description and data.get("organization_description"):
                org.description = data["organization_description"]; changed = True
            if changed:
                org.save(update_fields=["website", "description"])

        job.title = data.get("title") or job.title
        job.category = data.get("category") or job.category
        job.description = data.get("description") or job.description
        job.requirements = data.get("requirements") or job.requirements
        job.location = data.get("location") or job.location
        job.salary_min = data.get("salary_min")
        job.salary_max = data.get("salary_max")
        job.salary_currency = data.get("salary_currency") or job.salary_currency
        job.application_url = data.get("application_url") or ""
        job.application_email = data.get("application_email") or ""
        job.custom_questions = data.get("custom_questions") or []

        raw_payload = {
            "internal_contact": data.get("contact_email"),
            "start_timeline": data.get("start_timeline"),
            "impact": data.get("impact"),
            "benefits": data.get("benefits"),
            "how_to_apply": data.get("how_to_apply"),
        }
        job.raw_data = {k: v for k, v in raw_payload.items() if v}

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
