from unittest.mock import AsyncMock, patch
from asgiref.sync import async_to_sync
from django.test import TestCase, SimpleTestCase, override_settings

from jobs.services.importers.common import _upsert_job
from jobs.services.importers.reliefweb import _has_remote_work_evidence, _mark_filtered_existing_seen, _reliefweb_headers, _transform_reliefweb_item, import_reliefweb
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
            ("Remote Researcher", "This position is not remote.", False),
            ("Researcher", "**Location:** Fully remote", True),
            ("Researcher", "This is a fully remote position.", True),
            ("Researcher", "All work is remote.", True),
            ("Researcher", "This position is 100% remote.", True),
            ("Researcher", "**Workplace: Remote / Home-based (with mission travel)**", True),
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
        remote['id'] = 'remote'
        field = item([{"name": "Job"}], "Emergency Field Coordinator")
        field["id"] = "field"
        with patch('jobs.services.importers.reliefweb._fetch_reliefweb_list', return_value=([{'id': 'remote'}, {'id': 'field'}], 2)), \
             patch('jobs.services.importers.reliefweb._fetch_reliefweb_detail', side_effect=[remote, field]), \
             patch('jobs.services.importers.reliefweb._mark_filtered_existing_seen') as mark_seen, \
             patch('jobs.services.importers.reliefweb.batch_upsert_jobs', new_callable=AsyncMock, return_value={'created': 1}) as upsert:
            result = async_to_sync(import_reliefweb)()
        self.assertEqual([p['title'] for p in upsert.await_args.args[0]], ['Researcher (Remote)'])
        mark_seen.assert_called_once_with(['field'])
        self.assertEqual(result['source_fetched'], 2)
        self.assertFalse(result['skip_retirement'])

    @override_settings(RELIEFWEB_APP_NAME="remoteimpact-test")
    def test_limit_bounds_source_requests_even_without_remote_matches(self):
        field = item([{"name": "Job"}], "Emergency Field Coordinator")
        field['id'] = 'one'
        with patch('jobs.services.importers.reliefweb._fetch_reliefweb_list', return_value=([{'id': 'one'}, {'id': 'two'}], 2)), \
             patch('jobs.services.importers.reliefweb._fetch_reliefweb_detail', return_value=field) as detail, \
             patch('jobs.services.importers.reliefweb._mark_filtered_existing_seen'), \
             patch('jobs.services.importers.reliefweb.batch_upsert_jobs', new_callable=AsyncMock, return_value={'created': 0}):
            async_to_sync(import_reliefweb)(limit=1)
        self.assertEqual(detail.call_count, 1)

    @override_settings(RELIEFWEB_APP_NAME="remoteimpact-test")
    def test_incomplete_detail_or_source_page_does_not_retire_listings(self):
        for pages, detail in [
            ([([{'id': 'one'}], 1)], None),
            ([([{'id': 'one'}], 101), ([], 101)], item([{'name': 'Job'}])),
            ([([{'id': 'one'}], None), ([], None)], item([{'name': 'Job'}])),
        ]:
            if detail:
                detail['id'] = 'one'
            with self.subTest(pages=pages), \
                 patch('jobs.services.importers.reliefweb._fetch_reliefweb_list', side_effect=pages), \
                 patch('jobs.services.importers.reliefweb._fetch_reliefweb_detail', return_value=detail), \
                 patch('jobs.services.importers.reliefweb._mark_filtered_existing_seen'), \
                 patch('jobs.services.importers.reliefweb.batch_upsert_jobs', new_callable=AsyncMock, return_value={}):
                self.assertTrue(async_to_sync(import_reliefweb)()['skip_retirement'])

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
    def test_filtered_sighting_does_not_reactivate_a_held_listing(self):
        from django.utils import timezone
        job, _ = _upsert_job(_transform_reliefweb_item(item([{"name": "Job"}])))
        job.is_active = False
        job.last_seen_at = None
        job.save(update_fields=['is_active', 'last_seen_at'])
        _mark_filtered_existing_seen([job.external_id])
        job.refresh_from_db()
        self.assertFalse(job.is_active)
        self.assertIsNone(job.last_seen_at)
        job.is_active = True
        job.save(update_fields=['is_active'])
        _mark_filtered_existing_seen([job.external_id])
        job.refresh_from_db()
        self.assertTrue(job.is_active)
        self.assertLess((timezone.now() - job.last_seen_at).total_seconds(), 10)

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
