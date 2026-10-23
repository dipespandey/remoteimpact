import json
from unittest.mock import patch
from django.core.cache import caches
from django.test import SimpleTestCase,override_settings
from jobs.tests.test_scout_pipeline import job
from jobs.services.scout_reranker import rerank

cache = caches['scout_decisions']


@override_settings(SCOUT_RERANK_ENABLED=True,SCOUT_RERANK_BACKEND='deepseek',DEEPSEEK_API_KEY='test',DEEPSEEK_MODEL='cache-test')
class IndividualReviewCacheTests(SimpleTestCase):
    def setUp(self):
        cache.clear()
        self.prefs={'intent_text':'Software engineering jobs'}
        self.a=job(1,title='Software Engineer',description='Build Python software services.',requirements='')
        self.b=job(2,title='Backend Developer',description='Maintain software integrations.',requirements='')

    def model(self,prompt,**kwargs):
        rows=json.loads(prompt)['candidates']
        return json.dumps({'decisions':[{'id':row['id'],'relevant':True,
            'checks':{'function':['confirmed','description',
                'Build Python software services.' if row['id']==1 else 'Maintain software integrations.']}} for row in rows]})

    def test_changed_pool_reviews_only_the_new_source_candidate(self):
        with patch('jobs.services.scout_workflow.generate',side_effect=self.model) as model:
            self.assertEqual(rerank([self.a],self.prefs,[self.a],{}),[self.a])
            d={};self.assertEqual(rerank([self.a,self.b],self.prefs,[self.a,self.b],d),[self.a,self.b])
        self.assertEqual([row['id'] for row in json.loads(model.call_args.args[0])['candidates']],[2])
        self.assertEqual(d['rerank_cached_decisions'],1)

    def test_failed_new_review_retains_only_previously_grounded_results(self):
        with patch('jobs.services.scout_workflow.generate',side_effect=self.model):
            rerank([self.a],self.prefs,[self.a],{})
        d={}
        with patch('jobs.services.scout_workflow.generate',side_effect=TimeoutError):
            result=rerank([self.a,self.b],self.prefs,[self.a,self.b],d)
        self.assertEqual(result,[self.a])
        self.assertEqual(d['reranker'],'review_partial')
        self.assertEqual(d['review_failure_type'],'TimeoutError')

    def test_source_edit_requires_new_grounding_and_cannot_use_previous_good_decision(self):
        with patch('jobs.services.scout_workflow.generate',side_effect=self.model):
            rerank([self.a],self.prefs,[self.a],{})
        self.a.description='Prepare bookkeeping reports.'
        with patch('jobs.services.scout_workflow.generate',side_effect=TimeoutError) as model:
            self.assertEqual(rerank([self.a],self.prefs,[self.a],{}),[])
        model.assert_called_once()

    def test_changed_request_and_changed_model_do_not_reuse_decisions(self):
        with patch('jobs.services.scout_workflow.generate',side_effect=self.model) as model:
            rerank([self.a],self.prefs,[self.a],{})
            rerank([self.a],{'intent_text':'Software engineering at a nonprofit'},[self.a],{})
            with override_settings(DEEPSEEK_MODEL='different-model'):
                rerank([self.a],self.prefs,[self.a],{})
        self.assertEqual(model.call_count,3)

    def test_prompt_change_and_expiry_require_a_fresh_decision(self):
        from jobs.services.scout_review import CHECK_INSTRUCTIONS
        with override_settings(SCOUT_DECISION_CACHE_TTL=1):
            with patch('jobs.services.scout_workflow.generate',side_effect=self.model) as model:
                rerank([self.a],self.prefs,[self.a],{})
                with patch('jobs.services.scout_review.CHECK_INSTRUCTIONS',CHECK_INSTRUCTIONS+' Recheck changed prompt.'):
                    rerank([self.a],self.prefs,[self.a],{})
                self.assertEqual(model.call_count,2)
                import time
                time.sleep(1.1)
                rerank([self.a],self.prefs,[self.a],{})
        self.assertEqual(model.call_count,3)

    def test_live_country_conflict_is_rechecked_before_cached_decision(self):
        prefs={**self.prefs,'locations':['Norway']}
        def model(prompt,**kwargs):
            return json.dumps({'decisions':[{'id':1,'relevant':True,'checks':{
                'function':['confirmed','description','Build Python software services.'],
                'location':['confirmed','location','Remote worldwide']}}]})
        with patch('jobs.services.scout_workflow.generate',side_effect=model):
            self.assertEqual(rerank([self.a],prefs,[self.a],{}),[self.a])
        self.a.location='Remote United States';self.a.country='USA'
        with patch('jobs.services.scout_workflow.generate') as provider:
            self.assertEqual(rerank([self.a],prefs,[self.a],{}),[])
        provider.assert_not_called()

    def test_cache_outage_uses_fresh_review(self):
        with patch('jobs.services.scout_reranker.cache.get_many',side_effect=RuntimeError), \
             patch('jobs.services.scout_workflow.generate',side_effect=self.model):
            self.assertEqual(rerank([self.a],self.prefs,[self.a],{}),[self.a])

    def test_full_cache_read_outage_uses_fresh_review(self):
        diagnostics={}
        with patch('jobs.services.scout_reranker.cache.get',side_effect=RuntimeError), \
             patch('jobs.services.scout_workflow.generate',side_effect=self.model) as model:
            self.assertEqual(rerank([self.a],self.prefs,[self.a],diagnostics),[])
        model.assert_not_called()
        self.assertTrue(diagnostics['rerank_cache_unavailable'])
        self.assertEqual(diagnostics['reranker'],'review_unavailable')

    def test_synthetic_excerpt_quote_is_never_cached_as_a_valid_positive(self):
        bad='Build Python software services. […] Maintain software integrations.'
        with patch('jobs.services.scout_workflow.generate',return_value=json.dumps({'decisions':[
            {'id':1,'relevant':True,'checks':{'function':['confirmed','description',bad]}}]})):
            self.assertEqual(rerank([self.a],self.prefs,[self.a],{}),[])

    def test_cached_rejection_does_not_become_a_match_when_unknown_review_fails(self):
        rejected=json.dumps({'decisions':[{'id':1,'relevant':False}]})
        with patch('jobs.services.scout_workflow.generate',return_value=rejected):
            self.assertEqual(rerank([self.a],self.prefs,[self.a],{}),[])
        with patch('jobs.services.scout_workflow.generate',side_effect=TimeoutError):
            self.assertEqual(rerank([self.a,self.b],self.prefs,[self.a,self.b],{}),[])

    def test_partial_response_retains_valid_match_and_does_not_cache_missing_job_as_rejected(self):
        response=self.model(json.dumps({'candidates':[{'id':1}]}))
        d={}
        with patch('jobs.services.scout_workflow.generate',return_value=response):
            self.assertEqual(rerank([self.a,self.b],self.prefs,[self.a,self.b],d),[self.a])
        self.assertEqual(d['reranker'],'review_partial')
        self.assertEqual(d['review_missing_decisions'],1)
        with patch('jobs.services.scout_workflow.generate',side_effect=self.model) as provider:
            self.assertEqual(rerank([self.a,self.b],self.prefs,[self.a,self.b],{}),[self.a,self.b])
        self.assertEqual([row['id'] for row in json.loads(provider.call_args.args[0])['candidates']],[2])

    def test_invented_or_duplicate_ids_cannot_be_salvaged(self):
        good=json.loads(self.model(json.dumps({'candidates':[{'id':1}]})))['decisions'][0]
        for bad in [{**good,'id':99},good]:
            with patch('jobs.services.scout_workflow.generate',return_value=json.dumps({'decisions':[good,bad]})):
                self.assertEqual(rerank([self.a,self.b],self.prefs,[self.a,self.b],{}),[])

    def test_unsupported_positive_is_unknown_and_does_not_discard_supported_positive(self):
        good=json.loads(self.model(json.dumps({'candidates':[{'id':1}]})))['decisions'][0]
        bad={'id':2,'relevant':True,'checks':{'function':['confirmed','description','Invented perfect matching job']}}
        d={}
        with patch('jobs.services.scout_workflow.generate',return_value=json.dumps({'decisions':[good,bad]})):
            self.assertEqual(rerank([self.a,self.b],self.prefs,[self.a,self.b],d),[self.a])
        self.assertEqual(d['reranker'],'review_partial')

    def test_markdown_wrapped_json_keeps_all_source_checks(self):
        response='```json\n'+self.model(json.dumps({'candidates':[{'id':1}]}))+'\n```'
        with patch('jobs.services.scout_workflow.generate',return_value=response):
            self.assertEqual(rerank([self.a],self.prefs,[self.a],{}),[self.a])
        self.a.description='Maintain accounting records.'
        with patch('jobs.services.scout_workflow.generate',return_value=response):
            self.assertEqual(rerank([self.a],self.prefs,[self.a],{}),[])

    def test_truncated_json_cannot_promote_an_inner_decision_object(self):
        response=self.model(json.dumps({'candidates':[{'id':1}]}))[:-2]
        with patch('jobs.services.scout_workflow.generate',return_value=response):
            self.assertEqual(rerank([self.a],self.prefs,[self.a],{}),[])
