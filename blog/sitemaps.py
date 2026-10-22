from django.contrib.sitemaps import Sitemap
from .models import BlogPost
from django.utils import timezone


class BlogSitemap(Sitemap):
    changefreq = "weekly"
    priority = 0.7

    def items(self):
        return BlogPost.objects.filter(status=BlogPost.Status.PUBLISHED, published_at__lte=timezone.now())

    def lastmod(self, obj):
        return obj.updated_at

    def location(self, obj):
        return obj.get_absolute_url()
