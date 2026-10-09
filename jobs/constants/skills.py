"""
Skills taxonomy for Impact Match.

Curated list of 100+ skills relevant to impact-sector jobs.
Each skill has a slug (for storage), label (for display), and category.
"""

from dataclasses import dataclass
from typing import List


@dataclass
class Skill:
    slug: str
    label: str
    category: str


# Skill categories for organization
SKILL_CATEGORIES = [
    ("technical", "Technical & Engineering"),
    ("data", "Data & Analytics"),
    ("product", "Product & Design"),
    ("operations", "Operations & Administration"),
    ("finance", "Finance & Accounting"),
    ("communications", "Communications & Marketing"),
    ("research", "Research & Analysis"),
    ("policy", "Policy & Advocacy"),
    ("fundraising", "Fundraising & Development"),
    ("program", "Program & Project Management"),
    ("people", "People & Leadership"),
    ("domain", "Domain Expertise"),
]

# Full skills list organized by category
SKILLS: List[Skill] = [
    # === Technical & Engineering ===
    Skill("python", "Python", "technical"),
    Skill("java", "Java", "technical"),
    Skill("javascript", "JavaScript", "technical"),
    Skill("typescript", "TypeScript", "technical"),
    Skill("react", "React", "technical"),
    Skill("nodejs", "Node.js", "technical"),
    Skill("django", "Django", "technical"),
    Skill("spring-boot", "Spring Boot", "technical"),
    Skill("sql", "SQL", "technical"),
    Skill("aws", "AWS", "technical"),
    Skill("gcp", "Google Cloud", "technical"),
    Skill("azure", "Azure", "technical"),
    Skill("docker", "Docker", "technical"),
    Skill("kubernetes", "Kubernetes", "technical"),
    Skill("terraform", "Terraform", "technical"),
    Skill("git", "Git", "technical"),
    Skill("cicd", "CI/CD", "technical"),
    Skill("api-design", "API Design", "technical"),
    Skill("system-design", "System Design", "technical"),
    Skill("security", "Security Engineering", "technical"),
    Skill("mobile-dev", "Mobile Development", "technical"),
    Skill("backend", "Backend Development", "technical"),
    Skill("frontend", "Frontend Development", "technical"),
    Skill("fullstack", "Full Stack Development", "technical"),
    Skill("software-engineering", "Software Engineering", "technical"),
    Skill("linux", "Linux", "technical"),
    Skill("networking", "Networking", "technical"),
    Skill("csharp", "C#", "technical"),
    Skill("dotnet", ".NET", "technical"),
    Skill("angular", "Angular", "technical"),
    Skill("postgresql", "PostgreSQL", "technical"),
    Skill("fiber-optics", "Fiber Optics", "technical"),
    Skill("civil-engineering", "Civil Engineering", "technical"),
    Skill("structural-engineering", "Structural Engineering", "technical"),
    Skill("electrical-engineering", "Electrical Engineering", "technical"),
    Skill("mechanical-engineering", "Mechanical Engineering", "technical"),
    Skill("power-systems", "Power Systems", "technical"),
    Skill("gis", "GIS", "data"),
    Skill("lean", "Lean", "technical"),
    Skill("formal-methods", "Formal Methods", "research"),
    Skill("sap", "SAP", "technical"),
    Skill("abap", "ABAP", "technical"),
    Skill("fiori", "SAP Fiori", "technical"),
    Skill("prompt-engineering", "Prompt Engineering", "technical"),
    Skill("electrical-installation", "Electrical Installation", "technical"),
    Skill("hvac", "HVAC", "technical"),
    Skill("plumbing", "Plumbing", "technical"),

    # === Data & Analytics ===
    Skill("data-analysis", "Data Analysis", "data"),
    Skill("data-engineering", "Data Engineering", "data"),
    Skill("data-visualization", "Data Visualization", "data"),
    Skill("machine-learning", "Machine Learning", "data"),
    Skill("deep-learning", "Deep Learning", "data"),
    Skill("nlp", "Natural Language Processing", "data"),
    Skill("computer-vision", "Computer Vision", "data"),
    Skill("statistics", "Statistics", "data"),
    Skill("r", "R", "data"),
    Skill("pandas", "Pandas", "data"),
    Skill("tensorflow", "TensorFlow", "data"),
    Skill("pytorch", "PyTorch", "data"),
    Skill("tableau", "Tableau", "data"),
    Skill("powerbi", "Power BI", "data"),
    Skill("excel-advanced", "Advanced Excel", "data"),
    Skill("ab-testing", "A/B Testing", "data"),
    Skill("impact-measurement", "Impact Measurement", "data"),
    Skill("me-evaluation", "M&E / Evaluation", "data"),
    Skill("analytical-chemistry", "Analytical Chemistry", "research"),

    # === Product & Design ===
    Skill("product-management", "Product Management", "product"),
    Skill("ux-design", "UX Design", "product"),
    Skill("ui-design", "UI Design", "product"),
    Skill("user-research", "User Research", "product"),
    Skill("prototyping", "Prototyping", "product"),
    Skill("figma", "Figma", "product"),
    Skill("design-systems", "Design Systems", "product"),
    Skill("accessibility", "Accessibility", "product"),
    Skill("product-strategy", "Product Strategy", "product"),
    Skill("agile-scrum", "Agile/Scrum", "product"),
    Skill("roadmapping", "Roadmapping", "product"),

    # === Operations & Administration ===
    Skill("operations-management", "Operations Management", "operations"),
    Skill("process-improvement", "Process Improvement", "operations"),
    Skill("project-coordination", "Project Coordination", "operations"),
    Skill("office-management", "Office Management", "operations"),
    Skill("vendor-management", "Vendor Management", "operations"),
    Skill("logistics", "Logistics", "operations"),
    Skill("supply-chain", "Supply Chain", "operations"),
    Skill("event-planning", "Event Planning", "operations"),
    Skill("administrative", "Administrative Support", "operations"),
    Skill("crm", "CRM Systems", "operations"),
    Skill("salesforce", "Salesforce", "operations"),
    Skill("contract-management", "Contract Management", "operations"),
    Skill("compliance", "Compliance", "operations"),

    # === Finance & Accounting ===
    Skill("financial-modeling", "Financial Modeling", "finance"),
    Skill("budgeting", "Budgeting", "finance"),
    Skill("accounting", "Accounting", "finance"),
    Skill("financial-analysis", "Financial Analysis", "finance"),
    Skill("grant-management", "Grant Management", "finance"),
    Skill("financial-reporting", "Financial Reporting", "finance"),
    Skill("nonprofit-accounting", "Nonprofit Accounting", "finance"),
    Skill("forecasting", "Forecasting", "finance"),
    Skill("quickbooks", "QuickBooks", "finance"),

    # === Communications & Marketing ===
    Skill("writing", "Writing", "communications"),
    Skill("copywriting", "Copywriting", "communications"),
    Skill("content-strategy", "Content Strategy", "communications"),
    Skill("social-media", "Social Media", "communications"),
    Skill("email-marketing", "Email Marketing", "communications"),
    Skill("seo", "SEO", "communications"),
    Skill("public-relations", "Public Relations", "communications"),
    Skill("media-relations", "Media Relations", "communications"),
    Skill("storytelling", "Storytelling", "communications"),
    Skill("brand-strategy", "Brand Strategy", "communications"),
    Skill("video-production", "Video Production", "communications"),
    Skill("graphic-design", "Graphic Design", "communications"),
    Skill("presentation", "Presentation Skills", "communications"),
    Skill("communication", "Communication", "communications"),
    Skill("sales", "Sales", "communications"),
    Skill("customer-service", "Customer Service", "communications"),
    Skill("marketing", "Marketing", "communications"),
    Skill("negotiation", "Negotiation", "communications"),

    # === Research & Analysis ===
    Skill("research", "Research", "research"),
    Skill("qualitative-research", "Qualitative Research", "research"),
    Skill("quantitative-research", "Quantitative Research", "research"),
    Skill("literature-review", "Literature Review", "research"),
    Skill("academic-writing", "Academic Writing", "research"),
    Skill("survey-design", "Survey Design", "research"),
    Skill("interviewing", "Interviewing", "research"),
    Skill("market-research", "Market Research", "research"),
    Skill("competitive-analysis", "Competitive Analysis", "research"),
    Skill("cost-benefit-analysis", "Cost-Benefit Analysis", "research"),
    Skill("systems-thinking", "Systems Thinking", "research"),
    Skill("biology", "Biology", "domain"),

    # === Policy & Advocacy ===
    Skill("policy-analysis", "Policy Analysis", "policy"),
    Skill("policy-writing", "Policy Writing", "policy"),
    Skill("advocacy", "Advocacy", "policy"),
    Skill("government-relations", "Government Relations", "policy"),
    Skill("regulatory-affairs", "Regulatory Affairs", "policy"),
    Skill("legislative-tracking", "Legislative Tracking", "policy"),
    Skill("coalition-building", "Coalition Building", "policy"),
    Skill("stakeholder-engagement", "Stakeholder Engagement", "policy"),
    Skill("public-speaking", "Public Speaking", "policy"),

    # === Fundraising & Development ===
    Skill("fundraising", "Fundraising", "fundraising"),
    Skill("major-gifts", "Major Gifts", "fundraising"),
    Skill("grant-writing", "Grant Writing", "fundraising"),
    Skill("donor-relations", "Donor Relations", "fundraising"),
    Skill("crowdfunding", "Crowdfunding", "fundraising"),
    Skill("foundation-relations", "Foundation Relations", "fundraising"),
    Skill("corporate-partnerships", "Corporate Partnerships", "fundraising"),
    Skill("annual-giving", "Annual Giving", "fundraising"),
    Skill("planned-giving", "Planned Giving", "fundraising"),

    # === Program & Project Management ===
    Skill("program-management", "Program Management", "program"),
    Skill("project-management", "Project Management", "program"),
    Skill("strategic-planning", "Strategic Planning", "program"),
    Skill("implementation", "Implementation", "program"),
    Skill("monitoring", "Monitoring", "program"),
    Skill("reporting", "Reporting", "program"),
    Skill("risk-management", "Risk Management", "program"),
    Skill("capacity-building", "Capacity Building", "program"),
    Skill("partnership-development", "Partnership Development", "program"),
    Skill("community-engagement", "Community Engagement", "program"),

    # === People & Leadership ===
    Skill("people-management", "People Management", "people"),
    Skill("team-leadership", "Team Leadership", "people"),
    Skill("hiring", "Hiring", "people"),
    Skill("coaching", "Coaching", "people"),
    Skill("mentoring", "Mentoring", "people"),
    Skill("conflict-resolution", "Conflict Resolution", "people"),
    Skill("dei", "DEI", "people"),
    Skill("culture-building", "Culture Building", "people"),
    Skill("remote-management", "Remote Team Management", "people"),
    Skill("performance-management", "Performance Management", "people"),
    Skill("organizational-development", "Organizational Development", "people"),
    Skill("change-management", "Change Management", "people"),

    # === Domain Expertise ===
    Skill("climate-science", "Climate Science", "domain"),
    Skill("climate-policy", "Climate Policy", "domain"),
    Skill("renewable-energy", "Renewable Energy", "domain"),
    Skill("sustainability", "Sustainability", "domain"),
    Skill("circular-economy", "Circular Economy", "domain"),
    Skill("carbon-accounting", "Carbon Accounting", "domain"),
    Skill("global-health", "Global Health", "domain"),
    Skill("public-health", "Public Health", "domain"),
    Skill("epidemiology", "Epidemiology", "domain"),
    Skill("health-economics", "Health Economics", "domain"),
    Skill("development-economics", "Development Economics", "domain"),
    Skill("poverty-alleviation", "Poverty Alleviation", "domain"),
    Skill("microfinance", "Microfinance", "domain"),
    Skill("education-policy", "Education Policy", "domain"),
    Skill("curriculum-design", "Curriculum Design", "domain"),
    Skill("ai-safety", "AI Safety", "domain"),
    Skill("ai-ethics", "AI Ethics", "domain"),
    Skill("biosecurity", "Biosecurity", "domain"),
    Skill("nuclear-policy", "Nuclear Policy", "domain"),
    Skill("animal-welfare", "Animal Welfare", "domain"),
    Skill("effective-altruism", "Effective Altruism", "domain"),
    Skill("cause-prioritization", "Cause Prioritization", "domain"),
    Skill("humanitarian-response", "Humanitarian Response", "domain"),
    Skill("refugee-services", "Refugee Services", "domain"),
    Skill("human-rights", "Human Rights", "domain"),
    Skill("international-development", "International Development", "domain"),
    Skill("social-enterprise", "Social Enterprise", "domain"),
    Skill("impact-investing", "Impact Investing", "domain"),
]

