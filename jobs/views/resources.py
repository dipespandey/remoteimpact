import logging
from django.views.generic import TemplateView, View
from django.db.models import Count, Q
from django.http import Http404, HttpResponse, JsonResponse
from django.shortcuts import redirect
from django.urls import reverse
from django.contrib.auth.mixins import LoginRequiredMixin
from django.utils.decorators import method_decorator
from django.utils import timezone
from django.utils.html import escape
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.cache import cache_page
from django.conf import settings
from django.template import TemplateDoesNotExist
from django.template.loader import get_template
from functools import lru_cache
import json
from datetime import date, timedelta
import re

from jobs.services.safe_fetch import fetch_public_html
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

from ..models import Job, Category, SeekerProfile
from ..services.ai import AIClient
from ..services.payment_service import PaymentService
from ..services.job_service import JobService


class CostOfLivingComparisonView(TemplateView):
    template_name = "jobs/tools/cost_of_living.html"


class PayRaiseCalculatorView(TemplateView):
    template_name = "jobs/tools/pay_raise_calculator.html"


class PrivacyPolicyView(TemplateView):
    template_name = "jobs/privacy.html"


class TermsOfServiceView(TemplateView):
    template_name = "jobs/terms.html"


class PublicPricingView(TemplateView):
    """Public pricing and product comparison page for acquisition traffic."""
    template_name = "jobs/public_pricing.html"


