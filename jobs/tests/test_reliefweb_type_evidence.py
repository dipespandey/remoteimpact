from unittest.mock import AsyncMock, patch
from asgiref.sync import async_to_sync
from django.test import TestCase, SimpleTestCase, override_settings

from jobs.services.importers.common import _upsert_job
from jobs.services.importers.reliefweb import _has_remote_work_evidence, _reliefweb_headers, _transform_reliefweb_item, import_reliefweb
from jobs.services.job_quality import _schema_employment_type


def item(types, title="Research Assistant"):
    return {"id": "type-evidence-1", "fields": {
        "title": title, "body": "Support research for a humanitarian organisation.",
        "source": [{"name": "Example organisation"}],
        "url": "https://example.org/jobs/research", "type": types,
    }}


class ReliefWebTypeEvidenceTests(SimpleTestCase):
    def test_remote_evidence_is_about_the_job_not_a_missing_country(self):
        for title, description, expected in [
            ("Roving Emergency Advisor", "Deployed to country offices for emergency response.", False),
            ("Remote Sensing Analyst", "Work with geospatial data.", False),
            ("Researcher (Remote)", "Research for a nonprofit.", True),
            ("Remote Researcher", "Location: On-site, Nairobi", False),
            ("Researcher", "**Location:** Fully remote", True),
            ("Researcher", "This is a fully remote position.", True),
            ("Researcher", "Location: Not remote", False),
            ("Researcher", "Location: Remote / hybrid", False),
            ("Researcher", "Location: Remote, with required attendance at two workshops in Baghdad", False),
            ("Researcher", "Provide remote support to field teams.", False),
        ]:
            entry = item([{"name": "Job"}], title)
            entry["fields"]["body"] = description
            with self.subTest(title=title, description=description):
                self.assertEqual(_has_remote_work_evidence(entry), expected)

    def test_headers_do_not_depend_on_a_job(self):
        self.assertEqual(_reliefweb_headers("remoteimpact")["User-Agent"], "remoteimpact")

    @override_settings(RELIEFWEB_APP_NAME="remoteimpact-test")
    def test_import_only_passes_source_supported_remote_roles_to_upsert(self):
        remote = item([{"name": "Job"}], "Researcher (Remote)")
        field = item([{"name": "Job"}], "Emergency Field Coordinator")
        field["id"] = "field-2"
        with patch('jobs.services.importers.reliefweb._fetch_reliefweb_list', return_value=([{'id': 'remote'}, {'id': 'field'}], 2)), \
             patch('jobs.services.importers.reliefweb._fetch_reliefweb_detail', side_effect=[remote, field]), \
             patch('jobs.services.importers.reliefweb.batch_upsert_jobs', new_callable=AsyncMock, return_value={'created': 1}) as upsert:
            async_to_sync(import_reliefweb)()
        self.assertEqual([p['title'] for p in upsert.await_args.args[0]], ['Researcher (Remote)'])

    @override_settings(RELIEFWEB_APP_NAME="remoteimpact-test")
    def test_limit_bounds_source_requests_even_without_remote_matches(self):
        field = item([{"name": "Job"}], "Emergency Field Coordinator")
        with patch('jobs.services.importers.reliefweb._fetch_reliefweb_list', return_value=([{'id': 'one'}, {'id': 'two'}], 2)), \
             patch('jobs.services.importers.reliefweb._fetch_reliefweb_detail', return_value=field) as detail, \
             patch('jobs.services.importers.reliefweb.batch_upsert_jobs', new_callable=AsyncMock, return_value={'created': 0}):
            async_to_sync(import_reliefweb)(limit=1)
        self.assertEqual(detail.call_count, 1)

    def test_source_type_does_not_invent_hours(self):
        for types, expected in [
            ([{"name": "Job"}], ""),
            ([], ""),
            ([{"name": "Consultancy"}], "contract"),
            ({"name": "Consultancy"}, "contract"),
            ([{"name": "Consultancy"}, {"name": "Job"}], ""),
        ]:
            with self.subTest(types=types):
                self.assertEqual(_transform_reliefweb_item(item(types))["job_type"], expected)
        self.assertEqual(_transform_reliefweb_item(item([{"name": "Job"}], "Volunteer"))["job_type"], "")
        self.assertEqual(_transform_reliefweb_item(item([{"name": "Consultancy"}], "Researcher - Volunteer"))["job_type"], "")

    def test_explicit_schedule_and_ambiguous_arrangements(self):
        for description, expected in [
            ("**Commitment:** Full-time availability for the internship.", "full-time"),
            ("**Working arrangements:** Flexible, part-time or full-time.", ""),
            ("This role supports full-time staff.", ""),
            ("Commitment: Not full-time", ""),
            ("Schedule: Full-time is not required", ""),
            ("Schedule: Part-time", "part-time"),
            ("**Working Hours:** Part Time", "part-time"),
            ("**Hours:** Full time, with some weekend work", "full-time"),
            ("**Hours:** 20 hours per week", ""),
        ]:
            entry = item([{"name": "Internship"}])
            entry["fields"]["body"] = description
            with self.subTest(description=description):
                self.assertEqual(_transform_reliefweb_item(entry)["job_type"], expected)


class ReliefWebTypePersistenceTests(TestCase):
    def test_unknown_type_survives_create_and_refresh_and_is_not_structured_as_full_time(self):
        payload = _transform_reliefweb_item(item([{"name": "Job"}]))
        job, created = _upsert_job(payload)
        self.assertTrue(created)
        self.assertEqual(job.job_type, "")
        self.assertIsNone(_schema_employment_type(job, job.raw_data))
        job, created = _upsert_job(payload)
        self.assertFalse(created)
        self.assertEqual(job.job_type, "")

    def test_consultancy_is_recorded_as_contract(self):
        job, _ = _upsert_job(_transform_reliefweb_item(item([{"name": "Consultancy"}])))
        self.assertEqual(job.job_type, "contract")
