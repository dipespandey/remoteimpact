from django.contrib.syndication.views import Feed
from django.urls import reverse
from django.utils.html import strip_tags

from .models import Job, Category


class LatestJobsFeed(Feed):
    title = "Remote Impact - Latest Jobs"
    link = "/jobs/"
    description = "Latest remote jobs at non-profits and social enterprises"

    def items(self):
        return Job.objects.filter(is_active=True).select_related(
            "organization", "category"
        ).order_by("-posted_at")[:20]

    def item_title(self, item):
        return f"{item.title} at {item.organization.name}"

    def item_description(self, item):
        desc = strip_tags(item.description)
        if len(desc) > 500:
            desc = desc[:500] + "..."
        return desc

    def item_link(self, item):
        return reverse("jobs:job_detail", args=[item.slug])

    def item_pubdate(self, item):
        return item.posted_at

    def item_categories(self, item):
        if item.category:
            return [item.category.name]
        return []


class CategoryJobsFeed(Feed):
    description = "Latest remote jobs in this category"

    def get_object(self, request, slug):
        return Category.objects.get(slug=slug)

    def title(self, obj):
        return f"Remote Impact - {obj.name} Jobs"

    def link(self, obj):
        return f"/jobs/?category={obj.slug}"

    def items(self, obj):
        return Job.objects.filter(
            is_active=True, category=obj
        ).select_related("organization", "category").order_by("-posted_at")[:20]

    def item_title(self, item):
        return f"{item.title} at {item.organization.name}"

    def item_description(self, item):
        desc = strip_tags(item.description)
        if len(desc) > 500:
            desc = desc[:500] + "..."
        return desc

    def item_link(self, item):
        return reverse("jobs:job_detail", args=[item.slug])

    def item_pubdate(self, item):
        return item.posted_at