@method_decorator(cache_page(getattr(settings, 'CACHE_TTL_MEDIUM', 300)), name='dispatch')
class HomeView(TemplateView):
    template_name = "jobs/home.html"

    @staticmethod
    def _preview_tabs(available_jobs, latest_jobs, total):
        """Homepage preview: the latest roles, then the three biggest impact areas."""
        tabs = [{"key": "latest", "label": "Latest", "jobs": latest_jobs[:4],
                 "url": reverse("jobs:job_list"), "count": total, "noun": "open"}]
        for slug, label, noun in PREVIEW_AREAS:
            area_jobs = available_jobs.filter(category__slug=slug)
            jobs = _one_per_org(area_jobs, 4)
            if jobs:
                tabs.append({"key": slug, "label": label, "jobs": jobs,
                             "url": reverse("jobs:category_landing", kwargs={"slug": slug}),
                             "count": area_jobs.count(), "noun": noun})
        return tabs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        available_jobs = JobService.get_available_jobs()

        # Featured Jobs
        # "Featured" means an employer paid for placement.
        featured_jobs = list(
            available_jobs.filter(Job.sponsored_filter())
            .select_related("organization", "category")
            .order_by("featured_until")[:6]
        )

        # Latest Jobs: one per organization, so a single employer posting the same role
        # for five regions doesn't fill the list.
        from ..services.listing_language import listing_language

        latest_jobs, seen_orgs = [], set()
        for job in (
            available_jobs
            .exclude(id__in=[j.id for j in featured_jobs])
            .select_related("organization", "category")
            .order_by("-posted_at")[:80]
        ):
            if job.organization_id in seen_orgs or listing_language(job.title, job.description):
                continue
            seen_orgs.add(job.organization_id)
            latest_jobs.append(job)
            if len(latest_jobs) == 12:
                break

        # Categories with counts
        categories = Category.objects.annotate(
            job_count=Count("jobs", filter=Q(jobs__in=available_jobs))
        ).filter(job_count__gt=0).order_by("-job_count", "name")

        from ..services.trust_stats import trust_stats
        from ..services.country_activity import weekly_summary as country_activity_summary

        # Stats for hero section (rounded down to nearest 100)
        active_jobs_count = JobService.count_openings()
        active_jobs_rounded = (active_jobs_count // 100) * 100
        categories_count = Category.objects.count()

        # Organization count (rounded down to nearest 100)
        from ..models import Organization
        from django.utils import timezone
        from datetime import timedelta
        org_count = Organization.objects.filter(jobs__in=available_jobs).distinct().count()
        org_count_rounded = (org_count // 100) * 100

        # Professionals joined this month
        from django.contrib.auth import get_user_model
        User = get_user_model()
        now = timezone.now()
        month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        professionals_this_month = User.objects.filter(date_joined__gte=month_start).count()

        # Featured organizations (ones with active jobs)
        from ..models import Organization
        # Organizations hiring most right now, not alphabetical ("0Labs" first).
        featured_orgs = (
            Organization.objects.filter(jobs__in=available_jobs)
            .exclude(name__iexact="Unknown Organization")
            .exclude(slug="")
            .annotate(open_roles=Count("jobs", filter=Q(jobs__in=available_jobs)))
            .order_by("-open_roles", "name")[:8]
        )

        # Check if user has completed impact profile
        has_impact_profile = False
        if self.request.user.is_authenticated:
            try:
                seeker = SeekerProfile.objects.get(user=self.request.user)
                has_impact_profile = seeker.wizard_completed
            except SeekerProfile.DoesNotExist:
                pass

        context.update(
            {
                "featured_jobs": featured_jobs,
                "recent_jobs": latest_jobs,
                "categories": categories,
                "active_jobs_count": active_jobs_rounded,
                "categories_count": categories_count,
                "organizations_count": org_count_rounded,
                "professionals_this_month": professionals_this_month,
                "featured_orgs": featured_orgs,
                "has_impact_profile": has_impact_profile,
                "trust": trust_stats(),
                "activity": country_activity_summary(),
                "preview_tabs": self._preview_tabs(available_jobs, latest_jobs, active_jobs_rounded),
            }
        )
        return context


class AllDomainsView(TemplateView):
    template_name = "jobs/domains_list.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["categories"] = Category.objects.annotate(
            job_count=Count("jobs", filter=Q(jobs__is_active=True))
        ).order_by("name")
        return context


class ToolsIndexView(TemplateView):
    template_name = "jobs/tools/index.html"


@method_decorator(cache_page(getattr(settings, 'CACHE_TTL_LONG', 3600)), name='dispatch')
class PartnerResourcesView(TemplateView):
    """Public distribution page for communities that share impact jobs."""
    template_name = "jobs/partner_resources.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        available_jobs = JobService.get_available_jobs()
        context.update(
            {
                "active_jobs_count": available_jobs.count(),
                "jobs_feed_url": self.request.build_absolute_uri(reverse("jobs:jobs_feed")),
                "category_feed_url": self.request.build_absolute_uri(
                    reverse("jobs:category_feed", kwargs={"slug": "climate-environment"})
                ),
                "scout_preview_url": (
                    self.request.build_absolute_uri(reverse("jobs:job_scout_preview"))
                    + "?intent=Remote%20impact%20jobs%20in%20climate%20and%20social%20good"
                    + "&source=partner_page"
                ),
            }
        )
        return context


PREVIEW_AREAS = [  # (category slug, tab label, noun for "See all N … roles")
    ("climate-environment", "Climate", "climate"),
    ("ai-safety", "AI safety", "AI safety"),
    ("global-health", "Health", "global health"),
]


def _one_per_org(queryset, limit):
    from ..services.listing_language import listing_language
    picked, seen = [], set()
    for job in queryset.select_related("organization", "category").order_by("-posted_at")[: limit * 12]:
        if job.organization_id in seen or listing_language(job.title, job.description):
            continue
        seen.add(job.organization_id)
        picked.append(job)
        if len(picked) == limit:
            break
    return picked


@method_decorator(cache_page(getattr(settings, 'CACHE_TTL_LONG', 3600)), name='dispatch')
class HowWeCheckView(TemplateView):
    """Public methodology for keeping listings live, remote and real, with today's numbers."""
    template_name = "jobs/how_we_check.html"

    def get_context_data(self, **kwargs):
        from ..services.trust_stats import trust_stats
        context = super().get_context_data(**kwargs)
        context["stats"] = trust_stats()
        return context


@method_decorator(cache_page(getattr(settings, 'CACHE_TTL_LONG', 3600)), name='dispatch')
class ImpactJobsSnapshotView(TemplateView):
    """Transparent, dated market snapshot that partners can cite."""
    template_name = "jobs/impact_jobs_snapshot.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        now = timezone.now()
        active_jobs = JobService.get_available_jobs()
        active_count = active_jobs.count()

        explicitly_global_query = (
            Q(location__iregex=r"(^|[^a-z])(worldwide|anywhere|global|work from anywhere)([^a-z]|$)")
            | Q(country__iexact="worldwide")
            | Q(country__iexact="global")
            | Q(country__iexact="anywhere")
        )
        named_geography_query = (
            Q(country__isnull=False)
            & ~Q(country="")
            & ~Q(country__iexact="worldwide")
            & ~Q(country__iexact="global")
            & ~Q(country__iexact="anywhere")
        )
        explicitly_global_count = active_jobs.filter(explicitly_global_query).count()
        named_geography_count = active_jobs.filter(named_geography_query).exclude(
            explicitly_global_query
        ).count()
        unclear_scope_count = max(
            active_count - explicitly_global_count - named_geography_count,
            0,
        )

        def percent(count):
            return round((count / active_count) * 100) if active_count else 0

        def coverage(query):
            covered = active_jobs.filter(query).count()
            return {
                "count": covered,
                "percent": percent(covered),
            }

        source_labels = dict(Job.Source.choices)
        sources = [
            {
                "name": source_labels.get(row["source"], row["source"]),
                "count": row["job_count"],
                "percent": round((row["job_count"] / active_count) * 100) if active_count else 0,
            }
            for row in active_jobs.values("source")
            .annotate(job_count=Count("id"))
            .order_by("-job_count")
        ]
        domains = [
            {
                "name": row["category__name"] or "Uncategorized",
                "slug": row["category__slug"],
                "count": row["job_count"],
                "percent": round((row["job_count"] / active_count) * 100) if active_count else 0,
            }
            for row in active_jobs.values("category__name", "category__slug")
            .annotate(job_count=Count("id"))
            .order_by("-job_count")[:10]
        ]

        context.update(
            {
                "snapshot_date": timezone.localdate(),
                "snapshot_updated_at": now,
                "active_count": active_count,
                "last_7_days": active_jobs.filter(posted_at__gte=now - timedelta(days=7)).count(),
                "last_30_days": active_jobs.filter(posted_at__gte=now - timedelta(days=30)).count(),
                "remote_scope": {
                    "explicitly_global": {
                        "count": explicitly_global_count,
                        "percent": percent(explicitly_global_count),
                    },
                    "named_geography": {
                        "count": named_geography_count,
                        "percent": percent(named_geography_count),
                    },
                    "unclear": {
                        "count": unclear_scope_count,
                        "percent": percent(unclear_scope_count),
                    },
                },
                "worldwide_scout_url": (
                    self.request.build_absolute_uri(reverse("jobs:job_scout_preview"))
                    + "?intent=Remote%20impact%20jobs%20I%20can%20do%20from%20anywhere%20in%20the%20world"
                    + "&source=remote_scope_snapshot"
                ),
                "sources": sources,
                "domains": domains,
                "coverage": [
                    ("Salary", coverage(Q(salary_min__gt=0) | Q(salary_max__gt=0))),
                    ("Normalized country", coverage(~Q(country=""))),
                    ("Experience level", coverage(~Q(experience_level=""))),
                    ("Education level", coverage(~Q(education_level=""))),
                    ("Extracted skills", coverage(~Q(skills=[]))),
                ],
            }
        )
        return context


@method_decorator(cache_page(getattr(settings, 'CACHE_TTL_LONG', 3600)), name='dispatch')
class ResourcesView(TemplateView):
    template_name = "jobs/resources.html"

    def get_context_data(self, **kwargs):
        from ..services.job_boards_service import get_job_boards, get_job_board_categories

        context = super().get_context_data(**kwargs)

        # Job boards - fetched from Ethical Job Resources Board (maintained by Ted Fickes & Edward Saperia)
        job_boards, maintainers = get_job_boards()
        context["job_boards"] = job_boards
        context["job_board_categories"] = get_job_board_categories(job_boards)
        context["job_boards_maintainers"] = maintainers

        # Communities & Networks
        context["communities"] = [
            {
                "name": "Work on Climate Slack",
                "url": "https://workonclimate.org/",
                "description": "12k+ climate professionals sharing jobs, advice, and referrals.",
                "type": "Slack",
            },
            {
                "name": "EA Forum Job Board",
                "url": "https://forum.effectivealtruism.org/topics/job-board",
                "description": "Effective altruism community job posts and career discussions.",
                "type": "Forum",
            },
            {
                "name": "Climate Tech VC Talent Collective",
                "url": "https://www.climatetechvc.org/",
                "description": "Connect with climate-focused VCs and their portfolio companies.",
                "type": "Newsletter",
            },
            {
                "name": "Impact Guild",
                "url": "https://impactguild.com/",
                "description": "Community for impact professionals with mentorship and events.",
                "type": "Community",
            },
        ]

        # Learning resources
        context["learning"] = [
            {
                "name": "Terra.do",
                "url": "https://terra.do/",
                "description": "Climate education with career placement support and cohort learning.",
                "type": "Course",
            },
            {
                "name": "80,000 Hours Career Guide",
                "url": "https://80000hours.org/career-guide/",
                "description": "Comprehensive guide to high-impact career planning.",
                "type": "Guide",
            },
            {
                "name": "Climate Draft",
                "url": "https://climatedraft.org/",
                "description": "Free course on climate careers with fellowship opportunities.",
                "type": "Course",
            },
            {
                "name": "My Climate Journey",
                "url": "https://www.mcjcollective.com/",
                "description": "Climate education platform with courses and community.",
                "type": "Platform",
            },
        ]

        # Newsletters
        context["newsletters"] = [
            {
                "name": "Impactful",
                "url": "https://impactful.substack.com/",
                "description": "Weekly digest of impactful career opportunities and insights.",
            },
            {
                "name": "Climate Tech Weekly",
                "url": "https://climatetechweekly.substack.com/",
                "description": "Climate technology news, funding, and job opportunities.",
            },
            {
                "name": "Nonprofit Happy Hour",
                "url": "https://nonprofithappyhour.substack.com/",
                "description": "Nonprofit sector insights, jobs, and community stories.",
            },
            {
                "name": "Good Jobs",
                "url": "https://goodjobs.substack.com/",
                "description": "Curated impact jobs delivered weekly to your inbox.",
            },
        ]

        # Salary & compensation
        context["salary_tools"] = [
            {
                "name": "Glassdoor",
                "url": "https://glassdoor.com/",
                "description": "Salary data with company reviews and interview insights.",
            },
            {
                "name": "Levels.fyi",
                "url": "https://levels.fyi/",
                "description": "Tech salary data with compensation breakdowns by level.",
            },
            {
                "name": "Candid (GuideStar)",
                "url": "https://www.candid.org/",
                "description": "Nonprofit salary data from Form 990 filings.",
            },
        ]

        # Playbook steps
        context["playbook"] = [
            "Set up job alerts on 3-4 boards with specific keywords (role + cause area + 'remote').",
            "Join 2 relevant Slack communities and introduce yourself with your background and goals.",
            "Follow 10-15 target organizations on LinkedIn; engage with their content weekly.",
            "Create a tracking spreadsheet: org name, contact, last touch, status, next action.",
            "Reach out to 3-5 people per week for informational chats (15-20 min max).",
            "Build a 1-page portfolio showing problem → action → outcome for 2-3 projects.",
            "Follow up every 7-10 days with something useful: insight, intro, or iteration.",
        ]

        # Quick tips
        context["tips"] = [
            {
                "title": "Lead with time zone",
                "description": "Include your location and working hours upfront to reduce back-and-forth.",
            },
            {
                "title": "Quantify your impact",
                "description": "Translate metrics into outcomes: uptime → hours saved; funnel lift → beneficiaries served.",
            },
            {
                "title": "Ask for a vibe check",
                "description": "Request a '10-minute chat' instead of a formal interview to increase reply rates.",
            },
            {
                "title": "Show, don't tell",
                "description": "Link to actual work—a doc, repo, or case study beats any resume bullet.",
            },
        ]

        return context


class SalaryToHourlyView(TemplateView):
    template_name = "jobs/tools/salary_to_hourly.html"


class TimezoneOverlapView(TemplateView):
    template_name = "jobs/tools/timezone_overlap.html"


class ApplicantAssistantView(TemplateView):
    template_name = "jobs/applicant_assistant.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        # Pre-populate from query params (linked from job detail pages)
        context["prefill_job_url"] = self.request.GET.get("job_url", "")
        context["prefill_job_title"] = self.request.GET.get("job_title", "")
        context["prefill_org"] = self.request.GET.get("org", "")

        # Check if user is authenticated and get subscription status
        if self.request.user.is_authenticated:
            from ..models import AssistantSubscription, AssistantGeneration
            subscription = AssistantSubscription.get_or_create_for_user(self.request.user)
            context["subscription"] = subscription
            context["can_use"] = subscription.can_use_assistant()

            # Get past generations (limit to 10 most recent)
            context["past_generations"] = AssistantGeneration.objects.filter(
                user=self.request.user
            ).order_by("-created_at")[:10]
        else:
            context["subscription"] = None
            context["can_use"] = False
            context["past_generations"] = []

        return context


def _fetch_job_description(job_url: str) -> str:
    """Best-effort fetch of job description text from a URL."""
    if not job_url:
        return ""
    try:
        resp = fetch_public_html(job_url, total_timeout=10)
        if resp.status != "ok":
            return ""
        text_content = resp.text
        if BeautifulSoup:
            soup = BeautifulSoup(text_content, "html.parser")
            # remove scripts/styles
            for tag in soup(["script", "style", "noscript"]):
                tag.decompose()
            text = soup.get_text("\n", strip=True)
        else:
            # quick tag strip fallback
            text = re.sub(r"<[^>]+>", " ", text_content)
            text = re.sub(r"\s+", " ", text).strip()
        # take a reasonable slice
        return "\n".join(text.splitlines()[:400])
    except Exception:
        return ""


# Drafts are sent by real people under their own names; they must not read as generated.
HUMAN_WRITING_RULES = (
    "You write for job seekers and hiring teams. Write the way a thoughtful, experienced "
    "person writes: plain words, short sentences, specific details. Never use clichés such as "
    "'I am writing to express my interest', 'I am excited/thrilled to apply', 'passionate', "
    "'leverage', 'synergy', 'dynamic', 'fast-paced', 'unlock', 'elevate', 'journey', "
    "'dream job', 'make a difference' or 'stand out'. No exclamation marks and no emoji. "
    "Never invent facts, numbers, names or achievements: use only what you are given, and "
    "where a detail is missing write a short placeholder in square brackets, e.g. [project name]."
)


LLM_FAILED = "LLM call failed: We couldn't write a draft just now. Please try again in a minute."


def _call_llm(prompt: str, ai_client: AIClient | None = None) -> str:
    """Call AI service; injectable for testing or alternate providers."""
    client = ai_client or AIClient()
    try:
        return client.generate(prompt, system_message=HUMAN_WRITING_RULES)
    except Exception:
        logger.exception("Tool generation failed")
        # Callers detect this prefix; provider details stay in the logs.
        return LLM_FAILED


def _request_data(request):
    """Return POST-style data for form, HTMX, and JSON tool submissions."""
    if len(request.body) > 50_000:
        raise ValueError("Payload too large")
    content_type = request.META.get("CONTENT_TYPE", "")
    if "application/json" in content_type:
        try:
            return json.loads(request.body.decode("utf-8") or "{}")
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise ValueError("Invalid JSON payload")
    return request.POST


def _plain_text_result_html(title: str, content: str) -> str:
    return (
        '<div class="card p-6 sm:p-8">'
        f'<h2 class="card-title mb-4">{escape(title)}</h2>'
        f'<div class="whitespace-pre-wrap text-sm leading-7 text-foreground">{escape(content)}</div>'
        "</div>"
    )


def _check_anonymous_ai_limit(request, tool_name: str, limit: int = 10):
    """Bound unauthenticated AI spend per browser session and calendar day."""
    if request.user.is_authenticated:
        return None
    key = f"ai_limit:{tool_name}:{date.today().isoformat()}"
    count = request.session.get(key, 0)
    if count >= limit:
        return JsonResponse(
            {"error": "Free daily limit reached. Please try again tomorrow or sign in."},
            status=429,
        )
    request.session[key] = count + 1
    request.session.modified = True
    return None


@method_decorator(csrf_exempt, name="dispatch")
class ApplicantAssistantGenerateView(View):
    def post(self, request, *args, **kwargs):
        from ..models import AssistantSubscription, AssistantGeneration

        # Require authentication
        if not request.user.is_authenticated:
            return JsonResponse(
                {"error": "Please log in to use the Applicant Assistant", "requires_login": True},
                status=401
            )

        # Check usage limits
        subscription = AssistantSubscription.get_or_create_for_user(request.user)
        if not subscription.can_use_assistant():
            return JsonResponse(
                {
                    "error": "You've used all your free generations. Upgrade to Pro for unlimited access.",
                    "requires_upgrade": True
                },
                status=403
            )

        try:
            data = _request_data(request)
            job_url = (data.get("job_url") or "").strip()
            job_description = (data.get("job_description") or "").strip()
            user_resume = (data.get("resume") or "").strip()
            request_type = (data.get("request_type") or "cover_letter").strip()

            # Fetch job description from URL if provided and not already present
            if not job_description and job_url:
                fetched = _fetch_job_description(job_url)
                job_description = fetched

            # Either job_url or job_description must be provided
            if not job_description:
                if job_url:
                    job_description = f"Job URL (content could not be fetched): {job_url}"
                else:
                    return JsonResponse(
                        {"error": "Job description or job URL is required"}, status=400
                    )

            # Define instructions for each request type
            instructions = {
                "cover_letter": "Write a concise, high-signal cover letter (<= 220 words) with a 2-sentence hook, 3 bullet proof points with metrics, and a short closing. Include time zone if provided.",
                "interview_prep": """Generate 8-10 likely interview questions for this role, organized into:
1. Role-specific questions (3-4): Based on key responsibilities and required skills in the JD
2. Behavioral questions (2-3): STAR-format questions about past experience relevant to this role
3. Mission/values questions (2): Questions about alignment with the organization's impact focus

For each question, provide a brief hint about what the interviewer is looking for (1 sentence max).""",
            }
            instruction = instructions.get(request_type, instructions["cover_letter"])

            # Build the prompt
            prompt = f"""You are helping a candidate apply to a remote, impact-focused role.

Job description:
{job_description}

Candidate CV highlights (if any):
{user_resume if user_resume else "Not provided. Infer likely strengths from the JD and request clarifying details if critical."}

Task: {instruction}

Voice: confident, concise, specific. Avoid filler. Show measurable outcomes. Keep to the point and avoid repeating the JD verbatim.
"""

            # Generate the response
            ai_client = AIClient()
            content = _call_llm(prompt, ai_client)

            # Check if LLM call failed
            if content.startswith("LLM call failed:"):
                return JsonResponse({"error": "We couldn't write a draft just now. Please try again in a minute."}, status=500)

            # Save the generation to history
            AssistantGeneration.objects.create(
                user=request.user,
                generation_type=request_type,
                job_url=job_url,
                job_description=job_description[:5000],  # Limit stored description
                user_highlights=user_resume[:2000],  # Limit stored highlights
                generated_content=content,
            )

            # Record usage after successful generation
            subscription.record_usage()

            return JsonResponse({
                "result": content,
                "uses_remaining": subscription.free_uses_remaining if not subscription.is_subscribed else -1,
                "is_subscribed": subscription.is_subscribed,
            })

        except ValueError:
            return JsonResponse({"error": "Invalid JSON payload"}, status=400)
        except Exception as e:
            return JsonResponse({"error": str(e)}, status=500)


class AssistantSubscribeView(LoginRequiredMixin, View):
    """Send assistant users through the shared Pro confirmation step."""

    def get(self, request, *args, **kwargs):
        return redirect(
            f"{reverse('jobs:job_scout_subscribe')}?source=applicant_assistant"
        )


class ResignationLetterView(TemplateView):
    template_name = "jobs/tools/resignation_letter.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["faqs"] = [
            {"q": "Is this resignation letter generator really free?", "a": "Yes. You can generate up to 3 resignation letters for free without creating an account. Sign up for a free account to get more."},
            {"q": "Can I edit the generated resignation letter?", "a": "Yes. The generated letter is a starting point. We recommend reviewing and personalizing it before sending to your employer."},
            {"q": "How much notice should I give when resigning?", "a": "Two weeks is standard in most industries, but check your employment contract. Some senior roles or contracts may require 30 days or more."},
            {"q": "Should I give a reason for leaving in my resignation letter?", "a": "It's courteous but not required. A brief, positive reason is sufficient. Our generator helps you frame your reason professionally."},
            {"q": "Is my data stored when I use this tool?", "a": "We do not store the personal details you enter in the form. The generated letter is created on-the-fly and only exists in your browser."},
            {"q": "What tone should I choose for my resignation letter?", "a": "Professional is the safest choice for most situations. Choose Friendly if you have a close relationship with your manager, or Formal for traditional corporate environments."},
        ]
        return context


