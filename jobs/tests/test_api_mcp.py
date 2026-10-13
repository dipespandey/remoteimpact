import json

from django.core.cache import cache
from django.test import TestCase, override_settings
from django.utils import timezone

from jobs.models import ApiKey, Category, Job, Organization


class ApiBase(TestCase):
    def setUp(self):
        cache.clear()
        from allauth.socialaccount.models import SocialApp
        from django.contrib.sites.models import Site
        site = Site.objects.get_current()
        for provider, provider_id in (("google", ""), ("openid_connect", "linkedin")):
            app = SocialApp.objects.create(provider=provider, provider_id=provider_id, name=provider, client_id="x", secret="y")
            app.sites.add(site)
        self.org = Organization.objects.create(name="Example Org", slug="example-org", website="https://example.org/")
        self.cat = Category.objects.create(name="Climate & Environment", slug="climate-environment")
        now = timezone.now()
        self.posted = Job.objects.create(
            title="Program Lead", slug="program-lead", organization=self.org, category=self.cat,
            description="<p>Lead our climate programs across three regions. " * 20 + "</p>", requirements="Five years of experience.",
            location="Remote (US)", job_type="full-time", experience_level="senior", source=Job.Source.MANUAL,
            is_paid=True, is_active=True, posted_at=now, expires_at=now + timezone.timedelta(days=30),
            salary_min=90000, salary_max=120000, salary_currency="USD", skills=["python", "grants"],
            must_haves=[{"label": "Grant management", "quote": "managed grant-funded programs"}],
        )
        self.feed_job = Job.objects.create(
            title="Data Analyst", slug="data-analyst-example", organization=self.org, category=self.cat,
            description="Analyse climate data. " * 30, location="Remote, Worldwide", job_type="full-time",
            source="feed", is_active=True, posted_at=now, last_seen_at=now, application_url="https://example.org/jobs/1",
            raw_data={"source_verification": {"status": "open", "method": "ats_api", "checked_at": now.isoformat()}},
        )
        self.closed = Job.objects.create(title="Old Role", slug="old-role", organization=self.org, category=self.cat,
                                         description="x", location="Remote", job_type="full-time", source="feed",
                                         is_active=False, posted_at=now - timezone.timedelta(days=200))


