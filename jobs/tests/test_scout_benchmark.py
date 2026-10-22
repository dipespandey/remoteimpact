import json
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch, MagicMock
from django.test import TestCase, SimpleTestCase, override_settings
from django.core.cache import cache
from jobs.models import Job, Organization
from jobs.services.job_scout_service import JobScoutService as Scout
from jobs.services.scout_evidence import role_conflict, location_evidence_warning
from jobs.services.scout_review import review_checks
from jobs.services.scout_retrieval import retrieve
from jobs.management.commands.scout_benchmark import load_queries


@override_settings(DEEPSEEK_API_KEY='test',DEEPSEEK_MODEL='test')
class InterpretationCacheTests(TestCase):
    def setUp(self):cache.clear()

    @patch('jobs.services.scout_workflow.generate', return_value='{"titles":["Software Engineer"]}')
    def test_repeat_reuses_validated_interpretation_but_regrounds_original_request(self, provider):
        brief='Remote software engineer in Norway, no internships.'
        first=Scout.parse_intent(brief);second=Scout.parse_intent(brief)
        self.assertEqual(first,second)
        self.assertIn('internship',second['exclude_keywords'])
        self.assertEqual(provider.call_count,1)
        with override_settings(DEEPSEEK_MODEL='changed'):
            Scout.parse_intent(brief)
        self.assertEqual(provider.call_count,2)

    @patch('jobs.services.scout_workflow.generate', side_effect=['invalid json','{"titles":["Software Engineer"]}'])
    def test_provider_failure_is_not_cached(self, provider):
        Scout.parse_intent('Remote software engineer in Norway')
        Scout.parse_intent('Remote software engineer in Norway')
        self.assertEqual(provider.call_count,2)

    @patch('jobs.services.job_scout_service.cache')
    @patch('jobs.services.scout_workflow.generate', return_value='{"titles":["Software Engineer"]}')
    def test_cache_outage_does_not_disable_interpretation(self, provider, broken):
        broken.get.side_effect=RuntimeError();broken.set.side_effect=RuntimeError()
        self.assertIn('Software Engineer',Scout.parse_intent('Remote software engineer')['titles'])
        self.assertEqual(provider.call_count,1)


@override_settings(SCOUT_SEMANTIC_ENABLED=False)
class StreamingRetrievalTests(TestCase):
    def test_stops_materializing_rows_at_eligible_limit(self):
        org=Organization.objects.create(name='Streaming test')
        Job.objects.bulk_create([Job(title='Software Engineer',slug=f'stream-{i}',organization=org,
            description='Build useful software.',location='Remote worldwide',country='Worldwide') for i in range(450)])
        diagnostics={}
        with patch.object(Job,'from_db',wraps=Job.from_db) as loaded:
            result=retrieve(Job.objects.select_related('organization','category'),{},[],diagnostics)
        self.assertEqual(len(result),200)
        self.assertEqual(loaded.call_count,200)
        self.assertEqual(diagnostics['retrieval']['lexical']['scanned'],200)


class BroadQualityTests(SimpleTestCase):
    def test_disjunctive_marketing_and_communications_keeps_both_functions(self):
        prefs={'intent_text':'Senior marketing or communications leadership roles'}
        for title in ['Director of Marketing','Director of Communications']:
            self.assertFalse(role_conflict(SimpleNamespace(title=title),prefs,''))
        self.assertTrue(role_conflict(SimpleNamespace(title='Software Engineer'),prefs,''))

    def test_partner_operations_does_not_accept_sales_or_fundraising(self):
        prefs={'intent_text':'Partner operations roles building onboarding workflows'}
        for quote in ['Build and maintain relationships with grantmakers and collaborators',
                      'Experience structuring integration or reseller partnerships']:
            self.assertIsNone(review_checks({'checks':{'function':['confirmed','description',quote]}},{'description':quote},prefs))
        quote='Build onboarding workflows and operational toolkits for partners'
        self.assertEqual(review_checks({'checks':{'function':['confirmed','description',quote]}},{'description':quote},prefs),[])

    def test_us_abbreviation_keeps_residence_warning_without_applicant_location(self):
        job=SimpleNamespace(location='Remote',raw_data={})
        for text in ['This position may be fully remote from an approved U.S. location.',
                     'This position is remote within the United States.']:
            self.assertIn('United States',location_evidence_warning(job,{},text))

    def test_benchmark_refuses_duplicate_queries_and_preserves_holdout_split(self):
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'queries.json'
            rows=[{'id':'d1','split':'development','brief':'Remote engineering'},
                  {'id':'h1','split':'holdout','brief':'Remote grant writing'}]
            path.write_text(json.dumps(rows))
            self.assertEqual([r['id']for r in load_queries(path,'holdout')],['h1'])
            path.write_text(json.dumps(rows+[rows[0]]))
            with self.assertRaises(ValueError):load_queries(path)