@method_decorator(csrf_exempt, name="dispatch")
class ResignationLetterGenerateView(View):
    def post(self, request, *args, **kwargs):
        if response := _check_anonymous_ai_limit(request, "resignation"):
            return response
        try:
            data = _request_data(request)
        except ValueError:
            return JsonResponse({"error": "Invalid JSON"}, status=400)

        name = (data.get("name") or "").strip()
        manager = (data.get("manager") or "").strip()
        company = (data.get("company") or "").strip()
        position = (data.get("position") or "").strip()
        last_day = (data.get("last_day") or "").strip()
        reason = (data.get("reason") or "new opportunity").strip()
        tone = (data.get("tone") or "professional").strip()
        notes = (data.get("notes") or "").strip()

        if not all([name, manager, company, position, last_day]):
            return JsonResponse({"error": "All required fields must be filled."}, status=400)

        # Rate limit anonymous users via session
        if not request.user.is_authenticated:
            count = request.session.get("rl_gen_count", 0)
            if count >= 3:
                return JsonResponse({"error": "Free limit reached. Please sign up to continue.", "requires_signup": True}, status=429)
            request.session["rl_gen_count"] = count + 1

        prompt = f"""Write a resignation letter with the following details:
- Author: {name}
- Addressed to: {manager}
- Company: {company}
- Position: {position}
- Last working day: {last_day}
- Reason for leaving: {reason}
- Tone: {tone}
{f'- Additional context: {notes}' if notes else ''}

Write a complete, ready-to-send resignation letter. Include today's date, proper formatting with line breaks, and a signature line. Keep it concise (under 300 words). Be {tone} in tone. Express gratitude and offer to help with the transition."""

        content = _call_llm(prompt)

        if content.startswith("LLM call failed:"):
            return JsonResponse({"error": "Generation failed. Please try again."}, status=500)

        return JsonResponse({"result": content})


