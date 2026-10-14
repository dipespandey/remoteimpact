from types import SimpleNamespace
from unittest.mock import patch
from django.test import SimpleTestCase,TestCase,override_settings
from jobs.services.scout_constraints import plain_job_field
from jobs.services.scout_review import primary_duties,dominant_function_conflict,review_checks
from jobs.services.job_scout_service import JobScoutService as Scout
from jobs.models import Job,Organization


class PrimaryWorkEvidenceTests(SimpleTestCase):
    def test_html_headings_do_not_merge_into_following_words(self):
        job=SimpleNamespace(description='<p><b>Position Overview</b></p><p>Conduct policy analysis and project delivery.</p><p>Key Responsibilities</p><p>Build reports.</p>',requirements='')
        text=plain_job_field(job,'description')
        self.assertIn('Position Overview Conduct',text)
        self.assertEqual(primary_duties(text).strip(),'Conduct policy analysis and project delivery.')

    def test_weighted_major_duties_override_minor_crm_responsibility(self):
        text='Coordinate financial operations and grants. Financial Management (50%) Pay bills. Grant Administration (30%) Manage grants. Operations (20%) Maintain CRM.'
        overview=primary_duties(text)
        self.assertTrue(dominant_function_conflict(overview,{'intent_text':'COO, CRM or RevOps roles'}))
        self.assertFalse(dominant_function_conflict(overview,{'intent_text':'Accounting or CFO roles'}))
        self.assertFalse(dominant_function_conflict(overview,{'intent_text':'COO roles'},'Chief Operating Officer'))
        self.assertFalse(dominant_function_conflict('Use financial data in CRM reports.',{'intent_text':'CRM roles'}))

    def test_qualification_cannot_stand_in_for_function_duties(self):
        quote='Experience with Python and SQL is useful'
        self.assertIsNone(review_checks({'checks':{'function':['confirmed','requirements',quote]}},{'requirements':quote},{}))
        quote='Build Python and SQL services'
        self.assertEqual(review_checks({'checks':{'function':['confirmed','description',quote]}},{'description':quote},{}),[])

    def test_grounded_quote_overrun_is_preserved_but_unbounded_quote_is_rejected(self):
        quote='Conduct policy analysis and project delivery. '+('Develop evidence for policy decisions. '*5)
        self.assertGreater(len(quote),180)
        item={'checks':{'function':['confirmed','description',quote]}}
        self.assertEqual(review_checks(item,{'description':quote},{}),[])
        item['checks']['function'][2]=quote+'x'*400
        self.assertIsNone(review_checks(item,{'description':item['checks']['function'][2]},{}))

    def test_close_match_explanation_cannot_invent_requested_geography(self):
        import json
        from jobs.services.scout_close_matches import validate
        quote='Design substation systems remotely.'
        pick={'id':1,'same_kind_of_work':True,'difference':'Based in Mexico rather than a US/Europe remote position','evidence':{'field':'description','quote':quote},'checks':{'function':['confirmed','description',quote]}}
        evidence={1:{'description':quote}}
        self.assertEqual(validate(json.dumps({'picks':[pick]}),evidence,3,{'intent_text':'Substation engineering'}),[])
        pick['difference']='Substation design with an emphasis on grid integration'
        self.assertEqual(len(validate(json.dumps({'picks':[pick]}),evidence,3,{'intent_text':'Substation engineering'})),1)

    def test_worldwide_unknown_is_caveated_not_a_confirmed_conflict(self):
        from jobs.services.scout_constraints import location_compatibility,evaluate_job
        values=dict(title='Software Engineer',description='Build software.',requirements='',location='Remote',country='',raw_data={},job_type='full-time',experience_level='',salary_min=None,salary_max=None,salary_currency='')
        job=SimpleNamespace(**values)
        self.assertIsNone(location_compatibility(job,['Worldwide']))
        prefs={'locations':['Worldwide'],'require_verified_location':True}
        self.assertFalse(evaluate_job(job,prefs).allowed)
        self.assertIn('location_unconfirmed',evaluate_job(job,prefs,True).caveats)
        self.assertFalse(location_compatibility(SimpleNamespace(**{**values,'location':'Remote US','country':'USA'}),['Worldwide']))
        self.assertTrue(location_compatibility(SimpleNamespace(**{**values,'location':'Remote worldwide','country':'Worldwide'}),['Worldwide']))

    def test_revops_alias_is_prioritized_before_generic_operations(self):
        terms=Scout._search_terms({'intent_text':'COO, CRM, RevOps or data entry jobs','include_keywords':['operations']})
        self.assertEqual(terms[0],'revenue operations')
        self.assertIn('customer relationship management',terms[:6])
        self.assertFalse(dominant_function_conflict('Financial benefits (100%) paid by employer.',{'intent_text':'Software engineer'}))

    def test_aliases_match_both_abbreviated_and_expanded_job_titles(self):
        self.assertTrue(Scout._title_matches_intent('Sr Director of Revenue Operations',['RevOps']))
        self.assertTrue(Scout._title_matches_intent('CRM Administrator',['CRM']))
        self.assertTrue(Scout._title_matches_intent('Chief Operating Officer',['COO']))
        self.assertEqual(Scout._normalize_desired_titles(['RevOps','CRM Administrator']),['revenue operations','customer relationship management Administrator'])

    def test_incidental_phrase_the_role_is_not_an_overview_heading(self):
        text='This job coordinates community programmes. Benefits include resources consistent with the role and salary.'
        self.assertTrue(primary_duties(text).startswith('This job coordinates'))


