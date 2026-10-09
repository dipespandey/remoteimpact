from types import SimpleNamespace
from unittest.mock import patch
from django.test import TestCase, SimpleTestCase
from django.utils import timezone
from jobs.models import Job,Organization
from jobs.services.scout_constraints import extract_constraints,evaluate_job,location_compatibility,salary_compatible
from jobs.services.scout_evidence import compensation_display,nonprofit_compatibility,experience_evidence_warning,location_evidence_warning
from jobs.services.compensation_parser import extract_annual_salary
from jobs.services.job_scout_service import JobScoutService as S


def listing(**kw):
 data=dict(title='Operations Coordinator',description='Coordinate nonprofit programs.',requirements='',location='Remote worldwide',country='Worldwide',experience_level=None,job_type='full-time',salary_min=93600,salary_max=114400,salary_currency='USD',raw_data={})
 data.update(kw);return SimpleNamespace(**data)

class SourceEvidenceTests(SimpleTestCase):
 def test_named_employer_identity_is_distinct_from_nonprofit_clients(self):
  j=listing();j.organization=SimpleNamespace(name='Example Foods',organization_type='',description='')
  self.assertIs(nonprofit_compatibility(j,'<p>Example Foods is a national nonprofit that delivers produce.</p>'),True)
  self.assertIsNone(nonprofit_compatibility(j,'Our client Other Foods is a national nonprofit.'))
  self.assertIsNone(nonprofit_compatibility(j,'We support nonprofit customers.'))
  self.assertIsNone(nonprofit_compatibility(j,'We are not a nonprofit.'))
  j.organization.description='Our client is a nonprofit with national programs.'
  self.assertIsNone(nonprofit_compatibility(j,'We provide software.'))
  j.organization.description=''
  self.assertIs(nonprofit_compatibility(j,'We are a communications agency with nonprofit clients.'),False)
 def test_mandatory_sector_experience_is_unknown_for_a_career_changer(self):
  p=extract_constraints('Remote nonprofit operations coordinator. Career changer with one year of administration experience, entry level.');p['experience_levels']=['entry']
  text='Required Qualifications: Professional experience working for or directly supporting nonprofit organizations. Three references who can speak to your nonprofit experience. Applicants without direct nonprofit experience will not be considered.'
  self.assertIn('need confirmation',experience_evidence_warning(listing(),p,text))
  for intent in ['Career changer with one year of nonprofit administration experience.','Career changer. I have direct nonprofit operations experience.']:
   q=dict(p,intent_text=intent)
   self.assertIsNone(experience_evidence_warning(listing(),q,text))
  for intent in ['Career changer. I have no nonprofit experience.','Looking for a nonprofit operations role, one year of administration experience.']:
   self.assertIsNotNone(experience_evidence_warning(listing(),dict(p,intent_text=intent),text))
 def test_preferred_sector_experience_and_general_entry_roles_stay_eligible(self):
  p=extract_constraints('Entry level nonprofit operations coordinator, one year of administration experience.');p['experience_levels']=['entry']
  for text in ['Nonprofit experience preferred.','Required qualifications: Administrative experience. Nonprofit experience is a plus.','Zero to two years professional experience; no nonprofit experience required.','Required Qualifications: Experience with nonprofit organizations preferred.']:
   self.assertIsNone(experience_evidence_warning(listing(),p,text),text)
  self.assertTrue(evaluate_job(listing(requirements='0-2 years professional experience.'),p).allowed)
 def test_unstated_applicant_location_keeps_state_restriction_visible(self):
  j=listing(location='Remote US')
  warning=location_evidence_warning(j,{},'Fully remote; must reside in Massachusetts and be authorized to work in the U.S.')
  self.assertIn('Massachusetts',warning);self.assertIn('confirm eligibility',warning)
  self.assertIsNone(location_evidence_warning(j,{'locations':['Massachusetts']},'Must reside in Massachusetts.'))
  self.assertIsNone(location_evidence_warning(listing(location='Remote worldwide'),{},'Build nonprofit programs.'))
  self.assertNotIn('Massachusetts',location_evidence_warning(j,{},'No need to reside in Massachusetts.') or '')
  self.assertIn('Massachusetts',location_evidence_warning(j,{},'No need to reside in California, but must reside in Massachusetts.'))
  self.assertIn('Canada',location_evidence_warning(listing(location='Remote worldwide'),{},'Must reside in Canada.'))
 def test_missing_or_wrong_seniority_does_not_override_required_years(self):
  for brief in ['Entry level operations coordinator, one year of administration experience.','Junior operations jobs, no senior roles.','Early career operations coordinator.']:
   p=extract_constraints(brief);p['experience_levels']=['entry']
   for text in ['Required: 6+ years of benefits and payroll administration experience.','Minimum 4 years relevant experience.','Requires 3-5 years of professional experience.']:
    with self.subTest(brief=brief,text=text):self.assertFalse(evaluate_job(listing(requirements=text),p).allowed)
   self.assertTrue(evaluate_job(listing(requirements='One year of experience. Reports to the operations team.'),p).allowed)
 def test_preferred_years_are_not_a_mandatory_floor(self):
  p=extract_constraints('Operations coordinator with one year of administrative experience.')
  self.assertTrue(evaluate_job(listing(requirements='Three years of experience preferred.'),p).allowed)
 def test_role_request_is_not_a_domain_keyword(self):
  for brief in ['Remote behavioral health therapist in California.','Remote licensed clinical social worker in California.','Psychotherapist in California, remote.']:
   p=extract_constraints(brief)
   self.assertFalse(evaluate_job(listing(title='Case Manager',location='Remote US',description='Behavioral health navigation, a non-clinical role.'),p).allowed)
   self.assertTrue(evaluate_job(listing(title='Licensed Clinical Social Worker',location='Remote California',description='Deliver behavioral health therapy.'),p).allowed)
 def test_communications_paraphrases_exclude_other_functions(self):
  for brief in ['Communications roles at global health nonprofits.','Remote communication positions in global health.']:
   p=extract_constraints(brief)
   self.assertFalse(evaluate_job(listing(title='Global Health Researcher and Grantmaker',description='Strong communication skills required.'),p).allowed)
   self.assertTrue(evaluate_job(listing(title='Senior Media Relations Manager'),p).allowed)
 def test_fulltime_unknown_consultancy_is_not_a_verified_match(self):
  self.assertFalse(evaluate_job(listing(title='Open Call for Consultants, Communications'),{'job_types':['full-time']}).allowed)
  self.assertTrue(evaluate_job(listing(title='Communications Consultant',description='This is a full-time position.'),{'job_types':['full-time']}).allowed)
 def test_structured_source_geography_beats_generic_catalogue_label(self):
  j=listing(location='Remote',raw_data={'api_response':{'location':{'name':'Remote Spain'}}})
  self.assertTrue(location_compatibility(j,['Spain']))
  self.assertFalse(location_compatibility(j,['Canada']))
  j=listing(location='Remote',raw_data={'api_response':{'location':{'name':'Remote Spain'}}},description='Candidates must reside in the United States.')
  self.assertFalse(location_compatibility(j,['Spain']))
 def test_hourly_parttime_pay_preserves_source_units(self):
  j=listing(raw_data={'salaryPeriod':'HOUR','salaryMinimum':'45.00','salaryMaximum':'55.00'})
  self.assertEqual(compensation_display(j),'$45 – $55 per hour')
  self.assertFalse(salary_compatible(j,{'amount':'90000','currency':'USD','period':'year'}))
  self.assertEqual(extract_annual_salary('Hourly rate $45-$55 per hour, 10 hours per week.'),{})
 def test_salary_overlap_and_guaranteed_floor_are_distinct(self):
  j=listing(salary_min=104000,salary_max=183000)
  self.assertTrue(salary_compatible(j,{'amount':'150000','currency':'USD','period':'year'}))
  self.assertFalse(salary_compatible(j,{'amount':'150000','currency':'USD','period':'year','mode':'floor'}))

 def test_explicit_guaranteed_floor_and_actual_pay_are_grounded(self):
  for brief in ['Minimum salary $150000 USD per year.', 'At least $60000 actual annual pay, not full-time-equivalent.', 'Guaranteed pay above $80k.']:
   p=extract_constraints(brief)
   self.assertEqual(p['salary_requirement']['mode'],'floor')
   self.assertFalse(evaluate_job(listing(salary_min=50000,salary_max=200000),p).allowed)
 def test_on_call_variants_and_unknowns(self):
  p=extract_constraints('Remote software engineer. No on-call. No on-call duties.')
  for text in ['Join our on call rotation.', 'Participate in on-call duties.', 'We have an oncall schedule.']:
   self.assertFalse(evaluate_job(listing(description=text),p).allowed)
  self.assertTrue(evaluate_job(listing(description='No on-call duties are required.'),p).allowed)
  self.assertTrue(evaluate_job(listing(description='Build Python services.'),p).allowed)
 def test_preferred_experience_and_nonclinical_colleagues_are_not_conflicts(self):
  self.assertTrue(evaluate_job(listing(requirements='Three years of professional experience preferred.'),{'experience_levels':['entry']}).allowed)
  p=extract_constraints('Licensed clinical social worker in California.')
  self.assertTrue(evaluate_job(listing(title='Clinical Social Worker',location='Remote California',description='Deliver therapy and work with nonclinical staff.'),p).allowed)

 def test_actual_parttime_annual_pay_is_not_a_short_contract_retainer(self):
  p=extract_constraints('Part-time contract, at least $60000 actual annual pay, not full-time-equivalent.')
  self.assertFalse(evaluate_job(listing(job_type='part-time',description='Interim role from October through December.',raw_data={'salaryPeriod':'MONTH'},salary_min=78000),p).allowed)
  self.assertFalse(evaluate_job(listing(job_type='contract',description='Fixed 90-day engagement, 10-12 hours per week.',raw_data={'salaryPeriod':'MONTH'},salary_min=72000),p).allowed)
  self.assertTrue(evaluate_job(listing(job_type='part-time',description='12-month contract. Annual pay $70000 for this part-time appointment.',salary_min=70000),p).allowed)
 def test_nonprofit_clients_do_not_make_the_employer_a_nonprofit(self):
  p=extract_constraints('Communications roles at global health nonprofits.')
  self.assertFalse(evaluate_job(listing(title='Communications Manager',description='We are a communications agency that partners with non-profit clients.'),p).allowed)
  j=listing(title='Communications Officer');j.organization=SimpleNamespace(organization_type='nonprofit',description='')
  self.assertTrue(evaluate_job(j,p).allowed)
 def test_negative_oncall_clause_does_not_hide_positive_duty(self):
  p=extract_constraints('Software engineer. No on-call.')
  self.assertFalse(evaluate_job(listing(description='No on-call during onboarding, but join the on-call rotation afterward.'),p).allowed)

 def test_heldout_punctuated_minimum_and_company_history(self):
  p=extract_constraints('Junior nonprofit operations coordinator with one year of administration experience.');p['experience_levels']=['entry']
  self.assertFalse(evaluate_job(listing(requirements='Qualifications: 5+ years of nonprofit, business or organizational Operations experience.'),p).allowed)
  self.assertTrue(evaluate_job(listing(description='Our company has 25 years of professional experience.',requirements='One year of administration experience required.'),p).allowed)

