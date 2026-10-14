import json
from io import StringIO
from types import SimpleNamespace
from unittest.mock import patch

from django.core.management import call_command
from django.test import SimpleTestCase,TestCase,override_settings

from jobs.models import Job,Organization
from jobs.services.scout_constraints import evaluate_job,plain_job_field
from jobs.services.scout_source_profile import build_profile,source_fields,profile_for,duties,function_priority,shortlist,KEY
from jobs.services.scout_workflow import bounded_workflow
from jobs.management.commands.prepare_scout_sources import store_profile


def job(**overrides):
    values=dict(pk=1,organization_id=1,title='Operations Specialist',description='Build onboarding workflows.',
        requirements='',location='Remote worldwide',country='',job_type='full-time',experience_level='',
        salary_min=None,salary_max=None,salary_currency='',raw_data={})
    return SimpleNamespace(**{**values,**overrides})


class QueryConstraintParsingTests(TestCase):
    def test_explicit_tool_list_preserves_all_tools_and_cloud_aliases(self):
        from jobs.services.job_scout_service import JobScoutService as Scout
        prefs=Scout._fallback_parse('Senior data engineer roles using Python, SQL, GCP and dbt. Fully remote from Spain.')
        self.assertEqual(set(prefs['required_skills']),{'python','sql','gcp','dbt'})
        complete=job(title='Data Engineer',description='Build data pipelines using Python, SQL, Google Cloud and dbt.',location='Remote Spain',country='Spain')
        self.assertTrue(evaluate_job(complete,prefs,True).allowed)
        complete.description='Build data pipelines using Python, SQL and AWS.'
        self.assertFalse(evaluate_job(complete,prefs,True).allowed)

    def test_simple_tool_alternatives_require_one_instead_of_the_first_only(self):
        from jobs.services.job_scout_service import JobScoutService as Scout
        prefs=Scout._fallback_parse('Remote software engineering roles using Python or Java.')
        self.assertEqual(prefs['required_skills'],[])
        self.assertEqual(prefs['required_skill_groups'],[['python','java']])
        for tool in ['Python','Java']:
            self.assertTrue(evaluate_job(job(title='Software Engineer',description=f'Build services with {tool}.'),prefs,True).allowed)
        self.assertFalse(evaluate_job(job(title='Software Engineer',description='Build services with Ruby.'),prefs,True).allowed)

    def test_mixed_tool_boolean_expression_requires_clarification(self):
        from jobs.services.job_scout_service import JobScoutService as Scout
        prefs=Scout._fallback_parse('Remote engineering roles using Python or Java and SQL.')
        self.assertIn('skill_expression',prefs['unresolved_constraints'])

    def test_explicit_eligibility_survives_research_context(self):
        from jobs.services.scout_constraints import extract_constraints
        for brief,place in [
            ('Remote UX research roles using qualitative research, contract, open to Europe.','Europe'),
            ('Policy research roles open to applicants in Nepal.','Nepal'),
            ('Research jobs for candidates from Germany.','Germany'),
            ('I have policy research experience in India; now looking for roles based in Norway.','Norway')]:
            self.assertIn(place,extract_constraints(brief)['locations'])
        self.assertNotIn('India',extract_constraints('I research climate policy in India; looking for remote jobs.')['locations'])

    def test_comma_delimited_contract_is_employment_but_contract_drafting_is_not(self):
        from jobs.services.job_scout_service import JobScoutService as Scout
        prefs=Scout._fallback_parse('Remote UX research roles using qualitative research, contract, open to Europe.')
        self.assertEqual(prefs['locations'],['Europe'])
        self.assertEqual(prefs['job_types'],['contract'])
        for brief in ['Legal research and contract drafting roles','Contract law research jobs',
                      'Policy roles, contract negotiation, open to Europe.']:
            self.assertNotIn('contract',Scout._fallback_parse(brief)['job_types'])

    def test_dropped_query_constraints_cannot_admit_known_india_or_full_time_jobs(self):
        from jobs.services.job_scout_service import JobScoutService as Scout
        prefs=Scout._fallback_parse('Remote UX research roles using qualitative research, contract, open to Europe.')
        self.assertFalse(evaluate_job(job(location='Remote India',country='India',job_type='contract'),prefs,True).allowed)
        self.assertFalse(evaluate_job(job(location='Remote Europe',country='Europe',job_type='full-time'),prefs,True).allowed)


