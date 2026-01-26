"""
Data migration to fix duplicate categories in the database.

Fixes:
1. Replace one duplicate "Research and Education" with "Gender Equality and Social Inclusion (GESI)"
2. Remove duplicate "Policy and Advocacy"
3. Remove duplicate "Other" domain
"""

from django.db import migrations
from django.utils.text import slugify


def fix_duplicate_categories(apps, schema_editor):
    Category = apps.get_model('jobs', 'Category')

    # 1. Handle "Research and Education" duplicates
    # Find all categories with names containing "Research" and "Education"
    research_education_cats = Category.objects.filter(
        name__icontains='Research'
    ).filter(
        name__icontains='Education'
    )

    if research_education_cats.count() > 1:
        # Keep the first one, rename the second one to GESI
        cats_list = list(research_education_cats)
        gesi_cat = cats_list[1]  # Take the second duplicate
        gesi_cat.name = "Gender Equality and Social Inclusion (GESI)"
        gesi_cat.slug = "gender-equality-social-inclusion"
        gesi_cat.icon = "⚧️"
        gesi_cat.save()
    elif research_education_cats.count() == 0:
        # If no Research and Education exists, check if GESI is missing and add it
        if not Category.objects.filter(slug="gender-equality-social-inclusion").exists():
            Category.objects.create(
                name="Gender Equality and Social Inclusion (GESI)",
                slug="gender-equality-social-inclusion",
                icon="⚧️",
                description="Gender equality, social inclusion, and equity initiatives"
            )

    # 2. Handle "Policy and Advocacy" duplicates
    policy_cats = Category.objects.filter(name__icontains='Policy').filter(name__icontains='Advocacy')

    if policy_cats.count() > 1:
        # Keep the first one, delete the rest
        cats_list = list(policy_cats)
        for cat in cats_list[1:]:
            # Before deleting, move any related objects to the first category
            first_cat = cats_list[0]
            # Update jobs
            cat.jobs.update(category=first_cat)
            # Update stories
            for story in cat.stories.all():
                story.categories.add(first_cat)
                story.categories.remove(cat)
            # Update sprints
            for sprint in cat.sprints.all():
                sprint.categories.add(first_cat)
                sprint.categories.remove(cat)
            # Update seeker profiles
            for profile in cat.interested_seekers.all():
                profile.impact_areas.add(first_cat)
                profile.impact_areas.remove(cat)
            cat.delete()

    # 3. Handle "Other" duplicates
    other_cats = Category.objects.filter(name__icontains='Other')

    if other_cats.count() > 1:
        # Keep the first one, delete the rest
        cats_list = list(other_cats)
        for cat in cats_list[1:]:
            # Before deleting, move any related objects to the first category
            first_cat = cats_list[0]
            # Update jobs
            cat.jobs.update(category=first_cat)
            # Update stories
            for story in cat.stories.all():
                story.categories.add(first_cat)
                story.categories.remove(cat)
            # Update sprints
            for sprint in cat.sprints.all():
                sprint.categories.add(first_cat)
                sprint.categories.remove(cat)
            # Update seeker profiles
            for profile in cat.interested_seekers.all():
                profile.impact_areas.add(first_cat)
                profile.impact_areas.remove(cat)
            cat.delete()


def reverse_migration(apps, schema_editor):
    # This migration is not easily reversible
    # The GESI category would need to be renamed back, and deleted categories restored
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('jobs', '0023_add_experience_education_country'),
    ]

    operations = [
        migrations.RunPython(fix_duplicate_categories, reverse_migration),
    ]
