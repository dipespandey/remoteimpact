"""Single-call DeepSeek extraction with versioned facts and legacy field adapters."""

from __future__ import annotations

import asyncio
import copy
import logging
from typing import Any

from django.conf import settings
from jobs.constants.skills import normalize_skill_slugs
from jobs.services.experience_classifier import ExperienceClassifier
from jobs.services.json_utils import parse_json_object
from jobs.services.job_facts import validate, legacy_fields, source_hash, source_description, reusable_facts, VERSION
from jobs.services.extraction_contract import FIELDS, messages_for, CONTRACT_HASH
from jobs.services.extraction_review import review_fields
from jobs.services.extraction_source import resolve_evidence
from jobs.services import extraction_cache

logger = logging.getLogger(__name__)

# Provider configurations
PROVIDERS = {
    "deepseek": {
        "base_url": "https://api.deepseek.com",
        "model": getattr(settings, "DEEPSEEK_MODEL", "deepseek-v4-flash"),
        "env_key": "DEEPSEEK_API_KEY",
        "max_concurrent": 50,  # DeepSeek has very generous rate limits
        "batch_delay": 0.5,
    },
}


def get_default_provider() -> str:
    """Return the production structured-extraction provider."""
    if getattr(settings, "DEEPSEEK_API_KEY", None):
        return "deepseek"
    raise ValueError("DEEPSEEK_API_KEY is required for LLM job enrichment.")


