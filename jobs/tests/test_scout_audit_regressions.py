"""Public-boundary regressions from the October quality audit; synthetic briefs."""
from decimal import Decimal
from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone

from jobs.services.job_scout_service import JobScoutService as Scout
from jobs.services.scout_constraints import evaluate_job, salary_compatible
from jobs.services.scout_recovery import recover
from jobs.services.scout_tasks import claim_task, enqueue_preview, execute_claimed_task
from jobs.services.scout_verification import verify_matches
from jobs.models import ScoutPreviewTask
from jobs.tests.test_scout_recovery import job


class AuditedRequirementsTests(TestCase):
    def test_executive_compensation_range_survives_grounding_and_rejects_lower_pay(self):
        prefs = Scout._fallback_parse('Remote COO roles. Target compensation is $240,000–$480,000+ annually.')
        self.assertEqual(Decimal(prefs['salary_requirement']['amount']), Decimal(240000))
        self.assertEqual(prefs['salary_requirement']['currency'], 'USD')
        item = job(title='Chief Operating Officer', salary_min=140000, salary_max=180000)
        for relaxed in [False, True]:
            self.assertFalse(evaluate_job(item, prefs, relaxed=relaxed).allowed)

    def test_bounded_range_compares_overlap_in_the_requested_units(self):
        prefs = Scout._fallback_parse('Remote analyst, salary between $55k and $75k per year.')
        requirement = prefs['salary_requirement']
        self.assertEqual(Decimal(requirement['amount']), Decimal(55000))
        self.assertEqual(Decimal(requirement['max_amount']), Decimal(75000))
        self.assertTrue(salary_compatible(job(salary_min=60000, salary_max=80000), requirement))
        self.assertFalse(salary_compatible(job(salary_min=40000, salary_max=50000), requirement))
        self.assertFalse(salary_compatible(job(salary_min=85000, salary_max=95000), requirement))
        hourly = Scout._fallback_parse('Remote editor, pay between €30 and €45 per hour.')['salary_requirement']
        self.assertEqual(hourly['period'], 'hour')
        self.assertEqual(hourly['currency'], 'EUR')

    def test_currencyless_range_is_preserved_as_uncertain_not_dropped(self):
        prefs = Scout._fallback_parse('Remote role with salary between 55k and 75k.')
        self.assertEqual(Decimal(prefs['salary_requirement']['amount']), Decimal(55000))
        self.assertIsNone(prefs['salary_requirement']['currency'])
        self.assertIn('salary_currency', prefs['unresolved_constraints'])
        self.assertIn('salary_currency', evaluate_job(job(), prefs, relaxed=True).caveats)

    def test_held_out_range_variants_and_background_numbers(self):
        for brief, amount, maximum, currency in [
            ('Remote analyst, target salary £60–85k annually.', '60000', '85000', 'GBP'),
            ('Remote editor. Pay 30 to 45 EUR per hour.', '30', '45', 'EUR'),
            ('Remote researcher. Salary CAD 90000—110000.', '90000', '110000', 'CAD'),
        ]:
            with self.subTest(brief=brief):
                pay = Scout._fallback_parse(brief)['salary_requirement']
                self.assertEqual((Decimal(pay['amount']), Decimal(pay['max_amount']), pay['currency']),
                                 (Decimal(amount), Decimal(maximum), currency))
        prefs = Scout._fallback_parse('I managed budgets of $20k–$40k. Seeking remote operations at least $70k.')
        self.assertEqual(Decimal(prefs['salary_requirement']['amount']), Decimal(70000))
        for brief in ['Salary between $90k and $60k.', 'Salary between €60k and $90k.']:
            self.assertIn('salary_range', Scout._fallback_parse(brief)['unresolved_constraints'])

    def test_saved_and_model_parsed_preferences_cannot_erase_grounded_requirements(self):
        brief = 'Full-time remote COO. Target compensation $240,000–$480,000+ annually. I study part-time.'
        fallback = Scout._fallback_parse(brief)
        grounded = Scout._ground_preferences({'titles': ['COO'], 'job_types': ['part-time'], 'min_salary': None}, brief, fallback)
        self.assertEqual(grounded['job_types'], ['full-time'])
        self.assertEqual(Decimal(grounded['salary_requirement']['amount']), Decimal(240000))
        from jobs.models import JobAlert
        alert = JobAlert(intent_text=brief, parsed_preferences={'job_types': ['part-time'], 'min_salary': None})
        current = Scout.preferences_for_alert(alert)
        self.assertEqual(current['job_types'], ['full-time'])
        self.assertEqual(Decimal(current['salary_requirement']['amount']), Decimal(240000))

    def test_study_arrangements_do_not_change_job_types(self):
        for brief in ['I want a full-time remote job while I pursue a degree part-time.',
                      'Full-time operations role so I can study part-time.',
                      'I am a part-time student looking for full-time work.']:
            self.assertEqual(Scout._fallback_parse(brief)['job_types'], ['full-time'], brief)
        self.assertEqual(Scout._fallback_parse('I study full-time and want a part-time remote job.')['job_types'], ['part-time'])
        self.assertEqual(Scout._fallback_parse('I want full-time or part-time remote jobs.')['job_types'], ['full-time', 'part-time'])

    def test_out_of_country_is_clarified_instead_of_claiming_country_match(self):
        prefs = Scout._fallback_parse('Remote analyst role out of US, full-time.')
        self.assertNotIn('USA', prefs['locations'])
        self.assertIn('location_ambiguous', prefs['unresolved_constraints'])
        self.assertIn('location_ambiguous', evaluate_job(job(), prefs, relaxed=True).caveats)

    def test_country_restrictions_reach_final_verification_without_model_help(self):
        for text in ['Location: Remote, in Canada. Candidates must be legally entitled to work in Canada.',
                     'You must be legally authorized to work in Canada.',
                     'Applicants must reside in Canada.']:
            item = job(title='Operations Manager', description=text, location='Remote', country='Worldwide')
            item.scout_reasons = ['Relevant operations work']
            prefs = {'intent_text': 'Remote operations roles', 'titles': ['Operations Manager']}
            self.assertEqual(verify_matches([item], prefs, {}), [item])
            self.assertIn('location_required', item.scout_caveats, text)
            self.assertTrue(any('Canada' in r for r in item.scout_reasons))
            self.assertFalse(evaluate_job(item, {**prefs, 'locations': ['France']}, relaxed=True).allowed)

    def test_global_examples_and_negated_authorization_are_not_restrictions(self):
        for text in ['Remote worldwide, including Canada and France.',
                     'You do not need to be authorized to work in Canada. Remote worldwide.']:
            item = job(description=text)
            verify_matches([item], {'intent_text': 'Software engineer', 'titles': ['Software Engineer']}, {})
            self.assertNotIn('location_required', item.scout_caveats)


