from django.contrib.sitemaps import Sitemap
from django.urls import reverse
from .models import Job, Category


class JobSitemap(Sitemap):
    changefreq = "daily"
    priority = 0.8
    limit = 1000  # Paginate: max 1000 URLs per sitemap page

    def items(self):
        return Job.objects.filter(is_active=True).values_list('slug', flat=True)

    def location(self, slug):
        return reverse('jobs:job_detail', args=[slug])


class CategorySitemap(Sitemap):
    changefreq = "weekly"
    priority = 0.6

    def items(self):
        return Category.objects.all()

    def location(self, obj):
        return reverse('jobs:job_list') + f'?category={obj.slug}'


class StaticSitemap(Sitemap):
    changefreq = "monthly"
    priority = 1.0

    def items(self):
        return ['jobs:home', 'jobs:job_list']

    def location(self, item):
        return reverse(item)
