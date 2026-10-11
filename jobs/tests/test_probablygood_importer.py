from unittest.mock import AsyncMock, Mock, patch

from django.core.management import call_command
from django.test import SimpleTestCase

from jobs.services.importers.probablygood import (
    _extract_job_from_algolia_hit,
    _parse_algolia_salary,
    fetch_job_listings,
)


class ProbablyGoodImporterTest(SimpleTestCase):
    def _hit(self, **overrides):
        hit = {
            "id": "206316",
            "objectID": "writer-and-editor-epoch-ai",
            "slug": "writer-and-editor-epoch-ai",
            "title": "Writer and Editor",
            "description": "Write and edit accessible reporting about AI safety research.",
            "url_external": "https://jobs.lever.co/epoch-ai/example",
            "published_at": "2026-09-02T22:11:32.914628+00:00",
            "closes_at": "2099-10-01T00:00:00+00:00",
            "salary_text": "$150,000 - $180,000",
            "org": {
                "name": "Epoch AI",
                "website": "https://epoch.ai/",
                "description": "AI research institute",
                "is_highlighted": True,
            },
            "locations": [
                {
                    "name": "Remote, Global",
                    "type": "REMOTE",
                    "country": "Global",
                    "is_remote": True,
                }
            ],
            "tags_workload": [{"name": "Full-Time"}],
            "tags_area": [{"name": "AI Safety & Policy"}],
            "tags_skill": [{"name": "Communications & Outreach"}],
            "tags_experience": [{"name": "Mid (5-9y)"}],
            "tags_education": [{"name": "Undergraduate Degree or Less"}],
        }
        hit.update(overrides)
        return hit

    def test_structured_remote_hit_maps_to_complete_payload(self):
        payload = _extract_job_from_algolia_hit(self._hit())

        self.assertEqual(payload["external_id"], "206316")
        self.assertEqual(payload["organization_name"], "Epoch AI")
        self.assertEqual(payload["location"], "Remote, Global")
        self.assertEqual(payload["salary_min"], 150000)
        self.assertEqual(payload["salary_max"], 180000)
        self.assertEqual(payload["salary_currency"], "USD")
        self.assertEqual(payload["skills"], ["communications-outreach"])
        self.assertEqual(payload["category_name"], "AI Safety & Policy")
        self.assertFalse(payload["raw_data"]["needs_crawling"])

    def test_non_remote_hit_is_excluded(self):
        payload = _extract_job_from_algolia_hit(
            self._hit(
                locations=[
                    {
                        "name": "San Francisco, USA",
                        "type": "CITY",
                        "country": "USA",
                        "is_remote": False,
                    }
                ]
            )
        )

        self.assertIsNone(payload)

    def test_expired_and_descriptionless_hits_are_excluded(self):
        self.assertIsNone(
            _extract_job_from_algolia_hit(
                self._hit(id="expired", closes_at="2000-01-01T00:00:00+00:00")
            )
        )
        self.assertIsNone(
            _extract_job_from_algolia_hit(
                self._hit(id="empty-description", description="")
            )
        )

    def test_salary_parser_preserves_currency_and_rejects_hourly_rates(self):
        self.assertEqual(
            _parse_algolia_salary("C$50,000 - C$80,000"),
            (50000, 80000, "CAD"),
        )
        self.assertEqual(_parse_algolia_salary("$25 - $35 per hour"), (None, None, ""))

    @patch("jobs.services.importers.probablygood.requests.post")
    def test_fetch_uses_public_search_config_and_filters_remote_hits(self, post):
        config_response = Mock()
        config_response.raise_for_status.return_value = None
        config_response.json.return_value = {
            "data": {
                "algolia_search_key": {
                    "api_key": "public-search-key",
                    "app_id": "APP123",
                    "index_name_jobs": "jobs_prod",
                }
            }
        }
        search_response = Mock()
        search_response.raise_for_status.return_value = None
        search_response.json.return_value = {
            "results": [
                {
                    "hits": [
                        self._hit(),
                        self._hit(
                            id="closed-office-role",
                            locations=[
                                {
                                    "name": "London, UK",
                                    "type": "CITY",
                                    "is_remote": False,
                                }
                            ],
                        ),
                    ],
                    "nbPages": 1,
                }
            ]
        }
        # One search per index slice; the same hit in both slices is kept once.
        post.side_effect = [config_response, search_response, search_response]

        jobs = fetch_job_listings(max_pages=1, delay=0)

        self.assertEqual(len(jobs), 1)
        self.assertEqual([c.kwargs["json"]["requests"][0]["facetFilters"] for c in post.call_args_list[1:]],
                         [["has_salary:true"], ["has_salary:false"]])
        self.assertEqual(jobs[0]["title"], "Writer and Editor")
        search_call = post.call_args_list[1]
        self.assertEqual(search_call.kwargs["json"]["requests"][0]["indexName"], "jobs_prod")
        self.assertEqual(
            search_call.kwargs["headers"]["X-Algolia-API-Key"],
            "public-search-key",
        )

    @patch(
        "jobs.management.commands.import_remote_jobs.importers.import_probablygood",
        new_callable=AsyncMock,
    )
    @patch("jobs.management.commands.import_remote_jobs.call_command")
    @patch("jobs.management.commands.import_remote_jobs.crawlers.crawl_jobs_needing_update")
    def test_command_does_not_recrawl_structured_imports(self, crawl, maintenance, importer):
        importer.return_value = {
            "fetched": 3,
            "created": 3,
            "updated": 0,
            "failed": 0,
        }

        call_command("import_remote_jobs", source="probablygood")

        crawl.assert_not_called()
        self.assertEqual(
            [call.args[0] for call in maintenance.call_args_list],
            ["deactivate_expired_jobs", "deactivate_duplicate_jobs", "deactivate_non_opening_jobs"],
        )