class SourceProfileTests(SimpleTestCase):
    def test_labelled_remote_country_does_not_become_worldwide_after_lossy_import(self):
        from jobs.services.scout_constraints import location_compatibility
        for region in ['Canada','Europe']:
            j=job(title='Grant Writer',description=f'Write proposals. Location: Remote/{region}.',location='Remote',country='Worldwide')
            self.assertFalse(location_compatibility(j,['Worldwide']))
            self.assertTrue(location_compatibility(j,[region]))
        self.assertTrue(location_compatibility(job(description='Build remote/canada server routes.'),['Worldwide']))

    def test_coding_proficiency_is_a_qualification_not_function_evidence(self):
        from jobs.services.scout_review import review_checks
        quote='Strong coding proficiency in Python and/or Go'
        self.assertIsNone(review_checks({'checks':{'function':['confirmed','description',quote]}},{'description':quote},{}))

    def test_unverified_environmental_sector_is_visible_for_a_function_match(self):
        from jobs.services.scout_review import sector_caveats
        prefs={'intent_text':'Product strategy in sustainability or circular economy'}
        self.assertEqual(sector_caveats(job(description='Build Ethereum developer tools.'),prefs),['sector_unconfirmed'])
        self.assertEqual(sector_caveats(job(description='Build sustainable construction products.'),prefs),[])

    def test_alternative_cannot_invent_salary_or_working_hours(self):
        from jobs.services.scout_close_matches import validate
        quote='Lead the communications strategy and campaigns.'
        pick={'id':1,'same_kind_of_work':True,'difference':'Communications leadership but with a slightly lower salary','evidence':{'field':'description','quote':quote},'checks':{'function':['confirmed','description',quote]}}
        prefs={'intent_text':'Senior communications leadership roles'}
        self.assertEqual(validate(json.dumps({'picks':[pick]}),{1:{'description':quote}},3,prefs),[])
        pick['difference']='Part-time communications leadership rather than a full-time senior position'
        self.assertEqual(validate(json.dumps({'picks':[pick]}),{1:{'description':quote}},3,prefs),[])

    def test_duties_exclude_company_intro_and_qualification_only_mentions(self):
        j=job(description='<h2>About Us</h2><p>We research climate policy.</p><h2>Responsibilities</h2><p>Maintain accounting records and reconcile bank statements.</p><h2>Qualifications</h2><p>Experience with policy is helpful.</p>')
        text=duties(j)
        self.assertIn('Maintain accounting records',text)
        self.assertNotIn('We research',text)
        self.assertNotIn('Experience with policy',text)
        self.assertEqual(function_priority(j,{'titles':['Policy Researcher']}),0)

    def test_lowercase_requirements_in_actual_duties_does_not_end_section(self):
        j=job(description='Responsibilities Identify user requirements and maintain Salesforce workflows. Qualifications Salesforce certification required.')
        self.assertIn('maintain Salesforce workflows',duties(j))
        self.assertNotIn('certification',duties(j))

    def test_existing_stale_malformed_or_out_of_bounds_profile_is_rebuilt(self):
        j=job();expected=build_profile(source_fields(j))
        for stored in [{'version':0}, {**expected,'spans':{'description':[[0,999999]],'requirements':[]}},
                       {**expected,'spans':{'description':'bad','requirements':[]}},
                       {**expected,'spans':{'description':[[False,12]],'requirements':[]}}]:
            j.raw_data={KEY:stored};j.__dict__.pop('_scout_source_profile',None)
            self.assertEqual(profile_for(j,geography=True,workplace=True)[1],expected)

    def test_source_edit_invalidates_profile_and_plain_text(self):
        j=job(description='<h2>Responsibilities</h2><p>Build Python services.</p>')
        old=profile_for(j)[1];j.raw_data={KEY:old}
        j.description='<h2>Responsibilities</h2><p>Reconcile financial accounts.</p>'
        self.assertIn('Reconcile',duties(j))
        self.assertNotEqual(profile_for(j)[1]['source_hash'],old['source_hash'])

    def test_duplicate_fields_do_not_reuse_old_text_after_both_are_edited(self):
        j=job(description='Old Python duties.',requirements='Old Python duties.')
        plain_job_field(j,'description')
        j.description=j.requirements='New accounting duties.'
        self.assertEqual(plain_job_field(j,'requirements'),'New accounting duties.')

    def test_shortlist_prioritizes_actual_work_over_incidental_qualifications(self):
        incidental=job(pk=1,title='Operations Manager',description='Responsibilities Coordinate office logistics. Qualifications Prior policy analysis experience is preferred.',scout_score=99)
        relevant=job(pk=2,title='Programme Associate',description='Responsibilities Conduct climate policy analysis and draft regulatory recommendations. Qualifications Two years experience.',scout_score=40)
        result=shortlist([incidental,relevant],{'titles':['Policy Analyst']},limit=1)
        self.assertEqual(result,[relevant])

    def test_shortlist_preserves_different_requisitions_and_deduplicates_regional_copies(self):
        a=job(pk=1,scout_requisition='ABC');b=job(pk=2,scout_requisition='ABC');c=job(pk=3,scout_requisition='DEF')
        self.assertEqual([j.pk for j in shortlist([a,b,c],{})],[1,3])

    def test_source_offsets_contain_only_original_passages(self):
        j=job(description='Responsibilities Build services. Qualifications Python experience.')
        fields,profile=profile_for(j)
        self.assertNotIn('duties',profile)
        for field,spans in profile['spans'].items():
            for start,end in spans:self.assertIn(fields[field][start:end],plain_job_field(j,field))

    def test_excerpt_separator_cannot_be_used_as_function_evidence(self):
        from jobs.services.scout_review import apply_review
        j=job(description='Build Python services. Many other tasks. Maintain integrations.')
        fabricated='Build Python services. […] Maintain integrations.'
        item={'checks':{'function':['confirmed','description',fabricated]}}
        self.assertFalse(apply_review(j,item,{'description':fabricated},{}))

    def test_required_in_person_attendance_is_not_fully_remote(self):
        from jobs.services.scout_review import workplace_conflict
        prefs={'intent_text':'Remote COO roles'}
        for text in ['Remote (U.S.-based); flexible, with in-person attendance as required.',
                     'Required in-person attendance at our office.']:
            self.assertTrue(workplace_conflict(job(),prefs,text))
        for text in ['Remote with occasional travel to conferences.',
                     'No required in-person attendance.', 'Optional office attendance.']:
            self.assertFalse(workplace_conflict(job(),prefs,text))

    def test_prepared_workplace_flags_recheck_source_changes(self):
        j=job(description='This is a hybrid working model with office attendance.')
        profile=build_profile(source_fields(j));j.raw_data={KEY:profile}
        self.assertFalse(evaluate_job(j,{'intent_text':'Remote engineering roles'},True).allowed)
        j.description='Fully remote work; office visits are optional.'
        self.assertTrue(evaluate_job(j,{'intent_text':'Remote engineering roles'},True).allowed)

    def test_prepared_workplace_flag_avoids_reparsing_source(self):
        j=job();j.raw_data={KEY:build_profile(source_fields(j))}
        with patch('jobs.services.scout_review.workplace_conflict',side_effect=AssertionError('Source was already prepared')):
            self.assertTrue(evaluate_job(j,{'intent_text':'Remote operations roles'},True).allowed)

    def test_alternative_cannot_invent_a_corporate_employer_preference(self):
        from jobs.services.scout_close_matches import validate
        quote='Run the revenue operations reporting systems.'
        pick={'id':1,'same_kind_of_work':True,'difference':'Nonprofit operations rather than corporate operations',
            'evidence':{'field':'description','quote':quote},'checks':{'function':['confirmed','description',quote]}}
        self.assertEqual(validate(json.dumps({'picks':[pick]}),{1:{'description':quote}},3,{'intent_text':'COO or RevOps jobs'}),[])

    def test_anchor_retains_an_existing_match_without_blocking_duty_recovery(self):
        original=job(pk=1,description='Coordinate revenue systems.',scout_score=99)
        recovered=job(pk=2,title='Programme Associate',description='Responsibilities Analyze policy and draft legislative proposals.',scout_score=40)
        picks=shortlist([original,recovered],{'titles':['Policy Analyst']},limit=2,anchors=[original])
        self.assertEqual(picks,[original,recovered])

    def test_account_executive_does_not_get_an_accounting_title_bonus(self):
        prefs={'titles':['Accountant']}
        actual=job(title='Accountant',description='Responsibilities Reconcile financial records and prepare reports.')
        sales=job(title='Account Executive',description='Responsibilities Sell products to enterprise accounts.')
        self.assertGreater(function_priority(actual,prefs),function_priority(sales,prefs))
        self.assertEqual(function_priority(sales,prefs),0)


