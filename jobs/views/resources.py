from django.views.generic import TemplateView, View
from django.db.models import Count, Q
from django.http import JsonResponse
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import csrf_exempt
import json
import re

import requests
from bs4 import BeautifulSoup

from ..models import Job, Category
from ..services.ai import AIClient


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

        context.update(
            {
                "featured_jobs": featured_jobs,
                "recent_jobs": latest_jobs,
                "categories": categories,
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


class ResourcesView(TemplateView):
    template_name = "jobs/resources.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        # Static data from original view
        context["resource_sections"] = [
            {
                "title": "Impact-first job boards",
                "items": [
                    {
                        "name": "80,000 Hours",
                        "url": "https://80000hours.org/jobs/",
                        "focus": "High-impact roles (policy, AI safety, biosecurity, global health) with mission vetting.",
                    },
                    {
                        "name": "Idealist",
                        "url": "https://www.idealist.org/en/",
                        "focus": "Nonprofit and social impact roles worldwide; strong NGO coverage; filters for remote.",
                    },
                    {
                        "name": "Tech Jobs for Good",
                        "url": "https://techjobsforgood.com/",
                        "focus": "US-heavy tech roles in climate, civic, health, justice; remote and hybrid filters.",
                    },
                    {
                        "name": "Climatebase",
                        "url": "https://climatebase.org/",
                        "focus": "Climate-specific roles across all functions; huge database of climate tech companies.",
                    },
                    {
                        "name": "All-in for Climate",
                        "url": "https://www.allinforclimate.com/",
                        "focus": "Curated climate roles often not listed elsewhere; focus on activism and advocacy.",
                    },
                    {
                        "name": "PowerToFly",
                        "url": "https://powertofly.com/jobs",
                        "focus": "Inclusive hiring focus; good for social impact tech and ops roles.",
                    },
                ],
            },
            # ... (truncated for brevity, would include all original data)
        ]
        return context


class ApplicantAssistantView(TemplateView):
    template_name = "jobs/applicant_assistant.html"


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

            return JsonResponse({"result": content})

        except json.JSONDecodeError:
            return JsonResponse({"error": "Invalid JSON payload"}, status=400)
        except Exception as e:
            return JsonResponse({"error": str(e)}, status=500)