@override_settings(SCOUT_RERANK_ENABLED=True,SCOUT_RERANK_BACKEND='deepseek',DEEPSEEK_API_KEY='test',SCOUT_MULTISTEP_ENABLED=False)
class SharedCandidatePoolTests(TestCase):
    def test_single_retrieval_preserves_strict_and_caveated_candidates(self):
        org=Organization.objects.create(name='Shared pool')
        known=Job.objects.create(title='Software Engineer',slug='shared-known',organization=org,description='Build Python services.',location='Remote worldwide',country='Worldwide')
        unknown=Job.objects.create(title='Software Engineer',slug='shared-unknown',organization=org,description='Build Python services.',location='Remote',country='')
        prefs={'intent_text':'Software engineer in Norway','titles':['Software Engineer'],'skills':['Python'],'locations':['Norway'],'require_verified_location':True,'_scout_current_inventory':True}
        with patch('jobs.services.scout_retrieval.retrieve',return_value=[known,unknown]) as retrieve,patch('jobs.services.scout_reranker.rerank',return_value=[]),patch('jobs.services.scout_close_matches.close_matches',return_value=[]):
            d={};Scout.get_matches_for_preferences(prefs,diagnostics=d,limit=3)
        self.assertEqual(retrieve.call_count,1)
        self.assertEqual(retrieve.call_args.kwargs['candidate_limit'],400)
        self.assertTrue(d['shared_candidate_pool'])
        self.assertGreater(d['provisional']['candidate_count'],0)

    def test_canonical_revops_title_outranks_generic_operations_without_changing_cfo_score(self):
        from django.utils import timezone
        org=Organization.objects.create(name='Canonical title score')
        def candidate(title,slug):
            return Job.objects.create(title=title,slug=slug,organization=org,description='Own CRM reporting systems.',location='Remote',salary_min=90000,salary_max=100000,salary_currency='USD')
        revops=candidate('Sr Director of Revenue Operations','canonical-revops')
        generic=candidate('Senior Operations Manager','canonical-operations')
        prefs={'titles':['RevOps']}
        high,_=Scout._score_job(revops,prefs,timezone.now())
        low,_=Scout._score_job(generic,prefs,timezone.now())
        self.assertGreater(high,low)
        cfo=candidate('CFO','canonical-cfo')
        _,reasons=Scout._score_job(cfo,{'titles':['CFO']},timezone.now())
        self.assertIn('Role title closely matches what you described',reasons)
