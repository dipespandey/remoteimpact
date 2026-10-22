from datetime import timedelta
from io import StringIO
from unittest.mock import patch
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase, TestCase
from django.utils import timezone
from blog.models import BlogPost
from blog.sitemaps import BlogSitemap
from blog.views import BlogDetailView, BlogListView
from growth.editorial import approve_revision, publish_revision, sanitize_html
from growth.models import ArticleRevision, EditorialOpportunity
from growth.seed import seed_editorial


class SanitizerTests(SimpleTestCase):
    def test_scripts_attributes_embeds_and_unsafe_links_are_removed(self):
        result = sanitize_html('<script>bad()</script><svg><script>bad2()</script></svg><p onclick="bad()">Useful</p><a href="javascript:bad()">bad</a><a href="//evil.test">bad</a><iframe src="https://evil.test"></iframe><a href="https://example.org" onmouseover="bad()">source</a>')
        for value in ("script", "bad()", "bad2()", "onclick", "onmouseover", "javascript:", "//evil", "iframe"):
            self.assertNotIn(value, result)
        self.assertIn('<p>Useful</p>', result)
        self.assertIn('href="https://example.org"', result)

    def test_sponsored_links_are_qualified(self):
        self.assertIn('rel="sponsored nofollow noopener"', sanitize_html('<a href="https://example.org">Source</a>', sponsored=True))


class EditorialTests(TestCase):
    def setUp(self):
        self.editor = get_user_model().objects.create_superuser("editor", "editor@example.test", "test-password")
        self.post = BlogPost.objects.create(title="Draft", slug="draft", excerpt="Summary", body="")
        self.opportunity = EditorialOpportunity.objects.create(key="evidence", title="Evidence", audience="employer",
            intent="Hiring clarity", destination_path="/employers/", decision="accepted", review_note="Reviewed existing destinations and source limits.",
            evidence=[{"url": "https://remoteimpact.org/employers/", "captured_at": timezone.now().isoformat(), "note": "Current offer and limitations reviewed."}])
        self.revision = ArticleRevision.objects.create(opportunity=self.opportunity, post=self.post, title="A useful guide", excerpt="Useful summary",
            body="<h2>Actual advice</h2><p>Useful detail.</p>", publish_at=timezone.now()-timedelta(minutes=1))

    def test_approval_due_publication_and_retry_are_idempotent(self):
        with self.assertRaises(ValidationError):
            publish_revision(self.revision.pk)
        approve_revision(self.revision.pk, actor=self.editor)
        self.assertTrue(publish_revision(self.revision.pk))
        self.assertFalse(publish_revision(self.revision.pk))
        self.post.refresh_from_db()
        self.assertEqual(self.post.slug, "draft")
        self.assertEqual(self.post.status, "published")
        self.assertIn('href="/employers/"', self.post.body)
        self.revision.refresh_from_db()
        self.assertTrue(self.revision.evidence_snapshot["sources"])

    def test_future_revision_and_future_post_never_leak(self):
        self.revision.publish_at = timezone.now()+timedelta(days=1)
        self.revision.save()
        approve_revision(self.revision.pk, actor=self.editor)
        with self.assertRaises(ValidationError):
            publish_revision(self.revision.pk)
        self.post.status = "published"
        self.post.published_at = timezone.now()+timedelta(days=1)
        self.post.save()
        self.assertFalse(BlogListView().get_queryset().exists())
        self.assertFalse(BlogDetailView().get_queryset().exists())
        self.assertFalse(BlogSitemap().items().exists())

    def test_content_or_evidence_edit_requires_reapproval(self):
        approve_revision(self.revision.pk, actor=self.editor)
        self.revision.refresh_from_db()
        self.revision.body = "<p>Changed claim</p>"
        self.revision.save()
        self.assertEqual(self.revision.state, "draft")
        approve_revision(self.revision.pk, actor=self.editor)
        self.opportunity.coverage_notes = "Source changed"
        self.opportunity.save()
        with self.assertRaises(ValidationError):
            publish_revision(self.revision.pk)

    def test_no_evidence_or_unreviewed_opportunity_cannot_approve(self):
        self.opportunity.evidence = []
        self.opportunity.save()
        with self.assertRaises(ValidationError):
            approve_revision(self.revision.pk, actor=self.editor)

    def test_autopublish_is_disabled_and_never_calls_a_model(self):
        with self.assertRaises(CommandError):
            call_command("editorial_publish_due", apply=True)
        self.assertEqual(self.post.status, "draft")

    def test_seed_is_idempotent_and_only_creates_review_needed_drafts(self):
        result = seed_editorial()
        self.assertEqual(result, {"opportunities_created": 10, "drafts_created": 3})
        self.assertEqual(seed_editorial(), {"opportunities_created": 0, "drafts_created": 0})
        self.assertFalse(BlogPost.objects.filter(status="published").exists())
        self.assertEqual(ArticleRevision.objects.filter(state="draft").count(), 4)
