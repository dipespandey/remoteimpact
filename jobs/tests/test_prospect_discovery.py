import io
import json
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch
from django.core.management import call_command
from django.test import TestCase, SimpleTestCase, override_settings
from django.utils import timezone
from jobs.link_models import LinkProspect
from jobs.services.prospect_discovery import clean_candidate_url, plan_queries, assess_page, discover_prospects
from jobs.services.safe_fetch import FetchResult

GOOD = '<title>Climate career resources</title><main><h1>Climate career resources</h1><p>Job boards for climate careers. Suggest a resource for our readers.</p><a href="https://board-one.example/jobs">Jobs</a><a href="https://board-two.example/">Careers</a></main>'

class PageAssessmentTests(SimpleTestCase):
    def test_ads_private_urls_tracking_and_owned_subdomains(self):
        for url in ['https://bing.com/aclick?u=x','https://www.google.com/url?q=x','http://127.0.0.1/a','http://localhost/a','https://blog.remoteimpact.org/x','https://redd.it/x','https://x.example/pagead/aclk']:
            self.assertFalse(clean_candidate_url(url)[0], url)
        self.assertEqual(clean_candidate_url('https://university.example/Resources?subject=health&utm_source=search#top')[0],
                         'https://university.example/Resources?subject=health')

    def test_query_intents_rotate_on_next_sector_visit(self):
        d=date(2026,10,30)
        self.assertNotEqual(plan_queries('health',d),plan_queries('health',d+timedelta(days=4)))
        self.assertEqual(len(set(plan_queries('health',d))),3)

    def test_navigation_keyword_noise_does_not_qualify(self):
        result=assess_page('https://university.example/shop','climate',FetchResult('ok',text='<title>Shop</title><nav>climate career resources job boards</nav><main>Buy a shirt</main>'))
        self.assertEqual(result['status'],'unsuitable')

    def test_unknown_date_and_permission_are_not_invented(self):
        result=assess_page('https://university.example/resources','climate',FetchResult('ok',text=GOOD))
        self.assertEqual(result['status'],'promising')
        self.assertEqual(result['freshness'],'unknown')
        self.assertIn('Not established',result['permission'])

    def test_frozen_labelled_cases(self):
        output=io.StringIO()
        call_command('evaluate_partner_discovery',dataset=Path(__file__).parent/'fixtures/partner_research_cases.json',stdout=output)
        report=json.loads(output.getvalue())
        self.assertEqual(report['correct'],report['cases'],report['results'])
        self.assertEqual(report['false_positives'],0)

