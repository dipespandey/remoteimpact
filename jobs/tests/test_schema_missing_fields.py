import json
from datetime import timedelta
from decimal import Decimal
from django.test import TestCase
from django.utils import timezone
from jobs.models import Job, Organization
from jobs.services.job_quality import job_posting_schema_json, _omit_unknown_schema_values


class SchemaMissingFieldsTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(name='Schema Employer', slug='schema-employer')

    def data(self, **changes):
        values = dict(title='Programme Lead', slug='schema-role', organization=self.org, source='ashby',
                      description='Lead impact programmes.', location='Remote', raw_data={})
        values.update(changes)
        return json.loads(job_posting_schema_json(Job(**values), 'https://remoteimpact.org'))

    def test_unknown_optional_fields_are_absent(self):
        data = self.data(job_type='', salary_min=None, salary_max=None, expires_at=None)
        for key in ('employmentType','baseSalary','directApply','validThrough','applicantLocationRequirements','jobLocation'):
            self.assertNotIn(key,data)
        self.assertNotIn('sameAs',data['hiringOrganization'])
        self.assertNotIn('logo',data['hiringOrganization'])

    def test_database_full_time_default_without_source_evidence_is_omitted(self):
        self.assertNotIn('employmentType',self.data())
        self.assertEqual(self.data(raw_data={'employmentType':'FULL_TIME'})['employmentType'],'FULL_TIME')
        self.assertEqual(self.data(job_type='internship',description='This is a paid internship.')['employmentType'],'INTERN')

    def test_first_seen_date_is_not_employer_posting_date(self):
        self.assertNotIn('datePosted',self.data(raw_data={'posted_at_basis':'first_seen'}))
        self.assertNotIn('datePosted',self.data(posted_at=None))

    def test_missing_description_is_not_replaced_by_job_title_or_requirements(self):
        self.assertNotIn('description',self.data(description='',requirements='Some requirements.'))
        self.assertNotIn('description',self.data(description='<p> </p>'))

    def test_description_is_not_truncated_to_an_incomplete_excerpt(self):
        text='Responsibilities and qualifications. '*200
        self.assertEqual(self.data(description=text)['description'],text.strip())

    def test_salary_requires_currency_evidence_and_keeps_only_known_bound(self):
        args=dict(salary_min=Decimal('60000'),salary_max=None,salary_currency='USD')
        self.assertNotIn('baseSalary',self.data(raw_data={'salaryPeriod':'YEAR'},**args))
        salary=self.data(raw_data={'salaryPeriod':'YEAR','salaryCurrency':'USD'},**args)['baseSalary']
        self.assertEqual(salary['currency'],'USD')
        self.assertEqual(salary['value']['minValue'],60000)
        self.assertNotIn('maxValue',salary['value'])
        self.assertNotIn('baseSalary',self.data(raw_data={'salaryPeriod':'HOUR','salaryCurrency':'USD'},**args))

    def test_vague_location_is_not_written_as_a_physical_city(self):
        for label in ('Global','Worldwide','Europe','Hybrid','Remote / Hybrid'):
            data=self.data(location=label)
            self.assertNotIn('jobLocation',data)
            self.assertNotIn('jobLocationType',data)
        source={'jobLocation':{'address':{'addressCountry':'US','addressLocality':'Boston','streetAddress':''}}}
        address=self.data(location='Boston',raw_data=source)['jobLocation']['address']
        self.assertEqual(address['addressCountry'],'US')
        self.assertNotIn('streetAddress',address)

    def test_known_employer_supplied_values_survive(self):
        deadline=timezone.now()+timedelta(days=3)
        data=self.data(source='manual',job_type='part-time',expires_at=deadline)
        self.assertTrue(data['directApply'])
        self.assertEqual(data['employmentType'],'PART_TIME')
        self.assertEqual(data['validThrough'],deadline.isoformat())

    def test_recursive_cleanup_preserves_false_and_zero(self):
        self.assertEqual(_omit_unknown_schema_values({'empty':' ','placeholder':'Not specified','nested':{'@type':'Place','address':{}},'known':False,'zero':0}),{'known':False,'zero':0})