@override_settings(SCOUT_RETRIEVAL_CACHE_ENABLED=True,SCOUT_SEMANTIC_ENABLED=False)
class CandidateCacheTests(TestCase):
    def setUp(self):cache.clear()

    def test_cached_ids_recheck_edited_eligibility_and_current_availability(self):
        org=Organization.objects.create(name='Cache isolation fixture')
        j=Job.objects.create(title='Software Engineer',slug='cache-eligible',organization=org,
                             location='Remote worldwide',country='Worldwide')
        prefs={'_scout_preview_cache':True,'locations':['Norway']}
        qs=Job.objects.filter(is_active=True).select_related('organization','category')
        self.assertEqual([x.pk for x in retrieve(qs,prefs,[],{})],[j.pk])
        Job.objects.filter(pk=j.pk).update(location='Remote US',country='USA')
        d={}
        self.assertEqual(retrieve(qs,prefs,[],d),[])
        self.assertEqual(d['retrieval_cache'],'hit')
        Job.objects.filter(pk=j.pk).update(location='Remote worldwide',country='Worldwide',is_active=False)
        self.assertEqual(retrieve(qs,prefs,[],{}),[])

    def test_postgres_cache_hit_skips_embedding_before_reloading_current_rows(self):
        qs = MagicMock()
        qs.annotate.return_value = qs
        qs.defer.return_value = qs
        qs.query.sql_with_params.return_value = ('SELECT current_jobs', ())
        qs.filter.return_value = []
        diagnostics = {}
        with patch('jobs.services.scout_retrieval.connection', SimpleNamespace(vendor='postgresql')), \
             patch('jobs.services.scout_retrieval.query_embedding') as embedding, \
             patch('jobs.services.scout_retrieval.cache.get', return_value={'ids':[123], 'semantic_used':True}):
            self.assertEqual(retrieve(qs, {'_scout_preview_cache':True}, [], diagnostics), [])
        embedding.assert_not_called()
        qs.filter.assert_called_once_with(pk__in=[123])
        self.assertEqual(diagnostics['retrieval_cache'], 'hit')
        self.assertEqual(diagnostics['query_embedding_seconds'], 0.0)

    def test_saved_delivery_queries_do_not_use_preview_cache(self):
        org=Organization.objects.create(name='Saved query fixture')
        Job.objects.create(title='Software Engineer',slug='saved-query',organization=org)
        with patch('jobs.services.scout_retrieval.cache') as storage:
            retrieve(Job.objects.select_related('organization','category'),{},[],{})
        storage.get.assert_not_called();storage.set.assert_not_called()


class CompactReviewTests(SimpleTestCase):
    def test_compact_decisions_keep_source_grounding_and_complete_candidate_coverage(self):
        from jobs.services.scout_reranker import validate_decisions
        row={1:{'description':'Build Python services for our platform.'},2:{'description':'Sell services to clients.'}}
        selected={'id':1,'relevant':True,'checks':{'function':['confirmed','description','Build Python services for our platform.']}}
        output=json.dumps({'decisions':[selected,{'id':2,'relevant':False}]})
        self.assertEqual(validate_decisions(output,row,{}),[(1,[{'field':'description','quote':'Build Python services for our platform.'}])])
        selected['checks']['function'][2]='Invented responsibilities with no source evidence'
        self.assertEqual(validate_decisions(json.dumps({'decisions':[selected,{'id':2,'relevant':False}]}),row,{}),[])
        with self.assertRaises(ValueError):
            validate_decisions(json.dumps({'decisions':[{'id':2,'relevant':False}]}),row,{})


class EvaluationSummaryTests(SimpleTestCase):
    def test_does_not_count_unreviewed_results_as_relevant_or_empty_sets_as_perfect_stability(self):
        from ml.scout_reranker.live_evaluation import summarize
        rows=[{'id':'a','seconds':2,'matches':[{'id':1},{'id':2}]},
              {'id':'b','seconds':4,'matches':[]},
              {'id':'b','run':1,'seconds':1,'matches':[]}]
        result=summarize(rows,[{'query_id':'a','job_id':1,'relevant':True}])
        self.assertEqual(result['cold']['source_reviewed_pairs'],1)
        self.assertEqual(result['cold']['returned_pairs'],2)
        self.assertEqual(result['cold']['median_seconds'],3)
        self.assertIsNone(result['repeat_stability'][0]['jaccard'])