# Create lookup dictionaries for quick access
SKILLS_BY_SLUG = {skill.slug: skill for skill in SKILLS}

# These are deliberately narrow, role-relevant variants rather than broad
# semantic guesses. They make recurring import enrichment useful for common
# wording that does not exactly match a display label.
SKILL_ALIASES = {
    "networking": ("network infrastructure", "network devices"),
    "fiber-optics": ("fiber optic", "fibre optic"),
    "civil-engineering": ("civil engineer",),
    "structural-engineering": ("structural engineer",),
    "electrical-engineering": ("electrical engineer",),
    "mechanical-engineering": ("mechanical engineer",),
    "formal-methods": ("formal method", "formal verification"),
    "sales": (
        "account manager",
        "revenue officer",
        "sales representative",
        "sales experience",
        "field sales",
        "business development",
        "account executive",
    ),
    "power-systems": (
        "power system",
        "power engineer",
        "interconnection studies",
        "interconnection planning",
    ),
    "software-engineering": ("software engineer", "software product"),
    "machine-learning": ("ml models", "machine learning models"),
    "customer-service": (
        "customer service",
        "customer-centric",
        "customer centric",
        "customer support",
        "customer success",
    ),
    "sap": ("sap interface",),
    "abap": ("abap development",),
    "fiori": ("sap fiori",),
    "prompt-engineering": (
        "prompt engineering",
        "generative ai prompts",
        "genai prompts",
        "prompts for generative ai",
    ),
    "electrical-installation": ("electrical fixtures", "led fixtures"),
    "hvac": ("energy-efficient thermostats",),
    "plumbing": (
        "water-saving toilets",
        "shower heads",
        "sink faucets",
        "sink aerators",
    ),
    "analytical-chemistry": (
        "analytical chemistry",
        "food chemistry",
        "hplc",
        "aas",
        "uv spectrophotometer",
        "ftir",
    ),
    "biology": ("biology expert", "biology-related"),
}

