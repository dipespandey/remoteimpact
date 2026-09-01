from django.views.generic import ListView, DetailView, FormView, View
from django.contrib.auth.mixins import LoginRequiredMixin
from django.shortcuts import redirect, get_object_or_404
from django.urls import reverse
from django.http import JsonResponse, Http404
from django.contrib import messages
from django.utils.decorators import method_decorator
from django.views.decorators.http import require_POST
from django.db.models import Q, Count

from ..models import Job, Category, SeekerProfile, Application
from ..forms import JobSubmissionForm
from ..services.job_service import JobService
from ..services.payment_service import PaymentService
from ..services.unified_matching_service import UnifiedMatchingService
from ..seo_config import KEYWORD_SEO_PAGES, ROLE_SEO_PAGES


class JobListView(ListView):
    model = Job
    template_name = "jobs/job_list.html"
    context_object_name = "jobs"
    paginate_by = 20

    def get_queryset(self):
        return JobService.get_filtered_jobs(self.request.GET)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        categories = Category.objects.all()
        context["categories"] = categories

        # Current filters - support multiple selections
        context["current_categories"] = self.request.GET.getlist("category")
        context["current_types"] = self.request.GET.getlist("type")
        context["search_query"] = self.request.GET.get("q", "")
        context["sort"] = self.request.GET.get("sort", "")

        # For backward compatibility with single category display
        current_category = self.request.GET.get("category")
        context["current_category"] = current_category
        if current_category:
            context["current_category_obj"] = categories.filter(slug=current_category).first()

        # Filter values from request - support multiple selections
        context["filters"] = {
            "countries": self.request.GET.getlist("country"),
            "organizations": self.request.GET.getlist("organization"),
            "salary_min": self.request.GET.get("salary_min", ""),
            "salary_max": self.request.GET.get("salary_max", ""),
            "experiences": self.request.GET.getlist("experience"),
            "educations": self.request.GET.getlist("education"),
            "skills": self.request.GET.getlist("skill"),
            "posted": self.request.GET.get("posted", ""),
            "direct_apply": self.request.GET.get("direct_apply", ""),
            # Keep single value versions for backward compatibility
            "country": self.request.GET.get("country", ""),
            "organization": self.request.GET.get("organization", ""),
            "experience": self.request.GET.get("experience", ""),
            "education": self.request.GET.get("education", ""),
        }

        # Max salary across all active jobs for the salary slider ceiling
        from django.db.models import Max
        max_salary_val = Job.objects.filter(is_active=True).aggregate(
            max_val=Max("salary_max")
        )["max_val"]
        context["max_salary"] = int(max_salary_val) if max_salary_val else 300000

        # Get distinct countries from the new country field (normalized)
        # Filter out 2-letter codes except UK/USA, and ensure proper country names
        from ..models import Organization

        # Valid short codes we want to keep
        valid_short_codes = {"UK", "USA"}

        from django.utils import timezone
        from datetime import timedelta
        now = timezone.now()
        cutoff = now - timedelta(days=180)
        countries_raw = (
            Job.objects.filter(is_active=True)
            .exclude(expires_at__lt=now)
            .exclude(expires_at__isnull=True, posted_at__lt=cutoff)
            .exclude(country__isnull=True)
            .exclude(country="")
            .values_list("country", flat=True)
            .distinct()
            .order_by("country")
        )

        # Filter: keep if length > 3, or if it's a valid short code (UK, USA)
        countries_from_field = sorted(set(
            c for c in countries_raw
            if len(c) > 3 or c.upper() in valid_short_codes
        ))[:100]

        context["countries"] = countries_from_field
        # Top 500 orgs sorted by job count
        context["organizations"] = Organization.objects.filter(
            jobs__is_active=True
        ).annotate(
            job_count=Count('jobs', filter=Q(jobs__is_active=True))
        ).order_by('-job_count', 'name')[:500]

        # Get top skills with counts
        from collections import Counter
        all_skills = []
        for skills_list in Job.objects.filter(is_active=True).exclude(skills=[]).values_list("skills", flat=True):
            if skills_list:
                all_skills.extend(skills_list)
        skill_counts = Counter(all_skills)
        top_skills = [
            {"slug": skill, "display": skill.replace("-", " ").title(), "count": count}
            for skill, count in skill_counts.most_common(50)
        ]
        context["skills_list"] = top_skills

        # Knowledge-based filters with field value mappings
        context["knowledge_filters"] = {
            "experience_levels": [
                {"display": "Entry Level", "value": "entry"},
                {"display": "Mid Level", "value": "mid"},
                {"display": "Senior", "value": "senior"},
                {"display": "Executive", "value": "executive"},
                {"display": "Internship", "value": "internship"},
            ],
            "education_levels": [
                {"display": "High School", "value": "high_school"},
                {"display": "Associate", "value": "associate"},
                {"display": "Bachelor's", "value": "bachelor"},
                {"display": "Master's", "value": "master"},
                {"display": "PhD", "value": "phd"},
            ],
            # Keep simple lists for backward compatibility
            "experience_levels_simple": ["Entry Level", "Mid Level", "Senior", "Executive", "Internship"],
            "education_levels_simple": ["High School", "Associate", "Bachelor's", "Master's", "PhD"],
        }

        # Job types for multiselect
        context["job_types"] = [
            {"display": "Full-time", "value": "full-time"},
            {"display": "Part-time", "value": "part-time"},
            {"display": "Contract", "value": "contract"},
            {"display": "Freelance", "value": "freelance"},
            {"display": "Internship", "value": "internship"},
        ]

        # Check if user has seeker profile for showing "My Matches" link
        context["seeker_profile"] = None
        if self.request.user.is_authenticated:
            try:
                seeker = SeekerProfile.objects.get(user=self.request.user)
                if seeker.wizard_completed:
                    context["seeker_profile"] = seeker
            except SeekerProfile.DoesNotExist:
                pass

        # If no results, get recommended organizations
        if not context.get("object_list") or context["page_obj"].paginator.count == 0:
            context["recommended_orgs"] = self._get_recommended_organizations(
                search_query=context.get("search_query"),
                category_slugs=context.get("current_categories", []),
                skills=context["filters"].get("skills", []),
            )

        return context

    def _get_recommended_organizations(self, search_query=None, category_slugs=None, skills=None):
        """
        Get recommended organizations based on search context.
        Returns organizations that might be relevant even if no jobs match.
        """
        from ..models import Organization
        from django.db.models import Count, Q
        from django.contrib.postgres.search import TrigramSimilarity
        
        # Start with orgs that have active jobs
        orgs = Organization.objects.filter(
            jobs__is_active=True
        ).annotate(
            job_count=Count('jobs', filter=Q(jobs__is_active=True))
        ).distinct()

        recommendations = []
        
        # If there's a search query, find orgs with similar names or whose jobs match
        if search_query and search_query.strip():
            query = search_query.strip()
            # Organizations with similar names
            name_matches = orgs.annotate(
                similarity=TrigramSimilarity('name', query)
            ).filter(similarity__gt=0.1).order_by('-similarity', '-job_count')[:6]
            recommendations.extend(list(name_matches))
            
            # Organizations whose jobs contain the search term
            if len(recommendations) < 6:
                job_matches = orgs.filter(
                    Q(jobs__title__icontains=query) |
                    Q(jobs__description__icontains=query)
                ).exclude(id__in=[o.id for o in recommendations]).order_by('-job_count')[:6 - len(recommendations)]
                recommendations.extend(list(job_matches))

        # If there are category filters, get top orgs in those categories
        if category_slugs and len(recommendations) < 6:
            category_orgs = orgs.filter(
                jobs__category__slug__in=category_slugs
            ).exclude(id__in=[o.id for o in recommendations]).order_by('-job_count')[:6 - len(recommendations)]
            recommendations.extend(list(category_orgs))

        # If there are skill filters, get orgs whose jobs have those skills
        if skills and len(recommendations) < 6:
            skill_q = Q()
            for skill in skills[:5]:  # Limit to first 5 skills
                skill_q |= Q(jobs__skills__contains=[skill])
            skill_orgs = orgs.filter(skill_q).exclude(
                id__in=[o.id for o in recommendations]
            ).order_by('-job_count')[:6 - len(recommendations)]
            recommendations.extend(list(skill_orgs))

        # Fill remaining slots with top orgs by job count
        if len(recommendations) < 6:
            top_orgs = orgs.exclude(
                id__in=[o.id for o in recommendations]
            ).order_by('-job_count')[:6 - len(recommendations)]
            recommendations.extend(list(top_orgs))

        return recommendations[:6]


