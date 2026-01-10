"""
Location normalization utilities.

Converts messy location strings (addresses, city/state combos, garbage data)
into standardized country/region values for better filtering.
"""
from __future__ import annotations

import re


# Standard location values - these are what we normalize TO
STANDARD_LOCATIONS = {
    # Remote options
    "Remote": "Remote",  # Global remote
    "Remote (US)": "Remote (US)",
    "Remote (UK)": "Remote (UK)",
    "Remote (EU)": "Remote (EU)",
    "Remote (Canada)": "Remote (Canada)",
    "Remote (India)": "Remote (India)",
    "Remote (Australia)": "Remote (Australia)",
    "Remote (Germany)": "Remote (Germany)",
    "Remote (APAC)": "Remote (APAC)",
    "Remote (LATAM)": "Remote (LATAM)",
    "Remote (EMEA)": "Remote (EMEA)",
    # Countries
    "USA": "USA",
    "UK": "UK",
    "Canada": "Canada",
    "Germany": "Germany",
    "France": "France",
    "Netherlands": "Netherlands",
    "India": "India",
    "Australia": "Australia",
    "Sweden": "Sweden",
    "Switzerland": "Switzerland",
    "Ireland": "Ireland",
    "Spain": "Spain",
    "Italy": "Italy",
    "Poland": "Poland",
    "Belgium": "Belgium",
    "Austria": "Austria",
    "Denmark": "Denmark",
    "Norway": "Norway",
    "Finland": "Finland",
    "Singapore": "Singapore",
    "Japan": "Japan",
    "South Korea": "South Korea",
    "China": "China",
    "Brazil": "Brazil",
    "Mexico": "Mexico",
    "Israel": "Israel",
    "UAE": "UAE",
    "Kenya": "Kenya",
    "Nigeria": "Nigeria",
    "South Africa": "South Africa",
    "New Zealand": "New Zealand",
    "Greece": "Greece",
    "Portugal": "Portugal",
    # Regions
    "Europe": "Europe",
    "Asia": "Asia",
    "Africa": "Africa",
    "Americas": "Americas",
}

# Patterns to identify US locations
US_PATTERNS = [
    # State abbreviations - require comma before or be at end, to avoid matching "or" in "Paris or Lyon"
    # Exclude OR and IN which are common words - handle these separately with more context
    r",\s*(AL|AK|AZ|AR|CA|CO|CT|DE|FL|GA|HI|ID|IL|IA|KS|KY|LA|ME|MD|MA|MI|MN|MS|MO|MT|NE|NV|NH|NJ|NM|NY|NC|ND|OH|OK|PA|RI|SC|SD|TN|TX|UT|VT|VA|WA|WV|WI|WY|DC)\b",
    # OR and IN require more context (must have USA or be in specific format)
    r",\s*(OR|IN)\s*,?\s*(USA|United States|$)",
    # State names
    r"\b(Alabama|Alaska|Arizona|Arkansas|California|Colorado|Connecticut|Delaware|Florida|Georgia|Hawaii|Idaho|Illinois|Indiana|Iowa|Kansas|Kentucky|Louisiana|Maine|Maryland|Massachusetts|Michigan|Minnesota|Mississippi|Missouri|Montana|Nebraska|Nevada|New Hampshire|New Jersey|New Mexico|New York|North Carolina|North Dakota|Ohio|Oklahoma|Oregon|Pennsylvania|Rhode Island|South Carolina|South Dakota|Tennessee|Texas|Utah|Vermont|Virginia|Washington|West Virginia|Wisconsin|Wyoming)\b",
    # US cities
    r"\b(San Francisco|Los Angeles|New York|Chicago|Boston|Seattle|Austin|Denver|Portland|Miami|Atlanta|Dallas|Houston|Phoenix|San Diego|San Jose|Washington D\.?C\.?|Philadelphia|Detroit|Minneapolis|Nashville|Ann Arbor|Salt Lake City|Louisville|Boulder|Fremont|Burlington|Columbia|Emeryville|El Segundo)\b",
    # Explicit USA mentions
    r"\bUSA\b",
    r"\bU\.S\.A\b",
    r"\bUnited States\b",
]