class EligibilityMemoTests(SimpleTestCase):
    def test_decisions_are_reused_only_within_the_same_search(self):
        from jobs.services import scout_constraints as constraints
        j=job();prefs={'locations':['Norway']};d={}
        with patch.object(constraints,'_evaluate_job',wraps=constraints._evaluate_job) as real:
            with bounded_workflow(d):
                first=evaluate_job(j,prefs);self.assertEqual(evaluate_job(j,prefs),first)
                with bounded_workflow({}):self.assertEqual(evaluate_job(j,prefs),first)
            with bounded_workflow({}):evaluate_job(j,prefs)
        self.assertEqual(real.call_count,2)
        self.assertEqual(d['eligibility_cache_hits'],2)

    def test_modes_preferences_and_source_mutations_recheck_constraints(self):
        j=job(location='Remote',country='');prefs={'locations':['Norway']}
        with bounded_workflow({}):
            self.assertFalse(evaluate_job(j,prefs).allowed)
            self.assertTrue(evaluate_job(j,prefs,True).allowed)
            j.description='Candidates must be based in the United States.'
            self.assertFalse(evaluate_job(j,prefs,True).allowed)
            prefs['locations']=['United States']
            self.assertTrue(evaluate_job(j,prefs,True).allowed)
            j.salary_min=40000;j.salary_max=45000;j.salary_currency='USD'
            prefs['salary_requirement']={'amount':'80000','currency':'USD','period':'year'}
            self.assertFalse(evaluate_job(j,prefs,True).allowed)

    def test_raw_source_location_changes_recheck_even_when_imported_label_does_not(self):
        j=job(raw_data={'api_response':{'location':{'name':'Remote Norway'}}})
        with bounded_workflow({}):
            self.assertTrue(evaluate_job(j,{'locations':['Norway']}).allowed)
            j.raw_data['api_response']['location']['name']='Remote United States'
            self.assertFalse(evaluate_job(j,{'locations':['Norway']}).allowed)

    def test_different_objects_with_same_id_do_not_share_a_source_decision(self):
        with bounded_workflow({}):
            self.assertTrue(evaluate_job(job(),{'locations':['Norway']}).allowed)
            self.assertFalse(evaluate_job(job(location='Remote US',country='USA'),{'locations':['Norway']}).allowed)

    def test_employer_status_rechecks_after_source_edit_with_different_preferences(self):
        j=job(organization=SimpleNamespace(name='Example',description='',organization_type=''),
            description='We are a nonprofit. Build software.')
        with bounded_workflow({}):
            self.assertTrue(evaluate_job(j,{'required_employer_type':'nonprofit'}).allowed)
            j.description='We are a for-profit company. Build software.'
            self.assertFalse(evaluate_job(j,{'required_employer_type':'nonprofit','locations':['Norway']},True).allowed)


