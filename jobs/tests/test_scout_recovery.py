from types import SimpleNamespace
from unittest.mock import patch

from django.core.cache import cache
from django.test import SimpleTestCase, TestCase, override_settings
from django.utils import timezone

from jobs.services.scout_recovery import recover, duty_evidence
from jobs.services.scout_constraints import evaluate_job
from jobs.services.scout_tiers import tier_for_job
from jobs.services.scout_workflow import bounded_workflow, generate, plan_terms


def job(pk=1, **overrides):
    values = dict(pk=pk, organization_id=1, title='Software Engineer',
        description='Responsibilities: Build software services and reliable APIs. Qualifications: Relevant experience.',
        requirements='', location='Remote worldwide', country='Worldwide',
        salary_min=90000, salary_max=110000, salary_currency='USD',
        raw_data={}, job_type='full-time', experience_level='mid',
        posted_at=timezone.now(), scout_score=60)
    return SimpleNamespace(**{**values, **overrides})


@override_settings(SCOUT_RULE_RECOVERY_ENABLED=True)
class SourceRecoveryTests(SimpleTestCase):
    def setUp(self):
        self.prefs = {'intent_text':'Remote software engineering in Nepal',
                      'titles':['software engineer'], 'locations':['Nepal']}

    def test_role_and_actual_duties_support_only_a_caveated_possibility(self):
        item = job(); diag = {}
        self.assertEqual(recover([item], self.prefs, [], diag), [item])
        self.assertIn('Build software services', item.scout_reasons[0])
        self.assertIn('source_review_pending', item.scout_caveats)
        self.assertTrue(item.scout_needs_confirmation)
        self.assertEqual(tier_for_job(item), 'consider')

    def test_company_mission_qualifications_and_skill_mentions_are_not_role_evidence(self):
        for text in ['Our company builds software services. Join our mission.',
                     'Experience building software services is required.',
                     'Responsibilities: Recruit software engineers. Qualifications: Build software services.',
                     'Responsibilities: Collaborate with engineers to build software services.',
                     'Responsibilities: Support the software team. Qualifications: software experience.']:
            self.assertIsNone(duty_evidence(job(description=text), self.prefs), text)
        self.assertIsNone(duty_evidence(job(title='Technical Recruiter'), self.prefs))

    def test_background_cannot_become_an_invented_target_profession(self):
        prefs = {'intent_text':'I have software engineering experience. Help me find a new role.'}
        self.assertIsNone(duty_evidence(job(), prefs))
        self.assertIsNone(duty_evidence(job(), {'intent_text':'Remote impact jobs that fit my skills'}))

    def test_explicit_function_only_brief_can_be_recovered_without_role_title_parser(self):
        prefs = {'intent_text':'Media, marketing, or communications position', 'titles':[]}
        item = job(title='Communications Manager', description='Responsibilities: Develop communication campaigns and manage media relations.')
        self.assertIsNotNone(duty_evidence(item, prefs))

    def test_specialized_function_is_preserved(self):
        prefs = {'intent_text':'Data engineering roles', 'titles':['data engineer']}
        self.assertIsNone(duty_evidence(job(title='Data Analyst', description='Responsibilities: Analyze data and create dashboards.'), prefs))
        item = job(title='Data Engineer', description='Responsibilities: Build data pipelines using Python.')
        self.assertIsNotNone(duty_evidence(item, prefs))

    def test_late_role_section_is_found_after_long_company_intro(self):
        item = job(description='Our company is hiring. '*150+'Responsibilities: Build software services and reliable APIs. Qualifications: experience.')
        self.assertIsNotNone(duty_evidence(item, self.prefs))

    def test_known_conflicts_and_explicit_model_rejections_stay_excluded(self):
        for overrides in [{'location':'Remote US only','country':'USA'},
                          {'job_type':'internship'}, {'salary_max':50000,'salary_min':40000},
                          {'requirements':'Must work from our office.'}]:
            prefs = {**self.prefs, 'job_types':['full-time'], 'salary_requirement':{'amount':'70000','currency':'USD','period':'year'}}
            self.assertEqual(recover([job(**overrides)], prefs, [], {}), [], overrides)
        self.assertEqual(recover([job()], self.prefs, [], {}, rejected_ids=[1]), [])
        item = job(); item.scout_remote_policy = {'label':'incompatible'}
        self.assertEqual(recover([item], self.prefs, [], {}), [])

    def test_unknown_salary_and_location_are_visible(self):
        item = job(location='Remote',country='',salary_min=None,salary_max=None)
        prefs = {**self.prefs, 'require_verified_location':True,
                 'salary_requirement':{'amount':'70000','currency':'USD','period':'year'}}
        self.assertEqual(recover([item], prefs, [], {}), [item])
        self.assertIn('salary_unlisted', item.scout_caveats)
        self.assertIn('location_unconfirmed', item.scout_caveats)

    def test_starting_salary_without_currency_is_preserved_and_caveated(self):
        from jobs.services.scout_constraints import extract_constraints
        prefs=extract_constraints('Media, marketing, or communications position starting at 70k')
        self.assertEqual(prefs['salary_requirement']['amount'],'70000')
        self.assertIsNone(prefs['salary_requirement']['currency'])
        self.assertIn('salary_currency',prefs['unresolved_constraints'])
        item=job(title='Communications Manager')
        self.assertFalse(evaluate_job(item,prefs).allowed)
        result=evaluate_job(item,prefs,relaxed=True)
        self.assertTrue(result.allowed)
        self.assertIn('salary_currency',result.caveats)

    def test_reposts_and_selected_requisitions_are_not_added_twice(self):
        first = job()
        duplicate = job(pk=2)
        self.assertEqual(recover([duplicate], self.prefs, [first], {}), [])
        first.raw_data={'api_response':{'requisition_id':'r1'}}
        duplicate.raw_data=first.raw_data
        duplicate.description += ' Regional posting.'
        self.assertEqual(recover([duplicate], self.prefs, [first], {}), [])

    @override_settings(SCOUT_RULE_RECOVERY_ENABLED=False)
    def test_recovery_can_be_disabled(self):
        self.assertEqual(recover([job()],self.prefs,[],{}),[])