class AssistantSubscribeSuccessView(LoginRequiredMixin, TemplateView):
    """Handle successful subscription checkout."""

    template_name = "jobs/assistant_subscribe_success.html"

    def get(self, request, *args, **kwargs):
        session_id = request.GET.get("session_id")
        if session_id:
            success, subscription = PaymentService.verify_assistant_subscription(
                session_id, request.user
            )
            if success:
                return super().get(request, *args, **kwargs)

        # If verification failed, redirect to assistant page
        return redirect("jobs:applicant_assistant")


# ---------------------------------------------------------------------------
# Job Description Generator Tool
# ---------------------------------------------------------------------------

class JobDescriptionGeneratorView(TemplateView):
    """SEO landing page for the free Job Description Generator tool."""

    template_name = "jobs/tools/job_description_generator.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["uses_left"] = 3  # Client-side tracking via localStorage
        return context


@method_decorator(csrf_exempt, name="dispatch")
class JobDescriptionGenerateView(View):
    """AJAX endpoint that generates a job description via AI."""

    def post(self, request, *args, **kwargs):
        if response := _check_anonymous_ai_limit(request, "job-description"):
            return response
        try:
            data = _request_data(request)
        except ValueError:
            return JsonResponse({"error": "Invalid JSON payload"}, status=400)

        job_title = (data.get("job_title") or "").strip()
        if not job_title:
            return JsonResponse({"error": "Job title is required."}, status=400)

        company = (data.get("company") or "").strip()
        job_type = (data.get("job_type") or "full-time").strip()
        work_arrangement = (data.get("work_arrangement") or "remote").strip()
        experience_level = (data.get("experience_level") or "mid").strip()
        responsibilities = (data.get("responsibilities") or "").strip()
        skills = (data.get("skills") or "").strip()
        salary_range = (data.get("salary_range") or "").strip()
        impact_focus = (data.get("impact_focus") or "").strip()

        prompt = f"""You are an expert HR copywriter for mission-driven organizations.

Write a professional, compelling job description for the following role. Use inclusive, bias-free language. Structure the output with clear sections using markdown headings (##).

Role details:
- Job Title: {job_title}
- Company/Organization: {company or 'Not specified'}
- Job Type: {job_type}
- Work Arrangement: {work_arrangement}
- Experience Level: {experience_level}
- Key Responsibilities: {responsibilities or 'Not specified — infer typical responsibilities for this role'}
- Required Skills: {skills or 'Not specified — infer typical skills for this role'}
- Salary Range: {salary_range or 'Competitive, commensurate with experience'}
- Impact Focus Area: {impact_focus or 'General social impact'}

Include these sections:
1. **About the Role** — engaging 2-3 sentence overview
2. **About {company or 'the Organization'}** — brief placeholder the employer can customize
3. **Key Responsibilities** — 5-7 bullet points
4. **Qualifications** — Required and Nice-to-Have, using inclusive language (avoid "must have X years")
5. **What We Offer** — 4-5 benefits/perks typical for impact orgs (flexible schedule, mission-driven culture, etc.)
6. **How to Apply** — brief instructions placeholder

Keep it under 600 words. Be specific, avoid jargon, and emphasize mission alignment.
"""

        try:
            ai_client = AIClient()
            content = ai_client.generate(prompt, max_tokens=2000, system_message=HUMAN_WRITING_RULES)
        except Exception as exc:
            return JsonResponse({"error": f"AI generation failed: {exc}"}, status=500)

        if content.startswith("LLM call failed:"):
            return JsonResponse({"error": content}, status=500)

        return JsonResponse({"result": content})


