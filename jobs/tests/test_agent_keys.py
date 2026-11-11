import json
from datetime import timedelta
from unittest import mock

from django.test import override_settings
from django.utils import timezone

from jobs.models import ApiKey, FeatureTrial, Job
from jobs.tests.test_api_mcp import ApiBase


@override_settings(SECURE_SSL_REDIRECT=False)
class AgentKeyRequestTests(ApiBase):
    def setUp(self):
        super().setUp()
        self.send = mock.patch("jobs.services.email_service.email_service.send_email", return_value=True).start()
        self.addCleanup(mock.patch.stopall)

    def rpc(self, method, params=None, **extra):
        payload = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}}
        return self.client.post("/mcp/", json.dumps(payload), content_type="application/json", **extra)

    def ask(self, email="reader@example.org", **extra):
        return self.rpc("tools/call", {"name": "request_api_key",
                                       "arguments": {"email": email, "use_case": "job search for myself", **extra}})

    def test_an_agent_can_get_its_user_a_key_by_email_even_while_rate_limited(self):
        from jobs.api import auth
        with mock.patch.dict(auth.TIERS["anonymous"], per_minute=1):
            self.rpc("tools/call", {"name": "list_areas"})
            limited = self.rpc("tools/call", {"name": "list_areas"})
            self.assertEqual(limited.status_code, 429)
            self.assertIn("request_api_key", limited.json()["error"]["message"])
            result = self.ask().json()["result"]
        self.assertFalse(result["isError"])
        key = ApiKey.objects.get(email="reader@example.org")
        self.assertNotIn(key.prefix, result["content"][0]["text"])
        self.assertEqual((key.tier, key.is_active), ("free", True))
        self.assertIn("[mcp] use: job search for myself", key.use_case)
        self.assertEqual(self.send.call_args.kwargs["to"], "reader@example.org")

    def test_one_key_per_address_per_day_and_few_per_connection(self):
        self.ask(); self.ask()
        self.assertEqual(ApiKey.objects.filter(email="reader@example.org").count(), 1)
        self.ask("two@example.org"); self.ask("three@example.org")
        refused = self.ask("four@example.org").json()["result"]
        self.assertTrue(refused["isError"])
        self.assertFalse(ApiKey.objects.filter(email="four@example.org").exists())

    def test_rest_requests_need_a_real_address_and_never_return_the_key(self):
        bad = self.client.post("/api/v1/keys/", json.dumps({"email": "nope"}), content_type="application/json")
        self.assertEqual(bad.status_code, 400)
        good = self.client.post("/api/v1/keys/", json.dumps({"email": "dev@example.org", "use_case": "a tool"}),
                                content_type="application/json")
        self.assertEqual(good.status_code, 200)
        key = ApiKey.objects.get(email="dev@example.org", use_case__startswith="[rest]")
        self.assertNotIn(key.prefix, good.content.decode())

    def test_web_pages_cannot_make_visitors_request_keys(self):
        page = self.client.post("/api/v1/keys/", {"email": "victim@example.org"}, HTTP_ORIGIN="https://evil.example")
        self.assertEqual(page.status_code, 400)
        mcp = self.client.post("/mcp/", json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {
            "name": "request_api_key", "arguments": {"email": "victim@example.org"}}}),
            content_type="text/plain", HTTP_ORIGIN="https://evil.example")
        self.assertTrue(mcp.json()["result"]["isError"])
        self.assertFalse(ApiKey.objects.exists())
        self.send.assert_not_called()

    def test_the_email_carries_no_caller_text_and_a_failed_send_leaves_no_key(self):
        self.ask(name="CLICK evil.example/x")
        html = self.send.call_args.kwargs["html"] + self.send.call_args.kwargs["text"]
        self.assertNotIn("evil.example", html)
        self.assertIn("Hi there", html)
        self.send.return_value = False
        failed = self.ask("other@example.org").json()["result"]
        self.assertTrue(failed["isError"])
        self.assertFalse(ApiKey.objects.filter(email="other@example.org").exists())

    def test_non_object_json_is_a_bad_request(self):
        for body in ("[]", '"x"'):
            self.assertEqual(self.client.post("/api/v1/keys/", body, content_type="application/json").status_code, 400)

    def test_rest_rate_limit_points_to_the_key_request(self):
        from jobs.api import auth
        with mock.patch.dict(auth.TIERS["anonymous"], per_minute=1):
            self.client.get("/api/v1/areas/")
            limited = self.client.get("/api/v1/areas/")
        self.assertEqual(limited.status_code, 429)
        self.assertTrue(limited.json()["request_key"].endswith("/api/v1/keys/"))


class FeaturedFirstInApiTests(ApiBase):
    def test_featured_roles_lead_browse_results_paid_before_trial(self):
        now = timezone.now()
        Job.objects.filter(pk=self.posted.pk).update(posted_at=now - timedelta(days=5), featured_until=now + timedelta(days=10))
        Job.objects.filter(pk=self.feed_job.pk).update(posted_at=now - timedelta(days=9), featured_until=now + timedelta(days=3))
        FeatureTrial.objects.create(job=self.posted, started_at=now, ends_at=now + timedelta(days=10))
        newest = Job.objects.create(title="Newest Role", slug="newest-role", organization=self.org, category=self.cat,
                                    description="A real role " * 40, location="Remote, Worldwide", source="feed",
                                    is_active=True, posted_at=now, last_seen_at=now)
        slugs = [j["slug"] for j in self.client.get("/api/v1/jobs/").json()["jobs"]]
        self.assertEqual(slugs[:3], [self.feed_job.slug, self.posted.slug, newest.slug])
        flags = {j["slug"]: j["featured"] for j in self.client.get("/api/v1/jobs/").json()["jobs"]}
        self.assertTrue(flags[self.posted.slug] and flags[self.feed_job.slug])
        self.assertFalse(flags[newest.slug])