class JobDetailView(DetailView):
    model = Job
    template_name = "jobs/job_detail.html"
    context_object_name = "job"

    def get(self, request, *args, **kwargs):
        try:
            return super().get(request, *args, **kwargs)
        except Http404:
            # Fallback: check if this is an old slug and redirect to new one
            old_slug = self.kwargs.get("slug", "")
            try:
                import json
                import os
                for redirect_path in ["/data/slug_redirects.json", "/tmp/slug_redirects.json"]:
                    if not os.path.exists(redirect_path):
                        continue
                    with open(redirect_path) as f:
                        redirects = json.load(f)
                    if old_slug in redirects:
                        return redirect(
                            reverse("jobs:job_detail", kwargs={"slug": redirects[old_slug]}),
                            permanent=True,
                        )
            except Exception:
                pass
            raise

    def get_queryset(self):
        from django.utils import timezone
        from datetime import timedelta
        now = timezone.now()
        cutoff = now - timedelta(days=180)
        return Job.objects.filter(is_active=True).exclude(
            expires_at__lt=now,
        ).exclude(
            expires_at__isnull=True,
            posted_at__lt=cutoff,
        ).select_related("organization", "category")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        # Match score for authenticated users with seeker profiles
        context["match_data"] = None
        context["seeker_profile"] = None
        if self.request.user.is_authenticated:
            try:
                seeker = SeekerProfile.objects.get(user=self.request.user)
                if seeker.wizard_completed:
                    context["seeker_profile"] = seeker
                    job = self.object
                    result = UnifiedMatchingService.score_job_for_seeker(seeker, job)
                    context["match_data"] = {
                        "total": int(result.score),
                        "breakdown": {
                            "Semantic": int(result.semantic_score),
                            "Profile": int(result.profile_score),
                            "Impact": int(result.impact_score),
                        },
                        "impact_tier": result.impact_tier,
                        "reasons": result.reasons,
                        "gaps": result.gaps,
                        "impact_reasons": result.impact_reasons,
                    }
            except SeekerProfile.DoesNotExist:
                pass

        # Check if user already applied
        context["has_applied"] = False
        if self.request.user.is_authenticated:
            context["has_applied"] = Application.objects.filter(
                job=self.object, applicant=self.request.user
            ).exists()

        context["related_jobs"] = self._get_related_jobs()
        context["related_seo_links"] = self._get_related_seo_links()
        return context

    def _get_related_jobs(self):
        job = self.object
        qs = self.get_queryset().exclude(pk=job.pk)
        if job.category_id:
            qs = qs.filter(category_id=job.category_id)
        else:
            qs = qs.filter(organization_id=job.organization_id)
        return qs.order_by("-posted_at")[:4]

    def _get_related_seo_links(self):
        job = self.object
        links = []
        seen = set()

        def add(label, url):
            if url in seen:
                return
            seen.add(url)
            links.append({"label": label, "url": url})

        add("Remote impact jobs", reverse("jobs:job_list"))
        if job.category:
            add(
                f"Remote {job.category.name.lower()} jobs",
                reverse("jobs:category_landing", kwargs={"slug": job.category.slug}),
            )
        if job.organization and job.organization.slug:
            add(
                f"Jobs at {job.organization.name}",
                reverse("jobs:organization_profile", kwargs={"slug": job.organization.slug}),
            )

        title = (job.title or "").lower()
        for page in ROLE_SEO_PAGES:
            if any(pattern.lower() in title for pattern in page.get("patterns", [])):
                add(page["h1"], reverse("jobs:role_jobs", kwargs={"role_slug": page["slug"]}))
                if len(links) >= 7:
                    break

        keyword_lookup = {page["slug"]: page for page in KEYWORD_SEO_PAGES}
        keyword_slugs = ["remote-social-impact-jobs", "impact-jobs-remote", "remote-jobs-with-purpose"]
        if job.category and job.category.slug == "climate-environment":
            keyword_slugs.insert(0, "remote-climate-jobs")
        if job.job_type == "part-time":
            keyword_slugs.insert(0, "remote-part-time-jobs")
        org_text = f"{job.organization.name} {job.organization.organization_type}".lower()
        if any(term in org_text for term in ("nonprofit", "foundation", "charity", "ngo")):
            keyword_slugs.insert(0, "remote-nonprofit-jobs")

        for slug in keyword_slugs:
            page = keyword_lookup.get(slug)
            if page:
                add(page["h1"], reverse("jobs:keyword_jobs", kwargs={"keyword_slug": slug}))
            if len(links) >= 8:
                break
        return links[:8]


