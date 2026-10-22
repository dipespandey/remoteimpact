from django.http import HttpResponse
from django.test import RequestFactory, SimpleTestCase

from jobs.middleware import MarkdownResponseMiddleware, _accepts_markdown


class MarkdownResponseMiddlewareTests(SimpleTestCase):
    def setUp(self):
        self.factory = RequestFactory()

    def middleware(self, response):
        return MarkdownResponseMiddleware(lambda request: response)

    def test_explicit_markdown_accept_header_gets_a_readable_public_variant(self):
        response = HttpResponse(
            "<html><head><title>Remote Impact</title></head><body><nav>Skip</nav>"
            "<main><h1>Find impact work</h1><p>Browse <a href=\"/jobs/\">jobs</a>.</p>"
            "<ul><li>Climate</li><li>Health</li></ul><form><input value=\"hidden\"></form>"
            "</main></body></html>",
            content_type="text/html; charset=utf-8",
        )
        result = self.middleware(response)(self.factory.get("/", HTTP_ACCEPT="text/markdown"))

        body = result.content.decode()
        self.assertEqual(result["Content-Type"], "text/markdown; charset=utf-8")
        self.assertIn('title: "Remote Impact"', body)
        self.assertIn("source: http://testserver/", body)
        self.assertIn("# Find impact work", body)
        self.assertIn("[jobs](http://testserver/jobs/)", body)
        self.assertIn("- Climate", body)
        self.assertNotIn("Skip", body)
        self.assertNotIn("hidden", body)
        self.assertIn("Accept", result["Vary"])
        self.assertEqual(result["Content-Length"], str(len(result.content)))

    def test_wildcard_or_zero_quality_accept_does_not_change_html(self):
        for header in ("*/*", "text/markdown;q=0", "text/html, text/markdown;q=0"):
            with self.subTest(header=header):
                response = HttpResponse("<main><h1>HTML</h1></main>", content_type="text/html")
                result = self.middleware(response)(self.factory.get("/", HTTP_ACCEPT=header))
                self.assertTrue(result["Content-Type"].startswith("text/html"))
                self.assertIn(b"<main>", result.content)

    def test_private_path_never_negotiates_markdown(self):
        response = HttpResponse("<main><h1>Account</h1></main>", content_type="text/html")
        result = self.middleware(response)(self.factory.get("/accounts/login/", HTTP_ACCEPT="text/markdown"))
        self.assertTrue(result["Content-Type"].startswith("text/html"))

    def test_public_404_gets_a_markdown_recovery_document(self):
        response = HttpResponse(
            "<html><head><title>Page Not Found</title></head><body><main>"
            "<h1>Page not found</h1><p>Try <a href='/jobs/'>current jobs</a>.</p>"
            "</main></body></html>",
            status=404,
            content_type="text/html; charset=utf-8",
        )
        result = self.middleware(response)(self.factory.get("/missing/", HTTP_ACCEPT="text/markdown"))

        self.assertEqual(result.status_code, 404)
        self.assertEqual(result["Content-Type"], "text/markdown; charset=utf-8")
        self.assertIn("# Page not found", result.content.decode())
        self.assertIn("[current jobs](http://testserver/jobs/)", result.content.decode())

    def test_accept_parser_honors_an_explicit_positive_quality(self):
        self.assertTrue(_accepts_markdown("text/html, text/markdown;q=0.5"))
        self.assertFalse(_accepts_markdown("text/markdown;q=bogus"))

    def test_tab_panels_are_labeled_in_markdown_even_when_hidden_in_html(self):
        response = HttpResponse(
            "<main><button id=\"tab-climate\" role=\"tab\">Climate roles</button>"
            "<div role=\"tabpanel\" aria-labelledby=\"tab-climate\" hidden>"
            "<p>Current climate openings.</p></div></main>",
            content_type="text/html; charset=utf-8",
        )
        result = self.middleware(response)(self.factory.get("/", HTTP_ACCEPT="text/markdown"))

        self.assertIn("## Climate roles", result.content.decode())
        self.assertIn("Current climate openings.", result.content.decode())
