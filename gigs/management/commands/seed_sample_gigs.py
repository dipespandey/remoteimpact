from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.utils import timezone

from gigs.management.commands.seed_gig_categories import CATEGORIES
from gigs.models import Gig, GigCategory, RubricCriterion
from jobs.models import Organization
from jobs.utils import unique_slug

User = get_user_model()


class Command(BaseCommand):
    help = "Create sample Impact Gigs with paid trials (idempotent)."

    def handle(self, *args, **options):
        # Ensure categories exist (reuse the category seeder data)
        for data in CATEGORIES:
            GigCategory.objects.update_or_create(
                slug=data["slug"],
                defaults={
                    "name": data["name"],
                    "description": data["description"],
                    "is_field": data["is_field"],
                    "rubric_templates": data.get("rubric_templates", []),
                },
            )

        # Create a sample organization and employer
        org, _ = Organization.objects.get_or_create(
            slug="demo-impact-org",
            defaults={
                "name": "Demo Impact Org",
                "description": "Sample nonprofit for demo gigs",
                "website": "https://example.org",
                "verification_status": Organization.VerificationStatus.VERIFIED,
            },
        )
        employer, _ = User.objects.get_or_create(
            email="employer@example.com",
            defaults={"username": "employer", "is_staff": True},
        )
        org.members.add(employer)

        samples = [
            {
                "title": "Grant narrative sprint (rewrite 1 section)",
                "category_slug": "grant-fundraising-sprint",
                "remote_policy": Gig.RemotePolicy.ANYWHERE,
                "eligible_countries": [],
                "budget_fixed_cents": 90000,
                "currency": "USD",
                "trial_fee_cents": 15000,
                "trial_hours_cap": 3,
                "trial_due_days": 5,
                "deliverables": [
                    "Rewrite one grant section (500-700 words)",
                    "1-page summary with metrics alignment",
                ],
                "definition_of_done": "Section rewritten, summary delivered, rubric scores 4/5+.",
                "brief_redacted": "Rewriting a climate grant narrative section for clarity and funder fit.",
                "brief_full": "Full funder brief with past submissions and KPIs. Focus on outcomes and alignment.",
                "nda_required": False,
                "requires_field_verification": False,
            },
            {
                "title": "MEL survey design + analysis plan",
                "category_slug": "monitoring-evaluation-learning",
                "remote_policy": Gig.RemotePolicy.TIMEZONE,
                "eligible_countries": [],
                "timezone_overlap": "4 hours with UTC+1 to UTC+4",
                "budget_fixed_cents": 80000,
                "currency": "USD",
                "trial_fee_cents": 12000,
                "trial_hours_cap": 2.5,
                "trial_due_days": 4,
                "deliverables": [
                    "Lightweight survey instrument draft",
                    "Short analysis plan (methods + sampling)",
                ],
                "definition_of_done": "Instrument + plan approved, rubric scores 4/5+.",
                "brief_redacted": "Design a short beneficiary survey and outline an analysis plan.",
                "brief_full": "Full MEL context, prior surveys, and target respondent profiles.",
                "nda_required": False,
                "requires_field_verification": False,
            },
            {
                "title": "Field price check in Nepal (10 markets)",
                "category_slug": "field-data-collection",
                "remote_policy": Gig.RemotePolicy.COUNTRY,
                "eligible_countries": ["NP"],
                "budget_fixed_cents": 70000,
                "currency": "USD",
                "trial_fee_cents": 20000,
                "trial_hours_cap": 4,
                "trial_due_days": 5,
                "deliverables": [
                    "Collect prices from 10 markets with GPS-stamped photos",
                    "Upload receipts and short notes",
                ],
                "definition_of_done": "All markets covered with evidence; rubric scores 4/5+.",
                "brief_redacted": "Collect market price data for staple goods with photo evidence.",
                "brief_full": "Specific markets list, goods list, expense rules, and audit protocol.",
                "nda_required": True,
                "requires_field_verification": True,
            },
        ]

        created = 0
        for data in samples:
            category = GigCategory.objects.get(slug=data["category_slug"])
            slug = unique_slug(Gig, data["title"])
            gig, was_created = Gig.objects.get_or_create(
                slug=slug,
                defaults={
                    "organization": org,
                    "category": category,
                    "title": data["title"],
                    "remote_policy": data["remote_policy"],
                    "eligible_countries": data.get("eligible_countries", []),
                    "timezone_overlap": data.get("timezone_overlap", ""),
                    "budget_fixed_cents": data["budget_fixed_cents"],
                    "currency": data["currency"],
                    "trial_fee_cents": data["trial_fee_cents"],
                    "trial_hours_cap": data["trial_hours_cap"],
                    "trial_due_days": data["trial_due_days"],
                    "deliverables": data["deliverables"],
                    "definition_of_done": data["definition_of_done"],
                    "brief_redacted": data["brief_redacted"],
                    "brief_full": data["brief_full"],
                    "nda_required": data["nda_required"],
                    "requires_field_verification": data["requires_field_verification"],
                    "status": Gig.Status.LIVE,
                    "published_at": timezone.now(),
                },
            )
            if was_created:
                created += 1
            # Refresh rubric from category template
            gig.rubric.all().delete()
            templates = category.rubric_templates or [
                {"label": "Quality", "description": "Meets definition of done"}
            ]
            RubricCriterion.objects.bulk_create(
                [
                    RubricCriterion(
                        gig=gig,
                        label=tmpl["label"],
                        description=tmpl.get("description", ""),
                    )
                    for tmpl in templates
                ]
            )

        self.stdout.write(
            self.style.SUCCESS(
                f"Sample gigs ready. Created {created}, total gigs now {Gig.objects.count()}."
            )
        )
