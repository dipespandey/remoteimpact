import logging
from django.views.generic import ListView, DetailView, FormView, View, TemplateView
from django.conf import settings
from django.contrib.auth.mixins import LoginRequiredMixin
from django.shortcuts import redirect, get_object_or_404
from django.urls import reverse
from django.http import JsonResponse, Http404
from django.core.cache import cache
from django.contrib import messages
from django.utils.decorators import method_decorator
from django.utils.dateparse import parse_datetime
from django.views.decorators.http import require_POST
from django.db.models import Q, Count

from ..models import AssistantSubscription, ConsumerFunnelEvent, Job, Category, SeekerProfile, Application
from ..forms import JobSubmissionForm
from ..services.job_service import JobService
from ..services.employer_attribution import request_labels
from ..services.payment_service import PaymentService
from ..services.funnel import record_consumer_event
from ..services.unified_matching_service import UnifiedMatchingService
from ..seo_config import KEYWORD_SEO_PAGES, ROLE_SEO_PAGES
from .impact_wizard import profile_scout_url
from .mixins import FastJobPaginationMixin
from ..services.page_cache import cache_public_page

logger = logging.getLogger(__name__)



def _drop_pinned_duplicates(context):
    """A featured role pinned above the results shouldn't appear again in them."""
    pinned = {job.id for job in context.get("sponsored_jobs") or []}
    if pinned and context.get("jobs") is not None:
        context["jobs"] = [job for job in context["jobs"] if job.id not in pinned]