class DiscoveredHoldoutRegressions(SimpleTestCase):
    def test_currency_is_not_applicant_geography(self):
        from jobs.services.scout_constraints import location_requests
        self.assertEqual(location_requests('Applicants in Pakistan, paid in US dollars'),['pakistan'])
        self.assertIn('us',location_requests('Remote roles in US, paid in dollars'))

    def test_title_workplace_restriction_beats_generic_remote_label(self):
        from jobs.services.scout_constraints import location_compatibility
        for title in ['Software Engineer (Hub-Remote: DC or Philly Metro)',
                      'Substation Engineer - Remote (U.S.)']:
            job=SimpleNamespace(title=title,location='Remote',country='',description='',requirements='',raw_data={})
            self.assertFalse(location_compatibility(job,['Nepal']))
            self.assertTrue(location_compatibility(job,['USA']))

    def test_paid_request_rejects_explicit_volunteer_role_in_both_tiers(self):
        from jobs.services.scout_constraints import evaluate_job
        for title in ['Social Media Manager (Volunteer)','Volunteer, Social Media Assistant']:
            job=SimpleNamespace(title=title,location='Remote',country='',description='Create and schedule posts.',requirements='',raw_data={},job_type='part-time',experience_level='')
            for relaxed in [False,True]:
                self.assertEqual(evaluate_job(job,{'intent_text':'Paid social media roles in US dollars'},relaxed).reason,'salary')
        job._scout_plain_fields={};job.title='Volunteer Coordinator';job.salary_min=50000;job.salary_max=60000;job.salary_currency='USD'
        self.assertTrue(evaluate_job(job,{'intent_text':'Paid volunteer coordinator roles'},True).allowed)

    def test_guaranteed_salary_does_not_allow_undisclosed_pay_as_an_alternative(self):
        from jobs.services.scout_constraints import evaluate_job
        job=SimpleNamespace(title='Policy Analyst',location='Remote',country='',description='Analyze climate policy.',requirements='',raw_data={},job_type='full-time',experience_level='',salary_min=None,salary_max=None,salary_currency='USD')
        prefs={'intent_text':'Climate policy with guaranteed minimum salary USD 1000000','salary_requirement':{'amount':'1000000','currency':'USD','period':'year','mode':'floor'}}
        self.assertEqual(evaluate_job(job,prefs,True).reason,'salary')
        prefs['intent_text']='Climate policy salary USD 1000000'
        self.assertTrue(evaluate_job(job,prefs,True).allowed)

    def test_currency_only_pay_request_preserves_unknown_and_rejects_known_conflict(self):
        from jobs.services.scout_constraints import evaluate_job
        job=SimpleNamespace(title='Social Media Assistant',location='Remote',country='',description='Create social posts.',requirements='',raw_data={},job_type='part-time',experience_level='',salary_min=None,salary_max=None,salary_currency='USD')
        prefs={'intent_text':'Social media roles paid in US dollars'}
        self.assertIn('paid_currency_unconfirmed',evaluate_job(job,prefs,True).caveats)
        job.salary_min=30000;job.salary_currency='EUR'
        self.assertEqual(evaluate_job(job,prefs,True).reason,'salary')
        job.salary_currency='USD'
        self.assertTrue(evaluate_job(job,prefs,True).allowed)

    def test_work_authorization_geography_is_visible_without_applicant_location(self):
        job=SimpleNamespace(location='Remote',raw_data={})
        warning=location_evidence_warning(job,{},'Teammates need to be authorized to work from their home location (in the US or Canada).')
        self.assertIn('United States',warning);self.assertIn('Canada',warning)


class RetrievalDeadlineTests(TestCase):
    def test_exhausted_request_skips_database_scan_and_marks_incomplete(self):
        from jobs.services.scout_workflow import bounded_workflow
        diagnostics={}
        with patch('jobs.services.scout_workflow.monotonic',side_effect=[0,15]), bounded_workflow(diagnostics), self.assertNumQueries(0):
            self.assertEqual(retrieve(Job.objects.all(),{},[],diagnostics),[])
        self.assertTrue(diagnostics['retrieval_deadline_reached'])

    @override_settings(SCOUT_SEMANTIC_ENABLED=False,SCOUT_RETRIEVAL_CACHE_ENABLED=True)
    def test_partial_scan_is_not_cached_as_complete_candidate_pool(self):
        org=Organization.objects.create(name='Deadline fixture')
        Job.objects.bulk_create([Job(title='Engineer',slug=f'deadline-{i}',organization=org)for i in range(50)])
        d={}
        with patch('jobs.services.scout_workflow.retrieval_time_available',side_effect=[True,True,False]),patch('jobs.services.scout_retrieval.cache') as storage:
            storage.get.return_value=None
            result=retrieve(Job.objects.select_related('organization','category'),{'_scout_preview_cache':True},[],d)
        self.assertEqual(len(result),25)
        self.assertTrue(d['retrieval_deadline_reached'])
        storage.set.assert_not_called()
