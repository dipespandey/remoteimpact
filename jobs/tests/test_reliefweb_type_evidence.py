from django.test import TestCase, SimpleTestCase

from jobs.services.importers.common import _upsert_job
from jobs.services.importers.reliefweb import _reliefweb_headers, _transform_reliefweb_item
from jobs.services.job_quality import _schema_employment_type


def item(types, title="Research Assistant"):
    return {"id": "type-evidence-1", "fields": {
        "title": title, "body": "Support research for a humanitarian organisation.",
        "source": [{"name": "Example organisation"}],
        "url": "https://example.org/jobs/research", "type": types,
    }}


class ReliefWebTypeEvidenceTests(SimpleTestCase):
    def test_headers_do_not_depend_on_a_job(self):
        self.assertEqual(_reliefweb_headers("remoteimpact")["User-Agent"], "remoteimpact")

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