@method_decorator(cache_public_page(60), name="dispatch")
class JobListView(FastJobPaginationMixin, ListView):
    model = Job
    template_name = "jobs/job_list.html"
    context_object_name = "jobs"
    paginate_by = 20
    FILTER_OPTIONS_CACHE_KEY = "job-list:filter-options:v5"

    @classmethod
    def _get_filter_options(cls):
        """Cache expensive aggregate data used to render the filter controls."""
        cached = cache.get(cls.FILTER_OPTIONS_CACHE_KEY)
        if cached is not None:
            return cached

        from collections import Counter
        from django.db.models import Max
        from ..services.raw_data_extractor import RawDataExtractor

        max_salary_val = Job.objects.filter(Job.availability_filter()).aggregate(
            max_val=Max("salary_max")
        )["max_val"]

        countries_raw = (
            Job.objects.filter(Job.availability_filter())
            .exclude(country__isnull=True).exclude(country="")
            .values_list("country", flat=True).distinct().order_by("country")
        )
        valid_short_codes = {"UK", "USA"}
        countries = sorted(set(
            "Global" if country.lower() in {"global", "worldwide", "anywhere"}
            else RawDataExtractor._normalize_country(country) for country in countries_raw
            if len(country) > 3 or country.upper() in valid_short_codes
        ))[:100]

        all_skills = []
        for skills_list in (
            Job.objects.filter(Job.availability_filter())
            .exclude(skills=[])
            .values_list("skills", flat=True)
        ):
            if skills_list:
                all_skills.extend(skills_list)
        skill_counts = Counter(all_skills)
        skills = [
            {"slug": skill, "display": skill.replace("-", " ").title(), "count": count}
            for skill, count in skill_counts.most_common(50)
        ]

        live = Job.objects.filter(Job.availability_filter())
        options = {
            "max_salary": int(max_salary_val) if max_salary_val else 300000,
            "countries": countries,
            "skills": skills,
            # Only offer impact areas and "Apply directly" when some live role has them
            "category_ids": sorted(set(live.exclude(category__isnull=True).values_list("category_id", flat=True))),
            "has_direct_apply": live.filter(source=Job.Source.MANUAL).exists(),
        }
        cache.set(
            cls.FILTER_OPTIONS_CACHE_KEY,
            options,
            getattr(settings, "CACHE_TTL_MEDIUM", 300),
        )
        return options

    def get_queryset(self):
        jobs = JobService.get_filtered_jobs(self.request.GET)
        # Keep the public feed chronological by default. The completed-profile
        # handoff uses an explicit mode so new seekers see useful matches
        # without changing the meaning of normal search or the Newest option.
        requested_sort = self.request.GET.get("sort", "").strip()
        # The personalized feed keeps only a seeker's top matches, so applying
        # it under explicit filters would silently hide most results.
        has_filters = any(
            value.strip()
            for key, values in self.request.GET.lists()
            if key not in {"page", "source", "sort"} and not key.startswith("utm_")
            for value in values
        )
        use_personalized_default = (
            not requested_sort
            and not has_filters
            and self.request.user.is_authenticated
        )
        if (requested_sort == "best-match" or use_personalized_default) and self.request.user.is_authenticated:
            seeker = (
                SeekerProfile.objects.filter(
                    user=self.request.user, wizard_completed=True
                )
                .first()
            )
            if seeker:
                # Under filters, rank matches first but keep every result that fits them.
                # A cold ranking (including embedding a new profile) must not
                # delay the initial HTML. Live refreshes populate the cache.
                ranked = UnifiedMatchingService.order_jobs_for_seeker(
                    jobs, seeker, keep_all=has_filters,
                    allow_compute=self.request.headers.get("X-Requested-With") == "fetch",
                )
                if ranked is not None:
                    return ranked
                self.personalization_pending = True
        return jobs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["personalization_pending"] = getattr(self, "personalization_pending", False)
        categories = Category.objects.all()
        context["categories"] = categories

        # Current filters - support multiple selections
        context["current_categories"] = self.request.GET.getlist("category")
        context["current_types"] = self.request.GET.getlist("type")
        context["search_query"] = self.request.GET.get("q", "")
        context["sort"] = self.request.GET.get("sort", "")
        pagination_params = self.request.GET.copy()
        pagination_params.pop("page", None)
        context["pagination_query"] = pagination_params.urlencode()

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

        filter_options = self._get_filter_options()
        live_ids = set(filter_options.get("category_ids", []))
        selected_slugs = set(context["current_categories"])
        context["categories"] = [c for c in context["categories"] if c.pk in live_ids or c.slug in selected_slugs]
        context["has_direct_apply"] = filter_options.get("has_direct_apply", False) or bool(self.request.GET.get("direct_apply"))
        context["max_salary"] = filter_options["max_salary"]
        context["countries"] = filter_options["countries"]

        context["skills_list"] = filter_options["skills"]

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
        context["show_job_scout_nudge"] = not self.request.user.is_authenticated
        context["job_scout_nudge_url"] = (
            f"{reverse('jobs:home')}?scout=1&source=job_list_organic"
        )
        context["job_scout_nudge_label"] = "Describe your ideal job"
        context["job_scout_nudge_tracking_label"] = "anonymous_job_list_visitor"
        context["job_scout_nudge_kind"] = "free"
        if self.request.user.is_authenticated:
            subscription = AssistantSubscription.get_or_create_for_user(self.request.user)
            context["show_job_scout_nudge"] = not subscription.is_pro_active()
            context["job_scout_nudge_url"] = (
                f"{reverse('jobs:job_alerts')}?source=job_list_nudge"
            )
            context["job_scout_nudge_label"] = "Try AI Job Scout"
            context["job_scout_nudge_tracking_label"] = "signed_in_free_user"
            if subscription.free_job_scout_used_at is not None:
                context["job_scout_nudge_url"] = (
                    f"{reverse('jobs:job_scout_subscribe')}?source=job_list_pro_nudge"
                )
                context["job_scout_nudge_label"] = "Keep getting matches"
                context["job_scout_nudge_tracking_label"] = "free_scout_used"
                context["job_scout_nudge_kind"] = "pro"
            try:
                seeker = SeekerProfile.objects.get(user=self.request.user)
                if seeker.wizard_completed:
                    context["seeker_profile"] = seeker
                    # A completed profile can prefill the one-time Scout only
                    # while that free allowance is unused. Preserve the Pro
                    # upgrade CTA after the allowance has been consumed.
                    if subscription.free_job_scout_used_at is None:
                        context["job_scout_nudge_url"] = profile_scout_url(seeker)
                        context["job_scout_nudge_label"] = "Email my matches"
                        context["job_scout_nudge_tracking_label"] = "completed_profile_no_alert"
            except SeekerProfile.DoesNotExist:
                pass

        # Handle sort options
        sort = self.request.GET.get("sort")
        # The personal "Best for you" feed is the default only with no filters; filtered
        # results are newest first, so the dropdown says so.
        filtered = any(
            value.strip() for key, values in self.request.GET.lists()
            if key not in {"page", "source", "sort"} and not key.startswith("utm_") for value in values
        )
        if not sort and context.get("seeker_profile") and not context.get("search_query") and not filtered:
            context["sort"] = "best-match"
        # Paid placement: up to three featured roles matching the current
        # filters, pinned above page one and clearly labelled.
        context["sponsored_jobs"] = []
        if context.get("page_obj") is None or context["page_obj"].number == 1:
            try:
                context["sponsored_jobs"] = list(
                    JobService.get_filtered_jobs(self.request.GET)
                    .filter(Job.sponsored_filter())
                    .select_related("organization", "category")
                    .order_by("featured_until")[:3]
                )
            except Exception:  # noqa: BLE001 - placement must never break search
                logger.exception("Could not load featured jobs")
        _drop_pinned_duplicates(context)

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
        from django.contrib.postgres.search import TrigramSimilarity
        from django.db.models import Count, Q
        from django.db import connection

        # Work from live roles only: matching against every job ever imported
        # (closed ones included) made an empty search take ~3 seconds.
        live = Job.objects.filter(Job.availability_filter())
        counts = dict(live.values_list("organization_id").annotate(n=Count("id")))
        if not counts:
            return []

        def ranked(org_ids, exclude=()):
            ids = [oid for oid in dict.fromkeys(org_ids) if oid in counts and oid not in exclude]
            ids.sort(key=lambda oid: -counts[oid])
            return ids

        chosen = []
        if search_query and search_query.strip():
            query = search_query.strip()
            organizations = Organization.objects.filter(pk__in=counts)
            if connection.vendor == "postgresql":
                organizations = organizations.annotate(similarity=TrigramSimilarity("name", query)).filter(
                    similarity__gt=0.1).order_by("-similarity")
            else:
                organizations = organizations.filter(name__icontains=query).order_by("name")
            chosen += list(organizations.values_list("pk", flat=True)[:6])
            if len(chosen) < 6:
                matching = live.filter(Q(title__icontains=query) | Q(description__icontains=query)).values_list(
                    "organization_id", flat=True)
                chosen += ranked(matching, set(chosen))[: 6 - len(chosen)]
        if category_slugs and len(chosen) < 6:
            matching = live.filter(category__slug__in=category_slugs).values_list("organization_id", flat=True)
            chosen += ranked(matching, set(chosen))[: 6 - len(chosen)]
        if skills and len(chosen) < 6:
            skill_q = Q()
            for skill in skills[:5]:
                skill_q |= Q(skills__contains=[skill])
            chosen += ranked(live.filter(skill_q).values_list("organization_id", flat=True), set(chosen))[: 6 - len(chosen)]
        if len(chosen) < 6:
            chosen += ranked(counts.keys(), set(chosen))[: 6 - len(chosen)]

        orgs = Organization.objects.in_bulk(chosen[:6])
        result = []
        for oid in chosen[:6]:
            org = orgs.get(oid)
            if org is not None:
                org.job_count = counts.get(oid, 0)
                result.append(org)
        return result