@override_settings(DEEPSEEK_API_KEY='test',DEEPSEEK_MODEL='test',SCOUT_MULTISTEP_ENABLED=True,SCOUT_COMBINED_PLAN_ENABLED=True)
class CombinedInterpretationTests(TestCase):
    def setUp(self):
        from django.core.cache import cache
        cache.clear()

    def test_interpretation_supplies_plan_without_a_second_provider_call(self):
        from jobs.services.job_scout_service import JobScoutService as Scout
        from jobs.services.scout_workflow import plan_terms
        brief='Remote software engineer using Python in Europe, no internships.'
        parsed=json.dumps({'titles':['Software Engineer'],'retrieval_terms':['backend developer','software engineer']})
        with patch('jobs.services.scout_workflow.generate',return_value=parsed) as provider:
            prefs=Scout.parse_intent(brief)
            d={};self.assertEqual(plan_terms(prefs,d),['backend developer','software engineer'])
            self.assertEqual(provider.call_count,1)
        self.assertEqual(d['planning'],'interpretation')
        self.assertEqual(prefs['required_skills'],['python'])
        self.assertEqual(prefs['locations'],['Europe'])
        self.assertIn('internship',prefs['exclude_keywords'])

    def test_changed_saved_brief_cannot_reuse_the_old_plan(self):
        import hashlib
        from jobs.services.scout_workflow import plan_terms
        prefs={'intent_text':'Accounting roles','_retrieval_terms':['backend developer'],
            '_retrieval_brief_hash':hashlib.sha256(b'Software engineer').hexdigest()}
        with patch('jobs.services.scout_workflow.generate',return_value='{"terms":["bookkeeper"]}') as provider:
            self.assertEqual(plan_terms(prefs,{}),['bookkeeper'])
        provider.assert_called_once()

    def test_malformed_combined_terms_fall_back_to_existing_planner(self):
        from jobs.services.job_scout_service import JobScoutService as Scout
        from jobs.services.scout_workflow import plan_terms
        brief='Remote software engineer'
        for terms in ['backend developer',['x'],['x'*81],[123],['developer']*7]:
            parsed={'retrieval_terms':terms}
            prefs=Scout._ground_preferences(parsed,brief,Scout._fallback_parse(brief))
            self.assertNotIn('_retrieval_terms',prefs)
        prefs=Scout._ground_preferences({'retrieval_terms':[]},brief,Scout._fallback_parse(brief))
        with patch('jobs.services.scout_workflow.generate') as provider:
            self.assertEqual(plan_terms(prefs,{}),[])
        provider.assert_not_called()


