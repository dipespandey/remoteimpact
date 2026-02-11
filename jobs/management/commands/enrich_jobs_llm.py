"""
Use LLM to extract job descriptions from pages we can't scrape with custom crawlers.
"""
import time
import requests
from django.core.management.base import BaseCommand
from django.db.models.functions import Length
from django.conf import settings
from jobs.models import Job, Organization
from django.utils.text import slugify
import json
import re


FETCH_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

# Sources we already have dedicated crawlers for
SOURCES_WITH_CRAWLERS = ["greenhouse", "lever", "ashby", "charityjob", "idealist", "reliefweb"]


def fetch_page_text(url: str, max_chars: int = 15000) -> str:
    """Fetch page and return clean text content."""
    try:
        response = requests.get(url, headers=FETCH_HEADERS, timeout=30)
        if response.status_code != 200:
            return ""
        
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(response.text, "html.parser")
        
        # Remove script/style
        for tag in soup.find_all(["script", "style", "nav", "footer", "header"]):
            tag.decompose()
        
        text = soup.get_text(" ", strip=True)
        return text[:max_chars]
    except Exception as e:
        return ""


def extract_with_llm(page_text: str, job_title: str, provider: str = "groq") -> dict:
    """Use LLM to extract job details from page text."""
    
    prompt = f"""Extract job posting details from this webpage text. The job title is: "{job_title}"

Return a JSON object with these fields (use null if not found):
- description: Full job description (responsibilities, about the role, etc.)
- requirements: Requirements and qualifications
- organization_name: Company/organization name
- salary_min: Minimum salary as number (no currency symbol)
- salary_max: Maximum salary as number
- salary_currency: Currency code (USD, GBP, EUR, etc.)
- location: Job location
- job_type: full-time, part-time, contract, or freelance

Page text:
{page_text[:12000]}

Return ONLY valid JSON, no other text."""

    try:
        if provider == "groq":
            api_key = getattr(settings, 'GROQ_API_KEY', None)
            if not api_key:
                return {}
            
            response = requests.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": "llama-3.1-8b-instant",
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": 0.1,
                    "max_tokens": 4000,
                },
                timeout=60,
            )
            
            if response.status_code == 200:
                content = response.json()["choices"][0]["message"]["content"]
                # Extract JSON from response
                json_match = re.search(r'\{[\s\S]*\}', content)
                if json_match:
                    return json.loads(json_match.group())
        
        elif provider == "deepseek":
            api_key = getattr(settings, 'DEEPSEEK_API_KEY', None)
            if not api_key:
                return {}
            
            response = requests.post(
                "https://api.deepseek.com/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": "deepseek-chat",
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": 0.1,
                    "max_tokens": 4000,
                },
                timeout=60,
            )
            
            if response.status_code == 200:
                content = response.json()["choices"][0]["message"]["content"]
                json_match = re.search(r'\{[\s\S]*\}', content)
                if json_match:
                    return json.loads(json_match.group())
                    
    except Exception as e:
        pass
    
    return {}


class Command(BaseCommand):
    help = "Use LLM to enrich jobs with missing descriptions"

    def add_arguments(self, parser):
        parser.add_argument(
            "--limit",
            type=int,
            default=50,
            help="Max jobs to process (default: 50)",
        )
        parser.add_argument(
            "--min-desc-len",
            type=int,
            default=500,
            help="Process jobs with descriptions shorter than this (default: 500)",
        )
        parser.add_argument(
            "--provider",
            type=str,
            default="groq",
            choices=["groq", "deepseek"],
            help="LLM provider (default: groq)",
        )
        parser.add_argument(
            "--delay",
            type=float,
            default=2.0,
            help="Delay between requests (default: 2.0)",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Just show what would be processed",
        )

    def handle(self, *args, **options):
        limit = options["limit"]
        min_len = options["min_desc_len"]
        provider = options["provider"]
        delay = options["delay"]
        dry_run = options["dry_run"]

        # Find jobs with short descriptions from sources we don't have crawlers for
        jobs = list(
            Job.objects.filter(is_active=True)
            .exclude(source__in=SOURCES_WITH_CRAWLERS)
            .annotate(desc_len=Length("description"))
            .filter(desc_len__lt=min_len)
            .exclude(raw_data__llm_enriched=True)
            .order_by("posted_at")[:limit]
        )

        self.stdout.write(f"Found {len(jobs)} jobs to enrich with LLM ({provider})")

        if dry_run:
            for job in jobs[:10]:
                self.stdout.write(f"  [{job.source}] {job.title[:50]}...")
            return

        enriched = 0
        failed = 0

        for i, job in enumerate(jobs, 1):
            self.stdout.write(f"[{i}/{len(jobs)}] {job.source}: {job.title[:40]}...")

            # Fetch page
            page_text = fetch_page_text(job.application_url)
            if not page_text:
                self.stdout.write(self.style.WARNING("  Could not fetch page"))
                failed += 1
                time.sleep(delay)
                continue

            # Extract with LLM
            extracted = extract_with_llm(page_text, job.title, provider)
            
            if not extracted or not extracted.get("description"):
                self.stdout.write(self.style.WARNING("  LLM could not extract description"))
                failed += 1
                # Mark as attempted
                job.raw_data = job.raw_data or {}
                job.raw_data["llm_enriched"] = False
                job.raw_data["llm_error"] = "No description extracted"
                job.save(update_fields=["raw_data"])
                time.sleep(delay)
                continue

            # Update job
            desc = extracted.get("description", "")
            if len(desc) > len(job.description or ""):
                job.description = desc

            if extracted.get("requirements"):
                job.requirements = extracted["requirements"]

            if extracted.get("salary_min"):
                job.salary_min = extracted["salary_min"]
            if extracted.get("salary_max"):
                job.salary_max = extracted["salary_max"]
            if extracted.get("salary_currency"):
                job.salary_currency = extracted["salary_currency"]

            if extracted.get("location"):
                job.location = extracted["location"]

            if extracted.get("job_type"):
                job.job_type = extracted["job_type"]

            # Update organization if unknown
            org_name = extracted.get("organization_name")
            if org_name and job.organization and "unknown" in job.organization.name.lower():
                org, _ = Organization.objects.get_or_create(
                    slug=slugify(org_name)[:50],
                    defaults={"name": org_name}
                )
                job.organization = org

            job.raw_data = job.raw_data or {}
            job.raw_data["llm_enriched"] = True
            job.raw_data["llm_provider"] = provider
            job.save()

            enriched += 1
            new_len = len(job.description or "")
            self.stdout.write(self.style.SUCCESS(f"  ✓ Enriched: {new_len} chars"))

            time.sleep(delay)

        self.stdout.write(self.style.SUCCESS(
            f"\nDone! Enriched: {enriched}, Failed: {failed}"
        ))