# Patterns to identify UK locations
UK_PATTERNS = [
    r"\bUK\b",
    r"\bU\.K\b",
    r"\bUnited Kingdom\b",
    r"\bEngland\b",
    r"\bScotland\b",
    r"\bWales\b",
    r"\b(London|Manchester|Birmingham|Edinburgh|Glasgow|Bristol|Liverpool|Leeds|Belfast|Cardiff)\b",
]

# Patterns for other countries
COUNTRY_PATTERNS = {
    "Canada": [r"\bCanada\b", r"\b(Toronto|Vancouver|Montreal|Ottawa|Calgary)\b", r"\b(ON|BC|QC|AB)\b.*Canada"],
    "Germany": [r"\bGermany\b", r"\b(Berlin|Munich|Frankfurt|Hamburg|Cologne)\b"],
    "France": [r"\bFrance\b", r"\b(Paris|Lyon|Marseille)\b"],
    "Netherlands": [r"\bNetherlands\b", r"\b(Amsterdam|Rotterdam|Utrecht|Bilthoven)\b"],
    "India": [r"\bIndia\b", r"\b(Bangalore|Bengaluru|Mumbai|Delhi|New Delhi|Hyderabad|Chennai|Pune|Gurgaon|Kolkata|Ahmedabad|Karnataka|Maharashtra)\b"],
    "Australia": [r"\bAustralia\b", r"\b(Sydney|Melbourne|Brisbane|Perth|Adelaide|Newcastle)\b"],
    "Sweden": [r"\bSweden\b", r"\b(Stockholm|Gothenburg|Malmo)\b"],
    "Switzerland": [r"\bSwitzerland\b", r"\b(Zurich|Geneva|Basel|Bern)\b"],
    "Ireland": [r"\bIreland\b", r"\bDublin\b"],
    "Spain": [r"\bSpain\b", r"\b(Madrid|Barcelona|Valencia)\b"],
    "Italy": [r"\bItaly\b", r"\b(Rome|Milan|Florence)\b"],
    "Poland": [r"\bPoland\b", r"\b(Warsaw|Krakow|Wroclaw)\b"],
    "Belgium": [r"\bBelgium\b", r"\b(Brussels|Antwerp)\b"],
    "Austria": [r"\bAustria\b", r"\bVienna\b"],
    "Denmark": [r"\bDenmark\b", r"\bCopenhagen\b"],
    "Norway": [r"\bNorway\b", r"\bOslo\b"],
    "Finland": [r"\bFinland\b", r"\b(Helsinki|Tampere|Turku|Lahti)\b"],
    "Singapore": [r"\bSingapore\b"],
    "Japan": [r"\bJapan\b", r"\b(Tokyo|Osaka|Kyoto)\b"],
    "Israel": [r"\bIsrael\b", r"\bTel Aviv\b"],
    "UAE": [r"\bUAE\b", r"\bUnited Arab Emirates\b", r"\b(Dubai|Abu Dhabi)\b"],
    "Kenya": [r"\bKenya\b", r"\bNairobi\b"],
    "Nigeria": [r"\bNigeria\b", r"\bLagos\b"],
    "South Africa": [r"\bSouth Africa\b", r"\b(Johannesburg|Cape Town)\b"],
    "New Zealand": [r"\bNew Zealand\b", r"\b(Auckland|Wellington)\b"],
    "Greece": [r"\bGreece\b", r"\bAthens\b"],
    "Portugal": [r"\bPortugal\b", r"\bLisbon\b"],
    "Bangladesh": [r"\bBangladesh\b", r"\bDhaka\b"],
    "Brazil": [r"\bBrazil\b", r"\b(Sao Paulo|Rio de Janeiro)\b"],
    "Mexico": [r"\bMexico\b", r"\bMexico City\b"],
    "South Korea": [r"\bSouth Korea\b", r"\bKorea\b", r"\bSeoul\b"],
    "China": [r"\bChina\b", r"\b(Beijing|Shanghai|Shenzhen)\b"],
}