class ApplicationGuideView(DetailView):
    """SEO-optimized application guide page for each job."""
    model = Job
    template_name = "jobs/application_guide.html"
    context_object_name = "job"

    def get_queryset(self):
        from django.utils import timezone
        from datetime import timedelta
        now = timezone.now()
        cutoff = now - timedelta(days=180)
        return Job.objects.filter(is_active=True).exclude(
            expires_at__lt=now,
        ).exclude(
            expires_at__isnull=True,
            posted_at__lt=cutoff,
        ).select_related("organization", "category")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        
        # Generate personalized guide content
        from ..services.guide_generator import generate_application_guide
        context["guide"] = generate_application_guide(self.object)
        
        return context


class PostJobView(LoginRequiredMixin, FormView):
    template_name = "jobs/post_job.html"
    form_class = JobSubmissionForm

    def form_valid(self, form):
        job = JobService.create_job(form.cleaned_data, user=self.request.user)

        try:
            domain_url = self.request.build_absolute_uri("/")[:-1]
            checkout_url = PaymentService.create_checkout_session(job, domain_url)
            return redirect(checkout_url)
        except Exception as e:
            messages.error(self.request, f"Error creating payment session: {str(e)}")
            return redirect("jobs:home")


class SaveJobView(LoginRequiredMixin, View):
    def post(self, request, slug):
        saved = JobService.toggle_save_job(request.user, slug)
        return JsonResponse({"saved": saved})


