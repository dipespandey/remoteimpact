import os
from urllib.parse import parse_qs, urlsplit
from django.test import TestCase
from django.urls import reverse
from django.contrib.sites.models import Site
from allauth.socialaccount.models import SocialApp


class PublicPricingTests(TestCase):
    def setUp(self):
        site = Site.objects.get_current()
        for provider, provider_id in (("google", ""), ("openid_connect", "linkedin")):
            app = SocialApp.objects.create(
                provider=provider,
                provider_id=provider_id,
                name=(provider_id or provider).title(),
                client_id=f"{provider_id or provider}-client",
                secret=f"{provider_id or provider}-secret",
                settings={"server_url": "https://www.linkedin.com/oauth"} if provider_id else {},
            )
            app.sites.add(site)

    def test_pricing_page_is_public_and_describes_free_and_pro_offers(self):
        response = self.client.get(reverse("jobs:public_pricing"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Remote Impact Pro")
        self.assertContains(response, "$10")
        self.assertContains(response, "one AI Job Scout")
        self.assertContains(response, "pricing_free_scout")
        self.assertContains(response, 'name="robots" content="index, follow"')
        self.assertContains(response, 'rel="canonical"')

    def test_free_scout_collects_a_brief_before_searching(self):
        from playwright.sync_api import sync_playwright, expect

        html = self.client.get(reverse("jobs:public_pricing")).content.decode()
        intent = "Remote Python engineering roles at climate nonprofits in Europe."
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(
                headless=True,
                executable_path=os.environ.get("PLAYWRIGHT_CHROMIUM_EXECUTABLE", "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
            )
            for width in (390, 1280):
                page = browser.new_page(viewport={"width": width, "height": 844})
                searches = []

                def serve(route):
                    if urlsplit(route.request.url).path == reverse("jobs:public_pricing"):
                        route.fulfill(body=html, content_type="text/html")
                    elif urlsplit(route.request.url).path == reverse("jobs:job_scout_preview"):
                        searches.append((route.request.url, route.request.method, route.request.post_data))
                        route.fulfill(body="Submitted brief", content_type="text/html")
                    else:
                        route.abort()

                page.route("**/*", serve)
                page.goto("http://remoteimpact.test/pricing/")
                launcher = page.get_by_role("button", name="Try the free Scout", exact=True)
                dialog = page.get_by_role("dialog")
                field = page.get_by_label("Your job brief", exact=True)
                expect(dialog).not_to_be_visible()
                launcher.click()
                expect(dialog).to_be_visible()
                expect(field).to_be_empty()
                expect(field).to_be_focused()
                self.assertEqual(searches, [])
                page.get_by_role("button", name="Preview my best matches", exact=True).click()
                expect(dialog).to_be_visible()
                self.assertEqual(searches, [])
                page.keyboard.press("Escape")
                expect(dialog).not_to_be_visible()
                expect(launcher).to_be_focused()
                launcher.click()
                field.fill(intent)
                page.get_by_role("button", name="Close job brief editor").click()
                launcher.click()
                expect(field).to_have_value(intent)
                page.get_by_label("Email (optional)", exact=True).fill("popup@example.org")
                page.get_by_label("Contact me about this search.", exact=True).check()
                page.get_by_role("button", name="Preview my best matches", exact=True).click()
                page.wait_for_url("**/alerts/preview/")
                self.assertEqual(len(searches), 1)
                target, method, body = searches[0]
                self.assertEqual(method, "POST")
                self.assertEqual(urlsplit(target).query, "")
                posted = parse_qs(body)
                self.assertEqual(posted["intent"], [intent])
                self.assertEqual(posted["email"], ["popup@example.org"])
                self.assertEqual(posted["source"], ["pricing_free"])
                page.close()
            browser.close()
