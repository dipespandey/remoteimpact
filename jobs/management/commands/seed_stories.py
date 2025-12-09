from django.core.management.base import BaseCommand
from django.utils import timezone
from jobs.models import Story, Category, Organization


class Command(BaseCommand):
    help = 'Seed initial impact stories'

    SAMPLE_STORIES = [
        {
            "author_name": "Maria Chen",
            "author_title": "Climate Data Analyst",
            "org_name": "Ocean Conservancy",
            "content": "My ML model identified 3 illegal fishing zones last week. Coast guard intercepted 2 vessels. 47 tons of fish saved from poaching.",
            "categories": ["environment"],
            "skills": ["data analysis", "machine learning", "python"],
        },
        {
            "author_name": "James Okonkwo",
            "author_title": "Program Manager",
            "org_name": "GiveDirectly",
            "content": "This month we delivered $450,000 directly to 900 families in rural Kenya. Sarah used her transfer to start a vegetable business that now employs 4 people.",
            "categories": ["humanitarian"],
            "skills": ["program management", "logistics", "operations"],
        },
        {
            "author_name": "Alex Rivera",
            "author_title": "Software Engineer",
            "org_name": "Code for America",
            "content": "Built a SNAP eligibility screener. 12,000 people checked eligibility in the first month. Time dropped from 2 hours to 5 minutes.",
            "categories": ["technology"],
            "skills": ["python", "django", "ux design"],
        },
        {
            "author_name": "Dr. Amara Diallo",
            "author_title": "Epidemiologist",
            "org_name": "Partners in Health",
            "content": "Our TB screening program identified 340 cases that would have gone undetected. Early treatment means 95%+ will fully recover.",
            "categories": ["healthcare"],
            "skills": ["epidemiology", "data analysis", "public health"],
        },
        {
            "author_name": "Sophie Laurent",
            "author_title": "Conservation Biologist",
            "org_name": "WWF",
            "content": "Our rewilding project released 12 European bison into protected forest. First wild births in the region in 250 years. 3 calves born this spring.",
            "categories": ["environment"],
            "skills": ["conservation biology", "project management", "ecology"],
        },
        {
            "author_name": "Raj Patel",
            "author_title": "Education Technology Lead",
            "org_name": "Room to Read",
            "content": "Our literacy app reached 50,000 students in rural India. Reading scores improved 40% in 6 months. Teachers report kids asking for extra reading time.",
            "categories": ["education"],
            "skills": ["product management", "edtech", "mobile development"],
        },
    ]

    def handle(self, *args, **options):
        created_count = 0

        for story_data in self.SAMPLE_STORIES:
            org, _ = Organization.objects.get_or_create(
                name=story_data["org_name"],
                defaults={
                    "slug": story_data["org_name"].lower().replace(" ", "-").replace(",", "")
                }
            )

            story, created = Story.objects.get_or_create(
                author_name=story_data["author_name"],
                organization=org,
                defaults={
                    "author_title": story_data["author_title"],
                    "content_raw": story_data["content"],
                    "content": story_data["content"],
                    "skills": story_data["skills"],
                    "status": "published",
                    "published_at": timezone.now(),
                }
            )

            if created:
                for cat_slug in story_data["categories"]:
                    try:
                        cat = Category.objects.get(slug=cat_slug)
                        story.categories.add(cat)
                    except Category.DoesNotExist:
                        self.stdout.write(
                            self.style.WARNING(f"Category not found: {cat_slug}")
                        )

                created_count += 1
                self.stdout.write(
                    self.style.SUCCESS(f"✓ Created: {story.author_name}")
                )
            else:
                self.stdout.write(f"- Already exists: {story.author_name}")

        self.stdout.write(
            self.style.SUCCESS(f"\nDone! Created {created_count} new stories.")
        )