class MyMatchesView(LoginRequiredMixin, ListView):
    """Shows top matched jobs for the authenticated seeker."""

    template_name = "jobs/my_matches.html"
    context_object_name = "matches"

    def get_queryset(self):
        return []  # We'll populate in get_context_data

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        try:
            seeker = SeekerProfile.objects.get(user=self.request.user)
            if not seeker.wizard_completed:
                context["needs_profile"] = True
                return context

            context["seeker_profile"] = seeker

            # Unified matching with 4-component scoring
            results = UnifiedMatchingService.get_matches(seeker, limit=25)
            matches = [
                {
                    "job": result.job,
                    "score": int(result.score),
                    "semantic": int(result.semantic_score),
                    "lexical": int(result.lexical_score),
                    "profile": int(result.profile_score),
                    "impact": int(result.impact_score),
                    "impact_tier": result.impact_tier,
                    "reasons": result.reasons,
                    "gaps": result.gaps,
                    "impact_reasons": result.impact_reasons,
                }
                for result in results
            ]
            context["matches"] = matches

        except SeekerProfile.DoesNotExist:
            context["needs_profile"] = True

        return context


class OrganizationSearchView(View):
    """API endpoint for searching all organizations."""

    def get(self, request):
        query = request.GET.get("q", "").strip()
        if len(query) < 2:
            return JsonResponse({"results": []})

        from ..models import Organization

        orgs = Organization.objects.filter(
            jobs__is_active=True,
            name__icontains=query
        ).annotate(
            job_count=Count('jobs', filter=Q(jobs__is_active=True))
        ).order_by('-job_count', 'name').distinct()[:20]

        results = [{"name": org.name, "job_count": org.job_count} for org in orgs]
        return JsonResponse({"results": results})



