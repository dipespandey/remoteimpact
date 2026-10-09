from datetime import timedelta
from decimal import Decimal

from django.test import TestCase
from django.utils import timezone

from jobs.models import Category, Job, Organization
from jobs.services.job_service import JobService


class JobListOrderingTest(TestCase):
    def setUp(self):
        self.organization = Organization.objects.create(name="Test Organization")
        self.category = Category.objects.create(name="Climate", slug="climate")

    def create_job(self, title, posted_at, **overrides):
        defaults = {
            "slug": title.lower().replace(" ", "-"),
            "organization": self.organization,
            "category": self.category,
            "description": "A meaningful role.",
            "requirements": "Experience doing meaningful work.",
            "posted_at": posted_at,
        }
        defaults.update(overrides)
        return Job.objects.create(title=title, **defaults)

    def test_newest_sort_does_not_promote_old_featured_jobs(self):
        now = timezone.now()
        old_featured = self.create_job(
            "Old Featured", now - timedelta(days=80), is_featured=True
        )
        fresh = self.create_job("Fresh Role", now - timedelta(hours=2))

        jobs = list(JobService.get_filtered_jobs({"sort": "date"}))

        self.assertEqual(jobs, [fresh, old_featured])

    def test_default_sort_is_newest_first(self):
        now = timezone.now()
        yesterday = self.create_job("Yesterday", now - timedelta(days=1))
        today = self.create_job("Today", now)

        self.assertEqual(
            list(JobService.get_filtered_jobs({})),
            [today, yesterday],
        )

    def test_salary_sort_happens_before_pagination(self):
        now = timezone.now()
        lower = self.create_job(
            "Lower Salary", now, salary_max=Decimal("60000")
        )
        higher = self.create_job(
            "Higher Salary", now - timedelta(days=1), salary_max=Decimal("120000")
        )
        missing = self.create_job("No Salary", now - timedelta(days=2))

        jobs = list(JobService.get_filtered_jobs({"sort": "salary-high"}))

        self.assertEqual(jobs, [higher, lower, missing])

    def test_expired_and_stale_undated_jobs_are_hidden(self):
        now = timezone.now()
        expired = self.create_job(
            "Expired", now - timedelta(days=3), expires_at=now - timedelta(hours=1)
        )
        stale = self.create_job("Stale", now - timedelta(days=181))
        current = self.create_job("Current", now - timedelta(days=2))

        jobs = list(JobService.get_filtered_jobs({}))

        self.assertEqual(jobs, [current])
        self.assertNotIn(expired, jobs)
        self.assertNotIn(stale, jobs)
