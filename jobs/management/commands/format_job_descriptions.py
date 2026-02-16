"""
Use LLM to restructure cluttered job descriptions into clean, well-formatted markdown.
"""
import time
import requests
import json
import re
from django.core.management.base import BaseCommand
from django.conf import settings
from jobs.models import Job


def is_cluttered(description: str) -> bool:
    """Check if description is poorly formatted (no sections, everything runs together)."""
    if not description or len(description) < 500:
        return False
    
    line_breaks = description.count('\n')
    char_count = len(description)
    
    # Calculate average chars per line (high = wall of text)
    if line_breaks == 0:
        chars_per_line = float('inf')
    else:
        chars_per_line = char_count / line_breaks
    
    # Check for markdown structure
    has_headers = '##' in description or bool(re.search(r'\n\*\*[A-Z]', description))
    has_bullets = '- ' in description or '• ' in description
    
    # Criteria for "needs formatting":
    # 1. Wall of text: >300 chars per line on average
    if chars_per_line > 300:
        return True
    
    # 2. No structure: >150 chars per line AND no markdown headers
    if chars_per_line > 150 and not has_headers:
        return True
    
    # 3. Minimal breaks: large description with very few line breaks
    if line_breaks < 10 and char_count > 1000:
        return True
    
    return False


def format_with_llm(description: str, job_title: str, provider: str = "groq") -> str:
    """Use LLM to restructure description into clean markdown."""
    
    prompt = f"""Restructure this job description into clean, well-formatted markdown. 
The job title is: "{job_title}"

Rules:
- Use ## for main section headers (About the Role, Responsibilities, Requirements, etc.)
- Use bullet points (- ) for lists
- Add blank lines between sections
- Keep all the original information, just format it better
- Don't add information that isn't there
- If salary/location/dates are mentioned inline, extract them to the top

Original description:
{description[:10000]}

Return ONLY the formatted markdown, no explanation."""

    try:
        if provider == "groq":
            api_key = getattr(settings, 'GROQ_API_KEY', None)
            if not api_key:
                return ""
            
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
                return response.json()["choices"][0]["message"]["content"].strip()
        
        elif provider == "deepseek":
            api_key = getattr(settings, 'DEEPSEEK_API_KEY', None)
            if not api_key:
                return ""
            
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
                return response.json()["choices"][0]["message"]["content"].strip()
                    
    except Exception as e:
        pass
    
    return ""


class Command(BaseCommand):
    help = "Restructure cluttered job descriptions into clean markdown"

    def add_arguments(self, parser):
        parser.add_argument(
            "--limit",
            type=int,
            default=50,
            help="Max jobs to process (default: 50)",
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
        parser.add_argument(
            "--job-id",
            type=int,
            help="Format a specific job by ID",
        )

    def handle(self, *args, **options):
        limit = options["limit"]
        provider = options["provider"]
        delay = options["delay"]
        dry_run = options["dry_run"]
        job_id = options.get("job_id")

        if job_id:
            jobs = Job.objects.filter(id=job_id)
        else:
            # Find cluttered descriptions that haven't been formatted yet
            # Note: exclude() with JSONField key lookup doesn't work when key is missing
            # So we use contains() instead to find jobs WITH the flag, then exclude those
            jobs = Job.objects.filter(is_active=True).exclude(
                raw_data__contains={'description_formatted': True}
            )
            
            # Filter to cluttered ones
            cluttered_jobs = []
            for job in jobs.iterator():
                if is_cluttered(job.description):
                    cluttered_jobs.append(job)
                    if len(cluttered_jobs) >= limit:
                        break
            
            jobs = cluttered_jobs

        self.stdout.write(f"Found {len(jobs)} cluttered descriptions to format")

        if dry_run:
            for job in jobs[:10]:
                line_breaks = job.description.count('\n') if job.description else 0
                self.stdout.write(f"  [{job.source}] {job.title[:40]}... ({line_breaks} line breaks)")
            return

        formatted = 0
        failed = 0

        for i, job in enumerate(jobs, 1):
            self.stdout.write(f"[{i}/{len(jobs)}] {job.title[:40]}...")

            new_desc = format_with_llm(job.description, job.title, provider)
            
            if not new_desc or len(new_desc) < len(job.description) * 0.5:
                self.stdout.write(self.style.WARNING("  Failed to format"))
                failed += 1
                time.sleep(delay)
                continue

            # Update job
            job.description = new_desc
            job.raw_data = job.raw_data or {}
            job.raw_data["description_formatted"] = True
            job.raw_data["format_provider"] = provider
            job.save()

            formatted += 1
            self.stdout.write(self.style.SUCCESS("  ✓ Formatted"))

            time.sleep(delay)

        self.stdout.write(self.style.SUCCESS(
            f"\nDone! Formatted: {formatted}, Failed: {failed}"
        ))
