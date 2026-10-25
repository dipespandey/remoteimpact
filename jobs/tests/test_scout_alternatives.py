from datetime import timedelta
from unittest.mock import patch

from django.test import TestCase, override_settings
from django.utils import timezone
from django.urls import reverse

from jobs.models import Job, Organization
from jobs.services.scout_alternatives import older_openings


@override_settings(SCOUT_REMOTE_POLICY_ENABLED=False)
class OlderOpeningsTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(name='Example', slug='older-example')
        self.prefs = {'intent_text': 'Remote software engineer roles from Nepal',
                      'titles': ['software engineer'], 'locations': ['Nepal'], 'job_types': ['full-time']}

    def job(self, **values):
        defaults = dict(title='Software Engineer', slug=f'older-{Job.objects.count()}', organization=self.org,
                        description='Responsibilities: Build software services and reliable APIs. Qualifications: Relevant experience.',
                        location='Remote worldwide', country='Worldwide', job_type='full-time',
                        posted_at=timezone.now() - timedelta(days=45), last_seen_at=timezone.now())
        with patch('jobs.services.embedding_service.embed_job', return_value=None):
            return Job.objects.create(**{**defaults, **values})

    def test_only_available_older_same_function_jobs_returned_without_provider_calls(self):
        good = self.job()
        self.job(posted_at=timezone.now() - timedelta(days=5))
        self.job(posted_at=timezone.now() - timedelta(days=95))
        self.job(is_active=False)
        self.job(description='Our company builds software services. Join our mission.')
        with patch('jobs.services.scout_workflow.generate', side_effect=AssertionError('No provider calls')):
            result = older_openings(self.prefs)
        self.assertEqual([j.pk for j in result], [good.pk])
        self.assertIn('Build software services', result[0].alternative_evidence)

    def test_conflicting_location_type_pay_and_unresolved_constraints_remain_excluded(self):
        self.job(location='Remote US only', country='USA')
        self.job(job_type='part-time')
        self.assertEqual(older_openings(self.prefs), [])
        self.job(salary_min=10000, salary_max=20000, salary_currency='USD')
        prefs = {**self.prefs, 'salary_requirement': {'amount': '70000', 'currency': 'USD', 'period': 'year'}}
        self.assertEqual(older_openings(prefs), [])
        self.assertEqual(older_openings({**self.prefs, 'unresolved_constraints': ['location_code']}), [])

    def test_missing_target_and_duplicate_openings_do_not_fill_with_irrelevant_jobs(self):
        self.job()
        self.job()
        self.assertEqual(len(older_openings(self.prefs)), 1)
        self.assertEqual(older_openings({'titles': []}), [])

    def test_older_suggestion_uses_original_duties_instead_of_enriched_claims(self):
        self.job(raw_data={'description': 'Coordinate community programs remotely worldwide.'})
        good = self.job(raw_data={'description': 'Responsibilities: Build software services remotely worldwide.'})
        result = older_openings(self.prefs)
        self.assertEqual([job.pk for job in result], [good.pk])
        self.assertIn('Build software services remotely worldwide', result[0].alternative_evidence)


@override_settings(SECURE_SSL_REDIRECT=False, ACCOUNT_RATE_LIMITS={})
class OlderPreviewTests(TestCase):
    def setUp(self):
        from jobs.tests.test_scout_results import setup_scout_fixture
        setup_scout_fixture(self)

    def test_older_openings_are_separate_from_match_counts(self):
        from jobs.models import ConsumerFunnelEvent
        self.job.alternative_evidence = 'Build useful climate software.'
        with patch('jobs.services.job_scout_service.JobScoutService.search_intent', return_value=(self.prefs, [])), \
             patch('jobs.services.scout_alternatives.older_openings', return_value=[self.job]):
            response = self.client.get(reverse('jobs:job_scout_preview'), {'intent': self.intent})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Older openings to review')
        self.assertContains(response, 'not included in your match email')
        self.assertEqual(response.context['preview_jobs'], [])
        event = ConsumerFunnelEvent.objects.get(event='scout_previewed')
        self.assertEqual(event.metadata['match_count'], 0)

    def test_no_extra_search_on_success_or_incomplete_review(self):
        for incomplete, matches in ((False, [self.job]), (True, [])):
            def search(*args, **kwargs):
                kwargs['diagnostics']['review_incomplete'] = incomplete
                return self.prefs, matches
            with patch('jobs.services.job_scout_service.JobScoutService.search_intent', side_effect=search), \
                 patch('jobs.services.scout_alternatives.older_openings') as older:
                response = self.client.get(reverse('jobs:job_scout_preview'), {'intent': self.intent})
            self.assertEqual(response.status_code, 200)
            older.assert_not_called()
