"""
Raw Data Extractor Service

Extracts structured fields from the raw_data JSON for each job source.
This is a NO-COST operation - pure Python parsing, no AI involved.

Supports:
- Climatebase: experience_levels, salary_from/to, countries, job_types, dates
- 80000hours: tags_exp_required, tags_degree_required, salary_limit, dates
- Idealist: salaryMinimum/Maximum, jobType, remoteCountry, dates
"""

import re
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any


class RawDataExtractor:
    """Extract structured fields from job raw_data JSON."""

    # Experience level mappings
    EXPERIENCE_MAPPINGS = {
        # Climatebase experience levels
        "entry": "entry",
        "entry level": "entry",
        "junior": "entry",
        "mid": "mid",
        "mid level": "mid",
        "mid-level": "mid",
        "mid (5-9 years experience)": "mid",
        "senior": "senior",
        "senior level": "senior",
        "experienced": "senior",
        "director": "executive",
        "executive": "executive",
        "director / executive": "executive",
        "leadership": "executive",
        "c-level": "executive",
        "intern": "internship",
        "internship": "internship",
    }

    # Education level mappings
    EDUCATION_MAPPINGS = {
        "high school": "high_school",
        "ged": "high_school",
        "no degree": "high_school",
        "undergraduate degree or less": "bachelor",
        "associate": "associate",
        "associate's": "associate",
        "associates": "associate",
        "bachelor": "bachelor",
        "bachelor's": "bachelor",
        "bachelors": "bachelor",
        "undergraduate": "bachelor",
        "master": "master",
        "master's": "master",
        "masters": "master",
        "graduate": "master",
        "mba": "master",
        "phd": "phd",
        "doctorate": "phd",
        "doctoral": "phd",
        "post-graduate": "phd",
    }

    # Job type mappings
    JOB_TYPE_MAPPINGS = {
        # Climatebase
        "full time role": "full-time",
        "full-time role": "full-time",
        "full time": "full-time",
        "full-time": "full-time",
        "fulltime": "full-time",
        # Part-time
        "part time role": "part-time",
        "part-time role": "part-time",
        "part time": "part-time",
        "part-time": "part-time",
        "parttime": "part-time",
        # Contract
        "contract role": "contract",
        "contract": "contract",
        "contractor": "contract",
        "temporary": "contract",
        # Freelance
        "freelance": "freelance",
        "freelancer": "freelance",
        "consultant": "freelance",
        # Internship
        "internship": "internship",
        "intern": "internship",
        # Fellowship
        "fellowship": "internship",
        # Volunteer
        "volunteer": "volunteer",
        # Idealist format
        "full_time": "full-time",
        "part_time": "part-time",
    }

    # Country normalization
    COUNTRY_MAPPINGS = {
        "united states": "USA",
        "united states of america": "USA",
        "us": "USA",
        "usa": "USA",
        "u.s.": "USA",
        "u.s.a.": "USA",
        "united kingdom": "UK",
        "great britain": "UK",
        "gb": "UK",
        "uk": "UK",
        "england": "UK",
        "scotland": "UK",
        "wales": "UK",
        "northern ireland": "UK",
        "germany": "Germany",
        "de": "Germany",
        "deutschland": "Germany",
        "france": "France",
        "fr": "France",
        "canada": "Canada",
        "ca": "Canada",
        "australia": "Australia",
        "au": "Australia",
        "india": "India",
        "in": "India",
        "netherlands": "Netherlands",
        "nl": "Netherlands",
        "switzerland": "Switzerland",
        "ch": "Switzerland",
        "sweden": "Sweden",
        "se": "Sweden",
        "kenya": "Kenya",
        "ke": "Kenya",
        "south africa": "South Africa",
        "za": "South Africa",
        "nigeria": "Nigeria",
        "ng": "Nigeria",
        "brazil": "Brazil",
        "br": "Brazil",
        "mexico": "Mexico",
        "mx": "Mexico",
        "japan": "Japan",
        "jp": "Japan",
        "singapore": "Singapore",
        "sg": "Singapore",
        "ireland": "Ireland",
        "ie": "Ireland",
        "spain": "Spain",
        "es": "Spain",
        "italy": "Italy",
        "it": "Italy",
        "poland": "Poland",
        "pl": "Poland",
        "belgium": "Belgium",
        "be": "Belgium",
        "austria": "Austria",
        "at": "Austria",
        "new zealand": "New Zealand",
        "nz": "New Zealand",
        "denmark": "Denmark",
        "dk": "Denmark",
        "norway": "Norway",
        "no": "Norway",
        "finland": "Finland",
        "fi": "Finland",
        # Additional country codes found in data
        "argentina": "Argentina",
        "ar": "Argentina",
        "burkina faso": "Burkina Faso",
        "bf": "Burkina Faso",
        "ivory coast": "Ivory Coast",
        "côte d'ivoire": "Ivory Coast",
        "ci": "Ivory Coast",
        "cameroon": "Cameroon",
        "cm": "Cameroon",
        "ecuador": "Ecuador",
        "ec": "Ecuador",
        "guatemala": "Guatemala",
        "gt": "Guatemala",
        "honduras": "Honduras",
        "hn": "Honduras",
        "cambodia": "Cambodia",
        "kh": "Cambodia",
        "malaysia": "Malaysia",
        "my": "Malaysia",
        "togo": "Togo",
        "tg": "Togo",
        "thailand": "Thailand",
        "th": "Thailand",
        "vietnam": "Vietnam",
        "vn": "Vietnam",
        "china": "China",
        "cn": "China",
        "indonesia": "Indonesia",
        "id": "Indonesia",
        "philippines": "Philippines",
        "ph": "Philippines",
        "pakistan": "Pakistan",
        "pk": "Pakistan",
        "bangladesh": "Bangladesh",
        "bd": "Bangladesh",
        "egypt": "Egypt",
        "eg": "Egypt",
        "colombia": "Colombia",
        "co": "Colombia",
        "peru": "Peru",
        "pe": "Peru",
        "chile": "Chile",
        "cl": "Chile",
        "portugal": "Portugal",
        "pt": "Portugal",
        "greece": "Greece",
        "gr": "Greece",
        "czech republic": "Czech Republic",
        "cz": "Czech Republic",
        "hungary": "Hungary",
        "hu": "Hungary",
        "romania": "Romania",
        "ro": "Romania",
        "ukraine": "Ukraine",
        "ua": "Ukraine",
        "israel": "Israel",
        "il": "Israel",
        "united arab emirates": "UAE",
        "uae": "UAE",
        "ae": "UAE",
        "qatar": "Qatar",
        "qa": "Qatar",
        "saudi arabia": "Saudi Arabia",
        "sa": "Saudi Arabia",
        "turkey": "Turkey",
        "tr": "Turkey",
        "south korea": "South Korea",
        "kr": "South Korea",
        "taiwan": "Taiwan",
        "tw": "Taiwan",
        "hong kong": "Hong Kong",
        "hk": "Hong Kong",
        # Remote variations - keep generic Remote for now
        "remote": "Remote",
        "anywhere": "Remote",
        "worldwide": "Remote",
    }

    # Regional remote mappings - for detecting region-based remote work
    REGION_MAPPINGS = {
        # Global patterns
        "global": "Global",
        "remote, global": "Global",
        "remote - global": "Global",
        "worldwide": "Global",
        "anywhere": "Global",
        "international": "Global",
        # US patterns
        "remote, usa": "USA",
        "remote - usa": "USA",
        "remote (us)": "USA",
        "remote (usa)": "USA",
        "us only": "USA",
        "usa only": "USA",
        "united states only": "USA",
        # Europe patterns
        "remote, europe": "Europe",
        "remote - europe": "Europe",
        "europe only": "Europe",
        "eu only": "Europe",
        "european union": "Europe",
        "emea": "EMEA",
        # UK patterns
        "remote, uk": "UK",
        "remote - uk": "UK",
        "uk only": "UK",
        # Americas
        "americas": "Americas",
        "north america": "North America",
        "latin america": "Latin America",
        "latam": "Latin America",
        # APAC
        "apac": "Asia Pacific",
        "asia pacific": "Asia Pacific",
        "asia": "Asia Pacific",
    }

    # Countries by region for inference
    REGION_COUNTRIES = {
        "Europe": ["UK", "Germany", "France", "Netherlands", "Spain", "Italy",
                   "Switzerland", "Sweden", "Belgium", "Austria", "Denmark",
                   "Ireland", "Norway", "Poland", "Portugal", "Finland",
                   "Czech Republic", "Greece", "Hungary", "Romania"],
        "North America": ["USA", "Canada", "Mexico"],
        "Asia Pacific": ["Australia", "India", "Japan", "Singapore", "New Zealand",
                        "China", "South Korea", "Hong Kong", "Taiwan", "Philippines",
                        "Malaysia", "Thailand", "Indonesia", "Vietnam"],
        "Latin America": ["Brazil", "Argentina", "Colombia", "Chile", "Peru", "Costa Rica"],
        "Africa": ["South Africa", "Nigeria", "Kenya", "Egypt", "Morocco", "Ghana"],
        "Middle East": ["Israel", "UAE", "Qatar", "Saudi Arabia"],
    }

    @classmethod
    def extract(cls, source: str, raw_data: dict) -> dict:
        """
        Extract structured fields from raw_data based on source.

        Returns dict with keys:
        - experience_level
        - education_level
        - country
        - salary_min
        - salary_max
        - salary_currency
        - job_type
        - location (enhanced)
        - posted_at (datetime)
        - expires_at (datetime)
        """
        if not raw_data:
            return {}

        extractor_map = {
            "climatebase": cls._extract_climatebase,
            "80000hours": cls._extract_80000hours,
            "idealist": cls._extract_idealist,
            "reliefweb": cls._extract_reliefweb,
            "greenhouse": cls._extract_job_board,
            "lever": cls._extract_job_board,
            "ashby": cls._extract_job_board,
            "probablygood": cls._extract_probablygood,
        }

        extractor = extractor_map.get(source)
        if extractor:
            return extractor(raw_data)
        return {}

    @classmethod
    def _extract_climatebase(cls, raw_data: dict) -> dict:
        """Extract from Climatebase raw_data."""
        result = {}

        # Experience level
        experience_levels = raw_data.get("experience_levels", [])
        if experience_levels:
            result["experience_level"] = cls._map_experience(experience_levels[0])

        # Job type
        job_types = raw_data.get("job_types", [])
        if job_types:
            result["job_type"] = cls._map_job_type(job_types[0])

        # Country - first check countries array
        countries = raw_data.get("countries", [])
        if countries:
            result["country"] = cls._normalize_country(countries[0])

        # Location (more specific)
        locations = raw_data.get("locations", [])
        if locations:
            result["location"] = locations[0]
            # Try to extract country from location if not already set
            if "country" not in result:
                country = cls._extract_country_from_location(locations[0])
                if country:
                    result["country"] = country

        # Salary
        salary_from = raw_data.get("salary_from")
        salary_to = raw_data.get("salary_to")
        if salary_from is not None:
            result["salary_min"] = cls._parse_decimal(salary_from)
        if salary_to is not None:
            result["salary_max"] = cls._parse_decimal(salary_to)

        # Remote preferences - use to infer global remote
        remote_prefs = raw_data.get("remote_preferences", [])
        if remote_prefs:
            if "Remote" in remote_prefs:
                if not result.get("location"):
                    result["location"] = "Remote"
                # If Remote preference but no countries specified, it's likely global
                if "country" not in result and not countries:
                    result["country"] = "Global"
            elif "Hybrid" in remote_prefs and not result.get("location"):
                result["location"] = "Hybrid"

        # Dates - Climatebase uses ISO format
        activation_date = raw_data.get("activation_date") or raw_data.get("created_at")
        if activation_date:
            result["posted_at"] = cls._parse_iso_date(activation_date)

        expiration_date = raw_data.get("expiration_date")
        if expiration_date:
            result["expires_at"] = cls._parse_iso_date(expiration_date)

        return result

    @classmethod
    def _extract_80000hours(cls, raw_data: dict) -> dict:
        """Extract from 80,000 Hours raw_data."""
        result = {}

        # Experience level from tags_exp_required
        exp_tags = raw_data.get("tags_exp_required", [])
        if not exp_tags:
            # Also check _highlightResult for nested data
            highlight = raw_data.get("_highlightResult", {})
            exp_tags = [item.get("value", "") for item in highlight.get("tags_exp_required", [])]

        if exp_tags:
            result["experience_level"] = cls._map_experience(exp_tags[0])

        # Education level from tags_degree_required
        edu_tags = raw_data.get("tags_degree_required", [])
        if not edu_tags:
            highlight = raw_data.get("_highlightResult", {})
            edu_tags = [item.get("value", "") for item in highlight.get("tags_degree_required", [])]

        if edu_tags:
            result["education_level"] = cls._map_education(edu_tags[0])

        # Job type from tags_role_type
        role_tags = raw_data.get("tags_role_type", [])
        if not role_tags:
            highlight = raw_data.get("_highlightResult", {})
            role_tags = [item.get("value", "") for item in highlight.get("tags_role_type", [])]

        if role_tags:
            result["job_type"] = cls._map_job_type(role_tags[0])

        # Location and Country from tags_location_80k (most reliable source)
        # Values like "Remote, Global", "Remote, USA", "San Francisco, USA"
        location_tags = raw_data.get("tags_location_80k", [])
        if location_tags:
            result["location"] = location_tags[0]
            # Extract country/region from location tag
            for loc_tag in location_tags:
                loc_lower = loc_tag.lower().strip()
                # Check for regional remote patterns
                if loc_lower in cls.REGION_MAPPINGS:
                    result["country"] = cls.REGION_MAPPINGS[loc_lower]
                    break
                # Check for "Remote, Country" pattern
                if loc_lower.startswith("remote,"):
                    region_part = loc_tag.split(",", 1)[1].strip()
                    region_lower = region_part.lower()
                    # Check if it's a region name
                    if region_lower in cls.REGION_MAPPINGS:
                        result["country"] = cls.REGION_MAPPINGS[region_lower]
                    else:
                        # It's a specific country
                        result["country"] = cls._normalize_country(region_part)
                    break
                # Check for specific location "City, Country"
                elif "," in loc_tag:
                    parts = loc_tag.split(",")
                    country_part = parts[-1].strip()
                    result["country"] = cls._normalize_country(country_part)
                    break

        # Fallback to tags_country if no country found
        if "country" not in result:
            country_tags = raw_data.get("tags_country", [])
            if not country_tags:
                highlight = raw_data.get("_highlightResult", {})
                country_tags = [item.get("value", "") for item in highlight.get("tags_country", [])]

            if country_tags:
                # Filter out generic remote tags
                specific_countries = [c for c in country_tags if c.lower() not in ["remote, global", "global", "remote"]]
                if specific_countries:
                    result["country"] = cls._normalize_country(specific_countries[0])
                else:
                    result["country"] = "Global"

        # Salary - 80k has salary as string with currency and salary_limit as USD number
        salary_str = raw_data.get("salary", "")
        salary_limit = raw_data.get("salary_limit")

        if salary_limit is not None:
            result["salary_max"] = cls._parse_decimal(salary_limit)
            result["salary_currency"] = "USD"

        # Parse the salary string for min value
        if salary_str:
            parsed = cls._parse_salary_string(salary_str)
            if parsed:
                if "salary_min" in parsed and "salary_min" not in result:
                    result["salary_min"] = parsed["salary_min"]
                if "salary_currency" in parsed:
                    result["salary_currency"] = parsed["salary_currency"]

        # Dates - 80000hours uses Unix timestamps
        posted_at = raw_data.get("posted_at")
        if posted_at:
            result["posted_at"] = cls._parse_unix_timestamp(posted_at)

        closes_at = raw_data.get("closes_at")
        if closes_at:
            result["expires_at"] = cls._parse_unix_timestamp(closes_at)

        return result

    @classmethod
    def _extract_idealist(cls, raw_data: dict) -> dict:
        """Extract from Idealist raw_data."""
        result = {}

        # Salary
        salary_min = raw_data.get("salaryMinimum")
        salary_max = raw_data.get("salaryMaximum")
        salary_currency = raw_data.get("salaryCurrency", "USD")

        if salary_min is not None:
            result["salary_min"] = cls._parse_decimal(salary_min)
        if salary_max is not None:
            result["salary_max"] = cls._parse_decimal(salary_max)
        if salary_currency:
            result["salary_currency"] = salary_currency

        # Job type
        job_types = raw_data.get("jobType", [])
        if job_types:
            result["job_type"] = cls._map_job_type(job_types[0])

        # Country and Region from remoteZone/remoteCountry
        remote_zone = raw_data.get("remoteZone")
        remote_country = raw_data.get("remoteCountry")
        remote_state = raw_data.get("remoteState")

        if remote_zone == "GLOBAL" or remote_zone == "ANYWHERE":
            result["country"] = "Global"
        elif remote_zone == "COUNTRY" and remote_country:
            result["country"] = cls._normalize_country(remote_country)
        elif remote_zone == "STATE" and remote_country:
            # US state-based remote - still count as US
            result["country"] = cls._normalize_country(remote_country)
            if remote_state:
                result["location"] = f"Remote ({remote_state}, {remote_country})"
        elif remote_zone == "CITY" and remote_country:
            result["country"] = cls._normalize_country(remote_country)
        elif remote_country:
            result["country"] = cls._normalize_country(remote_country)

        # Dates - Idealist uses Unix timestamps
        published = raw_data.get("published")
        if published:
            result["posted_at"] = cls._parse_unix_timestamp(published)

        # Idealist doesn't seem to have expiration date in raw_data

        return result

    @classmethod
    def _extract_reliefweb(cls, raw_data: dict) -> dict:
        """Extract from ReliefWeb raw_data."""
        result = {}

        # ReliefWeb may have different structure
        # Check for common fields
        if "country" in raw_data:
            countries = raw_data.get("country", [])
            if isinstance(countries, list) and countries:
                country_name = countries[0].get("name") if isinstance(countries[0], dict) else countries[0]
                result["country"] = cls._normalize_country(country_name)

        if "type" in raw_data:
            job_types = raw_data.get("type", [])
            if isinstance(job_types, list) and job_types:
                type_name = job_types[0].get("name") if isinstance(job_types[0], dict) else job_types[0]
                result["job_type"] = cls._map_job_type(type_name)

        if "experience" in raw_data:
            exp = raw_data.get("experience", [])
            if isinstance(exp, list) and exp:
                exp_name = exp[0].get("name") if isinstance(exp[0], dict) else exp[0]
                result["experience_level"] = cls._map_experience(exp_name)

        return result

    @classmethod
    def _extract_job_board(cls, raw_data: dict) -> dict:
        """Extract from generic job board (Greenhouse, Lever, Ashby) raw_data."""
        result = {}

        # These typically have location info
        location = raw_data.get("location")
        if isinstance(location, dict):
            location = location.get("name", "")
        if location:
            result["location"] = location
            # Try to extract country from location string
            country = cls._extract_country_from_location(location)
            if country:
                result["country"] = country

        return result

    @classmethod
    def _extract_probablygood(cls, raw_data: dict) -> dict:
        """Extract from ProbablyGood raw_data - limited data available."""
        # ProbablyGood typically only has URL until crawled
        return {}

    # Helper methods

    @classmethod
    def _map_experience(cls, exp_str: str) -> str | None:
        """Map experience string to standard value."""
        if not exp_str:
            return None
        normalized = exp_str.lower().strip()
        return cls.EXPERIENCE_MAPPINGS.get(normalized)

    @classmethod
    def _map_education(cls, edu_str: str) -> str | None:
        """Map education string to standard value."""
        if not edu_str:
            return None
        normalized = edu_str.lower().strip()
        return cls.EDUCATION_MAPPINGS.get(normalized)

    @classmethod
    def _map_job_type(cls, type_str: str) -> str | None:
        """Map job type string to standard value."""
        if not type_str:
            return None
        normalized = type_str.lower().strip()
        return cls.JOB_TYPE_MAPPINGS.get(normalized)

    @classmethod
    def _normalize_country(cls, country_str: str) -> str | None:
        """Normalize country name."""
        if not country_str:
            return None
        normalized = country_str.lower().strip()
        # Check direct mapping
        if normalized in cls.COUNTRY_MAPPINGS:
            return cls.COUNTRY_MAPPINGS[normalized]
        # Return original with title case if not mapped
        return country_str.title()

    @classmethod
    def _extract_country_from_location(cls, location: str) -> str | None:
        """Try to extract country from a location string like 'New York, NY, USA'."""
        if not location:
            return None

        # Check for "Remote" keywords
        location_lower = location.lower()
        if "remote" in location_lower:
            # Check for "Remote (US)", "Remote (UK)" pattern
            match = re.search(r"remote\s*\((\w+)\)", location_lower)
            if match:
                country_code = match.group(1)
                return cls._normalize_country(country_code)
            return "Remote"

        # Split by comma and check last parts
        parts = [p.strip() for p in location.split(",")]
        for part in reversed(parts):
            normalized = cls._normalize_country(part)
            if normalized and normalized != part.title():
                return normalized

        return None

    @classmethod
    def _parse_decimal(cls, value: Any) -> Decimal | None:
        """Parse a value to Decimal, handling various formats."""
        if value is None:
            return None
        try:
            if isinstance(value, str):
                # Remove currency symbols, commas, spaces
                cleaned = re.sub(r"[^\d.]", "", value)
                if cleaned:
                    return Decimal(cleaned)
            else:
                return Decimal(str(value))
        except (InvalidOperation, ValueError):
            return None
        return None

    @classmethod
    def _parse_salary_string(cls, salary_str: str) -> dict | None:
        """Parse salary string like '£67,100' or '$50,000 - $70,000'."""
        if not salary_str:
            return None

        result = {}

        # Detect currency
        if "£" in salary_str or "gbp" in salary_str.lower():
            result["salary_currency"] = "GBP"
        elif "€" in salary_str or "eur" in salary_str.lower():
            result["salary_currency"] = "EUR"
        elif "$" in salary_str or "usd" in salary_str.lower():
            result["salary_currency"] = "USD"

        # Extract numbers
        numbers = re.findall(r"[\d,]+\.?\d*", salary_str)
        if numbers:
            try:
                cleaned_numbers = [Decimal(n.replace(",", "")) for n in numbers]
                if len(cleaned_numbers) == 1:
                    result["salary_min"] = cleaned_numbers[0]
                elif len(cleaned_numbers) >= 2:
                    result["salary_min"] = min(cleaned_numbers)
                    result["salary_max"] = max(cleaned_numbers)
            except InvalidOperation:
                pass

        return result if result else None

    @classmethod
    def _parse_unix_timestamp(cls, timestamp: Any) -> datetime | None:
        """Parse a Unix timestamp (seconds since epoch) to datetime."""
        if timestamp is None:
            return None
        try:
            if isinstance(timestamp, str):
                timestamp = int(timestamp)
            return datetime.fromtimestamp(timestamp, tz=timezone.utc)
        except (ValueError, TypeError, OSError):
            return None

    @classmethod
    def _parse_iso_date(cls, date_str: str) -> datetime | None:
        """Parse an ISO format date string to datetime."""
        if not date_str:
            return None
        try:
            # Handle various ISO formats
            # "2025-12-17T01:19:50.878Z" or "2025-12-17T01:19:50Z"
            if date_str.endswith("Z"):
                date_str = date_str[:-1] + "+00:00"
            # Handle milliseconds
            if "." in date_str:
                # Truncate to microseconds if more precision
                parts = date_str.split(".")
                if "+" in parts[1]:
                    frac, tz = parts[1].split("+")
                    frac = frac[:6]  # Max 6 digits for microseconds
                    date_str = f"{parts[0]}.{frac}+{tz}"
                elif "-" in parts[1]:
                    frac, tz = parts[1].split("-")
                    frac = frac[:6]
                    date_str = f"{parts[0]}.{frac}-{tz}"
            return datetime.fromisoformat(date_str)
        except (ValueError, TypeError):
            return None
