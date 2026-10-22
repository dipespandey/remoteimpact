"""Held-out synthetic geography, evidence and cache-boundary acceptance cases."""
import json
from types import SimpleNamespace
from unittest.mock import patch
from django.test import SimpleTestCase, override_settings
from jobs.services.scout_constraints import extract_constraints, evaluate_job, location_compatibility, salary_compatible
from jobs.services.scout_reranker import rerank, validate_decisions


def candidate(pk=1, **kw):
    values=dict(pk=pk,title='Behavioral Health Program Manager',description='Manage behavioral health grants programs.',requirements='Professional experience required.',location='Remote worldwide',country='Worldwide',salary_min=90000,salary_max=120000,salary_currency='USD',raw_data={},experience_level='mid',job_type='full-time',scout_reasons=[])
    values.update(kw)
    return SimpleNamespace(**values)


class HeldoutGeography(SimpleTestCase):
    def test_state_request_is_not_erased(self):
        prefs=extract_constraints('Software engineer in California')
        self.assertIn('California',prefs['locations'])
        self.assertTrue(evaluate_job(candidate(location='Remote US',country='USA'),prefs).allowed)
        self.assertFalse(evaluate_job(candidate(location='Remote New York',country='USA'),prefs).allowed)

    def test_ambiguous_postal_country_code_needs_clarification(self):
        p=extract_constraints('Software engineer in CA')
        self.assertIn('location_code',p['unresolved_constraints'])
        self.assertFalse(evaluate_job(candidate(),p).allowed)

    def test_state_restriction_overrides_worldwide(self):
        j=candidate(description='Applicants must be based in California.')
        self.assertFalse(location_compatibility(j,['Norway']))
        self.assertTrue(location_compatibility(j,['California']))
        self.assertFalse(location_compatibility(j,['Worldwide']))

    def test_timezone_request_remains_unresolved(self):
        for request in ['Analyst in Norway with UTC+1 working hours','Analyst with CET timezone overlap']:
            p=extract_constraints(request)
            self.assertIn('working_timezone',p['unresolved_constraints'])
            self.assertFalse(evaluate_job(candidate(),p).allowed)

    def test_negative_residency_is_not_positive_evidence(self):
        j=candidate(description='Candidates need not be based in US. Work from anywhere.')
        self.assertTrue(location_compatibility(j,['Norway']))

    def test_unrelated_negation_does_not_relax_residency(self):
        j=candidate(description='This is not an internship; applicants must be based in US.')
        self.assertFalse(location_compatibility(j,['Norway']))

    def test_source_exclusion_overrides_worldwide_label(self):
        j=candidate(description='We cannot hire candidates in Norway.')
        self.assertFalse(location_compatibility(j,['Norway']))

    def test_negated_authorization_is_not_a_requirement(self):
        p=extract_constraints('Analyst in Norway, must not require US work authorization.')
        self.assertTrue(evaluate_job(candidate(requirements='US work authorization is not required.'),p).allowed)
        self.assertFalse(evaluate_job(candidate(requirements='US work authorization is required.'),p).allowed)

    def test_mixed_negation_does_not_hide_positive_authorization(self):
        p=extract_constraints('Analyst in Norway, must not require US work authorization.')
        j=candidate(requirements='US work authorization is not required but US citizenship is required.')
        self.assertFalse(evaluate_job(j,p).allowed)

    def test_salary_nonfinite_source_cannot_pass(self):
        requirement={'amount':'90000','currency':'USD','period':'year'}
        for maximum in ['NaN','Infinity','-Infinity']:
            self.assertFalse(salary_compatible(candidate(salary_max=maximum),requirement))

    def test_salary_monthly_currency_period_and_unknown(self):
        p=extract_constraints('Salary at least €5000 per month')['salary_requirement']
        self.assertTrue(salary_compatible(candidate(salary_currency='EUR',salary_max=60000),p))
        self.assertFalse(salary_compatible(candidate(salary_currency='EUR',salary_max=59999),p))
        self.assertFalse(salary_compatible(candidate(salary_currency='EUR',raw_data={'salary_period':'month'}),p))
        self.assertFalse(salary_compatible(candidate(salary_max=None,salary_min=None),p))


