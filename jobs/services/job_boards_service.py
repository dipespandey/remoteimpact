"""
Service to fetch and cache job boards from the Ethical Job Resources Board.

Maintained by Ted Fickes & Edward Saperia
Source: https://docs.google.com/spreadsheets/d/1dFVoF6f9VU5pjaGhyyvQaBN0n6ae-iLCtlvsO1N2jhA/
"""

import csv
import io
import logging
from datetime import datetime, timedelta
from typing import Optional
from urllib.parse import urlparse

import requests
from django.core.cache import cache

logger = logging.getLogger(__name__)

# Google Sheets CSV export URL
SHEET_ID = "1dFVoF6f9VU5pjaGhyyvQaBN0n6ae-iLCtlvsO1N2jhA"
SHEET_GID = "0"
CSV_URL = f"https://docs.google.com/spreadsheets/d/{SHEET_ID}/export?format=csv&gid={SHEET_GID}"

CACHE_KEY = "job_boards_list"
CACHE_TIMEOUT = 60 * 60 * 24  # 24 hours

# Attribution info
MAINTAINERS = {
    "names": "Ted Fickes & Edward Saperia",
    "source_url": f"https://docs.google.com/spreadsheets/d/{SHEET_ID}/",
    "credits": [
        {"name": "Ted Fickes", "org": "Bright+3", "contact": "ted@brightplus3.com"},
        {"name": "Edward Saperia", "org": "Newspeak House", "contact": "@edsaperia"},
    ],
}


def categorize_board(name: str, url: str, board_type: str, geography: str, notes: str) -> str:
    """Categorize a job board based on its attributes."""
    name_lower = name.lower()
    notes_lower = notes.lower() if notes else ""
    type_lower = board_type.lower() if board_type else ""

    # Climate & Environment
    if any(kw in name_lower or kw in notes_lower for kw in ["climate", "environment", "green", "clean energy", "terra.do"]):
        return "Climate"

    # Effective Altruism
    if any(kw in name_lower or kw in notes_lower for kw in ["effective altruism", "80,000 hours", "80000", "ea job", "probably good"]):
        return "EA"

    # Tech for Good
    if any(kw in name_lower or kw in notes_lower for kw in ["tech for good", "responsible tech", "civic tech", "tech jobs for good", "digital rights", "foss", "open source"]):
        return "Tech"

    # Government & Policy
    if any(kw in name_lower or kw in notes_lower for kw in ["government", "policy", "usajobs", "progressive", "democracy", "political", "campaign"]):
        return "Government"

    # Journalism & Media
    if any(kw in name_lower or kw in notes_lower for kw in ["journalism", "media", "newsroom", "news"]):
        return "Media"

    # Nonprofit & NGO
    if any(kw in name_lower or kw in notes_lower for kw in ["nonprofit", "ngo", "charity", "philanthropy", "foundation", "idealist", "social impact"]):
        return "Nonprofit"

    # International Development
    if any(kw in name_lower or kw in notes_lower for kw in ["development", "humanitarian", "relief", "un ", "global health"]):
        return "Development"

    # Design & Creative
    if any(kw in name_lower or kw in notes_lower for kw in ["design", "creative", "ux", "ui"]):
        return "Design"

    # Startups & Innovation
    if any(kw in name_lower or kw in notes_lower for kw in ["startup", "angel", "unicorn", "venture"]):
        return "Startups"

    # Default based on geography
    if geography:
        geo_lower = geography.lower()
        if "uk" in geo_lower:
            return "UK"
        elif "us" in geo_lower or "america" in geo_lower:
            return "US"

    return "General"


def extract_tags(name: str, board_type: str, geography: str, notes: str) -> list:
    """Extract relevant tags from board info."""
    tags = []

    # Add geography tag
    if geography:
        geo_lower = geography.lower()
        if "global" in geo_lower or "international" in geo_lower:
            tags.append("Global")
        elif "uk" in geo_lower:
            tags.append("UK")
        elif "us" in geo_lower:
            tags.append("US")
        elif "europe" in geo_lower:
            tags.append("Europe")
        elif "australia" in geo_lower:
            tags.append("Australia")
        elif "remote" in geo_lower:
            tags.append("Remote")

    # Add type tag
    if board_type:
        type_lower = board_type.lower()
        if "newsletter" in type_lower:
            tags.append("Newsletter")
        elif "facebook" in type_lower or "google group" in type_lower:
            tags.append("Community")
        elif "slack" in type_lower:
            tags.append("Slack")

    # Extract topic tags from notes
    if notes:
        notes_lower = notes.lower()
        topic_keywords = {
            "climate": "Climate",
            "tech": "Tech",
            "nonprofit": "Nonprofit",
            "journalism": "Media",
            "design": "Design",
            "fundraising": "Fundraising",
            "startup": "Startups",
            "remote": "Remote",
        }
        for keyword, tag in topic_keywords.items():
            if keyword in notes_lower and tag not in tags:
                tags.append(tag)
                break  # Only add one topic tag

    return tags[:3]  # Limit to 3 tags


def is_valid_url(url: str) -> bool:
    """Check if URL is valid and accessible."""
    if not url or not url.startswith("http"):
        return False
    try:
        result = urlparse(url)
        return all([result.scheme, result.netloc])
    except Exception:
        return False