class CategoryLandingView(ListView):
    """SEO-optimized landing page for each impact category."""
    model = Job
    template_name = "jobs/category_landing.html"
    context_object_name = "jobs"
    paginate_by = 20

    def get_queryset(self):
        self.category = get_object_or_404(Category, slug=self.kwargs["slug"])
        from django.utils import timezone
        from datetime import timedelta
        now = timezone.now()
        cutoff = now - timedelta(days=180)
        return (
            Job.objects.filter(is_active=True, category=self.category)
            .exclude(expires_at__lt=now)
            .exclude(expires_at__isnull=True, posted_at__lt=cutoff)
            .select_related("organization", "category")
            .order_by("-posted_at")
        )

    # SEO-optimized titles for each category
    SEO_TITLES = {
        "advocacy-or-policy": "Remote Advocacy & Policy Jobs",
        "ai-safety": "Remote AI Safety & Governance Jobs",
        "animal-welfare": "Remote Animal Welfare Jobs",
        "biosecurity": "Remote Biosecurity & Pandemic Prep Jobs",
        "buildings": "Remote Sustainable Buildings Jobs",
        "capital": "Remote Impact Capital & Finance Jobs",
        "climate-environment": "Remote Climate & Environment Jobs",
        "coastal-ocean-sinks": "Remote Ocean & Marine Science Jobs",
        "communications": "Remote Communications & Media Jobs",
        "education": "Remote Education & EdTech Jobs",
        "effective-altruism": "Remote Effective Altruism Jobs",
        "energy": "Remote Clean Energy & Electricity Jobs",
        "food-agriculture-land-use": "Remote Sustainable Food & Agriculture Jobs",
        "gender-equality-social-inclusion": "Remote Social Justice & Equity Jobs",
        "global-health": "Remote Global Health & Development Jobs",
        "humanitarian": "Remote Humanitarian & Disaster Relief Jobs",
        "human-rights": "Remote Human Rights & Justice Jobs",
        "impact-careers": "Remote Impact Careers Jobs",
        "materials-manufacturing": "Remote Sustainable Industry Jobs",
        "media-journalism": "Remote Impact Media & Journalism Jobs",
        "nonprofit-charity": "Remote Philanthropy & Non-Profit Jobs",
        "nuclear-security": "Remote Nuclear Security Jobs",
        "operations": "Remote Operations & Administration Jobs",
        "other-1": "Remote Impact Jobs",
        "other": "Remote Impact Jobs",
        "policy-advocacy": "Remote Policy & Advocacy Jobs",
        "poverty-development": "Remote Poverty & Development Jobs",
        "technology": "Remote Tech for Good Jobs",
        "transportation": "Remote Clean Transportation Jobs",
    }

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        cat = self.category
        context["category"] = cat
        context["categories"] = Category.objects.all().order_by("name")
        
        # Use custom SEO title if available, otherwise generate one
        seo_title = self.SEO_TITLES.get(cat.slug, f"Remote {cat.name} Jobs")
        context["meta_title"] = f"{seo_title} — Remote Impact"
        context["meta_description"] = (
            f"Browse {context['page_obj'].paginator.count}+ remote {cat.name.lower()} jobs. "
            f"Find purpose-driven roles in {cat.name.lower()} from top impact organizations."
        )
        
        # If no jobs in this category, get recommended organizations
        if context["page_obj"].paginator.count == 0:
            context["recommended_orgs"] = self._get_recommended_orgs_for_category(cat)
        
        return context
    
    def _get_recommended_orgs_for_category(self, category):
        """Get organizations that have had jobs in this category or similar categories."""
        from ..models import Organization
        
        # Get orgs that have active jobs (any category) but prioritize those 
        # that have had jobs in this category before
        orgs = Organization.objects.filter(
            jobs__is_active=True
        ).annotate(
            job_count=Count('jobs', filter=Q(jobs__is_active=True))
        ).distinct().order_by('-job_count')[:6]
        
        return list(orgs)


class AppliedJobDetailView(LoginRequiredMixin, DetailView):
    """Show full job details for a job the user applied to, even if expired."""

    model = Application
    template_name = "jobs/applied_job_detail.html"
    context_object_name = "application"

    def get_queryset(self):
        return Application.objects.filter(
            applicant=self.request.user,
        ).select_related("job", "job__organization", "job__category")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["job"] = self.object.job
        return context
