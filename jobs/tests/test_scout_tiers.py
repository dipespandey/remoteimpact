from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import patch

from django.template.loader import render_to_string
from django.test import TestCase, override_settings
from django.utils import timezone

from jobs.models import Job, Organization
from jobs.services.job_scout_service import JobScoutService as Scout
from jobs.services.scout_tiers import groups, tier_for_job
from jobs.services.scout_workflow import bounded_workflow, generate, plan_terms


class ScoutTierTests(TestCase):
    def test_persisted_warnings_and_close_matches_stay_in_second_tier(self):
        for reasons in [['Check: salary not listed'], ['Close match, not exact: adjacent function'],
                        ['Location eligibility needs confirmation'], ['Worth considering: review the differences below']]:
            self.assertEqual(tier_for_job(SimpleNamespace(scout_reasons=reasons)), 'consider')
        self.assertEqual(tier_for_job(SimpleNamespace(scout_reasons=['Uses Python'])), 'good')

    def test_groups_keep_exact_results_separate_from_unknown_eligibility(self):
        exact = SimpleNamespace(scout_reasons=['Uses Python'])
        unknown = SimpleNamespace(scout_needs_confirmation=True)
        tiers = groups([unknown, exact])
        self.assertEqual(tiers[0]['jobs'], [exact])
        self.assertEqual(tiers[1]['jobs'], [unknown])

    @override_settings(DEEPSEEK_API_KEY='test', DEEPSEEK_MODEL='test')
    @patch('jobs.services.ai.AIClient')
    def test_planning_reserves_two_provider_calls_for_review(self, client):
        with bounded_workflow({}):
            generate('interpret')
            generate('plan')
            diagnostics = {}
            self.assertEqual(plan_terms({'intent_text': 'unique tier budget test'}, diagnostics, retry=True), [])
            self.assertEqual(diagnostics['refinement'], 'reserved_for_review')
            generate('evidence')
            generate('alternatives')
        self.assertEqual(client.return_value.generate.call_count, 4)

    def test_initial_search_includes_older_still_available_jobs_but_not_closed(self):
        org = Organization.objects.create(name='Fresh source', slug='fresh-source')
        old = Job.objects.create(title='Software Engineer', slug='old-available-engineer', organization=org, source=Job.Source.MANUAL,
                                 posted_at=timezone.now()-timedelta(days=60), last_seen_at=timezone.now())
        closed = Job.objects.create(title='Closed Engineer', slug='closed-engineer', organization=org, source=Job.Source.MANUAL, is_active=False)
        def ranked(qs, *args, **kwargs):
            self.assertIn(old.pk, qs.values_list('pk', flat=True))
            self.assertNotIn(closed.pk, qs.values_list('pk', flat=True))
            return [], []
        with patch.object(Scout, 'parse_intent', return_value={}), patch.object(Scout, '_rank_candidates', side_effect=ranked), \
             patch('jobs.services.scout_close_matches.close_matches', return_value=[]):
            Scout.search_intent('software engineer')

    def test_recurring_search_keeps_last_sent_cutoff(self):
        alert = SimpleNamespace(last_sent_at=timezone.now())
        with patch.object(Scout, 'preferences_for_alert', return_value={}), patch.object(Scout, 'get_matches_for_preferences', return_value=[]) as match:
            Scout.get_matches(alert)
        self.assertFalse(match.call_args.args[0]['_scout_current_inventory'])
        self.assertEqual(match.call_args.kwargs['since'], alert.last_sent_at)

    def test_email_never_calls_unknown_salary_a_strong_match(self):
        job = SimpleNamespace(title='Policy Analyst', organization=SimpleNamespace(name='Test'),
                              location='Europe', salary_min=None, salary_max=None, salary_currency='USD', raw_data={}, scout_reasons=['Check: salary not listed'],
                              get_job_type_display='Full time', alert_email_url='https://remoteimpact.org/jobs/test/')
        text = render_to_string('emails/job_alert.txt', {'jobs':[job], 'job_count':1, 'user':SimpleNamespace(first_name='')})
        self.assertIn('Worth considering', text)
        self.assertNotIn('strong job match', text)
        self.assertNotIn('\nGood matches\n', text)

    def test_alternative_search_preserves_unresolved_constraints_and_required_skills(self):
        from jobs.services.scout_close_matches import loose_preferences
        prefs = {'titles': ['software engineer'], 'required_skills': ['python'],
                 'unresolved_constraints': ['ambiguous timezone'], 'locations': ['Europe'],
                 'exclude_keywords': ['internship'], 'min_salary': 70000}
        loose = loose_preferences(prefs)
        for key in ['required_skills', 'unresolved_constraints', 'locations', 'exclude_keywords', 'min_salary']:
            self.assertEqual(loose[key], prefs[key])

    def test_open_ended_applications_are_never_inserted_to_fill_either_tier(self):
        from jobs.services.scout_constraints import evaluate_job
        for title in ['Software Engineer "Spontaneous Application"', 'Spontaneous application', 'General application']:
            job = SimpleNamespace(title=title)
            for relaxed in [False, True]:
                self.assertEqual(evaluate_job(job, {}, relaxed=relaxed).reason, 'not_job_opening')

    def test_policy_request_does_not_turn_science_work_into_an_alternative(self):
        from jobs.services.scout_evidence import role_conflict
        prefs = Scout._fallback_parse('Remote climate policy roles in Europe, mid-level, no internships.')
        job = SimpleNamespace(title='Program Scientist, Arctic Stabilization Initiative')
        self.assertTrue(role_conflict(job, prefs, 'Climate research funding and scientific programme management.'))