class WordCounterView(TemplateView):
    template_name = "jobs/tools/word_counter.html"


class PomodoroTimerView(TemplateView):
    template_name = "jobs/tools/pomodoro_timer.html"


class InterviewPrepView(TemplateView):
    template_name = "jobs/tools/interview_prep.html"


class InterviewPrepGenerateView(View):
    def post(self, request, *args, **kwargs):
        if response := _check_anonymous_ai_limit(request, "interview"):
            return response
        try:
            data = _request_data(request)
        except ValueError:
            return JsonResponse({"error": "Invalid JSON"}, status=400)

        job_title = (data.get("job_title") or "").strip()
        company_type = (data.get("company_type") or "Nonprofit").strip()
        experience = (data.get("experience_level") or "Mid").strip()
        focus = (data.get("focus_area") or "behavioral").strip()

        if not job_title:
            if request.headers.get("HX-Request"):
                return HttpResponse(
                    _plain_text_result_html("Something needs fixing", "Job title is required."),
                    status=400,
                )
            return JsonResponse({"error": "Job title is required."}, status=400)

        if not request.user.is_authenticated:
            count = request.session.get("ip_gen_count", 0)
            if count >= 3:
                if request.headers.get("HX-Request"):
                    return HttpResponse(
                        _plain_text_result_html(
                            "Free limit reached",
                            "Create a free account to continue generating interview questions.",
                        ),
                        status=429,
                    )
                return JsonResponse({"error": "Free limit reached. Please sign up to continue.", "requires_signup": True}, status=429)
            request.session["ip_gen_count"] = count + 1

        prompt = f"""Generate 10 interview questions for a {experience}-level {job_title} position at a {company_type} organization. Focus on {focus} questions.

For each question, provide:
1. The question
2. A brief suggested answer framework (2-3 bullet points on how to approach the answer)

Format as numbered list. Make questions specific to remote work and the impact/nonprofit sector where relevant. Be practical and realistic."""

        content = _call_llm(prompt)

        if content.startswith("LLM call failed:"):
            if request.headers.get("HX-Request"):
                return HttpResponse(
                    _plain_text_result_html("Generation failed", "Please try again."),
                    status=500,
                )
            return JsonResponse({"error": "Generation failed. Please try again."}, status=500)

        if request.headers.get("HX-Request"):
            return HttpResponse(_plain_text_result_html("Your Interview Questions", content))

        return JsonResponse({"result": content})


