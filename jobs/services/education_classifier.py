"""
Education Level Classifier Service

Rule-based classification for education_level based on job description.
This complements the LLM parser by providing fast, deterministic classification.
"""

import re
from typing import Optional, Tuple


class EducationClassifier:
    """Classify minimum education requirement from job description text."""

    # PhD patterns (check first - highest level)
    PHD_PATTERNS = [
        r'\bph\.?d\.?\b',
        r'\bdoctorate\b',
        r'\bdoctoral\s+degree\b',
        r'\bpost[\-\s]?doc(?:toral)?\b',
    ]

    # Master's patterns
    MASTER_PATTERNS = [
        r'\bmaster\'?s?\s+degree\b',
        r'\bmaster\'?s?\s+(?:in|of)\b',
        r'\b(?:ma|ms|msc|mba|mpa|mph|msw|med|mfa)\b(?!\w)',
        r'\bgraduate\s+degree\b',
        r'\badvanced\s+degree\b',
        r'\bpost[\-\s]?graduate\b',
    ]

    # Bachelor's patterns
    BACHELOR_PATTERNS = [
        r'\bbachelor\'?s?\s+degree\b',
        r'\bbachelor\'?s?\s+(?:in|of)\b',
        r'\b(?:ba|bs|bsc|bba)\b(?!\w)',
        r'\b4[\-\s]?year\s+degree\b',
        r'\bundergraduate\s+degree\b',
        r'\buniversity\s+degree\b',
        r'\bcollege\s+degree\b',
    ]

    # Associate patterns
    ASSOCIATE_PATTERNS = [
        r'\bassociate\'?s?\s+degree\b',
        r'\b2[\-\s]?year\s+degree\b',
        r'\b(?:aa|as)\s+degree\b',
        r'\bcommunity\s+college\b',
    ]

    # High school patterns
    HIGH_SCHOOL_PATTERNS = [
        r'\bhigh\s+school\s+diploma\b',
        r'\bged\b',
        r'\bhigh\s+school\s+(?:graduate|education)\b',
        r'\bsecondary\s+education\b',
        r'\bno\s+degree\s+required\b',
        r'\bdegree\s+not\s+required\b',
    ]

    # Context patterns that indicate requirement vs preference
    REQUIRED_CONTEXT = [
        r'require[ds]?\b',
        r'must\s+have\b',
        r'minimum\b',
        r'essential\b',
        r'mandatory\b',
    ]

    PREFERRED_CONTEXT = [
        r'prefer(?:red)?\b',
        r'desired\b',
        r'nice\s+to\s+have\b',
        r'plus\b',
        r'bonus\b',
        r'ideal(?:ly)?\b',
    ]

    @classmethod
    def _find_education_mention(cls, text: str, patterns: list) -> Optional[re.Match]:
        """Find first match of any pattern in text."""
        for pattern in patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                return match
        return None

    @classmethod
    def _is_required_context(cls, text: str, match_pos: int) -> bool:
        """Check if the education mention is in a 'required' context."""
        # Look at text around the match (100 chars before and after)
        start = max(0, match_pos - 100)
        end = min(len(text), match_pos + 100)
        context = text[start:end].lower()
        
        # Check for required indicators
        for pattern in cls.REQUIRED_CONTEXT:
            if re.search(pattern, context):
                return True
        
        # Check it's not in a "preferred" context
        for pattern in cls.PREFERRED_CONTEXT:
            if re.search(pattern, context):
                return False
        
        # Default to required if mentioned in requirements-like section
        return True

    @classmethod
    def classify(cls, description: str, requirements: str = "") -> Tuple[Optional[str], str]:
        """
        Classify minimum education level from job description and requirements.

        Returns:
            Tuple of (education_level, reason)
            education_level is one of: 'high_school', 'associate', 'bachelor', 'master', 'phd', None
        """
        # Combine description and requirements, prioritize requirements section
        full_text = f"{requirements} {description}".lower()
        
        if not full_text.strip():
            return (None, "No text to analyze")

        # Check from highest to lowest education level
        # PhD
        match = cls._find_education_mention(full_text, cls.PHD_PATTERNS)
        if match and cls._is_required_context(full_text, match.start()):
            return ('phd', f"Found PhD requirement: {match.group()}")

        # Master's
        match = cls._find_education_mention(full_text, cls.MASTER_PATTERNS)
        if match and cls._is_required_context(full_text, match.start()):
            return ('master', f"Found Master's requirement: {match.group()}")

        # Bachelor's
        match = cls._find_education_mention(full_text, cls.BACHELOR_PATTERNS)
        if match and cls._is_required_context(full_text, match.start()):
            return ('bachelor', f"Found Bachelor's requirement: {match.group()}")

        # Associate
        match = cls._find_education_mention(full_text, cls.ASSOCIATE_PATTERNS)
        if match and cls._is_required_context(full_text, match.start()):
            return ('associate', f"Found Associate's requirement: {match.group()}")

        # High school / no degree
        match = cls._find_education_mention(full_text, cls.HIGH_SCHOOL_PATTERNS)
        if match:
            return ('high_school', f"Found high school/no degree: {match.group()}")

        return (None, "No clear education requirement found")

    @classmethod
    def get_best_classification(
        cls,
        description: str,
        requirements: str = "",
        existing_level: Optional[str] = None
    ) -> Tuple[Optional[str], str]:
        """
        Get the best education level classification considering existing data.

        Priority:
        1. If existing_level is set and valid, keep it
        2. Use text-based classification
        """
        valid_levels = ['high_school', 'associate', 'bachelor', 'master', 'phd']
        
        # If already classified, don't override
        if existing_level and existing_level in valid_levels:
            return (existing_level, f"Already classified as {existing_level}")

        # Use text-based classification
        return cls.classify(description, requirements)
