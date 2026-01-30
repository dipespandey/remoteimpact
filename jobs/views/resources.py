from django.views.generic import TemplateView, View
from django.db.models import Count, Q
from django.http import JsonResponse
from django.shortcuts import redirect
from django.contrib.auth.mixins import LoginRequiredMixin
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import csrf_exempt
import json
import re

import requests
from bs4 import BeautifulSoup

from ..models import Job, Category, SeekerProfile
from ..services.ai import AIClient
from ..services.payment_service import PaymentService


class CostOfLivingComparisonView(TemplateView):
    template_name = "jobs/tools/cost_of_living.html"


class HomeView(TemplateView):
    template_name = "jobs/home.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        # Featured Jobs
        featured_jobs = (
            Job.objects.filter(is_active=True, is_featured=True)
            .select_related("organization", "category")
            .order_by("-posted_at")[:6]
        )

        # Latest Jobs
        latest_jobs = (
            Job.objects.filter(is_active=True)
            .exclude(id__in=[j.id for j in featured_jobs])
            .select_related("organization", "category")
            .order_by("-posted_at")[:12]
        )

        # Categories with counts
        categories = Category.objects.annotate(
            job_count=Count("jobs", filter=Q(jobs__is_active=True))
        ).order_by("name")

        # Stats for hero section (rounded down to nearest 100)
        active_jobs_count = Job.objects.filter(is_active=True).count()
        active_jobs_rounded = (active_jobs_count // 100) * 100
        categories_count = Category.objects.count()

        # Organization count (rounded down to nearest 100)
        from ..models import Organization
        from django.utils import timezone
        from datetime import timedelta
        org_count = Organization.objects.filter(jobs__is_active=True).distinct().count()
        org_count_rounded = (org_count // 100) * 100

        # Professionals joined this month
        from django.contrib.auth import get_user_model
        User = get_user_model()
        now = timezone.now()
        month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        professionals_this_month = User.objects.filter(date_joined__gte=month_start).count()

        # Featured organizations (ones with active jobs)
        from ..models import Organization
        featured_orgs = (
            Organization.objects.filter(jobs__is_active=True)
            .distinct()
            .order_by("name")[:5]
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
        resp = requests.get(
            job_url, timeout=10, headers={"User-Agent": "RemoteImpactBot/1.0"}
        )
        resp.raise_for_status()
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


def _call_llm(prompt: str, ai_client: AIClient | None = None) -> str:
    """Call AI service; injectable for testing or alternate providers."""
    client = ai_client or AIClient()
    try:
        return client.generate(prompt)
    except Exception as exc:
        return f"LLM call failed: {exc}"


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
            data = json.loads(request.body)
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
                return JsonResponse({"error": content}, status=500)

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

        except json.JSONDecodeError:
            return JsonResponse({"error": "Invalid JSON payload"}, status=400)
        except Exception as e:
            return JsonResponse({"error": str(e)}, status=500)


class AssistantSubscribeView(LoginRequiredMixin, View):
    """Redirect to Stripe checkout for Assistant Pro subscription."""

    def get(self, request, *args, **kwargs):
        domain_url = request.build_absolute_uri("/").rstrip("/")
        try:
            checkout_url = PaymentService.create_assistant_subscription_session(
                request.user, domain_url
            )
            return redirect(checkout_url)
        except Exception as e:
            # Log error and redirect back with error message
            return redirect("jobs:applicant_assistant")


class ResignationLetterView(TemplateView):
    template_name = "jobs/tools/resignation_letter.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["faqs"] = [
            {"q": "Is this resignation letter generator really free?", "a": "Yes! You can generate up to 3 resignation letters for free without creating an account. Sign up for a free account to get more."},
            {"q": "Can I edit the generated resignation letter?", "a": "Absolutely. The generated letter is a starting point. We recommend reviewing and personalizing it before sending to your employer."},
            {"q": "How much notice should I give when resigning?", "a": "Two weeks is standard in most industries, but check your employment contract. Some senior roles or contracts may require 30 days or more."},
            {"q": "Should I give a reason for leaving in my resignation letter?", "a": "It's courteous but not required. A brief, positive reason is sufficient. Our generator helps you frame your reason professionally."},
            {"q": "Is my data stored when I use this tool?", "a": "We do not store the personal details you enter in the form. The generated letter is created on-the-fly and only exists in your browser."},
            {"q": "What tone should I choose for my resignation letter?", "a": "Professional is the safest choice for most situations. Choose Friendly if you have a close relationship with your manager, or Formal for traditional corporate environments."},
        ]
        return context


@method_decorator(csrf_exempt, name="dispatch")
class ResignationLetterGenerateView(View):
    def post(self, request, *args, **kwargs):
        try:
            data = json.loads(request.body)
        except json.JSONDecodeError:
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
        try:
            data = json.loads(request.body)
        except json.JSONDecodeError:
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
            content = ai_client.generate(prompt, max_tokens=2000)
        except Exception as exc:
            return JsonResponse({"error": f"AI generation failed: {exc}"}, status=500)

        if content.startswith("LLM call failed:"):
            return JsonResponse({"error": content}, status=500)

        return JsonResponse({"result": content})
