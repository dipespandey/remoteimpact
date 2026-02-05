from django.db.models import Q, F, Value, Case, When, FloatField
from django.db.models.functions import Greatest, Coalesce
from django.contrib.postgres.search import SearchQuery, SearchRank, TrigramSimilarity
from django.shortcuts import get_object_or_404
from django.utils import timezone
from pgvector.django import CosineDistance
from ..models import Job, Category, SavedJob, Organization


class JobService:
    @staticmethod
    def get_filtered_jobs(filters):
        """
        Filter jobs based on query parameters.
        Supports both single values and multiple values (multiselect).

        Args:
            filters: QueryDict or dict-like object with filter parameters
        """
        from datetime import timedelta
        now = timezone.now()
        cutoff = now - timedelta(days=180)
        jobs = Job.objects.filter(
            is_active=True,
        ).exclude(
            # Exclude jobs with a past expiration date
            expires_at__lt=now,
        ).exclude(
            # Exclude old jobs that never had an expiry date set
            expires_at__isnull=True,
            posted_at__lt=cutoff,
        ).select_related(
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
            country_q = Q()
            for country in countries:
                country_q |= Q(country=country) | Q(location__icontains=country)
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
                jobs = jobs.filter(
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
                jobs = jobs.filter(
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
        if query:
            query = query.strip()
            jobs = JobService._apply_smart_search(jobs, query)
            # Return early - ordering is handled by search ranking
            return jobs

        return jobs.order_by("-is_featured", "-posted_at")

    @staticmethod
    def _apply_smart_search(queryset, query):
        """
        Enhanced search that combines:
        1. Full-text search (exact keyword matches) - highest weight
        2. Trigram similarity (fuzzy matches, typo tolerance)
        3. Semantic vector search (conceptually similar)
        
        Results are ranked by combined relevance score.
        """
        from jobs.services.embedding_service import get_embedding
        
        # Parse query for full-text search (handles phrases and operators)
        try:
            search_query = SearchQuery(query, search_type='websearch')
        except Exception:
            # Fallback to plain search if websearch parsing fails
            search_query = SearchQuery(query, search_type='plain')
        
        # Annotate with multiple relevance signals
        queryset = queryset.annotate(
            # Full-text search rank (exact keyword matches)
            fts_rank=SearchRank('search_vector', search_query),
            # Trigram similarity on title (fuzzy matching)
            title_similarity=TrigramSimilarity('title', query),
            # Trigram similarity on organization name
            org_similarity=TrigramSimilarity('organization__name', query),
        )
        
        # Try to add semantic search if embeddings are available
        try:
            query_embedding = get_embedding(query)
            if query_embedding:
                queryset = queryset.annotate(
                    vector_distance=CosineDistance('embedding', query_embedding)
                )
                # Semantic similarity (1 - distance)
                queryset = queryset.annotate(
                    semantic_score=Case(
                        When(embedding__isnull=False, then=1.0 - F('vector_distance')),
                        default=Value(0.0),
                        output_field=FloatField()
                    )
                )
            else:
                queryset = queryset.annotate(semantic_score=Value(0.0, output_field=FloatField()))
        except Exception:
            queryset = queryset.annotate(semantic_score=Value(0.0, output_field=FloatField()))
        
        # Combined relevance score with weights:
        # - FTS rank: 40% (exact keyword matches)
        # - Title similarity: 25% (fuzzy title match)
        # - Semantic: 25% (conceptual similarity)
        # - Org similarity: 10% (organization name match)
        queryset = queryset.annotate(
            relevance_score=(
                Coalesce(F('fts_rank'), Value(0.0)) * 0.40 +
                Coalesce(F('title_similarity'), Value(0.0)) * 0.25 +
                Coalesce(F('semantic_score'), Value(0.0)) * 0.25 +
                Coalesce(F('org_similarity'), Value(0.0)) * 0.10
            )
        )
        
        # Filter to only include jobs with some relevance
        # Use a low threshold to include fuzzy/semantic matches
        queryset = queryset.filter(
            Q(fts_rank__gt=0) |  # Has keyword matches
            Q(title_similarity__gt=0.15) |  # Fuzzy title match
            Q(semantic_score__gt=0.5) |  # Semantically similar
            Q(org_similarity__gt=0.3) |  # Org name match
            Q(title__icontains=query) |  # Fallback: basic title match
            Q(organization__name__icontains=query)  # Fallback: basic org match
        )
        
        # Order by featured status first, then relevance score
        return queryset.order_by('-is_featured', '-relevance_score', '-posted_at')

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
            salary_currency=data.get("salary_currency") or ("USD" if (data.get("salary_min") or data.get("salary_max")) else ""),
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
