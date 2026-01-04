"""
Multi-provider job description parser with batch processing support.

Supports multiple LLM providers:
- DeepSeek (default, cheapest: $0.14-0.28/1M tokens)
- Groq (fastest, free tier available)
- Mistral (original)

This module provides async batch processing of job descriptions to extract
structured fields for the job detail page display sections:
- Mission (description)
- Profile (requirements)
- Impact
- Benefits
- About (company_description)
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from django.conf import settings
from jobs.constants import IMPACT_AREAS_FOR_PROMPT
from jobs.constants.skills import SKILLS_FOR_PROMPT

logger = logging.getLogger(__name__)

EXTRACTION_PROMPT = """You are extracting structured data from a job posting for an impact-focused job board.

Given the job title, organization name, and raw description below, extract ALL available information:

## REQUIRED SECTIONS (format as clean HTML with <p>, <ul>, <li>):

1. **mission**: Core responsibilities - what the person will accomplish. 2-4 bullet points.

2. **profile**: Required skills, experience, qualifications. 2-4 bullet points.

3. **impact**: How this role creates positive change. 1-2 sentences.

4. **benefits**: Compensation, perks, culture highlights. Include salary if mentioned.

5. **about_org**: Organization's mission and work. 1-2 sentences.

## REQUIRED FIELDS (extract exact values or null):

6. **impact_area**: Map to ONE of these categories (use slug):
{impact_areas}

Choose the BEST match based on the organization's mission and role focus. Use "other" only if none fit.

7. **location**: Where the job is based. Use "Remote" if fully remote, or "Remote (US)" / "Remote (Europe)" etc. if location-restricted.

8. **job_type**: One of: "full-time", "part-time", "contract", "freelance", "internship"

9. **experience_level**: One of: "entry", "mid", "senior", "executive", or null

10. **salary_min**: Minimum annual salary as integer (convert to USD if needed), or null

11. **salary_max**: Maximum annual salary as integer (convert to USD if needed), or null

12. **salary_currency**: Currency code like "USD", "EUR", "GBP", or null

13. **skills**: Array of skill slugs required/preferred for this role. Select 3-10 from this list:
{skills_list}

Use exact slugs from the list. Only include skills explicitly mentioned or strongly implied by the requirements.

## GUIDELINES:
- Format text sections as HTML (<p> for paragraphs, <ul>/<li> for lists)
- Extract salary even if given as hourly/monthly (convert to annual)
- For impact_area, consider the organization's PRIMARY focus, not the job function
- For skills, focus on technical skills, tools, and domain expertise mentioned in requirements
- Return valid JSON only

Job Title: {title}
Organization: {organization}

Raw Description:
{description}