# Historical model output used several common aliases that are safe to fold
# into the curated taxonomy. Everything else remains auditable but is not used
# for matching until it has a canonical representation.
LEGACY_SKILL_NORMALIZATIONS = {
    "c#": "csharp",
    "operations": "operations-management",
    "business-development": "partnership-development",
    "legal-research": "research",
    "volunteer-management": "people-management",
    "customer-success": "customer-service",
    "customer-support": "customer-service",
    "customer-relationship-management": "crm",
    "client-relationship-management": "crm",
    "communication": "communication",
    "communications": "communication",
    "communications-outreach": "communication",
    "training": "capacity-building",
    "facilitation": "capacity-building",
    "management": "people-management",
    "technical-writing": "writing",
    "legal-writing": "writing",
    "policy": "policy-analysis",
    "analytics": "data-analysis",
    "data": "data-analysis",
    "finance": "financial-analysis",
    "quality-assurance": "testing",
    "customer-success-management": "customer-service",
    "ci-cd": "cicd",
    "devops": "cicd",
}


def normalize_skill_slugs(values) -> list[str]:
    """Return only curated skill slugs, folding high-confidence legacy aliases."""
    normalized = []
    for value in values or ():
        if not isinstance(value, str):
            continue
        slug = value.strip().lower().replace("_", "-").replace(" ", "-")
        slug = LEGACY_SKILL_NORMALIZATIONS.get(slug, slug)
        if slug in SKILLS_BY_SLUG and slug not in normalized:
            normalized.append(slug)
    return normalized
