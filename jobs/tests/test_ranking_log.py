import json

from django.test import TestCase

from jobs.models import RankingEvent

BROWSER = {"HTTP_USER_AGENT": "Mozilla/5.0 (Macintosh) Safari/605"}


class RankingBeaconTests(TestCase):
    def post(self, payload, **extra):
        return self.client.post("/t/rank/", json.dumps(payload), content_type="application/json", **{**BROWSER, **extra})

    def test_impression_then_click_is_linked(self):
        self.assertEqual(self.post({"t": "imp", "k": "abc123xyz9", "s": "search", "q": "policy", "ids": [5, 9, 12]}).status_code, 204)
        self.post({"t": "click", "k": "abc123xyz9", "j": 9, "p": 2})
        click = RankingEvent.objects.get(kind="click")
        self.assertEqual((click.job_id, click.position, click.surface, click.impression_key), (9, 2, "search", "abc123xyz9"))

    def test_click_on_a_job_that_was_not_shown_is_ignored(self):
        self.post({"t": "imp", "k": "abc123xyz9", "s": "search", "ids": [5]})
        self.post({"t": "click", "k": "abc123xyz9", "j": 999, "p": 1})
        self.post({"t": "click", "k": "unknownkey1", "j": 5, "p": 1})
        self.assertFalse(RankingEvent.objects.filter(kind="click").exists())

    def test_bots_and_garbage_are_ignored(self):
        self.client.post("/t/rank/", json.dumps({"t": "imp", "k": "abc123xyz9", "ids": [1]}),
                         content_type="application/json", HTTP_USER_AGENT="Googlebot/2.1")
        self.post({"t": "imp", "k": "bad key!", "ids": [1]})
        self.client.post("/t/rank/", "not json", content_type="application/json", **BROWSER)
        self.assertFalse(RankingEvent.objects.exists())
