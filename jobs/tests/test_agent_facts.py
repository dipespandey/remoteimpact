"""Regression cases where plausible metadata must not become an agent-facing fact."""
from jobs.tests.test_api_mcp import ApiBase
from jobs.api import serialize


class AgentFactsTests(ApiBase):
    def test_legacy_usd_default_and_unknown_period_do_not_become_annual_pay(self):
        job = self.feed_job
        job.salary_min, job.salary_max, job.salary_currency = 5000, 6000, 'USD'
        self.assertIsNone(serialize.salary(job))
        record = serialize.job_record(job)
        self.assertEqual(record['data_quality']['salary'], 'unknown')

    def test_hourly_source_cannot_relabel_normalized_values_as_hourly_pay(self):
        job = self.feed_job
        job.salary_min, job.salary_currency = 100000, 'USD'
        job.raw_data['salary_period'] = 'hour'
        self.assertIsNone(serialize.salary(job))

    def test_supported_annual_pay_is_exposed_without_inventing_a_bound(self):
        self.posted.salary_max = None
        self.assertEqual(serialize.salary(self.posted), {'min':90000.0,'max':None,'currency':'USD','period':'year'})
        from jobs.api.mcp import _summary
        text = _summary(serialize.job_record(self.posted))
        self.assertIn('from 90,000', text)
        self.assertNotIn('90,000–0', text)

    def test_international_and_global_metadata_do_not_override_residency(self):
        job = self.feed_job
        job.location, job.country = 'Remote, Worldwide', 'Global'
        job.description = 'Candidates must be based in Canada.'
        record = serialize.job_record(job)
        self.assertNotEqual(record['remote_scope'], 'worldwide')
        job.location, job.country, job.description = 'International team', '', 'We work with international partners.'
        self.assertEqual(serialize.job_record(job)['remote_scope'], 'unspecified')

    def test_country_label_is_not_presented_as_confirmed_eligibility(self):
        self.assertEqual(serialize.job_record(self.posted)['data_quality']['eligibility'], 'label_only')
        self.feed_job.raw_data = {}
        record = serialize.job_record(self.feed_job)
        self.assertIsNone(record['verification']['checked_at'])
        self.assertIsNotNone(record['last_seen_at'])

    def test_pay_filter_requires_currency_and_excludes_unknown_or_wrong_currency(self):
        self.assertEqual(self.client.get('/api/v1/jobs/?min_salary=80000').status_code, 400)
        data = self.client.get('/api/v1/jobs/?min_salary=80000&salary_currency=USD').json()
        self.assertEqual([j['slug'] for j in data['jobs']], ['program-lead'])
        self.assertEqual(self.client.get('/api/v1/jobs/?min_salary=80000&salary_currency=EUR').json()['total'],0)

    def test_agent_workflow_search_detail_and_uncertainty(self):
        # Exercise the real JSON-RPC endpoint, not a mocked search implementation.
        import json
        def rpc(method, params):
            return self.client.post('/mcp/',json.dumps({'jsonrpc':'2.0','id':1,'method':method,'params':params}),content_type='application/json')
        result = rpc('initialize',{'protocolVersion':'2025-06-18','capabilities':{},'clientInfo':{'name':'agent-workflow-test','version':'1'}})
        self.assertEqual(result.status_code,200)
        listing = rpc('tools/call',{'name':'search_jobs','arguments':{'country':'USA','min_salary':80000,'salary_currency':'USD'}}).json()['result']
        self.assertFalse(listing['isError'])
        role = listing['structuredContent']['jobs'][0]
        self.assertIn('eligibility_match',role)
        detail = rpc('tools/call',{'name':'get_job','arguments':{'id':role['slug']}}).json()['result']['structuredContent']['job']
        self.assertEqual(detail['id'],role['id'])
        self.assertEqual(detail['data_quality']['eligibility'],'label_only')

    def test_worldwide_exclusions_are_not_unrestricted_worldwide(self):
        self.feed_job.description = "Remote worldwide. We cannot hire candidates in the United States."
        self.feed_job.save()
        self.assertNotEqual(serialize.job_record(self.feed_job)['remote_scope'], 'worldwide')
        self.assertEqual(self.client.get('/api/v1/jobs/?remote_scope=worldwide').json()['total'],0)

    def test_fixed_report_uses_same_denominator_and_is_discoverable(self):
        import json
        from pathlib import Path
        from django.conf import settings
        data=json.loads((Path(settings.BASE_DIR)/'static/reports/remote-impact-jobs-2026-10-17.json').read_text())
        self.assertEqual(sum(r['count'] for r in data['domains']),data['total_listings'])
        response=self.client.get('/insights/remote-impact-jobs/2026-10-17/')
        self.assertContains(response,'Methodology and limitations')
        self.assertContains(response,data['data_url'])
        self.assertContains(self.client.get('/developers/'),'A reliable agent workflow')
