from datetime import timedelta
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from jobs.models import ConsumerFunnelEvent, Job, JobAlert, Organization
from jobs.services.admin_metrics import recent_scout_searches
from jobs.services.job_scout_service import JobScoutService as Scout
from jobs.services.scout_close_matches import loose_preferences
from jobs.services.scout_constraints import evaluate_job


class ReportedScoutQualityTests(TestCase):
    python = 'Remote software engineering roles using Python in Europe, mid-level, no internships.'
    climate = 'Remote climate policy roles in Europe, mid-level, at least $70k, no internships.'

    def test_policy_and_research_requests_keep_explicit_europe(self):
        for text in [self.climate, 'Remote research roles in Europe, mid-level.']:
            self.assertEqual(Scout._fallback_parse(text)['locations'], ['Europe'])
        self.assertEqual(Scout._fallback_parse('I worked in policy roles in Europe. Looking for remote jobs worldwide.')['locations'], ['Worldwide'])

    def test_required_python_survives_close_matching(self):
        prefs = Scout._fallback_parse(self.python)
        self.assertIn('python', prefs['required_skills'])
        loose = loose_preferences(prefs)
        self.assertIn('python', loose['required_skills'])
        org = Organization.objects.create(name='Software', slug='software')
        job = Job.objects.create(title='Software Engineer (.NET)', organization=org,
                                 description='Build applications using C# and .NET.',
                                 country='Worldwide', location='Remote worldwide', experience_level='mid')
        self.assertEqual(evaluate_job(job, loose, relaxed=True).reason, 'required_skill')

    def test_software_request_accepts_full_stack_developer_title(self):
        self.assertTrue(Scout._title_matches_intent('Full-Stack Developer', ['software engineer'], 'Build services using Python.'))
        self.assertFalse(Scout._title_matches_intent('Civil Engineer', ['software engineer'], 'Build infrastructure.'))

    def test_europe_climate_request_rejects_us_only_role(self):
        org = Organization.objects.create(name='Policy', slug='policy')
        job = Job.objects.create(title='Climate Policy Manager', organization=org,
                                 description='Climate policy research. US residents only.', country='USA',
                                 location='Remote (US)', experience_level='mid', salary_min=90000,
                                 salary_max=100000, salary_currency='USD')
        prefs = loose_preferences(Scout._fallback_parse(self.climate))
        self.assertFalse(evaluate_job(job, prefs, relaxed=True).allowed)


class ScoutSearchVisibilityTests(TestCase):
    def test_saved_and_preview_briefs_have_original_counts_and_no_invented_history(self):
        user = get_user_model().objects.create_user(username='searcher')
        alert = JobAlert.objects.create(user=user, intent_text='Python roles in Europe')
        ConsumerFunnelEvent.objects.create(event=ConsumerFunnelEvent.Event.SCOUT_CREATED, alert=alert, metadata={'match_count': 2})
        ConsumerFunnelEvent.objects.create(event=ConsumerFunnelEvent.Event.SCOUT_PREVIEWED, metadata={'intent_text': 'Climate policy in Europe', 'match_count': 0})
        ConsumerFunnelEvent.objects.create(event=ConsumerFunnelEvent.Event.SCOUT_PREVIEWED, metadata={'match_count': 5})
        rows = recent_scout_searches(timezone.now() - timedelta(days=1))
        self.assertEqual(len(rows), 2)
        self.assertEqual({r['intent']: r['match_count'] for r in rows}, {'Python roles in Europe': 2, 'Climate policy in Europe': 0})
