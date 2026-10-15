"""Browser-check the real tool template with a stubbed AI response; no paid model calls."""
import json, os
from pathlib import Path
from urllib.parse import urlparse, parse_qs
from playwright.sync_api import sync_playwright
OUT=Path(__file__).resolve().parent
checks=[]
with sync_playwright() as p:
    browser=p.chromium.launch(channel='chrome',headless=True)
    for fallback in (False,True):
        context=browser.new_context(viewport={'width':1280,'height':1000})
        page=context.new_page()
        errors=[]
        page.on('pageerror',lambda error: errors.append(str(error)))
        if fallback:
            page.route('**/*htmx*',lambda route:route.abort())
        response={'status':200,'content_type':'text/html','body':'<div class="card p-6"><h2>Your Interview Questions</h2><p>1. How would you evaluate an impact programme?</p></div>'}
        page.route('**/tools/interview-prep/generate/',lambda route:route.fulfill(**response))
        page.goto(os.environ.get('TOOL_BASE_URL','http://127.0.0.1:8879')+'/tools/interview-prep/',wait_until='networkidle')
        if not fallback: page.wait_for_function('!!window.htmx')
        assert not page.locator('#ip-job-next-step').is_visible()
        role='Data & Policy Analyst'
        page.locator('#job_title').fill(role)
        page.locator('#company_type').select_option('public_health')
        page.locator('#ip-generate').click()
        page.locator('#ip-job-next-step').wait_for(state='visible')
        jobs=page.locator('#ip-role-jobs').get_attribute('href')
        scout=page.locator('#ip-role-scout').get_attribute('href')
        assert parse_qs(urlparse(jobs).query)['q']==[role]
        assert 'experience' not in parse_qs(urlparse(jobs).query)
        assert parse_qs(urlparse(scout).query)['source']==['interview_prep_tool']
        intent=parse_qs(urlparse(scout).query)['intent'][0]
        assert role in intent and 'Public health' in intent
        if not fallback:
            page.locator('#ip-output-panel').screenshot(path=str(OUT/'interview-prep-result.png'))
        response.update(status=500,body='<div>Generation failed. Please try again.</div>')
        page.locator('#ip-generate').click()
        page.wait_for_function("!document.getElementById('ip-generate').disabled")
        assert not page.locator('#ip-job-next-step').is_visible()
        assert not errors,errors
        checks.append({'mode':'fetch_fallback' if fallback else 'htmx','success_cta':True,'encoded_role_preserved':True,'scout_context_preserved':True,'no_implicit_level_filter':True,'error_hides_old_cta':True,'browser_errors':errors})
        context.close()
    browser.close()
(OUT/'tool-flow-checks.json').write_text(json.dumps(checks,indent=2))
print(json.dumps(checks,indent=2))