# ---------------------------------------------------------------------------
# Salary Negotiation Script Generator
# ---------------------------------------------------------------------------

class SalaryNegotiationView(TemplateView):
    template_name = "jobs/tools/salary_negotiation.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["faqs"] = [
            {"q": "Is this salary negotiation script generator really free?", "a": "Yes. You can generate up to 3 scripts for free without creating an account. Sign up to unlock more."},
            {"q": "Will this work for remote job negotiations?", "a": "Yes. The scripts are tailored for modern remote and hybrid roles, including language about distributed teams, location-based pay, and remote-specific benefits."},
            {"q": "What if my employer says 'that's above our budget'?", "a": "Your generated script includes responses to common objections like budget constraints. It helps you pivot to alternative compensation like equity, bonuses, or extra PTO."},
            {"q": "Should I negotiate salary via email or in a call?", "a": "A live conversation (video or phone) is usually more effective for the initial negotiation. Use your script to prepare, then follow up in writing to confirm agreements."},
            {"q": "Is my data stored when I use this tool?", "a": "No. Your details are processed in real-time to generate the script and are not saved on our servers."},
            {"q": "When is the best time to negotiate salary?", "a": "After receiving an offer but before accepting. For current roles, the best times are during annual reviews, after completing a major project, or when taking on new responsibilities."},
        ]
        return context


@method_decorator(csrf_exempt, name="dispatch")
class SalaryNegotiationGenerateView(View):
    def post(self, request, *args, **kwargs):
        if response := _check_anonymous_ai_limit(request, "salary-negotiation"):
            return response
        try:
            data = _request_data(request)
        except ValueError:
            return JsonResponse({"error": "Invalid JSON"}, status=400)

        job_title = (data.get("job_title") or "").strip()
        company = (data.get("company") or "").strip()
        current_salary = (data.get("current_salary") or "").strip()
        target_salary = (data.get("target_salary") or "").strip()
        scenario = (data.get("scenario") or "new job offer").strip()
        achievements = (data.get("achievements") or "").strip()
        industry = (data.get("industry") or "").strip()
        tone = (data.get("tone") or "confident").strip()

        if not all([job_title, company, current_salary, target_salary]):
            return JsonResponse({"error": "Please fill in all required fields."}, status=400)

        # Rate limit anonymous users via session
        if not request.user.is_authenticated:
            count = request.session.get("sn_gen_count", 0)
            if count >= 3:
                return JsonResponse({"error": "Free limit reached. Please sign up to continue.", "requires_signup": True}, status=429)
            request.session["sn_gen_count"] = count + 1

        prompt = f"""Write a salary negotiation script for the following situation:
- Job title: {job_title}
- Company: {company}
- Current/offered salary: {current_salary}
- Target salary: {target_salary}
- Scenario: {scenario}
- Tone: {tone}
{f'- Industry/sector: {industry}' if industry else ''}
{f'- Key achievements: {achievements[:1500]}' if achievements else ''}

Create a complete, ready-to-use negotiation script with these clearly labeled sections:

**OPENING STATEMENT**
A natural, {tone} opening that frames the conversation positively.

**KEY TALKING POINTS**
3-5 bullet points based on the candidate's achievements and market value. Reference specific accomplishments if provided.

**RESPONSES TO COMMON OBJECTIONS**
Provide scripted responses for:
1. "That's above our budget range"
2. "We can't offer more than [current offer]"
3. "Budget constraints this quarter"
4. "We'll revisit this in 6 months"
Include alternative asks (signing bonus, equity, PTO, remote stipend) as fallback positions.

**CLOSING / THE ASK**
A clear, {tone} closing that reiterates the target and asks for a decision timeline.

Keep the script conversational and natural — something someone can actually say out loud. Under 600 words total."""

        content = _call_llm(prompt)

        if content.startswith("LLM call failed:"):
            return JsonResponse({"error": "Generation failed. Please try again."}, status=500)

        return JsonResponse({"result": content})


class SkillsGapView(TemplateView):
    template_name = "jobs/tools/skills_gap.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["faqs"] = [
            {"q": "Is the Skills Gap Analyzer really free?", "a": "Yes. You can run up to 3 analyses for free without creating an account. Sign up for a free account to unlock more."},
            {"q": "What should I paste in the resume field?", "a": "Anything that describes your experience — your full resume, a skills list, or even your LinkedIn summary. The more detail, the better the analysis."},
            {"q": "How accurate is the match score?", "a": "The AI compares keywords, concepts, and context between your background and the job description. It's a strong directional indicator but not a guarantee of interview outcomes."},
            {"q": "Does this tool store my resume or job description?", "a": "No. Your data is analyzed on-the-fly and is not stored on our servers. It only exists in your browser session."},
            {"q": "What are transferable skills?", "a": "Transferable skills are abilities you already have that relate to what the job requires, even if they aren't an exact match. For example, project management experience transfers well to program coordination roles."},
            {"q": "How can I improve my match score?", "a": "Follow the action plan provided in your results. Focus on the missing skills with the highest impact and consider online courses, certifications, or project-based learning."},
        ]
        return context