SKILLS_BY_CATEGORY = {}
for skill in SKILLS:
    if skill.category not in SKILLS_BY_CATEGORY:
        SKILLS_BY_CATEGORY[skill.category] = []
    SKILLS_BY_CATEGORY[skill.category].append(skill)


def get_skill_label(slug: str) -> str:
    """Get display label for a skill slug."""
    skill = SKILLS_BY_SLUG.get(slug)
    return skill.label if skill else slug.replace("-", " ").title()


def get_skills_for_category(category: str) -> List[Skill]:
    """Get all skills in a category."""
    return SKILLS_BY_CATEGORY.get(category, [])


def get_all_skill_choices() -> List[tuple]:
    """Get skills as Django choices format (slug, label)."""
    return [(skill.slug, skill.label) for skill in SKILLS]


def get_categorized_skill_choices() -> dict:
    """Get skills grouped by category for UI display."""
    result = {}
    for cat_slug, cat_label in SKILL_CATEGORIES:
        skills = SKILLS_BY_CATEGORY.get(cat_slug, [])
        if skills:
            result[cat_label] = [(s.slug, s.label) for s in skills]
    return result


def search_skills(query: str) -> List[Skill]:
    """Search skills by label (case-insensitive)."""
    query = query.lower()
    return [s for s in SKILLS if query in s.label.lower() or query in s.slug]


# Format skills for LLM extraction prompt (used by llm_parser.py)
SKILLS_FOR_PROMPT = "\n".join(
    f"- {skill.slug}: {skill.label}" for skill in SKILLS
)
