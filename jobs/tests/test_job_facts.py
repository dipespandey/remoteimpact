import asyncio
from unittest.mock import AsyncMock
from django.test import SimpleTestCase
from jobs.services.job_facts import validate, legacy_fields
from jobs.services.llm_parser import JobParser


class JobFactsTests(SimpleTestCase):
    source = 'Candidates must reside in Germany or France. Required Python. SQL preferred. Salary EUR 5000–6000 per month. Bachelor degree or equivalent experience. Full-time.'

    def facts(self, **values):
        return validate(values, 'Engineer', 'Org', self.source)

    def test_missing_and_unknown_remain_empty(self):
        facts = self.facts()
        self.assertIsNone(facts['education_level'])
        self.assertEqual(legacy_fields(facts)['skills'], [])

    def test_untraceable_and_unknown_labels_rejected(self):
        facts = self.facts(required_skills=[{'value': 'python', 'evidence': 'invented Python requirement'}, {'value': 'fake', 'evidence': 'Required Python'}], experience_level={'value': 'alien', 'evidence': 'Required Python'})
        self.assertEqual(facts['required_skills'], [])
        self.assertIn('experience_level', facts['rejected'])

    def test_html_and_whitespace_quotes(self):
        facts = validate({'required_skills': [{'value': 'python', 'evidence': 'Required Python'}]}, 'Engineer', 'Org', '<p>Required\n Python</p>')
        self.assertEqual(legacy_fields(facts)['skills'], ['python'])

    def test_skill_groups_and_deduplication(self):
        item = {'value': 'python', 'evidence': 'Required Python'}
        facts = self.facts(required_skills=[item, item], preferred_skills=[{'value': 'sql', 'evidence': 'SQL preferred'}])
        self.assertEqual(legacy_fields(facts)['skills'], ['python', 'sql'])

    def test_salary_normalization_in_code(self):
        facts = self.facts(salary={'min': 5000, 'max': 6000, 'currency': 'EUR', 'period': 'month', 'evidence': 'Salary EUR 5000–6000 per month'})
        self.assertEqual(facts['salary']['min'], 5000)
        self.assertEqual(legacy_fields(facts)['salary_min'], 60000)
        facts['salary']['period'] = 'hour'
        self.assertNotIn('salary_min', legacy_fields(facts))

    def test_salary_invalid_amounts(self):
        for value in (True, 'NaN', 'Infinity', -1, [], {}):
            with self.subTest(value=value):
                facts = self.facts(salary={'min': value, 'max': 6000, 'currency': 'EUR', 'period': 'month', 'evidence': 'Salary EUR 5000–6000 per month'})
                self.assertIsNone(facts['salary'])

    def test_multicountry_not_flattened(self):
        facts = self.facts(geography={'scope': 'restricted', 'countries': ['DE', 'FR'], 'excluded_countries': [], 'regions': [], 'restrictions': [], 'evidence': 'Candidates must reside in Germany or France'})
        self.assertEqual(facts['geography']['countries'], ['DE', 'FR'])
        self.assertNotIn('country', legacy_fields(facts))

    def test_conflicting_geography_rejected(self):
        for codes, excluded, scope in [(['DE'], ['DE'], 'restricted'), (['ZZ'], [], 'restricted'), (['DE'], [], 'global')]:
            facts = self.facts(geography={'scope': scope, 'countries': codes, 'excluded_countries': excluded, 'regions': [], 'restrictions': [], 'evidence': 'Candidates must reside in Germany or France'})
            self.assertIsNone(facts['geography'])

    def test_wrong_shapes_do_not_crash(self):
        for value in (False, [], 'bad', 10):
            facts = self.facts(impact_area=value, geography=value, salary=value, required_skills=value)
            self.assertIsNone(facts['salary'])
            self.assertIsNone(facts['geography'])

    def test_cached_enrichment_uses_no_call(self):
        parser = JobParser.__new__(JobParser)
        parser.config = {'model': 'test'}
        facts = self.facts()
        from jobs.services.extraction_contract import CONTRACT_HASH
        facts['model'] = 'test'
        facts['contract_hash'] = CONTRACT_HASH
        parser._parse_with_retry = AsyncMock(return_value=legacy_fields(facts))
        payload = {'description': self.source}
        first = asyncio.run(parser._parse_and_enrich(payload, 'Engineer', 'Org', self.source))
        second = asyncio.run(parser._parse_and_enrich(first, 'Engineer', 'Org', self.source))
        self.assertEqual(parser._parse_with_retry.await_count, 1)
        self.assertEqual(second['raw_data']['job_facts']['version'], 'job-facts-v3')
        asyncio.run(parser._parse_and_enrich(first, 'Engineer', 'Org', self.source + ' changed'))
        self.assertEqual(parser._parse_with_retry.await_count, 2)

    def test_import_does_not_reinfer_explicit_unknowns(self):
        from jobs.services.job_enrichment import enrich_job_payload
        facts = self.facts()
        result = enrich_job_payload({'title': 'Engineer', 'organization_name': 'Org', 'description': self.source, 'raw_data': {'job_facts': facts}, 'skills': []})
        for field in ('country', 'experience_level', 'education_level', 'salary_min', 'category_slug'):
            self.assertFalse(result.get(field), field)
        self.assertEqual(result['skills'], [])

    def test_new_format_roundtrip_preserves_original_and_upstream_values(self):
        parser = JobParser.__new__(JobParser)
        parser.config = {'model': 'test'}
        facts = self.facts(required_skills=[{'value': 'python', 'evidence': 'Required Python'}])
        parser._parse_with_retry = AsyncMock(return_value=legacy_fields(facts))
        payload = {'description': self.source, 'country': 'Germany', 'salary_min': 100000}
        result = asyncio.run(parser._parse_and_enrich(payload, 'Engineer', 'Org', self.source))
        self.assertEqual(result['description'], self.source)
        self.assertEqual(result['country'], 'Germany')
        self.assertEqual(result['salary_min'], 100000)
        self.assertEqual(result['skills'], ['python'])
        self.assertEqual(payload, {'description': self.source, 'country': 'Germany', 'salary_min': 100000})

    def test_salary_values_currency_and_period_must_be_in_quote(self):
        for change in ({'min': 5500}, {'currency': 'USD'}, {'period': 'year'}):
            salary = {'min': 5000, 'max': 6000, 'currency': 'EUR', 'period': 'month', 'evidence': 'Salary EUR 5000–6000 per month'}
            facts = self.facts(salary=dict(salary, **change))
            self.assertIsNone(facts['salary'])

    def test_jd_is_not_phd_and_preferred_degree_is_not_requirement(self):
        for quote in ('Juris Doctor (JD) required', 'Bachelor degree or equivalent experience', 'Bachelor degree preferred'):
            label = 'phd' if quote.startswith('Juris') else 'bachelor'
            facts = validate({'education_level': {'value': label, 'evidence': quote}}, 'Counsel', 'Org', quote)
            self.assertIsNone(facts['education_level'])

    def test_client_uses_one_structured_request_and_rejects_incomplete_contract(self):
        from types import SimpleNamespace
        parser = JobParser.__new__(JobParser)
        parser._semaphore = asyncio.Semaphore(1)
        parser.config = {'model': 'test'}
        parser.provider_name = 'deepseek'
        response = SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content='{}'))], usage=None)
        create = AsyncMock(return_value=response)
        parser.client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
        result = asyncio.run(parser._parse_openai_compat('Engineer', 'Org', self.source))
        self.assertEqual(result, {})
        self.assertEqual(create.await_count, 2)
        self.assertEqual(create.call_args.kwargs['response_format'], {'type': 'json_object'})

    def test_complete_source_includes_separate_requirements_and_location(self):
        from jobs.services.job_facts import source_description
        self.assertEqual(source_description({'description': 'About us', 'requirements': 'Required Python', 'location': 'Remote (Germany)'}), 'About us\nRequired Python\nRemote (Germany)')

    def test_monthly_fact_normalization_is_idempotent_in_import_and_job_quality(self):
        from types import SimpleNamespace
        from jobs.services.job_enrichment import enrich_job_payload
        from jobs.services.job_quality import normalize_salary
        facts = self.facts(salary={'min': 5000, 'max': 6000, 'currency': 'EUR', 'period': 'month', 'evidence': 'Salary EUR 5000–6000 per month'})
        payload = {'title': 'Engineer', 'organization_name': 'Org', 'description': self.source,
                   'source': 'idealist', 'salary_min': 60000, 'salary_max': 72000, 'salary_currency': 'EUR',
                   'raw_data': {'salaryPeriod': 'MONTH', 'job_facts': facts}}
        result = enrich_job_payload(payload)
        self.assertEqual(result['salary_min'], 60000)
        job = SimpleNamespace(title='Engineer', organization=SimpleNamespace(name='Org'), description=self.source,
                              requirements='', source='idealist', salary_min=60000, salary_max=72000,
                              salary_currency='EUR', raw_data={'salaryPeriod': 'MONTH', 'job_facts': facts})
        normalize_salary(job)
        normalize_salary(job)
        self.assertEqual(job.salary_min, 60000)
        self.assertEqual(job.salary_max, 72000)

    def test_annual_evidence_requires_matching_amounts_and_preserved_quote(self):
        from jobs.services.job_facts import annual_salary_matches
        facts = self.facts(salary={'min': 5000, 'max': 6000, 'currency': 'EUR', 'period': 'month', 'evidence': 'Salary EUR 5000–6000 per month'})
        self.assertTrue(annual_salary_matches(facts, 'Engineer', 'Org', self.source, 60000, 72000, 'EUR'))
        self.assertFalse(annual_salary_matches(facts, 'Engineer', 'Org', self.source, 5000, 6000, 'EUR'))
        self.assertFalse(annual_salary_matches(facts, 'Engineer', 'Org', 'No salary here.', 60000, 72000, 'EUR'))