@override_settings(DEEPSEEK_API_KEY='test',DEEPSEEK_MODEL='test')
class PreparationBudgetTests(SimpleTestCase):
    @patch('jobs.services.scout_workflow.monotonic',return_value=0)
    @patch('jobs.services.ai.AIClient')
    def test_preparation_has_three_seconds_total_and_preserves_review_budget(self, client, clock):
        with bounded_workflow({}):
            generate('parse',stage='parse',timeout=2.5)
            self.assertEqual(client.return_value.generate.call_args.kwargs['timeout'],2.5)
            clock.return_value=2.8
            with self.assertRaises(TimeoutError):generate('plan',stage='plan')
            generate('review',stage='review',timeout=10)
            self.assertEqual(client.return_value.generate.call_args.kwargs['timeout'],10)
        self.assertEqual(client.return_value.generate.call_count,2)

    @patch('jobs.services.ai.AIClient')
    def test_parse_outage_skips_planning_but_still_allows_review(self, client):
        client.return_value.generate.side_effect=[TimeoutError(), 'review result']
        diag={}
        with bounded_workflow(diag):
            with self.assertRaises(TimeoutError):generate('parse',stage='parse')
            self.assertEqual(plan_terms({'intent_text':'Software engineering'},diag),[])
            self.assertEqual(generate('review',stage='review'),'review result')
        self.assertEqual(client.return_value.generate.call_count,2)
        self.assertTrue(diag['preparation_failed'])
        self.assertEqual(diag['planning'],'preparation_failed')


@override_settings(DEEPSEEK_API_KEY='test',DEEPSEEK_MODEL='test',SCOUT_RERANK_ENABLED=True,
                   SCOUT_REMOTE_POLICY_ENABLED=True,SCOUT_RULE_RECOVERY_ENABLED=True)
class RecoveryPipelineTests(TestCase):
    def setUp(self):cache.clear()

    @patch('jobs.services.scout_workflow.plan_terms',return_value=[])
    @patch('jobs.services.scout_close_matches.close_matches',return_value=[])
    @patch('jobs.services.scout_retrieval.retrieve')
    @patch('jobs.services.scout_reranker.rerank')
    @patch('jobs.services.job_scout_service.JobScoutService._rank_candidates')
    def test_refills_after_policy_rejection_without_another_api_call(self, rank, rerank, retrieve, close, plan):
        from jobs.services.job_scout_service import JobScoutService
        blocked = job(description='Responsibilities: Build software services. Applicants must reside in United States.')
        allowed = job(pk=2,organization_id=2)
        retrieve.return_value=[blocked,allowed]
        rank.return_value=([blocked,allowed],[blocked])
        rerank.return_value=[blocked]
        diag={}
        with patch('jobs.services.scout_workflow.generate') as api:
            result=JobScoutService.get_matches_for_preferences({'intent_text':'Remote software engineering from Nepal',
                'titles':['software engineer'],'locations':['Nepal']},limit=3,diagnostics=diag)
        self.assertEqual(result,[allowed])
        self.assertEqual(diag['source_rule_recovery'],1)
        self.assertEqual(tier_for_job(allowed),'consider')
        api.assert_not_called()

    @patch('jobs.services.ai.AIClient.generate',side_effect=TimeoutError)
    def test_complete_provider_outage_still_returns_grounded_possibilities(self, api):
        from jobs.models import Job,Organization
        from jobs.services.job_scout_service import JobScoutService
        org=Organization.objects.create(name='Software',slug='recovery-software')
        Job.objects.create(title='Software Engineer',organization=org,
            description='Responsibilities: Build software services using Python. Qualifications: experience.',
            location='Remote worldwide',country='Worldwide',job_type='full-time',posted_at=timezone.now())
        diag={}
        with patch('jobs.services.scout_close_matches.close_matches',
                   side_effect=AssertionError('A grounded preview does not need alternative review')):
            prefs,result=JobScoutService.search_intent('Remote software engineering roles using Python from Nepal',limit=3,diagnostics=diag)
        self.assertEqual(len(result),1)
        self.assertEqual(tier_for_job(result[0]),'consider')
        self.assertEqual(diag['source_rule_recovery'],1)
        self.assertTrue(diag['fast_preview_result'])
        self.assertLessEqual(api.call_count,2)

    @patch('jobs.services.ai.AIClient.generate',return_value='not json')
    def test_invalid_interpretation_does_not_trigger_more_planning(self, api):
        from jobs.services.job_scout_service import JobScoutService
        diag={}
        with bounded_workflow(diag):
            prefs=JobScoutService.parse_intent('Software engineering roles in Nepal')
            self.assertEqual(plan_terms(prefs,diag),[])
        self.assertEqual(api.call_count,1)
        self.assertTrue(diag['preparation_failed'])