def fetch_job_boards_from_sheet() -> list:
    """Fetch job boards from Google Sheet CSV."""
    try:
        response = requests.get(CSV_URL, timeout=15)
        response.raise_for_status()

        # Parse CSV
        content = response.content.decode("utf-8")
        reader = csv.reader(io.StringIO(content))
        rows = list(reader)

        # Skip header rows (first few rows contain maintainer info)
        # Find the header row with "List / Site / Job Source"
        header_idx = None
        for i, row in enumerate(rows):
            if len(row) > 1 and "List / Site / Job Source" in str(row):
                header_idx = i
                break

        if header_idx is None:
            # Try to find by column content
            for i, row in enumerate(rows):
                if len(row) > 2 and row[1] and "http" in str(row[2]):
                    header_idx = i - 1
                    break

        if header_idx is None:
            header_idx = 3  # Default fallback

        job_boards = []
        seen_urls = set()

        for row in rows[header_idx + 1:]:
            if len(row) < 3:
                continue

            # CSV columns: empty, Name, URL, Type, Geography, Notes, ...
            name = row[1].strip() if len(row) > 1 else ""
            url = row[2].strip() if len(row) > 2 else ""
            board_type = row[3].strip() if len(row) > 3 else ""
            geography = row[4].strip() if len(row) > 4 else ""
            notes = row[5].strip() if len(row) > 5 else ""

            # Skip invalid entries
            if not name or not is_valid_url(url):
                continue

            # Skip duplicates
            if url in seen_urls:
                continue
            seen_urls.add(url)

            # Skip entries that are just lists of lists or meta-resources
            if "list of" in name.lower() and "job board" in name.lower():
                continue

            # Create board entry
            category = categorize_board(name, url, board_type, geography, notes)
            tags = extract_tags(name, board_type, geography, notes)

            # Use notes as description, or create one from type/geography
            description = notes if notes else f"{board_type} - {geography}" if board_type else ""
            if len(description) > 200:
                description = description[:197] + "..."

            job_boards.append({
                "name": name,
                "url": url,
                "description": description,
                "tags": tags,
                "category": category,
                "type": board_type,
                "geography": geography,
            })

        return job_boards

    except Exception as e:
        logger.error(f"Failed to fetch job boards from Google Sheet: {e}")
        return []


def get_job_boards() -> tuple[list, dict]:
    """
    Get job boards list with caching.
    Returns tuple of (job_boards, maintainer_info).
    """
    # Try cache first
    cached = cache.get(CACHE_KEY)
    if cached:
        return cached, MAINTAINERS

    # Fetch from Google Sheet
    job_boards = fetch_job_boards_from_sheet()

    # If fetch failed or returned empty, use fallback
    if not job_boards:
        job_boards = get_fallback_job_boards()

    # Cache the result
    if job_boards:
        cache.set(CACHE_KEY, job_boards, CACHE_TIMEOUT)

    return job_boards, MAINTAINERS


def get_job_board_categories(job_boards: list) -> list:
    """Generate category list with counts from job boards."""
    category_counts = {}
    for board in job_boards:
        cat = board.get("category", "General")
        category_counts[cat] = category_counts.get(cat, 0) + 1

    # Define category display order and names
    category_info = {
        "Climate": "Climate & Environment",
        "EA": "Effective Altruism",
        "Tech": "Tech for Good",
        "Nonprofit": "Nonprofit & NGO",
        "Government": "Government & Policy",
        "Media": "Journalism & Media",
        "Development": "International Development",
        "Design": "Design & Creative",
        "Startups": "Startups",
        "UK": "UK Jobs",
        "US": "US Jobs",
        "General": "General",
    }

    categories = [{"id": "all", "name": "All Boards", "count": len(job_boards)}]

    for cat_id, cat_name in category_info.items():
        count = category_counts.get(cat_id, 0)
        if count > 0:
            categories.append({"id": cat_id, "name": cat_name, "count": count})

    return categories


def get_fallback_job_boards() -> list:
    """Fallback static list in case Google Sheet fetch fails."""
    return [
        {
            "name": "Climatebase",
            "url": "https://climatebase.org/",
            "description": "The largest climate job board with 40k+ roles across all functions and levels.",
            "tags": ["Climate", "Global", "Tech"],
            "category": "Climate",
        },
        {
            "name": "80,000 Hours",
            "url": "https://80000hours.org/job-board/",
            "description": "High-impact roles in AI safety, biosecurity, global health, and existential risk.",
            "tags": ["EA", "Global", "Policy"],
            "category": "EA",
        },
        {
            "name": "Idealist",
            "url": "https://www.idealist.org/",
            "description": "The largest nonprofit job board with roles at NGOs and social enterprises worldwide.",
            "tags": ["Nonprofit", "Global"],
            "category": "Nonprofit",
        },
        {
            "name": "Tech Jobs for Good",
            "url": "https://techjobsforgood.com/",
            "description": "Tech roles at mission-driven organizations in climate, health, and civic tech.",
            "tags": ["Tech", "US"],
            "category": "Tech",
        },
        {
            "name": "Probably Good",
            "url": "https://jobs.probablygood.org/",
            "description": "Impact-focused roles curated by career advisors with cause area filtering.",
            "tags": ["EA", "Global"],
            "category": "EA",
        },
        {
            "name": "Work on Climate",
            "url": "https://workonclimate.org/",
            "description": "Climate jobs with salary transparency and active Slack community.",
            "tags": ["Climate", "Remote"],
            "category": "Climate",
        },
    ]
