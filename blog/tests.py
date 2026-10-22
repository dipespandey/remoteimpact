from datetime import timedelta

from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from django.contrib.sites.models import Site
from allauth.socialaccount.models import SocialApp

from .models import BlogPost


@override_settings(
    SECURE_SSL_REDIRECT=False, SITE_URL="https://remoteimpact.org",
)
class BlogEntryTests(TestCase):
    def setUp(self):
        for provider, provider_id in (("google", ""), ("openid_connect", "linkedin")):
            app = SocialApp.objects.create(
                provider=provider, provider_id=provider_id, name=provider,
                client_id="test", secret="test",
                settings={"server_url": "https://www.linkedin.com/oauth"} if provider_id else {},
            )
            app.sites.add(Site.objects.get_current())

    def test_cached_legacy_redirect_destination_serves_without_another_redirect(self):
        published = BlogPost.objects.create(
            title="Published guide", slug="published-guide", excerpt="A guide",
            body="<p>Guide</p>", status=BlogPost.Status.PUBLISHED,
        )
        BlogPost.objects.create(title="Private draft", slug="draft", body="Draft")
        BlogPost.objects.create(
            title="Future guide", slug="future", body="Future",
            status=BlogPost.Status.PUBLISHED,
            published_at=timezone.now() + timedelta(days=1),
        )
        for path in ("/blog", "/blog/"):
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 200)
                self.assertFalse(response.has_header("Location"))
                self.assertContains(response, published.title)
                self.assertNotContains(response, "Private draft")
                self.assertNotContains(response, "Future guide")
                self.assertContains(response, '<link rel="canonical" href="https://remoteimpact.org/blog/">')
                self.assertEqual(self.client.head(path).status_code, 200)
        self.assertEqual(reverse("blog:post_list"), "/blog/")
        self.assertEqual(self.client.get(published.get_absolute_url()).status_code, 200)