class RestApiTests(ApiBase):
    def test_list_is_open_without_a_key_and_carries_verification_and_eligibility(self):
        response = self.client.get("/api/v1/jobs/?area=climate-environment")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Access-Control-Allow-Origin"], "*")
        self.assertEqual(response["X-RateLimit-Tier"], "anonymous")
        data = response.json()
        self.assertEqual(data["total"], 2)
        by_slug = {j["slug"]: j for j in data["jobs"]}
        self.assertEqual(by_slug["program-lead"]["eligible_countries"], ["United States of America"])
        self.assertEqual(by_slug["program-lead"]["source"], "employer_posted")
        self.assertEqual(by_slug["data-analyst-example"]["remote_scope"], "worldwide")
        self.assertEqual(by_slug["data-analyst-example"]["verification"]["method"], "employer_job_board")
        self.assertTrue(by_slug["data-analyst-example"]["apply_url"].endswith("/go/"))
        # Free tier: excerpt only, no enriched fields
        self.assertNotIn("skills", by_slug["program-lead"])
        self.assertLess(len(by_slug["program-lead"]["description_excerpt"]), 320)

    def test_commercial_key_gets_enriched_fields_and_full_text_only_for_employer_posts(self):
        raw, _ = ApiKey.issue(name="Acme", email="dev@acme.example", tier=ApiKey.Tier.COMMERCIAL)
        response = self.client.get("/api/v1/jobs/", HTTP_AUTHORIZATION=f"Bearer {raw}")
        self.assertEqual(response["X-RateLimit-Tier"], "commercial")
        by_slug = {j["slug"]: j for j in response.json()["jobs"]}
        self.assertEqual(by_slug["program-lead"]["must_haves"][0]["label"], "Grant management")
        self.assertIn("Lead our climate programs", by_slug["program-lead"]["description"])
        self.assertIsNone(by_slug["data-analyst-example"]["description"])

    def test_inactive_or_unknown_keys_fall_back_to_anonymous(self):
        raw, key = ApiKey.issue(name="x", email="x@example.com", tier=ApiKey.Tier.COMMERCIAL, is_active=False)
        self.assertEqual(self.client.get("/api/v1/jobs/", HTTP_AUTHORIZATION=f"Bearer {raw}")["X-RateLimit-Tier"], "anonymous")
        self.assertEqual(self.client.get("/api/v1/jobs/", HTTP_X_API_KEY="ri_nonsense")["X-RateLimit-Tier"], "anonymous")

    def test_detail_closed_and_filters(self):
        self.assertEqual(self.client.get("/api/v1/jobs/program-lead/").json()["job"]["title"], "Program Lead")
        closed = self.client.get("/api/v1/jobs/old-role/")
        self.assertEqual((closed.status_code, closed.json()["status"]), (404, "closed"))
        self.assertEqual(self.client.get("/api/v1/jobs/?source=employer_posted").json()["total"], 1)
        self.assertEqual(self.client.get("/api/v1/jobs/?remote_scope=worldwide").json()["total"], 1)
        self.assertEqual(self.client.get("/api/v1/jobs/?posted_since=bogus").status_code, 400)
        self.assertEqual(self.client.get("/api/v1/jobs/?limit=999").json()["limit"], 10)
        beyond = self.client.get("/api/v1/jobs/?page=4")
        self.assertEqual((beyond.status_code, beyond.json()["error"]), (403, "tier_limit"))
        raw, _ = ApiKey.issue(name="Acme", email="dev2@acme.example", tier=ApiKey.Tier.COMMERCIAL)
        self.assertEqual(self.client.get("/api/v1/jobs/?page=4", HTTP_AUTHORIZATION=f"Bearer {raw}").status_code, 200)
        self.assertEqual(self.client.get("/api/v1/areas/").json()["areas"][0]["open_roles"], 2)
        self.assertEqual(self.client.get("/api/v1/organizations/example-org/").json()["organization"]["open_roles"], 2)
        self.assertEqual(self.client.get("/api/v1/openapi.json").json()["openapi"], "3.1.0")

    def test_rate_limit_per_minute(self):
        from jobs.api import auth
        original = auth.TIERS["anonymous"]["per_minute"]
        auth.TIERS["anonymous"]["per_minute"] = 2
        try:
            self.client.get("/api/v1/areas/")
            self.client.get("/api/v1/areas/")
            limited = self.client.get("/api/v1/areas/")
            self.assertEqual(limited.status_code, 429)
            self.assertIn("Retry-After", limited)
        finally:
            auth.TIERS["anonymous"]["per_minute"] = original