class JobDetailView(DetailView):
    model = Job
    template_name = "jobs/job_detail.html"
    context_object_name = "job"

    def get(self, request, *args, **kwargs):
        try:
            response = super().get(request, *args, **kwargs)
            if not response.context_data.get('job_available'):
                from ..models import JobSlugRedirect
                moved = JobSlugRedirect.objects.filter(old_slug=self.object.slug,
                    job__in=self._available_queryset()).select_related('job').first()
                if moved and moved.job_id != self.object.pk:
                    return redirect(moved.job.get_absolute_url(), permanent=True)
            return response
        except Http404:
            # Fallback: check if this is an old slug and redirect to new one
            old_slug = self.kwargs.get("slug", "")
            from ..models import JobSlugRedirect
            moved = JobSlugRedirect.objects.filter(old_slug=old_slug).select_related("job").first()
            if moved and moved.job.slug != old_slug:
                return redirect(moved.job.get_absolute_url(), permanent=True)
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
        return Job.objects.select_related("organization", "category")

    @staticmethod
    def _available_queryset():
        return Job.objects.filter(Job.availability_filter()).select_related("organization", "category")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["job_available"] = self._available_queryset().filter(
            pk=self.object.pk
        ).exists()

        from ..services.job_quality import safe_application_destination
        from ..services.seo_identity import job_detail_identity
        context.update(job_detail_identity(
            self.object, getattr(settings, "SITE_URL", "https://remoteimpact.org"),
            available=context["job_available"],
        ))
        # Imported fields can repeat an entire source description. Display a
        # complete source section once, without rewriting the stored evidence.
        from ..services.scout_constraints import plain_job_field
        rendered_sections = [plain_job_field(self.object, "description").casefold()]
        for field in ("impact", "requirements", "benefits"):
            text = plain_job_field(self.object, field).casefold()
            duplicate = bool(text) and any(
                text == previous or (len(text) >= 120 and text in previous)
                for previous in rendered_sections
            )
            context["show_" + field] = bool(text) and not duplicate
            if text and not duplicate:
                rendered_sections.append(text)
        context["safe_application_url"] = safe_application_destination(self.object.application_url)
        if context["safe_application_url"]:
            # Route through the counting redirect so employers can see clicks.
            context["apply_redirect_url"] = reverse("jobs:job_apply_redirect", kwargs={"slug": self.object.slug})
        # Paid posts take applications on Remote Impact; their own link stays as a secondary option.
        context["apply_inplatform"] = self.object.source == Job.Source.MANUAL and (
            self.object.is_paid or not context.get("apply_redirect_url"))
        context["safe_organization_website"] = safe_application_destination(self.object.organization.website)
        verification = (self.object.raw_data or {}).get("source_verification") or {}
        verification = dict(verification) if isinstance(verification, dict) else {}
        checked = parse_datetime(str(verification.get("checked_at") or ""))
        verification["checked_on"] = checked
        context["source_verification"] = verification

        if self.request.GET.get("source") == "scout_email":
            alert = None
            alert_id = self.request.GET.get("alert_id", "")
            if self.request.user.is_authenticated and alert_id.isdigit():
                alert = self.request.user.job_alerts.filter(pk=int(alert_id)).first()
            record_consumer_event(
                ConsumerFunnelEvent.Event.SCOUT_JOB_VIEWED,
                user=self.request.user,
                alert=alert,
                metadata={
                    "source": "scout_email",
                    "alert_id": alert.pk if alert else None,
                    "job_id": self.object.pk,
                },
            )

        # Match score for authenticated users with seeker profiles
        context["match_data"] = None
        context["seeker_profile"] = None
        if context["job_available"] and self.request.user.is_authenticated:
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
        from ..services.seo_text import job_meta_description, job_title_tag
        from ..templatetags.job_extras import compensation_display_filter as compensation_display
        # Avoid duplicate title tags when the source has multiple live
        # requisitions with the same role, employer, and location. The
        # qualifier is factual and short, so it preserves the role and
        # employer while making each public URL distinct in search results.
        title_siblings = self._available_queryset().filter(
            organization_id=self.object.organization_id,
            title__iexact=self.object.title,
            location=self.object.location,
        ).exclude(pk=self.object.pk)
        disambiguator = ""
        if title_siblings.exists():
            experience_labels = dict(Job.EXPERIENCE_LEVEL_CHOICES)
            candidate = experience_labels.get(self.object.experience_level, "")
            candidate = candidate.replace(" Level", "")
            sibling_rows = list(title_siblings.values("experience_level", "posted_at"))
            sibling_labels = {
                experience_labels.get(value, "").replace(" Level", "")
                for value in (row["experience_level"] for row in sibling_rows)
            }
            if not candidate or candidate in sibling_labels:
                candidate = self.object.posted_at.strftime("%b %d, %Y") if self.object.posted_at else ""
                if candidate and any(
                    row["posted_at"] and row["posted_at"].strftime("%b %d, %Y") == candidate
                    for row in sibling_rows
                ):
                    candidate = ""
            if not candidate:
                candidate = f"Listing {self.object.pk}"
            disambiguator = candidate
        context["seo_title"] = job_title_tag(
            self.object.title,
            self.object.organization.name,
            location=self.object.location,
            disambiguator=disambiguator,
        )
        context["seo_description"] = job_meta_description(
            self.object.title, self.object.organization.name, self.object.location,
            self.object.get_job_type_display(), compensation_display(self.object) or "", self.object.description)
        context["role_insights"] = {}
        if context["job_available"]:
            from ..services.role_insights import role_insights
            context["role_insights"] = role_insights(self.object, self.object.category.name if self.object.category_id else "")
        return context

    def _get_related_jobs(self):
        job = self.object
        qs = self._available_queryset().exclude(pk=job.pk)
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
        return Job.objects.filter(Job.availability_filter()).select_related("organization", "category")

    def get(self, request, *args, **kwargs):
        try:
            return super().get(request, *args, **kwargs)
        except Http404:
            # Preserve useful historical links when a role has closed. The
            # detail page can explain that the role is unavailable, while a
            # missing guide should not strand visitors on a bare 404.
            slug = self.kwargs.get("slug", "")
            if Job.objects.filter(slug=slug).exists():
                return redirect(
                    reverse("jobs:job_detail", kwargs={"slug": slug}),
                    permanent=True,
                )
            raise

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        
        # Crawlers and visitors get an immediate response; generation is an
        # explicit offline operation, never a side effect of GET or HEAD.
        from ..services.guide_generator import public_application_guide
        context["guide"], context["guide_is_personalized"] = public_application_guide(self.object)
        from ..services.job_quality import safe_application_destination
        context["safe_application_url"] = safe_application_destination(self.object.application_url)
        context["safe_organization_website"] = safe_application_destination(self.object.organization.website)
        if context["safe_application_url"]:
            context["apply_redirect_url"] = reverse("jobs:job_apply_redirect", kwargs={"slug": self.object.slug})
        context["apply_inplatform"] = self.object.source == Job.Source.MANUAL and (
            self.object.is_paid or not context.get("apply_redirect_url"))

        return context