@override_settings(SITE_URL='https://remoteimpact.org')
class DiscoveryPipelineTests(TestCase):
    @patch('jobs.services.safe_fetch.fetch_public_html')
    def test_filters_dedupes_pages_and_preserves_optouts(self, fetch):
        LinkProspect.objects.create(name='Opted out',url='https://blocked.example/old',status='do_not_contact')
        fetch.return_value=FetchResult('ok',text=GOOD)
        rows=[{'href':'https://bing.com/aclick?q=x'}, {'href':'https://blocked.example/new'},
              {'href':'https://university.example/resources'}, {'href':'https://www.university.example/jobs'}]
        report=discover_prospects('climate',search=lambda *a,**k:rows)
        self.assertEqual(report['counts']['imported'],1)
        self.assertEqual(fetch.call_count,1)
        prospect=LinkProspect.objects.get(url='https://university.example/resources')
        self.assertEqual(prospect.research_assessment['status'],'promising')
        self.assertFalse(prospect.contact_email)
        self.assertFalse(prospect.audience)
        self.assertEqual(LinkProspect.objects.get(url='https://blocked.example/old').status,'do_not_contact')

    @patch('jobs.services.safe_fetch.fetch_public_html')
    def test_failed_searches_and_fetches_are_not_reported_as_no_opportunities(self, fetch):
        fetch.return_value=FetchResult('http_403')
        n=0
        def search(*a,**k):
            nonlocal n
            n+=1
            if n==1: raise RuntimeError('private provider payload')
            return [{'href':'https://blocked-fetch.example/resources'}]
        report=discover_prospects('climate',search=search)
        self.assertEqual(report['counts']['search_failures'],1)
        self.assertEqual(report['counts']['fetch_failures'],1)
        self.assertEqual(report['counts']['imported'],0)
        self.assertNotIn('private provider payload',str(report))

    @patch('jobs.services.safe_fetch.fetch_public_html')
    def test_final_redirect_target_is_filtered_before_import(self, fetch):
        fetch.return_value=FetchResult('ok',text=GOOD,final_url='https://www.reddit.com/r/example')
        report=discover_prospects('climate',search=lambda *a,**k:[{'href':'https://redirect.example/resource'}])
        self.assertEqual(report['counts']['imported'],0)
        self.assertEqual(report['candidates'][0]['decision'],'excluded_redirect')

    @patch('jobs.services.safe_fetch.fetch_public_html')
    def test_pause_prevents_network_and_dry_run_never_writes(self, fetch):
        fetch.return_value=FetchResult('ok',text=GOOD)
        search=lambda *a,**k:[{'href':'https://university.example/resources'}]
        report=discover_prospects('climate',search=search,active=lambda:False)
        self.assertTrue(report['interrupted']);fetch.assert_not_called()
        report=discover_prospects('climate',search=search,persist=False)
        self.assertEqual(report['counts']['promising'],1)
        self.assertEqual(LinkProspect.objects.count(),0)

    @patch('growth.acquisition.sample_jobs', return_value=[])
    def test_negative_assessment_blocks_ready_queue_even_with_contact(self, jobs):
        from growth.acquisition import sync_partner
        p=LinkProspect.objects.create(name='Old page',url='https://old.example/page',audience='Climate seekers',
            fit_reason='Career resources',contact_url='https://old.example/contact',contact_source_url='https://old.example/contact',
            last_checked_at=timezone.now(),check_status='ok',research_assessment={'status':'unsuitable','reasons':['No audience fit']})
        c=sync_partner(p,inspect=False)
        self.assertEqual(c.status,'research');self.assertLessEqual(c.score,20)

    @patch('jobs.services.safe_fetch.fetch_public_html')
    def test_fetch_budget_keeps_all_search_hits_in_report(self, fetch):
        fetch.return_value = FetchResult('ok', text=GOOD)
        calls = 0
        def search(*args, **kwargs):
            nonlocal calls
            calls += 1
            return [{'href':f'https://site-{calls}-{i}.example/resources'} for i in range(5)]
        report = discover_prospects('climate', search=search, persist=False)
        self.assertEqual(fetch.call_count, 8)
        self.assertEqual(report['counts']['search_hits'], 10)
        self.assertEqual(len(report['candidates']), 10)
        self.assertEqual(sum(r['decision']=='not_checked_run_limit' for r in report['candidates']), 2)

    @patch('growth.acquisition.sample_jobs', return_value=[])
    def test_fresh_assessment_still_requires_contact_and_stale_cannot_qualify(self, jobs):
        from growth.acquisition import sync_partner
        p = LinkProspect.objects.create(name='Resource', url='https://resource.example/', audience='Climate seekers',
            fit_reason='Career resources', last_checked_at=timezone.now(), check_status='ok',
            research_assessment={'status':'promising', 'checked_on':timezone.localdate().isoformat()})
        self.assertEqual(sync_partner(p, inspect=False).status, 'research')
        p.contact_url = p.contact_source_url = 'https://resource.example/contact'
        p.save()
        self.assertEqual(sync_partner(p, inspect=False).status, 'review')
        p.research_assessment['checked_on'] = (timezone.localdate()-timedelta(days=8)).isoformat()
        p.save()
        self.assertEqual(sync_partner(p, inspect=False).status, 'research')
