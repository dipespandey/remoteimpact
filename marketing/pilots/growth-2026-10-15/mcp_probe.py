"""Read-only MCP smoke benchmark, anonymous or optional free key. Not fresh assistant discovery."""
import json, time, os
from datetime import datetime, timezone
from pathlib import Path
OUT = Path(os.environ.get('MCP_PROBE_OUT', Path(__file__).resolve().parent))
API_KEY = os.environ.get('MCP_PROBE_KEY')

def fetch(url, payload=None):
    import requests
    headers = {'Accept': 'application/json, text/event-stream'}
    if API_KEY:
        headers['Authorization'] = 'Bearer ' + API_KEY
    start = time.monotonic()
    response = requests.request('POST' if payload is not None else 'GET', url,
                                json=payload, headers=headers, timeout=35)
    try: data = response.json()
    except ValueError: data = {'raw': response.text[:2000]}
    return {'http_status': response.status_code, 'elapsed_seconds': round(time.monotonic()-start,3), 'body': data}

cases = [
 ('areas', 'What impact areas can I search?', 'list_areas', {}),
 ('norway_climate', 'Find climate engineering roles open from Norway.', 'search_jobs', {'query':'engineer','area':'climate-environment','country':'Norway','limit':3}),
 ('kenya_health', 'Find health program roles open from Kenya.', 'search_jobs', {'query':'program','area':'global-health','country':'Kenya','limit':3}),
 ('canada_data', 'Find data analyst roles open from Canada.', 'search_jobs', {'query':'data analyst','country':'Canada','limit':3}),
 ('us_entry', 'Find entry-level education roles open from the United States.', 'search_jobs', {'area':'education','level':'entry','country':'United States','limit':3}),
 ('worldwide_ai', 'Find worldwide AI safety jobs.', 'search_jobs', {'area':'ai-safety','worldwide_only':True,'limit':3}),
 ('negative', 'Search for an impossible exact job title.', 'search_jobs', {'query':'zzqv_unobtainium_test_94716','limit':3}),
]
results={'started_at':datetime.now(timezone.utc).isoformat(),'access_mode':'temporary_free_key' if API_KEY else 'anonymous','limitations':['Installed connector already present in this chat. No unprimed assistant-selection inference.','Synthetic briefs mapped by researcher to tool arguments; tests protocol and results, not natural-language interpretation.','No-key tier caps search results; zero results may reflect catalogue coverage or filter quality.'], 'setup':[], 'cases':[]}
for method, params in [('initialize',{'protocolVersion':'2025-06-18','capabilities':{},'clientInfo':{'name':'remoteimpact-growth-pilot','version':'1.0'}}),('tools/list',{})]:
    results['setup'].append({'method':method,**fetch('https://remoteimpact.org/mcp/',{'jsonrpc':'2.0','id':method,'method':method,'params':params})})
for i,(key,brief,name,args) in enumerate(cases):
    result=fetch('https://remoteimpact.org/mcp/',{'jsonrpc':'2.0','id':key,'method':'tools/call','params':{'name':name,'arguments':args}})
    results['cases'].append({'key':key,'brief':brief,'tool':name,'arguments':args,**result})
    (OUT/'mcp-results.json').write_text(json.dumps(results,indent=2))
# Eighth task: retrieve one returned role by its supplied ID, avoiding fabricated IDs.
jobs=[]
for c in results['cases']:
    jobs += c['body'].get('result',{}).get('structuredContent',{}).get('jobs',[])
if jobs:
    args={'id':str(jobs[0]['id'])}
    result=fetch('https://remoteimpact.org/mcp/',{'jsonrpc':'2.0','id':'detail','method':'tools/call','params':{'name':'get_job','arguments':args}})
    results['cases'].append({'key':'detail','brief':'Show details and original source for a returned job.','tool':'get_job','arguments':args,**result})
else:
    results['cases'].append({'key':'detail','status':'blocked_no_returned_job'})
(OUT/'mcp-results.json').write_text(json.dumps(results,indent=2))
for c in results['cases']:
    r=c.get('body',{}).get('result',{})
    s=r.get('structuredContent',{})
    print(c['key'],c.get('http_status'), 'error',c.get('body',{}).get('error') or r.get('isError'), 'total',s.get('total'),'jobs',len(s.get('jobs',[])))