Return JSON with keys: mission, profile, impact, benefits, about_org, impact_area, location, job_type, experience_level, salary_min, salary_max, salary_currency, skills
"""

# Provider configurations
PROVIDERS = {
    "deepseek": {
        "base_url": "https://api.deepseek.com",
        "model": "deepseek-chat",
        "env_key": "DEEPSEEK_API_KEY",
        "max_concurrent": 50,  # DeepSeek has very generous rate limits
        "batch_delay": 0.5,
    },
    "groq": {
        "base_url": "https://api.groq.com/openai/v1",
        "model": "llama-3.1-8b-instant",  # Fast and free
        "env_key": "GROQ_API_KEY",
        "max_concurrent": 20,  # Groq is fast
        "batch_delay": 1,
    },
    "mistral": {
        "base_url": None,  # Uses native Mistral client
        "model": "mistral-small-latest",
        "env_key": "MISTRAL_API_KEY",
        "max_concurrent": 10,
        "batch_delay": 2,
    },
}


def get_default_provider() -> str:
    """Get the best available provider based on configured API keys."""
    # Priority: DeepSeek (cheapest) > Groq (free) > Mistral (original)
    if getattr(settings, "DEEPSEEK_API_KEY", None):
        return "deepseek"
    if getattr(settings, "GROQ_API_KEY", None):
        return "groq"
    if getattr(settings, "MISTRAL_API_KEY", None):
        return "mistral"
    raise ValueError("No LLM API key configured. Set DEEPSEEK_API_KEY, GROQ_API_KEY, or MISTRAL_API_KEY.")


class JobParser:
    """Async job parser with multi-provider support."""

    def __init__(self, provider: str | None = None):
        """
        Initialize parser with specified provider.

        Args:
            provider: One of 'deepseek', 'groq', 'mistral', or None for auto-detect
        """
        self.provider_name = provider or get_default_provider()
        self.config = PROVIDERS[self.provider_name]

        api_key = getattr(settings, self.config["env_key"], None)
        if not api_key:
            raise ValueError(f"{self.config['env_key']} is not configured in settings")

        self._semaphore = asyncio.Semaphore(self.config["max_concurrent"])

        if self.provider_name == "mistral":
            from mistralai import Mistral
            self.client = Mistral(api_key=api_key)
            self._parse_fn = self._parse_mistral
        else:
            # OpenAI-compatible providers (DeepSeek, Groq)
            from openai import AsyncOpenAI
            self.client = AsyncOpenAI(
                api_key=api_key,
                base_url=self.config["base_url"],
            )
            self._parse_fn = self._parse_openai_compat

        logger.info(f"Initialized JobParser with provider: {self.provider_name}")

    async def _parse_openai_compat(
        self, title: str, organization: str, description: str
    ) -> dict[str, Any]:
        """Parse using OpenAI-compatible API (DeepSeek, Groq)."""
        prompt = EXTRACTION_PROMPT.format(
            title=title,
            organization=organization,
            description=description,
            impact_areas=IMPACT_AREAS_FOR_PROMPT,
            skills_list=SKILLS_FOR_PROMPT,
        )

        async with self._semaphore:
            try:
                response = await self.client.chat.completions.create(
                    model=self.config["model"],
                    messages=[
                        {
                            "role": "system",
                            "content": "You are a helpful assistant that extracts structured data from job postings. Always respond with valid JSON only.",
                        },
                        {"role": "user", "content": prompt},
                    ],
                    response_format={"type": "json_object"},
                    temperature=0.1,
                )

                content = response.choices[0].message.content
                data = json.loads(content)

                # Small delay to respect rate limits
                await asyncio.sleep(0.2)
                return data

            except json.JSONDecodeError as e:
                logger.error(f"Failed to parse JSON response for '{title}': {e}")
                return {}
            except Exception as e:
                logger.error(f"Error parsing job '{title}' with {self.provider_name}: {e}")
                raise

    async def _parse_mistral(
        self, title: str, organization: str, description: str
    ) -> dict[str, Any]:
        """Parse using native Mistral client."""
        prompt = EXTRACTION_PROMPT.format(
            title=title,
            organization=organization,
            description=description,
            impact_areas=IMPACT_AREAS_FOR_PROMPT,
            skills_list=SKILLS_FOR_PROMPT,
        )

        async with self._semaphore:
            try:
                response = await self.client.chat.complete_async(
                    model=self.config["model"],
                    messages=[
                        {
                            "role": "system",
                            "content": "You are a helpful assistant that extracts structured data from job postings. Always respond with valid JSON only.",
                        },
                        {"role": "user", "content": prompt},
                    ],
                    response_format={"type": "json_object"},
                    temperature=0.1,
                )

                content = response.choices[0].message.content
                data = json.loads(content)

                await asyncio.sleep(0.5)
                return data

            except json.JSONDecodeError as e:
                logger.error(f"Failed to parse JSON response for '{title}': {e}")
                return {}
            except Exception as e:
                logger.error(f"Error parsing job '{title}' with Mistral: {e}")
                raise

    async def parse_single(
        self, title: str, organization: str, description: str
    ) -> dict[str, Any]:
        """Parse a single job description into structured fields."""
        return await self._parse_fn(title, organization, description)

    async def parse_batch(
        self,
        payloads: list[dict],
        batch_size: int = 10,
        progress_callback: callable = None,
    ) -> list[dict]:
        """
        Parse multiple job descriptions in batches with rate limiting.

        Args:
            payloads: List of job payload dicts with keys: title, organization_name, description
            batch_size: Number of jobs per batch (default: 10)
            progress_callback: Optional callback(completed, total) for progress updates

        Returns:
            List of payloads enriched with parsed fields
        """
        if not payloads:
            return []

        total = len(payloads)
        completed = 0
        enriched_payloads = []
        batch_delay = self.config["batch_delay"]

        for batch_start in range(0, total, batch_size):
            batch_end = min(batch_start + batch_size, total)
            batch = payloads[batch_start:batch_end]

            tasks = []
            for payload in batch:
                title = payload.get("title", "Untitled")
                org = payload.get("organization_name", "Unknown")
                desc = payload.get("description", "")

                if len(desc) < 50:
                    logger.debug(f"Skipping '{title}' - description too short")
                    tasks.append(self._return_original(payload))
                else:
                    tasks.append(self._parse_and_enrich(payload, title, org, desc))

            results = await asyncio.gather(*tasks, return_exceptions=True)

            for i, result in enumerate(results):
                if isinstance(result, Exception):
                    logger.error(f"Failed to process job: {result}")
                    enriched_payloads.append(batch[i])
                else:
                    enriched_payloads.append(result)

                completed += 1
                if progress_callback:
                    progress_callback(completed, total)

            if batch_end < total:
                logger.info(f"Batch {batch_start // batch_size + 1} complete, waiting {batch_delay}s...")
                await asyncio.sleep(batch_delay)

        return enriched_payloads

    async def _return_original(self, payload: dict) -> dict:
        """Helper to return original payload (for skipped jobs)."""
        return payload

    async def _parse_and_enrich(
        self, payload: dict, title: str, org: str, desc: str
    ) -> dict:
        """Parse a single job and merge results into payload."""
        parsed = await self._parse_with_retry(title, org, desc)

        enriched = payload.copy()
        if parsed:
            # Text sections (HTML formatted)
            if parsed.get("mission"):
                enriched["description"] = parsed["mission"]
            if parsed.get("profile"):
                enriched["requirements"] = parsed["profile"]
            if parsed.get("impact"):
                enriched["impact"] = parsed["impact"]
            if parsed.get("benefits"):
                enriched["benefits"] = parsed["benefits"]
            if parsed.get("about_org"):
                enriched["company_description"] = parsed["about_org"]

            # Structured fields
            if parsed.get("impact_area"):
                enriched["category_slug"] = parsed["impact_area"]
            if parsed.get("location"):
                enriched["location"] = parsed["location"]
            if parsed.get("job_type"):
                enriched["job_type"] = parsed["job_type"]
            if parsed.get("experience_level"):
                enriched["experience_level"] = parsed["experience_level"]

            # Salary fields (only if extracted and not already set)
            if parsed.get("salary_min") and not enriched.get("salary_min"):
                enriched["salary_min"] = parsed["salary_min"]
            if parsed.get("salary_max") and not enriched.get("salary_max"):
                enriched["salary_max"] = parsed["salary_max"]
            if parsed.get("salary_currency") and not enriched.get("salary_currency"):
                enriched["salary_currency"] = parsed["salary_currency"]

            # Skills (for matching)
            if parsed.get("skills") and isinstance(parsed["skills"], list):
                enriched["skills"] = parsed["skills"]

        return enriched

    async def _parse_with_retry(
        self,
        title: str,
        organization: str,
        description: str,
        max_retries: int = 5,
    ) -> dict[str, Any]:
        """Parse with exponential backoff retry on rate limit errors."""
        for attempt in range(max_retries):
            try:
                return await self.parse_single(title, organization, description)
            except Exception as e:
                error_str = str(e).lower()
                if "rate" in error_str or "429" in error_str or "too many" in error_str:
                    wait_time = (2**attempt) * 2
                    logger.warning(
                        f"Rate limited on '{title}', retrying in {wait_time}s (attempt {attempt + 1}/{max_retries})..."
                    )
                    await asyncio.sleep(wait_time)
                else:
                    logger.error(f"Error parsing '{title}': {e}")
                    return {}
        logger.error(f"Max retries exceeded for '{title}'")
        return {}


# Backwards compatibility alias
MistralJobParser = JobParser
