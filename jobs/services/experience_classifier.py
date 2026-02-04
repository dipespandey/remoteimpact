"""
Experience Level Classifier Service

Rule-based classification for experience_level based on title and description.
This complements the LLM parser by providing fast, deterministic classification
for obvious cases (internships, entry-level, etc.)
"""

import re
from typing import Optional, Tuple


class ExperienceClassifier:
    """Classify job experience level from title and description text."""

    # Patterns that indicate internship (highest priority)
    INTERNSHIP_PATTERNS = [
        r'\bintern\b',
        r'\binternship\b',
        r'\binterns\b',
        r'\bworking student\b',
        r'\bwerkstudent\b',  # German
        r'\bstage\b',  # French internship
    ]

    # Patterns that indicate entry level
    ENTRY_PATTERNS = [
        r'\bentry[\s\-]?level\b',
        r'\bjunior\b',
        r'\bjr\.?\b(?!\s*(?:sr|senior|manager|director))',  # Jr. but not Jr Sr
        r'\b(?:0|zero)[\s\-]?(?:to|\-)[\s\-]?[123][\s\-]?(?:years?|yrs?)\b',
        r'\b[12][\s\-]?(?:\+|\-|to)[\s\-]?[123]?[\s\-]?(?:years?|yrs?)?\s+(?:of\s+)?(?:experience|exp)\b',
        r'\b(?:recent|new)\s+graduate?s?\b',
        r'\bgraduate\s+(?:program|scheme|role|position|opportunity)\b',
        r'\bno\s+(?:prior\s+)?experience\s+(?:required|necessary|needed)\b',
        r'\btrainee\b',
        # Associate but not followed by "director"
        # We handle "senior associate" separately in classify()
        r'\bassociate\b(?!\s+(?:director|professor|dean))',
        r'\bapprentice\b',
        r'\bearly[\s\-]?career\b',
        r'\beinstieg\b',  # German entry-level
    ]

    # Patterns that indicate mid level
    MID_PATTERNS = [
        r'\bmid[\s\-]?level\b',
        r'\b[3-6][\s\-]?(?:\+|\-)[\s\-]?(?:years?|yrs?)\s+(?:of\s+)?(?:experience|exp)\b',
        r'\bintermediate\b',
        r'\bexperienced\b(?!\s+(?:director|executive|leader))',
    ]

    # Patterns that indicate senior level
    SENIOR_PATTERNS = [
        r'\bsenior\b(?!\s+(?:vice|vp|director|executive|manager|partner|associate))',
        r'\bsr\.?\b(?!\s+(?:vp|director|executive))',
        r'\b(?:7|8|9|10)[\s\-]?\+?[\s\-]?(?:years?|yrs?)\s+(?:of\s+)?(?:experience|exp)\b',
        r'\blead\b(?!\s+(?:developer|engineer|designer))\s+(?:developer|engineer|designer)',
        r'\bstaff\s+(?:engineer|developer|designer)\b',
        r'\bprincipal\b',
    ]

    # Patterns that indicate executive level
    EXECUTIVE_PATTERNS = [
        r'\bexecutive\b',  # We handle "account executive" separately in classify()
        r'\bdirector\b',
        r'\bvice\s+president\b',
        r'\bvp\b',
        r'\bc[\-\s]?suite\b',
        r'\bchief\s+\w+\s+officer\b',
        r'\b(?:ceo|cto|cfo|coo|cmo|cio)\b',
        r'\bmanaging\s+director\b',
        r'\bhead\s+of\b',
        r'\b15\+?\s+(?:years?|yrs?)\b',
    ]

    @classmethod
    def _has_senior_indicator(cls, text: str) -> bool:
        """Check if text contains senior-level indicators."""
        senior_words = ['senior', 'sr.', 'sr ']
        return any(word in text.lower() for word in senior_words)

    @classmethod
    def _is_account_executive(cls, text: str) -> bool:
        """Check if this is 'Account Executive' which is typically mid-level sales."""
        return bool(re.search(r'\baccount\s+executive\b', text.lower()))

    @classmethod
    def classify(cls, title: str, description: str = "") -> Tuple[Optional[str], str]:
        """
        Classify experience level from job title and description.

        Returns:
            Tuple of (experience_level, reason)
            experience_level is one of: 'internship', 'entry', 'mid', 'senior', 'executive', None
            reason explains why this classification was made
        """
        title_lower = title.lower() if title else ""
        desc_lower = description.lower() if description else ""

        # Combine for checking, but weight title more heavily
        full_text = f"{title_lower} {desc_lower}"

        # Check internship first (highest priority)
        for pattern in cls.INTERNSHIP_PATTERNS:
            if re.search(pattern, title_lower, re.IGNORECASE):
                return ('internship', f"Title contains internship keyword: {pattern}")

        # Check title for executive level (but handle edge cases)
        for pattern in cls.EXECUTIVE_PATTERNS:
            if re.search(pattern, title_lower, re.IGNORECASE):
                # Skip "Account Executive" - that's typically mid-level sales
                if cls._is_account_executive(title_lower):
                    continue
                return ('executive', f"Title contains executive keyword: {pattern}")

        # Check title for senior level
        for pattern in cls.SENIOR_PATTERNS:
            if re.search(pattern, title_lower, re.IGNORECASE):
                return ('senior', f"Title contains senior keyword: {pattern}")

        # Check title for entry level (but skip if "senior" is also present)
        if not cls._has_senior_indicator(title_lower):
            for pattern in cls.ENTRY_PATTERNS:
                if re.search(pattern, title_lower, re.IGNORECASE):
                    return ('entry', f"Title contains entry-level keyword: {pattern}")

        # Now check description for internship (still high priority)
        for pattern in cls.INTERNSHIP_PATTERNS:
            if re.search(pattern, desc_lower, re.IGNORECASE):
                # Verify it's about the role, not just mentioning interns
                # Check for context like "intern position", "internship opportunity", etc.
                context_patterns = [
                    r'this\s+(?:is\s+(?:an?\s+)?)?intern',
                    r'intern(?:ship)?\s+(?:position|role|opportunity|program)',
                    r'seeking\s+(?:an?\s+)?intern',
                    r'looking\s+for\s+(?:an?\s+)?intern',
                    r'hiring\s+(?:an?\s+)?intern',
                    r'join\s+(?:us\s+)?as\s+(?:an?\s+)?intern',
                ]
                for ctx in context_patterns:
                    if re.search(ctx, desc_lower, re.IGNORECASE):
                        return ('internship', f"Description indicates internship role: {ctx}")

        # Check description for entry level (but skip if "senior" is in title)
        if not cls._has_senior_indicator(title_lower):
            for pattern in cls.ENTRY_PATTERNS:
                if re.search(pattern, desc_lower, re.IGNORECASE):
                    return ('entry', f"Description contains entry-level keyword: {pattern}")

        # Check for mid-level indicators
        for pattern in cls.MID_PATTERNS:
            if re.search(pattern, full_text, re.IGNORECASE):
                return ('mid', f"Contains mid-level keyword: {pattern}")

        return (None, "No clear experience level indicators found")

    @classmethod
    def classify_from_job_type(cls, job_type: str) -> Tuple[Optional[str], str]:
        """
        Infer experience_level from job_type field.
        """
        if job_type == 'internship':
            return ('internship', "job_type is internship")
        return (None, "job_type doesn't indicate experience level")

    @classmethod
    def get_best_classification(
        cls,
        title: str,
        description: str = "",
        job_type: str = "",
        existing_level: Optional[str] = None
    ) -> Tuple[Optional[str], str]:
        """
        Get the best experience level classification considering all inputs.

        Priority:
        1. If existing_level is set and valid, keep it
        2. job_type == 'internship' always maps to internship
        3. Title-based classification
        4. Description-based classification
        """
        # If already classified, don't override unless it's clearly wrong
        if existing_level and existing_level in ['entry', 'mid', 'senior', 'executive', 'internship']:
            return (existing_level, f"Already classified as {existing_level}")

        # job_type=internship is definitive
        if job_type == 'internship':
            return ('internship', "job_type is internship")

        # Use text-based classification
        return cls.classify(title, description)
