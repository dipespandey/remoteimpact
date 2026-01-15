"""
Organization Signals Service

Auto-detects third-party endorsements and certifications for organizations.
Used during onboarding and job imports to enrich organization profiles.
"""

import logging
import re
from typing import Optional
from urllib.parse import urlparse

import httpx

from ..models import Organization, Job

logger = logging.getLogger(__name__)


# GiveWell top and standout charities (as of 2024)
# Source: https://www.givewell.org/charities/top-charities
GIVEWELL_TOP_CHARITIES = {
    "against malaria foundation",
    "malaria consortium",
    "helen keller international",
    "new incentives",
    "givewell",
    "givedirectly",
    "evidence action",
    "sightsavers",
    "unlimit health",
    "development media international",
}


class OrgSignalsService:
    """Service for detecting and updating organization signals."""

    @classmethod
    def detect_all_signals(cls, org: Organization) -> dict:
        """
        Run all signal detections for an organization.

        Returns dict of detected signals without saving to DB.
        Call update_org_signals() to persist changes.
        """
        signals = {
            "is_80k_recommended": cls.detect_80k_recommended(org),
            "is_givewell_top_charity": cls.detect_givewell(org),
            "is_bcorp_certified": False,
            "bcorp_score": None,
            "bcorp_profile_url": "",
        }

        # B Corp detection (may involve API call)
        bcorp_data = cls.detect_bcorp(org)
        if bcorp_data:
            signals.update(bcorp_data)

        return signals

    @classmethod
    def update_org_signals(cls, org: Organization) -> dict:
        """
        Detect and save all signals for an organization.

        Returns dict of what was detected/updated.
        """
        signals = cls.detect_all_signals(org)

        # Update org with detected signals
        updated = False
        for field, value in signals.items():
            if value and getattr(org, field) != value:
                setattr(org, field, value)
                updated = True

        if updated:
            org.save(update_fields=list(signals.keys()))
            logger.info(f"Updated signals for org {org.name}: {signals}")

        return signals

    @classmethod
    def detect_80k_recommended(cls, org: Organization) -> bool:
        """
        Check if org is recommended by 80,000 Hours.

        Detection methods:
        1. Org has jobs imported from 80,000 Hours source
        2. Org name matches known 80K orgs (future: maintain list)
        """
        # Check if any jobs were imported from 80,000 Hours
        has_80k_jobs = Job.objects.filter(
            organization=org,
            source=Job.Source.EIGHTY_THOUSAND
        ).exists()

        return has_80k_jobs

    @classmethod
    def detect_givewell(cls, org: Organization) -> bool:
        """
        Check if org is a GiveWell top/standout charity.

        Uses a curated list of GiveWell top charities.
        """
        org_name_lower = org.name.lower().strip()

        # Direct name match
        if org_name_lower in GIVEWELL_TOP_CHARITIES:
            return True

        # Partial match for variations
        for charity in GIVEWELL_TOP_CHARITIES:
            if charity in org_name_lower or org_name_lower in charity:
                return True

        return False

    @classmethod
    def detect_bcorp(cls, org: Organization) -> Optional[dict]:
        """
        Check if org is B Corp certified.

        Uses the B Corp directory search.
        Returns dict with bcorp fields or None if not found.
        """
        if not org.name:
            return None

        try:
            # Try the B Corp API (unofficial, may have rate limits)
            # Fallback to simple name-based search
            bcorp_data = cls._search_bcorp_directory(org.name)
            if bcorp_data:
                return {
                    "is_bcorp_certified": True,
                    "bcorp_score": bcorp_data.get("score"),
                    "bcorp_profile_url": bcorp_data.get("profile_url", ""),
                }
        except Exception as e:
            logger.warning(f"B Corp lookup failed for {org.name}: {e}")

        return None

    @classmethod
    def _search_bcorp_directory(cls, org_name: str) -> Optional[dict]:
        """
        Search B Corp directory for organization.

        Note: This uses a simple search approach.
        For production, consider caching or using official API if available.
        """
        # Clean org name for search
        search_name = re.sub(r'[^\w\s]', '', org_name).strip()
        if not search_name:
            return None

        try:
            # Try the community B Corps API
            # https://github.com/PRANAVBHATIA1999/B-Corps-API
            api_url = f"https://bcorps.herokuapp.com/bcorps"

            with httpx.Client(timeout=5.0) as client:
                response = client.get(api_url, params={"name": search_name[:50]})

                if response.status_code == 200:
                    data = response.json()
                    if data and isinstance(data, list) and len(data) > 0:
                        # Find best match
                        for corp in data:
                            corp_name = corp.get("company_name", "").lower()
                            if search_name.lower() in corp_name or corp_name in search_name.lower():
                                return {
                                    "score": corp.get("overall_score"),
                                    "profile_url": corp.get("company_url", ""),
                                }
        except httpx.TimeoutException:
            logger.debug(f"B Corp API timeout for {org_name}")
        except Exception as e:
            logger.debug(f"B Corp API error for {org_name}: {e}")

        return None

    @classmethod
    def mark_80k_orgs_from_imports(cls) -> int:
        """
        Batch job: Mark all orgs that have 80K-imported jobs.

        Run periodically after job imports.
        Returns count of orgs updated.
        """
        # Find orgs with 80K jobs that aren't marked yet
        orgs_with_80k = Organization.objects.filter(
            jobs__source=Job.Source.EIGHTY_THOUSAND,
            is_80k_recommended=False
        ).distinct()

        count = orgs_with_80k.update(is_80k_recommended=True)
        if count:
            logger.info(f"Marked {count} orgs as 80K recommended")
        return count

    @classmethod
    def get_signals_summary(cls, org: Organization) -> dict:
        """
        Get a summary of org signals for display.

        Returns dict suitable for templates.
        """
        signals = []

        if org.is_80k_recommended:
            signals.append({
                "name": "80,000 Hours",
                "label": "Recommended by 80,000 Hours",
                "icon": "🎯",
                "verified": True,
                "description": "This organization's jobs are listed on 80,000 Hours, "
                               "a leading career guide for high-impact work."
            })

        if org.is_givewell_top_charity:
            signals.append({
                "name": "GiveWell",
                "label": "GiveWell Top Charity",
                "icon": "⭐",
                "verified": True,
                "description": "Rated as one of the most cost-effective charities "
                               "by GiveWell's rigorous analysis."
            })

        if org.is_bcorp_certified:
            score_str = f" (Score: {org.bcorp_score})" if org.bcorp_score else ""
            signals.append({
                "name": "B Corp",
                "label": f"Certified B Corp{score_str}",
                "icon": "🌱",
                "verified": True,
                "url": org.bcorp_profile_url,
                "description": "Certified to meet high standards of social and "
                               "environmental performance."
            })

        if org.has_public_impact_report:
            signals.append({
                "name": "Impact Report",
                "label": "Publishes Impact Report",
                "icon": "📊",
                "verified": False,  # Self-reported
                "url": org.impact_report_url,
                "description": "Organization publishes regular impact reports."
            })

        if org.has_public_financials:
            signals.append({
                "name": "Financials",
                "label": "Public Financials",
                "icon": "📄",
                "verified": False,
                "url": org.financials_url,
                "description": "Financial information is publicly available."
            })

        return {
            "signals": signals,
            "verified_count": sum(1 for s in signals if s.get("verified")),
            "total_count": len(signals),
            "has_any": len(signals) > 0,
        }
