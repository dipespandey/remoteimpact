from django.core.management.base import BaseCommand

from gigs.models import GigCategory

CATEGORIES = [
    {
        "slug": "grant-fundraising-sprint",
        "name": "Grant & Fundraising Sprint",
        "is_field": False,
        "description": "Rewrite a section, create a pitch deck, or shortlist funders.",
        "rubric_templates": [
            {"label": "Clarity", "description": "Straightforward narrative and request"},
            {"label": "Alignment with funder", "description": "Shows why the org fits"},
            {"label": "Evidence/metrics", "description": "Uses proof and outcomes"},
            {"label": "Tone", "description": "Respectful, concise, and donor-ready"},
        ],
    },
    {
        "slug": "monitoring-evaluation-learning",
        "name": "Monitoring, Evaluation & Learning",
        "is_field": False,
        "description": "Survey design, cleaning data, and summarizing insights.",
        "rubric_templates": [
            {"label": "Method appropriateness", "description": "Right approach for question"},
            {"label": "Data quality", "description": "Handles bias and limitations"},
            {"label": "Insights clarity", "description": "Actionable findings"},
        ],
    },
    {
        "slug": "ops-automation",
        "name": "Ops & Automation",
        "is_field": False,
        "description": "Airtable/Notion/Zapier setups and lightweight automation.",
        "rubric_templates": [
            {"label": "Reliability", "description": "Runs without breaking"},
            {"label": "Documentation", "description": "Clear handoff and steps"},
            {"label": "Security/permissions", "description": "Access scoped appropriately"},
        ],
    },
    {
        "slug": "data-research",
        "name": "Data & Research",
        "is_field": False,
        "description": "Desk research, literature scans, stakeholder maps.",
        "rubric_templates": [
            {"label": "Sources quality", "description": "Credible, current sources"},
            {"label": "Rigor", "description": "Transparent method and citations"},
            {"label": "Synthesis clarity", "description": "Concise findings and limits"},
        ],
    },
    {
        "slug": "design-comms",
        "name": "Design & Comms",
        "is_field": False,
        "description": "Social packs, one-pagers, landing page copy/layout.",
        "rubric_templates": [
            {"label": "Meets brief", "description": "Matches objectives and audience"},
            {"label": "Accessibility", "description": "Readable, inclusive choices"},
            {"label": "Consistency", "description": "Brand + visual consistency"},
            {"label": "File hygiene", "description": "Organized files and exports"},
        ],
    },
    {
        "slug": "product-engineering",
        "name": "Product/Engineering (Scoped)",
        "is_field": False,
        "description": "Small features, bugfixes, or scripts with tests.",
        "rubric_templates": [
            {"label": "Acceptance criteria", "description": "Meets DoD and tests"},
            {"label": "Maintainability", "description": "Readable, small blast radius"},
            {"label": "Performance", "description": "No obvious regressions"},
        ],
    },
    {
        "slug": "translation-localization",
        "name": "Translation & Localization",
        "is_field": False,
        "description": "Translate or localize outreach materials.",
        "rubric_templates": [
            {"label": "Accuracy", "description": "Meaning preserved"},
            {"label": "Tone/context", "description": "Local nuance respected"},
            {"label": "Terminology", "description": "Consistent glossary use"},
        ],
    },
    {
        "slug": "field-data-collection",
        "name": "Field Data Collection (Verified)",
        "is_field": True,
        "description": "GPS-stamped surveys, price checks, or interviews.",
        "rubric_templates": [
            {"label": "Protocol adherence", "description": "Follows instrument and sampling"},
            {"label": "Evidence completeness", "description": "Proof and uploads present"},
            {"label": "Data quality", "description": "Clean, consistent entries"},
            {"label": "Ethics/privacy", "description": "Respects consent and safety"},
        ],
    },
    {
        "slug": "local-partner-liaison",
        "name": "Local Partner Liaison",
        "is_field": True,
        "description": "Find and vet local NGOs or contacts.",
        "rubric_templates": [
            {"label": "Contact quality", "description": "Relevant, responsive leads"},
            {"label": "Follow-through", "description": "Documented outreach steps"},
            {"label": "Reporting clarity", "description": "Summaries and next steps"},
        ],
    },
    {
        "slug": "local-procurement-verification",
        "name": "Local Procurement/Verification",
        "is_field": True,
        "description": "Quotes, verification photos, and receipts.",
        "rubric_templates": [
            {"label": "Compliance", "description": "Follows expense/quote rules"},
            {"label": "Proof provided", "description": "Photos/receipts attached"},
            {"label": "Timeliness", "description": "Delivered within agreed window"},
        ],
    },
]


class Command(BaseCommand):
    help = "Seed Impact Gig categories and rubric templates."

    def handle(self, *args, **options):
        created = 0
        for data in CATEGORIES:
            _, was_created = GigCategory.objects.update_or_create(
                slug=data["slug"],
                defaults={
                    "name": data["name"],
                    "description": data["description"],
                    "is_field": data["is_field"],
                    "rubric_templates": data.get("rubric_templates", []),
                },
            )
            if was_created:
                created += 1
        self.stdout.write(
            self.style.SUCCESS(f"Seeded categories. Created {created}, updated {len(CATEGORIES) - created}.")
        )