# Regional patterns
REGION_PATTERNS = {
    "Remote (EU)": [r"\bEU\b", r"\bEurope\b", r"\bEuropean\b", r"\bEMEA\b"],
    "Remote (APAC)": [r"\bAPAC\b", r"\bAsia Pacific\b", r"\bAsia-Pacific\b"],
    "Remote (LATAM)": [r"\bLATAM\b", r"\bLatin America\b"],
}


def normalize_location(location: str) -> str:
    """
    Normalize a location string to a standard value.

    Args:
        location: Raw location string from job posting

    Returns:
        One of the STANDARD_LOCATIONS values (e.g., "Remote", "USA", "Remote (UK)")
    """
    if not location:
        return "Remote"

    loc_lower = location.lower().strip()

    # Already clean "Remote" variants
    if loc_lower == "remote" or loc_lower == "remote, global" or loc_lower == "global":
        return "Remote"

    if loc_lower == "flexible" or loc_lower == "anywhere":
        return "Remote"

    # Check for garbage data (job titles in location field)
    # Pattern: "Job Title Company Added Date Location"
    garbage_pattern = r"Added\s+(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+\d+"
    if re.search(garbage_pattern, location, re.IGNORECASE):
        # Try to extract the actual location from the end
        # e.g., "Clinical Development Lead Flagship Pioneering Added Jan 9 Cambridge MA, USA"
        match = re.search(r"Added\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+\d+\s+(.+)$", location, re.IGNORECASE)
        if match:
            location = match.group(1).strip()
            loc_lower = location.lower()
        else:
            return "Remote"  # Can't parse, default to Remote

    # Check for explicit remote with region
    remote_region_patterns = [
        (r"\bremote\b.*\b(us|usa|united states|america)\b", "Remote (US)"),
        (r"\bremote\b.*\b(uk|united kingdom|england|britain)\b", "Remote (UK)"),
        (r"\bremote\b.*\b(eu|europe|european)\b", "Remote (EU)"),
        (r"\bremote\b.*\b(canada|canadian)\b", "Remote (Canada)"),
        (r"\bremote\b.*\b(india|indian)\b", "Remote (India)"),
        (r"\bremote\b.*\b(australia|australian)\b", "Remote (Australia)"),
        (r"\bremote\b.*\b(germany|german)\b", "Remote (Germany)"),
        (r"\bremote\b.*\b(apac|asia.?pacific)\b", "Remote (APAC)"),
        (r"\bremote\b.*\b(latam|latin.?america)\b", "Remote (LATAM)"),
        (r"\bremote\b.*\b(emea)\b", "Remote (EMEA)"),
        # Also check reverse order
        (r"\b(us|usa|united states)\b.*\bremote\b", "Remote (US)"),
        (r"\b(uk|united kingdom)\b.*\bremote\b", "Remote (UK)"),
        (r"\b(canada)\b.*\bremote\b", "Remote (Canada)"),
    ]

    for pattern, result in remote_region_patterns:
        if re.search(pattern, loc_lower):
            return result

    # Check for US patterns
    for pattern in US_PATTERNS:
        if re.search(pattern, location, re.IGNORECASE):
            # If it mentions remote, make it Remote (US)
            if "remote" in loc_lower:
                return "Remote (US)"
            return "USA"

    # Check for UK patterns
    for pattern in UK_PATTERNS:
        if re.search(pattern, location, re.IGNORECASE):
            if "remote" in loc_lower:
                return "Remote (UK)"
            return "UK"

    # Check for other countries
    for country, patterns in COUNTRY_PATTERNS.items():
        for pattern in patterns:
            if re.search(pattern, location, re.IGNORECASE):
                if "remote" in loc_lower:
                    # Map to Remote (Country) if we have it
                    remote_key = f"Remote ({country})"
                    if remote_key in STANDARD_LOCATIONS:
                        return remote_key
                    return "Remote"
                return country

    # Check for regional patterns
    for region, patterns in REGION_PATTERNS.items():
        for pattern in patterns:
            if re.search(pattern, location, re.IGNORECASE):
                return region

    # If location contains "remote" but no region identified, default to Remote
    if "remote" in loc_lower:
        return "Remote"

    # If we couldn't identify any country/region, default to Remote
    # (since this is a remote job board)
    return "Remote"