class JobParser:
    """Async job parser with multi-provider support."""

    def __init__(self, provider: str | None = None, *, optimized: bool | None = None):
        """
        Initialize parser with specified provider.

        Args:
            provider: 'deepseek' or None for the production default
        """
        enabled = getattr(settings, "JOB_EXTRACTION_OPTIMIZED_ENABLED", False) if optimized is None else optimized
        if not enabled:
            from jobs.services.legacy_job_parser import JobParser as LegacyParser
            self._legacy_parser = LegacyParser(provider)
            self.provider_name = self._legacy_parser.provider_name
            self.config = self._legacy_parser.config
            self.client = self._legacy_parser.client
            return
        self.provider_name = provider or get_default_provider()
        self.config = PROVIDERS[self.provider_name]

        api_key = getattr(settings, self.config["env_key"], None)
        if not api_key:
            raise ValueError(f"{self.config['env_key']} is not configured in settings")

        self._semaphore = asyncio.Semaphore(self.config["max_concurrent"])

        from openai import AsyncOpenAI
        self.client = AsyncOpenAI(
            api_key=api_key,
            base_url=self.config["base_url"],
            max_retries=0, timeout=45.0,
        )
        self._parse_fn = self._parse_openai_compat

        logger.info(f"Initialized JobParser with provider: {self.provider_name}")

    async def _parse_openai_compat(
        self, title: str, organization: str, description: str
    ) -> dict[str, Any]:
        """One extraction plus at most one field-scoped repair; never retry absence."""
        attempts = []

        async def request(fields=None):
            messages, spans = messages_for(title, organization, description, fields)
            response = await self.client.chat.completions.create(
                model=self.config["model"], messages=messages,
                response_format={"type": "json_object"}, temperature=0.1,
                max_tokens=2400, extra_body={"thinking": {"type": "disabled"}},
            )
            usage = response.usage
            attempts.append({
                "fields": list(fields or FIELDS),
                "resolved_model": getattr(response, "model", self.config["model"]),
                "input_tokens": getattr(usage, "prompt_tokens", 0) or 0,
                "output_tokens": getattr(usage, "completion_tokens", 0) or 0,
                "cached_input_tokens": getattr(usage, "prompt_cache_hit_tokens", 0) or 0,
            })
            extracted = parse_json_object(response.choices[0].message.content)
            required = set(fields or FIELDS)
            if not isinstance(extracted, dict) or not required <= extracted.keys():
                raise ValueError("Incomplete extraction contract")
            return resolve_evidence({field: extracted[field] for field in required}, spans)

        async with self._semaphore:
            try:
                try:
                    extracted = await request()
                except ValueError:
                    # An empty/invalid object is a failed request, not eight unknown facts.
                    extracted = None
                facts = validate(extracted, title, organization, description) if extracted is not None else {}
                repair_fields = review_fields(facts, extracted, "\n".join((title, organization, description))) if facts else list(FIELDS)
                if repair_fields and getattr(settings, "JOB_EXTRACTION_REVIEW_ENABLED", True):
                    try:
                        repaired = await request(repair_fields)
                        checked = validate(repaired, title, organization, description)
                        for field in repair_fields:
                            if field not in checked.get("rejected", []):
                                if extracted is None:
                                    extracted = {}
                                extracted[field] = repaired[field]
                        if extracted is not None and set(FIELDS) <= extracted.keys():
                            facts = validate(extracted, title, organization, description)
                    except Exception:
                        logger.warning("Extraction repair unavailable or returned an invalid contract")
                if not facts:
                    return {}
                facts["unresolved_fields"] = sorted(set(review_fields(facts, extracted, "\n".join((title, organization, description))) + facts.get("blocked_fields", [])))
                facts["contract_hash"] = CONTRACT_HASH
                facts["model"] = self.config["model"]
                facts["resolved_model"] = attempts[-1]["resolved_model"]
                facts["usage"] = {
                    "input_tokens": sum(row["input_tokens"] for row in attempts),
                    "output_tokens": sum(row["output_tokens"] for row in attempts),
                    "cached_input_tokens": sum(row["cached_input_tokens"] for row in attempts),
                    "requests": len(attempts),
                }
                facts["reviewed_fields"] = repair_fields if len(attempts) > 1 else []
                return legacy_fields(facts)
            except Exception:
                logger.warning("Structured extraction request failed", exc_info=False)
                raise

    async def parse_single(
        self, title: str, organization: str, description: str
    ) -> dict[str, Any]:
        """Reuse source-identical facts across instances, feeds, and concurrent tasks."""
        if hasattr(self, "_legacy_parser"):
            return await self._legacy_parser.parse_single(title, organization, description)
        digest = source_hash(title, organization, description)
        key = extraction_cache.cache_key(VERSION + CONTRACT_HASH, self.config["model"], self.config.get("base_url", ""), digest)
        if not hasattr(self, "_inflight"):
            self._inflight = {}

        async def work():
            cached = await extraction_cache.get_facts(key)
            if reusable_facts(cached, title, organization, description, self.config["model"], CONTRACT_HASH):
                return legacy_fields(copy.deepcopy(cached))
            result = await self._parse_fn(title, organization, description)
            facts = result.get("job_facts") if isinstance(result, dict) else None
            # Do not cache provider failures or still-rejected interpretations for 30 days.
            if facts and not facts.get("unresolved_fields", facts.get("rejected")):
                facts["cache_key"] = key
                await extraction_cache.set_facts(key, facts)
            return result

        task = self._inflight.get(key)
        if task is None:
            task = asyncio.create_task(work())
            self._inflight[key] = task
            def release(done):
                if self._inflight.get(key) is done:
                    self._inflight.pop(key, None)
            task.add_done_callback(release)
        return copy.deepcopy(await asyncio.shield(task))

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
        if hasattr(self, "_legacy_parser"):
            return await self._legacy_parser.parse_batch(payloads, batch_size, progress_callback)
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
                desc = source_description(payload)

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
        if hasattr(self, "_legacy_parser"):
            return await self._legacy_parser._parse_and_enrich(payload, title, org, payload.get("description", ""))
        cached = (payload.get("raw_data") or {}).get("job_facts")
        if reusable_facts(cached, title, org, desc, self.config["model"], CONTRACT_HASH):
            parsed = legacy_fields(cached)
        else:
            parsed = await self._parse_with_retry(title, org, desc)

        enriched = payload.copy()
        # Structured source data outranks generated enrichment, including when
        # it lives in a feed's raw payload rather than the top-level fields.
        from jobs.services.raw_data_extractor import RawDataExtractor
        upstream = RawDataExtractor.extract(str(payload.get("source") or ""), payload.get("raw_data") or {})
        for field in ("job_type", "location", "country", "experience_level", "education_level",
                      "salary_min", "salary_max", "salary_currency"):
            if not enriched.get(field) and upstream.get(field) is not None:
                enriched[field] = upstream[field]
        evidence_source = "\n".join((title, org, desc))
        if parsed:
            # Keep the source text authoritative. AI output is enrichment, not a
            # replacement for qualifications, legal terms, or application details.
            raw_data = dict(enriched.get("raw_data") or {})
            if parsed.get("job_facts"):
                raw_data["job_facts"] = parsed["job_facts"]
            enriched["raw_data"] = raw_data

            # Structured fields
            if parsed.get("impact_area"):
                enriched["category_slug"] = parsed["impact_area"]
            if parsed.get("location") and not enriched.get("location"):
                enriched["location"] = parsed["location"]
            if parsed.get("job_type") and not enriched.get("job_type"):
                enriched["job_type"] = parsed["job_type"]
            if parsed.get("experience_level") and not enriched.get("experience_level") and _evidence_is_present(parsed.get("experience_evidence"), evidence_source):
                enriched["experience_level"] = parsed["experience_level"]
            if parsed.get("education_level") and not enriched.get("education_level") and _evidence_is_present(parsed.get("education_evidence"), evidence_source):
                enriched["education_level"] = parsed["education_level"]
            if parsed.get("country") and not enriched.get("country"):
                enriched["country"] = parsed["country"]

            # Salary fields (only if extracted and not already set)
            salary_supported = _evidence_is_present(parsed.get("salary_evidence"), evidence_source)
            if salary_supported and parsed.get("salary_min") and not enriched.get("salary_min"):
                enriched["salary_min"] = parsed["salary_min"]
            if salary_supported and parsed.get("salary_max") and not enriched.get("salary_max"):
                enriched["salary_max"] = parsed["salary_max"]
            if salary_supported and parsed.get("salary_currency") and not enriched.get("salary_currency"):
                enriched["salary_currency"] = parsed["salary_currency"]

            # Skills (for matching)
            if isinstance(parsed.get("skills"), list):
                enriched["skills"] = normalize_skill_slugs(parsed["skills"])


        if parsed.get("job_facts"):
            return enriched

        # Legacy fallback when extraction failed.
        classified_level, reason = ExperienceClassifier.get_best_classification(
            title=title,
            description=desc,
            job_type=enriched.get("job_type", ""),
            existing_level=enriched.get("experience_level")
        )
        if classified_level and classified_level != enriched.get("experience_level"):
            logger.debug(f"Classifier refined experience_level to {classified_level}: {reason}")
            enriched["experience_level"] = classified_level
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


# Backwards compatibility alias for legacy imports; parsing is DeepSeek-only.
MistralJobParser = JobParser


def _evidence_is_present(evidence: Any, source_text: str) -> bool:
    """Require an LLM quote to be traceable to the original posting."""
    from jobs.services.job_facts import supported
    return supported(evidence, source_text)
