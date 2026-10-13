from urllib.parse import parse_qs, urlsplit
from unittest.mock import patch

from django.http import HttpResponse, StreamingHttpResponse
from django.test import SimpleTestCase, RequestFactory, override_settings

from jobs.middleware import OutboundAttributionMiddleware
from jobs.services.job_stats import with_referral
from jobs.services.outbound_links import attribute_html_links, with_outbound_attribution


@override_settings(SITE_URL="https://remoteimpact.org")
class OutboundLinksTests(SimpleTestCase):
    def test_existing_query_bytes_and_fragment_survive(self):
        url = "https://employer.org/jobs?department=linkedin&token=a%2fb%20c&x=1&x=2&blank=&utm_source=Climatebase&gh_src=Climatebase#apply"
        tagged = with_referral(url)
        self.assertIn("department=linkedin&token=a%2fb%20c&x=1&x=2&blank=", tagged)
        self.assertNotIn("gh_src=", tagged)
        self.assertEqual(urlsplit(tagged).fragment, "apply")
        self.assertEqual(parse_qs(urlsplit(tagged).query)["utm_source"], ["remoteimpact"])

    def test_lever_attribution_is_replaced_once_and_idempotent(self):
        url = "https://jobs.eu.lever.co/acme/1?lever-source=Other&UTM_SOURCE=old&utm_campaign=old"
        tagged = with_referral(url)
        query = parse_qs(urlsplit(tagged).query)
        self.assertEqual(query["lever-source"], ["RemoteImpact"])
        self.assertEqual(query["utm_campaign"], ["remote_impact_jobs"])
        self.assertEqual(with_referral(tagged), tagged)

    def test_non_navigation_and_internal_urls_unchanged(self):
        for url in ["/jobs/", "#apply", "mailto:jobs@example.org", "tel:123", "javascript:alert(1)", "https://remoteimpact.org/jobs/?utm_source=partner", "https://www.remoteimpact.org/", "https://user:password@example.org/"]:
            with self.subTest(url=url):
                self.assertEqual(with_outbound_attribution(url), url)
        self.assertEqual(with_outbound_attribution("https://preview.example/jobs/", internal_hosts=("preview.example",)), "https://preview.example/jobs/")

    def test_protocol_relative_links_are_tagged(self):
        self.assertIn("utm_source=remoteimpact", with_outbound_attribution("//employer.org/careers"))

    def test_signed_payment_and_authentication_urls_are_unchanged(self):
        urls = ["https://files.example/cv?X-Amz-Signature=abc", "https://example.org/?sig=abc", "https://example.org/?Signature=abc", "https://checkout.stripe.com/c/pay/abc", "https://billing.stripe.com/p/session/abc", "https://accounts.google.com/o/oauth2/auth?state=abc", "https://www.linkedin.com/oauth/v2/authorization?state=abc", "https://example.org/sso/start?state=abc"]
        for url in urls:
            with self.subTest(url=url):
                self.assertEqual(with_outbound_attribution(url), url)

    def test_html_only_changes_real_anchor_hrefs(self):
        html = '''<!doctype html><script>const html = '<a href="https://script.example/">';</script><!-- <a href="https://comment.example/"> --><img src="https://image.example/"><form action="https://form.example/"><a title='href="https://title.example/"' data-href="https://data.example/" href='https://employer.org/?a=1&amp;b=2'>Go &amp; apply</a></form>'''
        updated = attribute_html_links(html)
        self.assertIn('utm_source=remoteimpact', updated)
        self.assertIn('a=1&amp;b=2&amp;utm_source=', updated)
        self.assertEqual(updated[:updated.index('<a title')], html[:html.index('<a title')])
        self.assertIn('data-href="https://data.example/"', updated)
        self.assertIn('title=\'href="https://title.example/"\'', updated)
        self.assertEqual(attribute_html_links(updated), updated)

    def test_html_handles_unquoted_multiline_hrefs_and_downloads(self):
        html = '<p>first\n</p><A\n href=https://example.org/ >Go</A><a download href="https://files.example/cv.pdf">CV</a>'
        updated = attribute_html_links(html)
        self.assertIn('href="https://example.org/?utm_source=remoteimpact', updated)
        self.assertIn('<a download href="https://files.example/cv.pdf">', updated)

    def test_response_headers_match_changed_body(self):
        response = HttpResponse('<a href="https://employer.org/">Go</a>')
        response['Content-Length'] = len(response.content)
        response['ETag'] = '"old"'
        result = OutboundAttributionMiddleware(lambda request: response)(RequestFactory().get('/jobs/'))
        self.assertIn(b'utm_source=remoteimpact', result.content)
        self.assertEqual(int(result['Content-Length']), len(result.content))
        self.assertFalse(result.has_header('ETag'))

    def test_htmx_fragments_are_covered(self):
        result = OutboundAttributionMiddleware(lambda request: HttpResponse('<a href="https://org.example/">Org</a>'))(RequestFactory().get('/jobs/', HTTP_HX_REQUEST='true'))
        self.assertIn(b'utm_source=remoteimpact', result.content)

    def test_json_streams_admin_and_auth_responses_are_not_rewritten(self):
        for path, response in [('/admin/', HttpResponse('<a href="https://example.org/">')), ('/accounts/login/', HttpResponse('<a href="https://accounts.google.com/oauth/authorize">')), ('/api/', HttpResponse('{"url":"https://example.org/"}', content_type='application/json')), ('/stream/', StreamingHttpResponse(iter([b'hello'])) )]:
            original = None if response.streaming else response.content
            result = OutboundAttributionMiddleware(lambda request: response)(RequestFactory().get(path))
            if not result.streaming:
                self.assertEqual(result.content, original)

    @override_settings(EMAIL_PROVIDER='resend')
    def test_email_outbound_links_are_tagged_without_touching_unsubscribe(self):
        from jobs.services.email_service import EmailService
        service = EmailService()
        html = '<a href="https://external.org/">Resource</a><a href="https://remoteimpact.org/unsubscribe/token/">Unsubscribe</a>'
        with patch.object(service, '_send_resend', return_value=True) as sender:
            service.send_email('qa@example.org', 'Test', html)
        sent = sender.call_args.args[2]
        self.assertIn('utm_medium=email', sent)
        self.assertIn('href="https://remoteimpact.org/unsubscribe/token/"', sent)