class McpTests(ApiBase):
    def rpc(self, method, params=None, id_=1, **extra):
        payload = {"jsonrpc": "2.0", "method": method, "params": params or {}}
        if id_ is not None:
            payload["id"] = id_
        return self.client.post("/mcp/", json.dumps(payload), content_type="application/json", **extra)

    def test_initialize_and_tools_list(self):
        init = self.rpc("initialize", {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t", "version": "1"}})
        self.assertEqual(init.status_code, 200)
        result = init.json()["result"]
        self.assertEqual(result["protocolVersion"], "2025-06-18")
        self.assertEqual(result["serverInfo"]["name"], "remote-impact-jobs")
        self.assertEqual(self.rpc("notifications/initialized", id_=None).status_code, 202)
        tools = self.rpc("tools/list").json()["result"]["tools"]
        self.assertEqual({t["name"] for t in tools}, {"search_jobs", "get_job", "list_areas", "get_organization"})

    def test_search_and_get_tools(self):
        response = self.rpc("tools/call", {"name": "search_jobs", "arguments": {"area": "climate-environment", "level": "senior"}})
        result = response.json()["result"]
        self.assertFalse(result["isError"])
        self.assertEqual(result["structuredContent"]["total"], 1)
        self.assertIn("Program Lead at Example Org", result["content"][0]["text"])
        self.assertIn("/jobs/program-lead/", result["content"][0]["text"])
        job = self.rpc("tools/call", {"name": "get_job", "arguments": {"id": "program-lead"}}).json()["result"]
        self.assertEqual(job["structuredContent"]["job"]["slug"], "program-lead")
        areas = self.rpc("tools/call", {"name": "list_areas", "arguments": {}}).json()["result"]
        self.assertEqual(areas["structuredContent"]["areas"][0]["slug"], "climate-environment")

    def test_protocol_errors(self):
        self.assertEqual(self.client.post("/mcp/", "not json", content_type="application/json").status_code, 400)
        self.assertEqual(self.rpc("nope").json()["error"]["code"], -32601)
        self.assertEqual(self.rpc("tools/call", {"name": "missing", "arguments": {}}).json()["error"]["code"], -32602)
        self.assertEqual(self.client.get("/mcp/").status_code, 405)
        self.assertEqual(self.client.options("/mcp/").status_code, 204)


@override_settings(EMAIL_PROVIDER="none")
class DevelopersPageTests(ApiBase):
    def test_page_renders_and_free_key_is_issued_once(self):
        self.assertContains(self.client.get("/developers/"), "MCP server")
        response = self.client.post("/developers/", {"kind": "free", "name": "Ana", "email": "ana@example.com"}, follow=True)
        self.assertContains(response, "ri_")
        key = ApiKey.objects.get(email="ana@example.com")
        self.assertTrue(key.is_active)
        self.assertEqual(key.tier, "free")
        self.assertNotContains(self.client.get("/developers/"), "ri_" + key.prefix[3:])

    def test_commercial_request_is_inactive_until_approved_and_bots_are_ignored(self):
        self.client.post("/developers/", {"kind": "commercial", "name": "Bo", "email": "bo@acme.example", "organization": "Acme", "use_case": "job agent"})
        key = ApiKey.objects.get(email="bo@acme.example")
        self.assertEqual((key.tier, key.is_active), ("commercial", False))
        self.client.post("/developers/", {"kind": "free", "name": "Bot", "email": "bot@example.com", "website": "spam"})
        self.assertFalse(ApiKey.objects.filter(email="bot@example.com").exists())


class UsageAndCardTests(ApiBase):
    def test_calls_are_counted_by_surface_endpoint_and_client(self):
        from jobs.models import ApiUsage
        self.client.get("/api/v1/areas/", HTTP_USER_AGENT="curl/8.7")
        self.client.post("/mcp/", json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                                              "params": {"name": "list_areas", "arguments": {}}}), content_type="application/json")
        self.assertEqual(ApiUsage.objects.get(surface="rest", endpoint="areas").client, "curl")
        self.assertEqual(ApiUsage.objects.get(surface="mcp", endpoint="list_areas").count, 1)

    def test_server_card_and_tool_annotations(self):
        card = self.client.get("/.well-known/mcp/server-card.json").json()
        self.assertTrue(card["serverInfo"]["description"])
        self.assertTrue(all(t["annotations"]["readOnlyHint"] and t["outputSchema"] for t in card["tools"]))


class AdminApiUsageTests(ApiBase):
    def test_staff_see_usage_page_others_do_not(self):
        from django.contrib.auth import get_user_model
        self.client.get("/api/v1/areas/")
        self.assertEqual(self.client.get("/admin/api-usage/").status_code, 302)
        staff = get_user_model().objects.create_user("boss", "boss@example.com", "pw", is_staff=True, is_superuser=True)
        self.client.force_login(staff)
        response = self.client.get("/admin/api-usage/")
        self.assertContains(response, "Jobs API and MCP")
        self.assertContains(response, "areas")