class PostJobView(LoginRequiredMixin, FormView):
    template_name = "jobs/post_job.html"
    form_class = JobSubmissionForm

    def get(self, request, *args, **kwargs):
        from ..services.funnel import record_employer_page_request
        record_employer_page_request(request, page="post")
        return super().get(request, *args, **kwargs)

    def form_valid(self, form):
        job = JobService.create_job(form.cleaned_data, user=self.request.user)
        from ..models import EmployerFunnelEvent
        from ..services.funnel import record_employer_event
        record_employer_event(EmployerFunnelEvent.Event.JOB_DRAFT_CREATED, user=self.request.user, job=job)

        try:
            domain_url = self.request.build_absolute_uri("/")[:-1]
            checkout_url = PaymentService.create_checkout_session(job, domain_url, attribution=request_labels(self.request))
            record_employer_event(EmployerFunnelEvent.Event.CHECKOUT_STARTED, user=self.request.user, job=job)
            return redirect(checkout_url)
        except Exception as e:
            record_employer_event(
                EmployerFunnelEvent.Event.CHECKOUT_FAILED,
                user=self.request.user,
                job=job,
                metadata={"error_type": type(e).__name__},
            )
            logger.exception("Could not create job posting checkout for job %s", job.pk)
            messages.error(self.request, "We couldn't start checkout. Your draft is saved; please try again in a minute.")
            return redirect("jobs:job_checkout", slug=job.slug)