@override_settings(SCOUT_SEMANTIC_ENABLED=False)
class SourceProfileDatabaseTests(TestCase):
    def setUp(self):self.org=Organization.objects.create(name='Source profile tests')

    def make_job(self,slug,**values):
        return Job.objects.create(title='Research Associate',slug=slug,organization=self.org,
            location='Remote worldwide',country='Worldwide',**values)

    def test_atomic_profile_update_preserves_other_metadata_and_source(self):
        j=self.make_job('profile-store',description='Responsibilities Analyze policy evidence.',raw_data={'indexnow':{'sent':True}})
        profile=build_profile(source_fields(j))
        Job.objects.filter(pk=j.pk).update(raw_data={'indexnow':{'sent':True},'concurrent_import':'preserved'})
        self.assertEqual(store_profile(j,profile),1)
        j.refresh_from_db()
        self.assertEqual(j.raw_data['concurrent_import'],'preserved')
        self.assertEqual(j.raw_data[KEY],profile)
        self.assertEqual(j.description,'Responsibilities Analyze policy evidence.')

    def test_concurrently_changed_listing_does_not_receive_stale_offsets(self):
        j=self.make_job('profile-race',description='Build Python services.')
        profile=build_profile(source_fields(j))
        Job.objects.filter(pk=j.pk).update(description='Maintain financial accounts.')
        self.assertEqual(store_profile(j,profile),0)
        j.refresh_from_db();self.assertNotIn(KEY,j.raw_data)

    def test_command_is_idempotent_and_dry_run_is_read_only(self):
        j=self.make_job('profile-command',description='Responsibilities Analyze policy evidence.')
        out=StringIO();call_command('prepare_scout_sources',dry_run=True,stdout=out)
        j.refresh_from_db();self.assertNotIn(KEY,j.raw_data)
        call_command('prepare_scout_sources',stdout=out);j.refresh_from_db();self.assertIn(KEY,j.raw_data)
        out=StringIO();call_command('prepare_scout_sources',stdout=out)
        self.assertIn('prepared=0, unchanged=1',out.getvalue())

    def test_sql_skill_prefilter_preserves_html_phrase_and_requires_all_skills(self):
        from jobs.services.scout_retrieval import retrieve
        relevant=self.make_job('skill-html',description='Conduct qualitative <b>research</b> using Python.')
        wrong=self.make_job('skill-missing',description='Conduct research using Python.')
        qs=Job.objects.select_related('organization','category')
        prefs={'required_skills':['qualitative research','python']}
        d={};result=retrieve(qs,prefs,['research'],d,True)
        self.assertEqual([j.pk for j in result],[relevant.pk])
        self.assertEqual(d['retrieval']['lexical']['scanned'],1)

    def test_sql_domain_prefilter_preserves_variants_employer_evidence_and_disjunction(self):
        from jobs.services.scout_retrieval import retrieve
        a=self.make_job('domain-british',description='Coordinate behavioural <b>health</b> intake services.')
        b=self.make_job('domain-public',description='Analyze public health programme outcomes.')
        org=Organization.objects.create(name='Behavioral Health Network',slug='behavioral-health-network')
        c=Job.objects.create(title='Intake Coordinator',slug='domain-employer',organization=org,
            description='Coordinate intake and referrals.',location='Remote worldwide',country='Worldwide')
        unrelated=self.make_job('domain-unrelated',description='Build software applications.')
        benefits=self.make_job('domain-benefits',description='Build software. Benefits include behavioral health coverage.')
        d={};result=retrieve(Job.objects.select_related('organization','category'),
            {'required_domains':['behavioral health','public health']},[],d,True)
        self.assertEqual({j.pk for j in result},{a.pk,b.pk,c.pk})
        self.assertLess(d['retrieval']['lexical']['scanned'],5)

    def test_sql_skill_groups_preserve_aliases_and_either_alternative(self):
        from jobs.services.scout_retrieval import retrieve
        a=self.make_job('alternative-java',description='Build services using Java on Google Cloud.')
        b=self.make_job('alternative-python',description='Build services using Python on GCP.')
        wrong=self.make_job('alternative-wrong',description='Build services using Ruby on AWS.')
        prefs={'required_skills':['gcp'],'required_skill_groups':[['python','java']]}
        result=retrieve(Job.objects.select_related('organization','category'),prefs,[],{},True)
        self.assertEqual({j.pk for j in result},{a.pk,b.pk})

    def test_nonannual_source_pay_does_not_load_deferred_raw_json(self):
        from jobs.services.scout_retrieval import retrieve
        j=self.make_job('hourly-source',description='Analyze policy evidence.',salary_min=99999,
            salary_currency='USD',raw_data={'salaryPeriod':'hour','salaryMinimum':25,'salaryMaximum':30})
        qs=Job.objects.select_related('organization','category')
        result=retrieve(qs,{},[],{},True)
        from jobs.services.scout_evidence import compensation
        with self.assertNumQueries(0):self.assertEqual(str(compensation(result[0])[0]),'25')

    def test_partial_preview_keeps_verified_results_and_discloses_incomplete_review(self):
        from django.urls import reverse
        from django.contrib.sites.models import Site
        from allauth.socialaccount.models import SocialApp
        app=SocialApp.objects.create(provider='google',name='Test Google',client_id='test',secret='test')
        app.sites.add(Site.objects.get_current())
        app=SocialApp.objects.create(provider='openid_connect',provider_id='linkedin',name='Test LinkedIn',
            client_id='test-linkedin',secret='test',settings={'server_url':'https://www.linkedin.com/oauth'})
        app.sites.add(Site.objects.get_current())
        from jobs.services.job_scout_service import JobScoutService as Scout
        j=self.make_job('partial-preview',description='Analyze policy evidence.')
        j.scout_reasons=['Relevant evidence: Analyze policy evidence.']
        def search(brief,**kwargs):
            kwargs['diagnostics']['review_incomplete']=True
            return {'titles':['Policy Analyst']},[j]
        with patch.object(Scout,'search_intent',side_effect=search):
            response=self.client.get(reverse('jobs:job_scout_preview'),{'intent':'Remote policy analyst roles with nonprofits.'})
        self.assertEqual(response.status_code,200)
        self.assertContains(response,'These matches were checked')
        self.assertContains(response,j.title)
        self.assertNotContains(response,'we will email the complete result')