@method_decorator(csrf_exempt, name="dispatch")
class SkillsGapAnalyzeView(View):
    def post(self, request, *args, **kwargs):
        if response := _check_anonymous_ai_limit(request, "skills-gap"):
            return response
        try:
            data = _request_data(request)
        except ValueError:
            return JsonResponse({"error": "Invalid JSON"}, status=400)

        resume = (data.get("resume") or "").strip()
        job_description = (data.get("job_description") or "").strip()

        if not resume or not job_description:
            return JsonResponse({"error": "Both fields are required."}, status=400)

        # Rate limit anonymous users via session
        if not request.user.is_authenticated:
            count = request.session.get("sg_gen_count", 0)
            if count >= 3:
                return JsonResponse({"error": "Free limit reached. Please sign up to continue.", "requires_signup": True}, status=429)
            request.session["sg_gen_count"] = count + 1

        prompt = f"""You are a career coach. Analyze the gap between a candidate's background and a job description.

CANDIDATE RESUME / SKILLS:
{resume[:3000]}

JOB DESCRIPTION:
{job_description[:3000]}

Return ONLY valid JSON (no markdown, no code fences) with this exact structure:
{{
  "match_score": <integer 0-100>,
  "match_summary": "<one sentence summary of overall fit>",
  "matching_skills": ["skill1", "skill2", ...],
  "missing_skills": [
    {{"skill": "skill name", "resource": "specific learning resource or course suggestion"}},
    ...
  ],
  "transferable_skills": [
    {{"skill": "skill name", "relevance": "brief explanation of how it transfers"}},
    ...
  ],
  "action_plan": [
    "Step 1: ...",
    "Step 2: ...",
    "Step 3: ..."
  ]
}}

Be specific and actionable. Limit to top 8 matching, 6 missing, and 5 transferable skills. Exactly 3 action plan steps."""

        content = _call_llm(prompt)

        if content.startswith("LLM call failed:"):
            return JsonResponse({"error": "Analysis failed. Please try again."}, status=500)

        # Parse JSON from LLM response
        try:
            # Strip potential markdown code fences
            cleaned = content.strip()
            if cleaned.startswith("```"):
                cleaned = cleaned.split("\n", 1)[1]
                cleaned = cleaned.rsplit("```", 1)[0]
            parsed = json.loads(cleaned)
        except (json.JSONDecodeError, IndexError):
            return JsonResponse({"error": "Analysis failed. Please try again."}, status=500)

        return JsonResponse(parsed)


class FreelanceRateView(TemplateView):
    template_name = "jobs/tools/freelance_rate.html"


class PTOCalculatorView(TemplateView):
    template_name = "jobs/tools/pto_calculator.html"


class ThankYouEmailView(TemplateView):
    template_name = "jobs/tools/thank_you_email.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["faqs"] = [
            {"q": "Is this thank you email generator really free?", "a": "Yes. You can generate up to 3 thank you emails for free without creating an account. Sign up for a free account to get more."},
            {"q": "When should I send a thank you email after an interview?", "a": "Ideally within 24 hours. Same-day is best — it shows enthusiasm and keeps you top of mind while the conversation is fresh."},
            {"q": "Should I send a thank you email after a phone screen?", "a": "Yes. Even a brief thank you after a phone screen sets you apart. Most candidates skip this step, so it's an easy way to stand out."},
            {"q": "What if I interviewed with multiple people?", "a": "Send a personalized thank you to each interviewer. Reference something specific from your conversation with each person. Use this tool once per interviewer."},
            {"q": "Is my data stored when I use this tool?", "a": "We do not store the personal details you enter in the form. The generated email is created on-the-fly and only exists in your browser."},
            {"q": "Can I edit the generated email before sending?", "a": "Of course! The generated email is a strong starting point. We recommend reviewing it, adding personal touches, and proofreading before you send."},
        ]
        return context


@method_decorator(csrf_exempt, name="dispatch")
class ThankYouEmailGenerateView(View):
    def post(self, request, *args, **kwargs):
        if response := _check_anonymous_ai_limit(request, "thank-you"):
            return response
        try:
            data = _request_data(request)
        except ValueError:
            return JsonResponse({"error": "Invalid JSON"}, status=400)

        interviewer = (data.get("interviewer") or "").strip()
        company = (data.get("company") or "").strip()
        position = (data.get("position") or "").strip()
        interview_type = (data.get("interview_type") or "video").strip()
        topics = (data.get("topics") or "").strip()
        tone = (data.get("tone") or "professional").strip()

        if not all([interviewer, company, position, topics]):
            return JsonResponse({"error": "All required fields must be filled."}, status=400)

        # Rate limit anonymous users via session
        if not request.user.is_authenticated:
            count = request.session.get("ty_gen_count", 0)
            if count >= 3:
                return JsonResponse({"error": "Free limit reached. Please sign up to continue.", "requires_signup": True}, status=429)
            request.session["ty_gen_count"] = count + 1

        type_labels = {"phone": "phone screen", "video": "video call", "onsite": "on-site interview", "panel": "panel interview"}
        type_label = type_labels.get(interview_type, interview_type)

        prompt = f"""Write a post-interview thank you email with the following details:
- Interviewer name: {interviewer}
- Company: {company}
- Position applied for: {position}
- Interview type: {type_label}
- Key topics discussed: {topics}
- Tone: {tone}

Write a complete, ready-to-send thank you email. Include a subject line on the first line prefixed with "Subject: ". Keep it concise (under 250 words). Be {tone} in tone. Reference specific topics from the interview to show genuine engagement. Reaffirm enthusiasm for the role and company. End with a professional sign-off."""

        content = _call_llm(prompt)

        if content.startswith("LLM call failed:"):
            return JsonResponse({"error": "Generation failed. Please try again."}, status=500)

        return JsonResponse({"result": content})


class RemoteReadinessQuizView(TemplateView):
    template_name = "jobs/tools/remote_readiness_quiz.html"


# ---------------------------------------------------------------------------
# Pillar / Cluster Content Hub Views
# ---------------------------------------------------------------------------

PILLAR_MAP = {
    "climate-environment-careers": "resources/pillars/climate-environment.html",
    "ai-safety-careers": "resources/pillars/ai-safety.html",
    "global-health-careers": "resources/pillars/global-health.html",
    "humanitarian-ingo-careers": "resources/pillars/humanitarian-ingo.html",
    "effective-altruism-careers": "resources/pillars/effective-altruism.html",
    # New pillars (Feb 2026)
    "mental-health-careers": "resources/pillars/mental-health.html",
    "education-edtech-careers": "resources/pillars/education-edtech.html",
    "economic-development-careers": "resources/pillars/economic-development.html",
    "social-justice-careers": "resources/pillars/social-justice.html",
    "tech-for-good-careers": "resources/pillars/tech-for-good.html",
}

