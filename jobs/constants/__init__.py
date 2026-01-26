# Re-export from original constants module (now merged)
# These are used by importers and LLM parser

# Standard impact areas for job categorization
IMPACT_AREAS = [
    {
        "slug": "ai-safety",
        "name": "AI Safety & Governance",
        "icon": "🤖",
        "keywords": ["ai safety", "ai alignment", "machine learning safety", "ai governance", "ai policy", "artificial intelligence", "llm", "responsible ai"],
    },
    {
        "slug": "climate-environment",
        "name": "Climate & Environment",
        "icon": "🌍",
        "keywords": ["climate", "environment", "sustainability", "carbon", "renewable", "energy", "conservation", "biodiversity", "nature", "ocean", "forest"],
    },
    {
        "slug": "global-health",
        "name": "Global Health",
        "icon": "🏥",
        "keywords": ["health", "medical", "disease", "pandemic", "public health", "healthcare", "medicine", "nutrition", "mental health"],
    },
    {
        "slug": "biosecurity",
        "name": "Biosecurity & Pandemic Preparedness",
        "icon": "🦠",
        "keywords": ["biosecurity", "pandemic", "biodefense", "infectious disease", "epidemic", "pathogen", "biological risk"],
    },
    {
        "slug": "animal-welfare",
        "name": "Animal Welfare",
        "icon": "🐾",
        "keywords": ["animal", "farmed animal", "animal rights", "animal protection", "wildlife", "vegan", "factory farming"],
    },
    {
        "slug": "poverty-development",
        "name": "Poverty & Economic Development",
        "icon": "💰",
        "keywords": ["poverty", "economic development", "microfinance", "cash transfer", "global development", "inequality", "financial inclusion"],
    },
    {
        "slug": "education",
        "name": "Education & Research",
        "icon": "📚",
        "keywords": ["education", "research", "academic", "university", "learning", "training", "scholarship", "school"],
    },
    {
        "slug": "human-rights",
        "name": "Human Rights & Justice",
        "icon": "⚖️",
        "keywords": ["human rights", "justice", "civil liberties", "democracy", "freedom", "equality", "discrimination", "refugee", "immigrant"],
    },
    {
        "slug": "gender-equality-social-inclusion",
        "name": "Gender Equality and Social Inclusion (GESI)",
        "icon": "⚧️",
        "keywords": ["gender equality", "gesi", "social inclusion", "women empowerment", "lgbtq", "disability inclusion", "equity", "diversity", "inclusion"],
    },
    {
        "slug": "humanitarian",
        "name": "Humanitarian & Disaster Relief",
        "icon": "🆘",
        "keywords": ["humanitarian", "disaster", "relief", "emergency", "crisis", "refugee", "conflict", "war"],
    },
    {
        "slug": "nuclear-security",
        "name": "Nuclear Security",
        "icon": "☢️",
        "keywords": ["nuclear", "weapons", "disarmament", "nonproliferation", "arms control"],
    },
    {
        "slug": "policy-advocacy",
        "name": "Policy & Advocacy",
        "icon": "📢",
        "keywords": ["policy", "advocacy", "campaign", "lobbying", "government", "legislation", "regulation", "public affairs"],
    },
    {
        "slug": "effective-altruism",
        "name": "Effective Altruism",
        "icon": "🎯",
        "keywords": ["effective altruism", "ea", "global priorities", "cause prioritization", "impact measurement", "givewell", "open philanthropy"],
    },
    {
        "slug": "technology",
        "name": "Technology & Engineering",
        "icon": "💻",
        "keywords": ["software", "engineering", "developer", "programming", "tech", "data", "platform", "product"],
    },
    {
        "slug": "communications",
        "name": "Communications & Media",
        "icon": "📱",
        "keywords": ["communications", "media", "journalism", "marketing", "content", "social media", "pr", "writing"],
    },
    {
        "slug": "operations",
        "name": "Operations & Administration",
        "icon": "⚙️",
        "keywords": ["operations", "admin", "hr", "finance", "accounting", "legal", "management", "project management", "coordinator"],
    },
    {
        "slug": "other",
        "name": "Other Impact Areas",
        "icon": "✨",
        "keywords": [],
    },
]

# Quick lookup by slug
IMPACT_AREA_SLUGS = [area["slug"] for area in IMPACT_AREAS]
IMPACT_AREA_NAMES = [area["name"] for area in IMPACT_AREAS]

# For AI prompt - formatted list with slugs
IMPACT_AREAS_FOR_PROMPT = "\n".join(
    f"  - \"{area['slug']}\": {area['name']}" for area in IMPACT_AREAS
)

# Export skills module
from .skills import (
    SKILLS,
    SKILL_CATEGORIES,
    SKILLS_BY_SLUG,
    SKILLS_BY_CATEGORY,
    get_skill_label,
    get_skills_for_category,
    get_all_skill_choices,
    get_categorized_skill_choices,
    search_skills,
)