class MatchingSourceTests(TestCase):
 def setUp(self):self.org=Organization.objects.create(name='Source Evidence Org')
 def make(self,title='Senior Software Engineer',req='R1',location='Remote Spain'):
  return Job.objects.create(organization=self.org,title=title,slug='evidence-'+str(Job.objects.count()),description='Build backend applications using Python and Django.',requirements='Strong experience with Python and Django.',skills=['python'],location='Remote',posted_at=timezone.now(),job_type='full-time',raw_data={'api_response':{'location':{'name':location},'requisition_id':req}})
 def test_spain_opening_is_recalled_and_same_requisition_collapses(self):
  a=self.make();b=self.make(location='Remote Canada');c=self.make(req='R2')
  p=S._fallback_parse('Remote backend software engineer using Python. Based in Spain, eligible to work in Spain. Any seniority.')
  with patch('jobs.services.scout_reranker.rerank',side_effect=lambda candidates,prefs,scored,diag,**kwargs:scored):
   matches=S.get_matches_for_preferences(p,limit=5)
  self.assertEqual({j.pk for j in matches},{a.pk,c.pk})
  p=S._fallback_parse('Remote software engineer using Python.')
  matches=S.get_matches_for_preferences(p,limit=5)
  self.assertEqual(len(matches),2)
 def test_saved_and_direct_preferences_share_source_gates(self):
  self.make(title='Case Manager',req='CLINICAL')
  p=S._fallback_parse('Remote behavioral health therapist, licensed clinical social worker in California, part-time. No software engineering.')
  self.assertIn('therapist',' '.join(p['titles']))
  self.assertEqual(S.get_matches_for_preferences(p),[])

 def test_unproven_required_sector_experience_is_demoted_not_blanket_excluded(self):
  experienced=self.make(title='Nonprofit Operations Coordinator',req='SECTOR')
  experienced.description='Required Qualifications: Professional experience working for or directly supporting nonprofit organizations. Applicants without direct nonprofit experience will not be considered.'
  experienced.requirements='';experienced.experience_level='entry';experienced.save()
  entry=self.make(title='Operations Coordinator',req='ENTRY')
  entry.description='Coordinate operations and administration. Required: 0-2 years professional experience.'
  entry.requirements='';entry.experience_level='entry';entry.save()
  p=S._fallback_parse('Remote nonprofit operations coordinator. Career changer with one year of administration experience, entry level.')
  jobs=S.get_matches_for_preferences(p,limit=5)
  self.assertIn(experienced.pk,[j.pk for j in jobs]);self.assertIn(entry.pk,[j.pk for j in jobs])
  sector=next(j for j in jobs if j.pk==experienced.pk)
  self.assertLessEqual(sector.scout_score,59)
  self.assertTrue(sector.scout_needs_confirmation)
  self.assertIn('Required nonprofit experience',sector.scout_reasons[0])
  self.assertLess([j.pk for j in jobs].index(entry.pk),[j.pk for j in jobs].index(experienced.pk))
  qualified=dict(p,intent_text='Remote nonprofit operations coordinator. Career changer with one year of nonprofit administration experience, entry level.')
  qualified_jobs=S.get_matches_for_preferences(qualified,limit=5)
  qualified_sector=next(j for j in qualified_jobs if j.pk==experienced.pk)
  self.assertGreater(qualified_sector.scout_score,59)

 def test_provisional_preview_wording_and_single_or_empty_result_lists(self):
  from django.template import engines
  from django.test import RequestFactory
  j=self.make(title='Operations Coordinator',req='PREVIEW')
  j.scout_score=59;j.scout_needs_confirmation=True;j.scout_reasons=['Required nonprofit experience needs confirmation']
  request=RequestFactory().get('/alerts/preview/');request.user=SimpleNamespace(is_authenticated=True,is_staff=False,first_name='Test')
  template=engines['django'].get_template('jobs/job_scout_preview.html')
  context={'preview_jobs':[j],'preferences':{},'intent':'Entry level nonprofit operations coordinator.','next_path':'/alerts/','user':request.user}
  html=template.render(context,request)
  self.assertIn('Current matches to review',html);self.assertIn('provisional fit',html);self.assertNotIn('Strong current matches',html)
  context['preview_jobs']=[]
  self.assertIn('No eligible matches in this preview',template.render(context,request))

 def test_annual_filter_excludes_hourly_estimates_and_preserves_missing_json(self):
  from jobs.services.job_service import JobService
  from django.http import QueryDict
  hourly=self.make(req='PAY')
  hourly.salary_min=93600;hourly.salary_max=114400;hourly.raw_data={'salaryPeriod':'HOUR','salaryMinimum':'45','salaryMaximum':'55'};hourly.title='Hourly role';hourly.save()
  annual=self.make(req='ANNUAL');annual.salary_min=95000;annual.salary_max=110000;annual.raw_data={};annual.title='Annual role';annual.save()
  self.assertEqual(set(JobService.get_filtered_jobs(QueryDict('salary_min=90000')).values_list('pk',flat=True)),{annual.pk})
  self.assertEqual(set(JobService.get_filtered_jobs(QueryDict('salary_min=inf')).values_list('pk',flat=True)),{annual.pk,hourly.pk})

 def test_schema_source_hourly_unit_overrides_legacy_annual_normalization(self):
  import json
  from jobs.services.job_quality import job_posting_schema_json
  hourly=self.make(req='SCHEMA-HOURLY')
  hourly.salary_min=93600;hourly.salary_max=114400
  hourly.raw_data={'salaryPeriod':'HOUR','salaryMinimum':'45','salaryMaximum':'55','quality_enrichment':{'salary':{'method':'source_period_normalization_v1'}}}
  self.assertNotIn('baseSalary',json.loads(job_posting_schema_json(hourly,'https://remoteimpact.org')))
  annual=self.make(req='SCHEMA-ANNUAL');annual.salary_min=95000;annual.salary_max=110000;annual.raw_data={'salaryPeriod':'YEAR'}
  self.assertEqual(json.loads(job_posting_schema_json(annual,'https://remoteimpact.org'))['baseSalary']['value']['minValue'],95000)