class BroadSearchRecoveryTests(TestCase):
    def test_generic_worldwide_request_can_show_source_backed_options_with_caveats(self):
        prefs = Scout._fallback_parse('I want a remote part-time job which can be done from anywhere in the world.')
        item = job(title='Grant Writer', job_type='part-time', description='Responsibilities: Write grant proposals for research projects. Qualifications: Good writing skills.')
        result = recover([item], prefs, [], {})
        self.assertEqual(result, [item])
        self.assertIn('request_details_needed', item.scout_caveats)
        self.assertTrue(item.scout_needs_confirmation)

    def test_broad_recovery_never_overrides_country_pay_or_explicit_rejection(self):
        prefs = Scout._fallback_parse('Any remote part-time job worldwide, at least $70k.')
        base = dict(title='Grant Writer', job_type='part-time', description='Responsibilities: Write grant proposals for research projects.')
        for extra in [dict(location='Remote Canada', country='CA'), dict(salary_min=40000, salary_max=50000), dict(job_type='full-time')]:
            self.assertEqual(recover([job(**{**base, **extra})], prefs, [], {}), [])
        self.assertEqual(recover([job(**base)], prefs, [], {}, rejected_ids=[1]), [])

    def test_broad_recovery_requires_actual_duties_not_company_or_qualifications(self):
        prefs = Scout._fallback_parse('Any remote part-time job worldwide.')
        for text in ['Our company builds software for schools.',
                     'Qualifications: Provide evidence of writing experience.',
                     'Responsibilities: Work with our team. Qualifications: Write grant proposals.']:
            self.assertEqual(recover([job(job_type='part-time', description=text)], prefs, [], {}), [])


class IncompleteReviewRetryTests(TestCase):
    def test_empty_incomplete_review_retries_once_then_keeps_honest_outcome(self):
        task, _ = enqueue_preview('Remote policy work in Europe.', 'quality-audit-session')
        payload = {'matches': [], 'older_jobs': [], 'diagnostics': {'review_incomplete': True, 'empty_reason': 'review_incomplete'}}
        with patch('jobs.services.scout_tasks._compute_with_deadline', return_value=payload):
            pk, token = claim_task()
            self.assertFalse(execute_claimed_task(pk, token))
            task.refresh_from_db()
            self.assertEqual(task.state, ScoutPreviewTask.State.QUEUED)
            task.available_at = timezone.now()
            task.save(update_fields=['available_at'])
            pk, token = claim_task()
            execute_claimed_task(pk, token)
        task.refresh_from_db()
        self.assertEqual(task.state, ScoutPreviewTask.State.SUCCEEDED)
        self.assertEqual(task.attempts, 2)
        self.assertTrue(task.result['diagnostics']['review_incomplete'])
        self.assertTrue(task.result['diagnostics']['review_retry_exhausted'])
        self.assertIsNone(claim_task())

    def test_true_no_match_and_partial_results_are_not_delayed_for_retry(self):
        for index, payload in enumerate([
            {'matches': [], 'diagnostics': {'review_incomplete': False}},
            {'matches': [{'job_id': 1}], 'diagnostics': {'review_incomplete': True}},
        ]):
            task, _ = enqueue_preview(f'Remote policy work in Europe {index}.', f'quality-session-{index}')
            pk, token = claim_task()
            with patch('jobs.services.scout_tasks._compute_with_deadline', return_value=payload):
                execute_claimed_task(pk, token)
            task.refresh_from_db()
            self.assertEqual(task.state, ScoutPreviewTask.State.SUCCEEDED)