class EmployerPricingView(TemplateView):
    template_name = "jobs/employer_pricing.html"


class SaveJobView(LoginRequiredMixin, View):
    def post(self, request, slug):
        saved = JobService.toggle_save_job(request.user, slug)
        if saved:
            from ..services.ranking_log import log_outcome
            job_id = Job.objects.filter(slug=slug).values_list("pk", flat=True).first()
            if job_id:
                log_outcome(request, "save", job_id)
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
            subscription = AssistantSubscription.get_or_create_for_user(self.request.user)
            context["is_pro"] = subscription.is_pro_active()
            context["has_job_scout"] = self.request.user.job_alerts.exists()
            context["profile_scout_url"] = profile_scout_url(seeker)

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
            jobs__in=Job.objects.filter(Job.availability_filter()),
            name__icontains=query
        ).annotate(
            job_count=Count('jobs', filter=Q(jobs__in=Job.objects.filter(Job.availability_filter())))
        ).order_by('-job_count', 'name').distinct()[:20]

        results = [{"name": org.name, "job_count": org.job_count} for org in orgs]
        return JsonResponse({"results": results})



@method_decorator(cache_public_page(120), name="dispatch")
class CategoryLandingView(FastJobPaginationMixin, ListView):
    """SEO-optimized landing page for each impact category."""
    model = Job
    template_name = "jobs/category_landing.html"
    context_object_name = "jobs"
    paginate_by = 20

    def get_queryset(self):
        if hasattr(self, '_category_queryset'):
            return self._category_queryset
        self.category = get_object_or_404(Category, slug=self.kwargs["slug"])
        filters = self.request.GET.copy()
        filters["category"] = self.category.slug
        self._category_queryset = JobService.get_filtered_jobs(filters, collapse_variants=False).order_by("-posted_at")
        return self._category_queryset

    # SEO-optimized titles for each category
    SEO_TITLES = {
        "advocacy-or-policy": "Remote Advocacy & Policy Jobs",
        "ai-safety": "Remote AI Safety & Governance Jobs",
        "animal-welfare": "Remote Animal Welfare & Farmed Animal Jobs",
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
        from ..seo_config import MIN_INDEXABLE_JOBS
        context["index_page"] = context["page_obj"].paginator.count >= MIN_INDEXABLE_JOBS and not self.request.GET
        
        # Use custom SEO title if available, otherwise generate one
        seo_title = self.SEO_TITLES.get(cat.slug, f"Remote {cat.name} Jobs")
        context["meta_title"] = f"{seo_title} — Remote Impact"
        context["meta_description"] = (
            f"Browse {context['page_obj'].paginator.count} {cat.name} listings at impact organizations. "
            "Review remote-work eligibility, pay when published and employer application details."
        )
        context["search_query"] = self.request.GET.get("q", "").strip()
        context["job_count"] = context["page_obj"].paginator.count
        pagination_params = self.request.GET.copy()
        pagination_params.pop('page', None)
        context['pagination_query'] = pagination_params.urlencode()
        from .seo_pages import inventory_evidence
        context.update(inventory_evidence(self.get_queryset(), [
            (cat.name, {"slug": cat.slug, "count": context["job_count"]})]))
        context["category_faqs"] = [
            {"question": f"How many {cat.name.lower()} listings are available?",
             "answer": f"There are {context['job_count']} listings in this search. The count follows the same availability rules as our job board."},
            {"question": "Can I apply from any country?",
             "answer": "Remote eligibility varies by employer. Check residency, work authorization, time zone and travel requirements in the source posting before applying."},
            {"question": "How are these listings checked?",
             "answer": "We aggregate employer and job-board sources and show recorded source sightings and verification results on job pages. A recent source sighting is not confirmation that applications are still open."},
        ]
        from ..services.search_guidance import category_search_guidance
        context["search_guidance"] = category_search_guidance(cat.slug)
        if context["search_guidance"]:
            context["category_faqs"].append({
                "question": context["search_guidance"]["question"],
                "answer": context["search_guidance"]["answer"],
            })
        from ..services.seo_text import json_ld
        page_url = settings.SITE_URL.rstrip("/") + reverse("jobs:category_landing", args=[cat.slug])
        context["category_structured_data"] = json_ld([
            {"@context": "https://schema.org", "@type": "BreadcrumbList", "itemListElement": [
                {"@type": "ListItem", "position": 1, "name": "Home", "item": settings.SITE_URL.rstrip("/") + "/"},
                {"@type": "ListItem", "position": 2, "name": "Jobs", "item": settings.SITE_URL.rstrip("/") + reverse("jobs:job_list")},
                {"@type": "ListItem", "position": 3, "name": cat.name, "item": page_url}]},
            {"@context": "https://schema.org", "@type": "CollectionPage",
             "name": seo_title, "description": context["meta_description"], "url": page_url,
             "isPartOf": {"@type": "WebSite", "name": "Remote Impact", "url": settings.SITE_URL}},
            {"@context": "https://schema.org", "@type": "FAQPage", "mainEntity": [
                {"@type": "Question", "name": faq["question"], "acceptedAnswer":
                 {"@type": "Answer", "text": faq["answer"]}} for faq in context["category_faqs"]]},
        ])
        
        context["sponsored_jobs"] = (
            list(self.get_queryset().filter(Job.sponsored_filter()).order_by("featured_until")[:3])
            if context["page_obj"].number == 1 else []
        )
        _drop_pinned_duplicates(context)

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
            jobs__in=Job.objects.filter(Job.availability_filter())
        ).annotate(
            job_count=Count('jobs', filter=Q(jobs__in=Job.objects.filter(Job.availability_filter())))
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