CLUSTER_MAP = {
    # Climate & Environment
    ("climate-environment-careers", "break-into-climate-tech-no-stem"): "resources/clusters/climate-break-into-no-stem.html",
    ("climate-environment-careers", "top-remote-climate-employers"): "resources/clusters/climate-top-remote-employers.html",
    ("climate-environment-careers", "climate-career-switch-guide"): "resources/clusters/climate-career-switch-90-day.html",
    # AI Safety
    ("ai-safety-careers", "non-technical-ai-safety-roles"): "resources/clusters/ai-safety-non-technical-roles.html",
    ("ai-safety-careers", "alignment-research-pathway"): "resources/clusters/ai-safety-alignment-researcher-path.html",
    ("ai-safety-careers", "ai-governance-fellowships"): "resources/clusters/ai-safety-governance-fellowships.html",
    # Global Health
    ("global-health-careers", "remote-global-health-without-mph"): "resources/clusters/health-remote-without-mph.html",
    ("global-health-careers", "un-agency-remote-jobs-guide"): "resources/clusters/health-un-agency-guide.html",
    ("global-health-careers", "health-tech-impact-careers"): "resources/clusters/health-tech-startups-2026.html",
    # Humanitarian / INGO
    ("humanitarian-ingo-careers", "remote-ingo-job-no-field-experience"): "resources/clusters/ingo-no-field-experience.html",
    ("humanitarian-ingo-careers", "un-consultancy-guide"): "resources/clusters/ingo-un-consultancy-guide.html",
    ("humanitarian-ingo-careers", "mel-skills-humanitarian-career"): "resources/clusters/ingo-mel-skills-career.html",
    # Effective Altruism
    ("effective-altruism-careers", "ea-operations-roles-guide"): "resources/clusters/ea-operations-roles.html",
    ("effective-altruism-careers", "mid-career-switch-to-ea"): "resources/clusters/ea-mid-career-switch.html",
    ("effective-altruism-careers", "ea-grantmaking-career-path"): "resources/clusters/ea-grantmaking-career.html",
    # Mental Health (new)
    ("mental-health-careers", "telehealth-therapy-careers"): "resources/clusters/mental-health-telehealth.html",
    ("mental-health-careers", "mental-health-tech-startups"): "resources/clusters/mental-health-startups.html",
    ("mental-health-careers", "mental-health-nonprofit-careers"): "resources/clusters/mental-health-nonprofits.html",
    # Education & EdTech (new)
    ("education-edtech-careers", "edtech-product-roles"): "resources/clusters/edtech-product-roles.html",
    ("education-edtech-careers", "nonprofit-education-careers"): "resources/clusters/education-nonprofits.html",
    ("education-edtech-careers", "instructional-design-careers"): "resources/clusters/education-instructional-design.html",
    # Economic Development (new)
    ("economic-development-careers", "world-bank-ifc-careers"): "resources/clusters/econ-dev-world-bank.html",
    ("economic-development-careers", "microfinance-fintech-impact"): "resources/clusters/econ-dev-microfinance.html",
    ("economic-development-careers", "international-development-consulting"): "resources/clusters/econ-dev-consulting.html",
    # Social Justice (new)
    ("social-justice-careers", "civil-rights-advocacy-careers"): "resources/clusters/social-justice-advocacy.html",
    ("social-justice-careers", "legal-aid-public-interest-law"): "resources/clusters/social-justice-legal.html",
    ("social-justice-careers", "dei-careers-corporate-nonprofit"): "resources/clusters/social-justice-dei.html",
    # Tech for Good (new)
    ("tech-for-good-careers", "software-engineer-nonprofit"): "resources/clusters/tech-for-good-engineering.html",
    ("tech-for-good-careers", "civic-tech-government-careers"): "resources/clusters/tech-for-good-civic.html",
    ("tech-for-good-careers", "data-science-social-impact"): "resources/clusters/tech-for-good-data.html",
}


@lru_cache(maxsize=None)
def guide_template_exists(template_name):
    """Planned guides can be mapped before their template is written."""
    try:
        get_template(template_name)
    except TemplateDoesNotExist:
        return False
    return True


def published_clusters():
    return [key for key, template in CLUSTER_MAP.items() if guide_template_exists(template)]


class PillarDetailView(TemplateView):
    def get_template_names(self):
        slug = self.kwargs["pillar_slug"]
        try:
            return [PILLAR_MAP[slug]]
        except KeyError:
            raise Http404

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["pillar_slug"] = self.kwargs["pillar_slug"]
        return ctx


class ClusterDetailView(TemplateView):
    def get_template_names(self):
        key = (self.kwargs["pillar_slug"], self.kwargs["cluster_slug"])
        template = CLUSTER_MAP.get(key)
        if not template or not guide_template_exists(template):
            raise Http404
        return [template]

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["pillar_slug"] = self.kwargs["pillar_slug"]
        ctx["cluster_slug"] = self.kwargs["cluster_slug"]
        return ctx


class SalaryBenchmarksView(TemplateView):
    """Salary benchmarks across impact domains - SEO and transparency."""
    template_name = "jobs/tools/salary_benchmarks.html"
    
    def get_context_data(self, **kwargs):
        from django.db.models import Avg, Min, Max, Count, Q
        from django.db.models.functions import Coalesce
        from ..models import Job, Category
        
        ctx = super().get_context_data(**kwargs)
        
        # Base queryset - jobs with salary data
        salary_jobs = Job.objects.filter(
            is_active=True,
            salary_min__isnull=False,
            salary_max__isnull=False,
            salary_min__gt=0,
            salary_max__lt=1000000,  # Filter outliers
        )
        
        # Stats by category/domain
        by_domain = salary_jobs.values(
            'category__name', 'category__slug', 'category__icon'
        ).annotate(
            job_count=Count('id'),
            avg_min=Avg('salary_min'),
            avg_max=Avg('salary_max'),
            min_salary=Min('salary_min'),
            max_salary=Max('salary_max'),
        ).filter(
            category__name__isnull=False,
            job_count__gte=10  # Only domains with enough data
        ).order_by('-job_count')
        
        # Stats by job type
        by_type = salary_jobs.values('job_type').annotate(
            job_count=Count('id'),
            avg_min=Avg('salary_min'),
            avg_max=Avg('salary_max'),
        ).order_by('-job_count')
        
        # Stats by experience level
        by_experience = salary_jobs.exclude(
            experience_level__isnull=True
        ).exclude(
            experience_level=''
        ).values('experience_level').annotate(
            job_count=Count('id'),
            avg_min=Avg('salary_min'),
            avg_max=Avg('salary_max'),
        ).order_by('-job_count')
        
        # Overall stats
        overall = salary_jobs.aggregate(
            total_jobs=Count('id'),
            avg_min=Avg('salary_min'),
            avg_max=Avg('salary_max'),
            min_salary=Min('salary_min'),
            max_salary=Max('salary_max'),
        )
        
        # Top paying roles (sample)
        top_paying = salary_jobs.order_by('-salary_max')[:10]
        
        ctx.update({
            'by_domain': list(by_domain),
            'by_type': list(by_type),
            'by_experience': list(by_experience),
            'overall': overall,
            'top_paying': top_paying,
            'total_with_salary': salary_jobs.count(),
        })
        
        return ctx
