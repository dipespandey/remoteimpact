from django.test import SimpleTestCase

from jobs.services.importers.climatebase import _pick_location
from jobs.services.importers.common import (
    _canonical_duplicate_url,
    _is_generic_listing_title,
    _normalize_duplicate_title,
    _public_listing_quality_ready,
    _raw_description_signature,
    _sanitize_public_url,
)
from jobs.services.importers.idealist import _idealist_url
from jobs.services.location_normalizer import normalize_location


class DuplicateImportHelperTests(SimpleTestCase):
    def test_unique_ats_urls_ignore_tracking_but_preserve_posting_ids(self):
        first = "https://jobs.ashbyhq.com/far.ai/a94080f7-288b-4a98-ad4a-aaaa4bbf9968"
        self.assertEqual(_canonical_duplicate_url(first), _canonical_duplicate_url(first + "?utm_source=80000hours&embed=js"))
        second = first.replace("a94080f7", "a5cc0d32")
        self.assertNotEqual(_canonical_duplicate_url(first), _canonical_duplicate_url(second))
        self.assertEqual(_canonical_duplicate_url(first), _canonical_duplicate_url(first + "/application"))
        self.assertEqual(_canonical_duplicate_url("https://jobs.ashbyhq.com/far.ai"), "")
        self.assertEqual(_canonical_duplicate_url("https://job-boards.greenhouse.io/example/jobs/12345?utm_source=foo"), _canonical_duplicate_url("https://boards.greenhouse.io/example/jobs/12345"))
        self.assertEqual(_canonical_duplicate_url("https://job-boards.greenhouse.io/example/jobs/12345"), _canonical_duplicate_url("https://job-boards.eu.greenhouse.io/example/jobs/12345?t=tracking-token"))

    def test_canonical_duplicate_url_preserves_smartrecruiters_ids(self):
        first = (
            "https://jobs.smartrecruiters.com/Eurofins/"
            "744000133858449-senior-software-engineer?source=Climatebase"
        )
        second = (
            "https://jobs.smartrecruiters.com/Eurofins/"
            "744000133039917-senior-software-engineer?utm_source=Climatebase"
        )

        self.assertNotEqual(_canonical_duplicate_url(first), _canonical_duplicate_url(second))
        self.assertEqual(_canonical_duplicate_url(first), _canonical_duplicate_url(first.split('?')[0] + '/apply'))

    def test_canonical_duplicate_url_preserves_breezy_hashes(self):
        first = "https://everlight-solar.breezy.hr/p/54375f57ce44-salesforce-administrator/apply"
        second = "https://everlight-solar.breezy.hr/p/fadedf5997dd-salesforce-administrator/apply"

        self.assertNotEqual(_canonical_duplicate_url(first), _canonical_duplicate_url(second))

    def test_normalize_duplicate_title_removes_remote_suffix(self):
        self.assertEqual(
            _normalize_duplicate_title("Senior Engineer - Remote"),
            "senior engineer",
        )

    def test_raw_description_signature_uses_cleaned_text(self):
        first = (
            "<p>Design and build reliable systems for impact teams, improve "
            "workflow quality, and support thoughtful product delivery.</p>"
        )
        second = (
            "Design and build reliable systems for impact teams, improve "
            "workflow quality, and support thoughtful product delivery."
        )

        self.assertEqual(_raw_description_signature(first), _raw_description_signature(second))

    def test_climatebase_remote_location_preserves_region(self):
        location = _pick_location(
            {"locations": ["Bengaluru, KA, IN"]},
            remote_pref="remote",
        )

        self.assertEqual(location, "Remote (Bengaluru, KA, IN)")

    def test_climatebase_remote_location_defaults_to_remote(self):
        location = _pick_location({"locations": []}, remote_pref="remote")

        self.assertEqual(location, "Remote")

    def test_remote_canadian_country_code_is_not_california(self):
        self.assertEqual(
            normalize_location("Remote (Vancouver, BC, CA)"),
            "Remote (Canada)",
        )

    def test_remote_indian_country_code_is_not_indiana(self):
        self.assertEqual(
            normalize_location("Remote (New Delhi, DL, IN)"),
            "Remote (India)",
        )

    def test_idealist_multilingual_url_uses_english_absolute_url(self):
        self.assertEqual(
            _idealist_url(
                {
                    "en": "/en/nonprofit/example",
                    "es": "/es/ong/example",
                }
            ),
            "https://www.idealist.org/en/nonprofit/example",
        )

    def test_public_url_sanitizer_adds_scheme_and_rejects_objects(self):
        self.assertEqual(
            _sanitize_public_url("example.org/about"),
            "https://example.org/about",
        )
        self.assertEqual(_sanitize_public_url("{'en': '/bad'}"), "")

    def test_generic_talent_pools_are_not_job_openings(self):
        for title in ("General Application", "Expression of Interest", "Internships", "Volunteer"):
            self.assertTrue(_is_generic_listing_title(title))

        self.assertFalse(_is_generic_listing_title("Volunteer Program Manager"))
        self.assertFalse(_is_generic_listing_title("Software Engineering Internship"))

    def test_public_listing_quality_gate_requires_description_and_destination(self):
        payload = {
            "source": "climatebase",
            "description": "A" * 120,
            "application_url": "https://example.org/apply",
        }
        self.assertTrue(_public_listing_quality_ready(payload))
        self.assertFalse(_public_listing_quality_ready({**payload, "description": "Short"}))
        self.assertFalse(_public_listing_quality_ready({**payload, "application_url": ""}))
        self.assertFalse(
            _public_listing_quality_ready(
                {**payload, "raw_data": {"needs_crawling": True}}
            )
        )

    def test_descriptive_talent_pools_and_generic_forms_are_not_openings(self):
        for title in (
            "Join Our Talent Community",
            "Future Opportunities",
            "Submit Your Resume Here",
            "Don't See the Right Role?",
            "CV Database",
            "Wildlife Works - Future Talent",
            "Proactive application",
            "List of Events for Career Capital",
            "Volunteers",
            "Your (role) here",
            "Submit your CV/Soumettez votre CV",
            "General Submission",
            "General Role",
        ):
            self.assertTrue(_is_generic_listing_title(title))

        self.assertFalse(_is_generic_listing_title("Software Engineering Internship"))