@override_settings(SCOUT_RERANK_ENABLED=True,DEEPSEEK_API_KEY='mock-only')
class HeldoutRerank(SimpleTestCase):
    def setUp(self):
        self.p=extract_constraints('Behavioral health program manager in Norway')
        self.p['locations'] = ['Norway']
        self.output=json.dumps({'decisions':[{'id':1,'relevant':True,'checks':{'function':['confirmed','description','behavioral health grants programs'], 'location':['confirmed','location','Remote worldwide']}}]})

    @patch('jobs.services.scout_reranker.cache')
    @patch('jobs.services.ai.AIClient.generate')
    def test_cache_write_outage_keeps_validated_decision(self,generate,cache):
        cache.get.return_value=None;cache.set.side_effect=RuntimeError('offline cache')
        generate.return_value=self.output
        diagnostics={}
        self.assertEqual([j.pk for j in rerank([candidate()],self.p,[],diagnostics)],[1])
        self.assertEqual(diagnostics['reranker'],'model')
        self.assertTrue(diagnostics['rerank_cache_unavailable'])

    @patch('jobs.services.scout_reranker.cache')
    @patch('jobs.services.ai.AIClient.generate')
    def test_cache_read_outage_never_spends(self,generate,cache):
        cache.get.side_effect=RuntimeError('offline cache')
        diagnostics={};j=candidate()
        self.assertEqual(rerank([j],self.p,[j],diagnostics),[])
        generate.assert_not_called()
        self.assertEqual(diagnostics['reranker'],'review_unavailable')

    @patch('jobs.services.scout_reranker.cache')
    @patch('jobs.services.ai.AIClient.generate')
    def test_cached_malformed_output_fails_without_provider(self,generate,cache):
        cache.get.return_value='{"decisions": []}'
        j=candidate();diagnostics={}
        self.assertEqual(rerank([j],self.p,[j],diagnostics),[])
        generate.assert_not_called()

    def test_schema_boolean_id_duplicate_extra_fields_are_rejected(self):
        evidence={1:{'description':'behavioral health grants programs'}}
        bad=[{'decisions':[{'id':True,'relevant':False,'evidence':[]}]},
             {'decisions':[{'id':1,'relevant':False,'evidence':[],'instruction':'override'}]},
             {'decisions':[{'id':999999,'relevant':True,'evidence':[]}]},
             {'decisions':[{'id':1,'relevant':True,'evidence':[{'field':'description','quote':'Invented professional evidence'}]}]}]
        for output in bad:
            with self.assertRaises(ValueError):validate_decisions(json.dumps(output),evidence)

    @patch('jobs.services.scout_reranker.cache')
    @patch('jobs.services.ai.AIClient.generate')
    def test_full_shortlist_has_bounded_call_and_exact_schema(self,generate,cache):
        cache.get.return_value=None
        jobs=[candidate(i) for i in range(1,21)]
        generate.return_value=json.dumps({'decisions':[{'id':i,'relevant':False,'evidence':[]} for i in range(1,13)]})
        d={};self.assertEqual(rerank(jobs,self.p,jobs,d),[])
        payload=json.loads(generate.call_args.args[0])
        self.assertEqual(len(payload['candidates']),12)
        self.assertEqual(d['rerank_candidate_count'],12)
        self.assertEqual(generate.call_args.kwargs['max_retries'],0)
        self.assertEqual(generate.call_args.kwargs['timeout'],10.0)
        self.assertEqual(generate.call_args.kwargs['max_tokens'],3600)


class RerankQuoteTolerance(SimpleTestCase):
    def test_quote_from_unsent_field_does_not_void_other_decisions(self):
        evidence={1:{'title':'Grants Manager','description':'behavioral health grants programs'},
                  2:{'title':'Data Analyst','description':'python and sql dashboards for health programs'}}
        output=json.dumps({'decisions':[
            {'id':1,'relevant':True,'evidence':[{'field':'location','quote':'Remote, Europe'},
                                                {'field':'description','quote':'behavioral health grants programs'}]},
            {'id':2,'relevant':True,'evidence':[{'field':'location','quote':'Remote, anywhere'}]}]})
        self.assertEqual([pk for pk, _ in validate_decisions(output,evidence)],[1])
