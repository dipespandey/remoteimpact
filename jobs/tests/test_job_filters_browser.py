"""Real browser coverage of search controls against PostgreSQL JSON/regex queries."""
import json
import os
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlencode, parse_qs, urlsplit
from playwright.sync_api import expect

from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.core.cache import cache
from django.test import override_settings
from django.utils import timezone
from jobs.models import Category, Job
from jobs.tests.test_scout_results import setup_scout_fixture


@override_settings(SECURE_SSL_REDIRECT=False, ACCOUNT_RATE_LIMITS={})
class JobFiltersBrowserTests(StaticLiveServerTestCase):
    def setUp(self):
        self.embedding = patch('jobs.services.embedding_service.embed_job', return_value=None)
        self.embedding.start(); self.addCleanup(self.embedding.stop)
        setup_scout_fixture(self)
        self.job.delete()
        self.climate = Category.objects.create(name='Climate', slug='climate')
        self.health = Category.objects.create(name='Health', slug='health')
        self.rows = []
        for i in range(12):
            row = dict(title=f'Matrix role {i:02}', slug=f'matrix-{i}', organization_id=self.job.organization_id,
                       description=f'Build useful distinct project number {i}.', requirements='SpecialToken' if i == 0 else '',
                       category=self.climate if i % 2 == 0 else self.health,
                       job_type=['full-time', 'part-time', 'contract', 'freelance'][i % 4],
                       experience_level=['entry', 'mid', 'senior', 'executive', 'internship'][i % 5],
                       education_level=['high_school', 'associate', 'bachelor', 'master', 'phd'][i % 5],
                       country=['Norway', 'Canada', 'USA'][i % 3], location='Remote',
                       skills=['python'] if i % 2 == 0 else ['sql'],
                       source='manual' if i == 0 else 'lever',
                       salary_min=60000 + i * 10000, salary_max=80000 + i * 10000,
                       salary_currency='NOK' if i == 10 else 'USD', raw_data={'salaryPeriod':'YEAR'})
            if i == 11: row.update(salary_min=None, salary_max=None)
            job = Job.objects.create(**row)
            age = [2, 10, 21, 40][i % 4]
            Job.objects.filter(pk=job.pk).update(posted_at=timezone.now()-timedelta(days=age), last_seen_at=timezone.now())
            self.rows.append(dict(row, pk=job.pk, age=age))
        cache.clear()
        self.folder = Path('.audit/search-filters-2026-10-16'); self.folder.mkdir(parents=True, exist_ok=True)
        self.proofs = []

    def browser(self, p, width):
        b = p.chromium.launch(headless=True, executable_path=os.environ.get('PLAYWRIGHT_CHROMIUM_EXECUTABLE', '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'))
        ctx = b.new_context(viewport={'width':width, 'height':900})
        ctx.route('**/*', lambda r:r.continue_() if r.request.url.startswith(self.live_server_url) else r.abort())
        page = ctx.new_page()
        return b, ctx, page

    def open(self, page, query=''):
        page.goto(self.live_server_url+'/jobs/'+('?' + query if query else ''))
        expect(page.locator('#options-areas')).not_to_have_attribute('x-cloak', '')
        if page.locator('#filter-toggle-btn').is_visible():
            page.locator('#filter-toggle-btn').click()

    def assert_rows(self, page, expected, case, param=None, value=None):
        if param:
            page.wait_for_url(lambda url: value in parse_qs(urlsplit(url).query).get(param, []), timeout=10000)
        expect(page.locator('#job-results-area')).to_have_attribute('aria-busy', 'false')
        actual = [int(x) for x in page.locator('#job-results-area [data-rank-job]').evaluate_all('(els) => els.map(e=>e.dataset.rankJob)')]
        expected = sorted(expected)
        self.assertEqual(sorted(actual), expected, case)
        print(f'PASS {page.viewport_size["width"]} {case}', flush=True)
        self.proofs.append({'case':case, 'width':page.viewport_size['width'], 'expected_ids':expected, 'actual_ids':actual})
        self.assertLessEqual(page.evaluate('document.documentElement.scrollWidth'),page.viewport_size['width'])

    def select(self, page, panel, name, value):
        page.locator('#filter-'+panel).click()
        page.locator('#options-'+panel+f' input[name="{name}"][value="{value}"]').check()

    def test_all_visible_filters_desktop_and_mobile(self):
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            for width in [1440,390]:
                browser, ctx, page = self.browser(p,width)
                all_ids = [r['pk'] for r in self.rows]
                cases = [('areas','category',v, [r['pk'] for r in self.rows if r['category'].slug == v]) for v in ['climate','health']]
                cases += [('role-types','type',v,[r['pk'] for r in self.rows if r['job_type']==v]) for v in ['full-time','part-time','contract','freelance']]
                cases += [('experience','experience',display,[r['pk'] for r in self.rows if r['experience_level']==v]) for v,display in Job.EXPERIENCE_LEVEL_CHOICES]
                cases += [('education','education',display,[r['pk'] for r in self.rows if r['education_level']==v]) for v,display in Job.EDUCATION_LEVEL_CHOICES]
                cases += [('skills','skill',v,[r['pk'] for r in self.rows if v in r['skills']]) for v in ['python','sql']]
                cases += [('countries','country',v,[r['pk'] for r in self.rows if r['country']==v]) for v in ['Norway','Canada','USA']]
                for panel,name,value,expected in cases:
                    self.open(page)
                    self.select(page,panel,name,value)
                    self.assert_rows(page,expected,f'{name}={value}',name,value)
                for value,label,age in [('7','Less than 1 week',2),('14','1-2 weeks',10),('28','2-4 weeks',21),('29','More than 4 weeks',40)]:
                    self.open(page);page.locator('#filter-posted').click()
                    page.locator('#options-posted').get_by_role('button',name=label,exact=True).click()
                    self.assert_rows(page,[r['pk'] for r in self.rows if r['age']==age],f'posted={value}','posted',value)
                self.open(page);page.locator('#filter-countries').click()
                page.locator('#options-countries').get_by_text('Europe',exact=True).click()
                self.assert_rows(page,[r['pk'] for r in self.rows if r['country']=='Norway'],'Europe region shortcut','country','Norway')
                self.open(page);page.locator('#filter-direct-apply').check()
                self.assert_rows(page,[self.rows[0]['pk']],'direct_apply','direct_apply','1')
                # OR inside one dimension, AND across dimensions; persisted after reload.
                self.open(page);self.select(page,'role-types','type','full-time')
                self.assert_rows(page,[r['pk'] for r in self.rows if r['job_type']=='full-time'],'first type','type','full-time')
                page.locator('[name="type"][value="part-time"]').check()
                expected = [r['pk'] for r in self.rows if r['job_type'] in ['full-time','part-time']]
                self.assert_rows(page,expected,'type OR','type','part-time')
                page.locator('#filter-role-types').click();self.select(page,'countries','country','Norway')
                expected=[r['pk'] for r in self.rows if r['job_type'] in ['full-time','part-time'] and r['country']=='Norway']
                self.assert_rows(page,expected,'type OR plus location AND','country','Norway')
                page.reload(); self.assert_rows(page,expected,'reload persistence')
                if page.locator('#filter-toggle-btn').is_visible():page.locator('#filter-toggle-btn').click()
                page.get_by_role('link',name='Clear all',exact=True).click()
                self.assert_rows(page,all_ids,'clear all')
                self.open(page);self.select(page,'skills','skill','python')
                self.assert_rows(page,[r['pk'] for r in self.rows if 'python' in r['skills']],'skill before clear','skill','python')
                page.get_by_role('textbox',name='Search skills',exact=True).fill('sql')
                page.locator('#options-skills input[name="skill"][value="sql"]').check()
                self.assert_rows(page,all_ids,'skill search preserves earlier selection','skill','sql')
                self.assertEqual(sorted(page.locator('#options-skills input[name="skill"]:checked').evaluate_all('(es)=>es.map(e=>e.value)')),['python','sql'])
                page.locator('#options-skills').get_by_role('button',name='Clear',exact=True).click()
                page.wait_for_url(lambda url: 'skill' not in parse_qs(urlsplit(url).query))
                self.assert_rows(page,all_ids,'per-filter clear')
                self.open(page);page.locator('#job-search').fill('Matrix role 00')
                self.assert_rows(page,[self.rows[0]['pk']],'keyword debounce','q','Matrix role 00')
                page.locator('#job-search').fill('DefinitelyUnmatchedFixtureTerm')
                self.assert_rows(page,[],'empty state','q','DefinitelyUnmatchedFixtureTerm')
                page.get_by_role('heading',name='No roles match these filters').wait_for()
                page.get_by_role('link',name='Reset all filters',exact=True).click()
                self.assert_rows(page,all_ids,'empty-state reset')
                # Salary slider keyboard operation sends annual USD bounds and preserves overlap.
                self.open(page);page.locator('#filter-salary').click()
                lo=page.get_by_role('slider',name='Minimum annual salary in USD')
                lo.focus();lo.press('Home')
                for _ in range(100):lo.press('ArrowRight')
                page.locator('#options-salary').get_by_role('button',name='Apply',exact=True).click()
                expected=[r['pk'] for r in self.rows if r['salary_currency']=='USD' and r['salary_max'] and r['salary_max']>=100000]
                self.assert_rows(page,expected,'salary lower bound','salary_min','100000')
                page.screenshot(path=str(self.folder/f'filtered-{width}.png'),full_page=True)
                self.open(page);page.locator('#filter-salary').click()
                hi=page.get_by_role('slider',name='Maximum annual salary in USD')
                hi.focus();hi.press('Home')
                for _ in range(100):hi.press('ArrowRight')
                page.locator('#options-salary').get_by_role('button',name='Apply',exact=True).click()
                expected=[r['pk'] for r in self.rows if r['salary_currency']=='USD' and r['salary_min'] and r['salary_min']<=100000]
                self.assert_rows(page,expected,'salary upper bound','salary_max','100000')
                self.open(page,'posted=7');page.locator('#filter-posted').click()
                page.locator('#options-posted').get_by_role('button',name='Any time',exact=True).click()
                page.wait_for_url(lambda url: 'posted' not in parse_qs(urlsplit(url).query))
                self.assert_rows(page,all_ids,'date reset')
                browser.close()
        (self.folder/'visible-filter-results.json').write_text(json.dumps(self.proofs,indent=2)+'\n')

    def test_legacy_urls_salary_sort_pagination_and_save_events(self):
        from playwright.sync_api import sync_playwright
        from django.contrib.auth import get_user_model
        user=get_user_model().objects.create_user('save-browser','save@example.org','test-password-531!')
        self.client.force_login(user)
        def add_paging_jobs():
            from django.db import connections
            try:
                for i in range(24):
                    Job.objects.create(title=f'Paging unique {i:02}',slug=f'paging-{i}',organization_id=self.job.organization_id,description=f'Paging distinct project {i}',category=self.climate,source='manual')
            finally:
                connections.close_all()
        with sync_playwright() as p:
            browser,ctx,page=self.browser(p,1440)
            for query,expected in [
                ('organization=Climate+Test',[r['pk'] for r in self.rows]),
                ('skillset=SpecialToken',[self.rows[0]['pk']]),
                ('has_salary=1',[r['pk'] for r in self.rows if r['salary_min']]),
                ('salary_max=90000',[r['pk'] for r in self.rows if r['salary_currency']=='USD' and r['salary_min'] and r['salary_min']<=90000]),
                ('salary_min=100000&salary_max=120000',[r['pk'] for r in self.rows if r['salary_currency']=='USD' and r['salary_min'] and r['salary_max']>=100000 and r['salary_min']<=120000]),
                ('salary_min=0&salary_max=1000000',[r['pk'] for r in self.rows]),
                ('salary_min=NaN&salary_max=inf',[r['pk'] for r in self.rows]),
                ('country=US',[r['pk'] for r in self.rows if r['country']=='USA']),
                ('country=Europe',[]),
            ]:
                self.open(page,query);self.assert_rows(page,expected,query)
            for sort in ['salary-high','salary-low','date']:
                self.open(page,'has_salary=1')
                page.locator('#sort-select').select_option(sort)
                page.wait_for_url(lambda url: parse_qs(urlsplit(url).query).get('sort') == [sort])
                expect(page.locator('#job-results-area')).to_have_attribute('aria-busy', 'false')
                ids=[int(v) for v in page.locator('[data-rank-job]').evaluate_all('(es)=>es.map(e=>e.dataset.rankJob)')]
                self.assertEqual(len(ids),11)
                if sort.startswith('salary'):
                    # Non-USD pay is deliberately excluded from comparable salary ordering.
                    usd=[r for r in self.rows if r['salary_currency']=='USD' and r['salary_min']]
                    expected=[r['pk'] for r in sorted(usd,key=lambda r:r['salary_min'],reverse=sort=='salary-high')]
                    self.assertEqual([v for v in ids if v in expected],expected)
                self.proofs.append({'case':'sort '+sort,'actual_ids':ids})
            from concurrent.futures import ThreadPoolExecutor
            with ThreadPoolExecutor(max_workers=1) as pool:
                pool.submit(add_paging_jobs).result()
            cache.clear();self.open(page,'q=Paging&sort=date')
            first=page.locator('[data-rank-job]').evaluate_all('(es)=>es.map(e=>e.dataset.rankJob)');self.assertEqual(len(first),20)
            page.get_by_role('link',name='Next',exact=True).click()
            expect(page.locator('[data-rank-job]')).to_have_count(4)
            second=page.locator('[data-rank-job]').evaluate_all('(es)=>es.map(e=>e.dataset.rankJob)');self.assertEqual(len(second),4)
            self.assertFalse(set(first)&set(second));self.assertIn('q=Paging',page.url);self.assertIn('sort=date',page.url)
            self.select(page,'areas','category','climate')
            page.wait_for_url(lambda url: 'category' in parse_qs(urlsplit(url).query) and 'page' not in parse_qs(urlsplit(url).query))
            self.assertEqual(page.locator('[data-rank-job]').count(),20)
            self.open(page,'q=Matrix')
            page.locator('#sort-select').select_option('relevance')
            self.assert_rows(page,[r['pk'] for r in self.rows],'relevance sort','sort','relevance')
            # Failed refresh retains results and exposes a working retry.
            self.open(page)
            page.route('**/jobs/?*',lambda r:r.fulfill(status=503,body='temporary failure'))
            page.locator('#job-search').fill('Matrix role 00')
            page.locator('#job-results-retry').wait_for(state='visible')
            self.assertEqual(page.locator('[data-rank-job]').count(),20)
            page.unroute('**/jobs/?*');page.locator('#job-results-retry').click()
            self.assert_rows(page,[self.rows[0]['pk']],'failed refresh retry','q','Matrix role 00')
            # Successful save/unsave emits an event; failures emit none and allow retry.
            ctx.add_cookies([{'name':'sessionid','value':self.client.cookies['sessionid'].value,'url':self.live_server_url}])
            page.goto(self.live_server_url+'/jobs/matrix-0/')
            page.evaluate('() => { window.saveEvents=[]; window.gtag=(...args)=>window.saveEvents.push(args); }')
            button=page.locator('#save-job-btn')
            save_url=button.get_attribute('data-url')
            page.route('**'+save_url,lambda r:r.fulfill(status=500,body='temporary failure'))
            button.click();expect(page.locator('#save-job-btn')).to_be_enabled()
            self.assertEqual(page.evaluate('window.saveEvents'),[])
            page.unroute('**'+save_url)
            button.click();expect(page.locator('#save-job-btn')).to_have_attribute('data-saved', 'true')
            button.click();expect(page.locator('#save-job-btn')).to_have_attribute('data-saved', 'false')
            self.assertEqual(page.evaluate('window.saveEvents.map(e=>e[1])'),['save_job','unsave_job'])
            browser.close()
        (self.folder/'additional-filter-results.json').write_text(json.dumps(self.proofs,indent=2)+'\n')

    def test_authenticated_best_match_and_explicit_filters_keep_all(self):
        from playwright.sync_api import sync_playwright
        from django.contrib.auth import get_user_model
        from jobs.models import SeekerProfile
        user=get_user_model().objects.create_user('filter-profile','filters@example.org','test-password-531!')
        with patch('jobs.services.embedding_service.embed_seeker',return_value=None):
            SeekerProfile.objects.create(user=user,wizard_completed=True)
        self.client.force_login(user)
        calls=[]
        def cached_order(jobs,seeker,**kwargs):
            calls.append(kwargs)
            return jobs.order_by('-id') if kwargs['keep_all'] else jobs.filter(pk__in=[r['pk'] for r in self.rows[:3]]).order_by('-id')
        with patch('jobs.views.jobs.UnifiedMatchingService.order_jobs_for_seeker',side_effect=cached_order),sync_playwright() as p:
            browser,ctx,page=self.browser(p,390)
            ctx.add_cookies([{'name':'sessionid','value':self.client.cookies['sessionid'].value,'url':self.live_server_url}])
            self.open(page,'sort=date')
            page.locator('#sort-select').select_option('best-match')
            self.assert_rows(page,[r['pk'] for r in self.rows[:3]],'authenticated best match','sort','best-match')
            self.select(page,'areas','category','climate')
            self.assert_rows(page,[r['pk'] for r in self.rows if r['category']==self.climate],'filtered personalized results retain all','category','climate')
            page.locator('#sort-select').select_option('best-match')
            self.assert_rows(page,[r['pk'] for r in self.rows if r['category']==self.climate],'explicit best match retains filtered jobs','sort','best-match')
            self.assertTrue(calls[-1]['keep_all'])
            page.locator('#sort-select').select_option('date')
            self.assert_rows(page,[r['pk'] for r in self.rows if r['category']==self.climate],'explicit newest preserves filters','sort','date')
            browser.close()
        (self.folder/'authenticated-filter-results.json').write_text(json.dumps({'note':'Cached ranking substituted; real filter querysets, browser controls and requests tested; no model quality claim','cases':self.proofs},indent=2)+'\n')
