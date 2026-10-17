#!/usr/bin/env python3
"""Bounded read-only MCP workflow. No keys needed, applications, emails or data mutations."""
import argparse
import json
import os
import urllib.request


def run(base):
    def rpc(method, params):
        body=json.dumps({'jsonrpc':'2.0','id':1,'method':method,'params':params}).encode()
        headers={'Content-Type':'application/json','Accept':'application/json, text/event-stream',
                 'User-Agent':'RemoteImpact-Agent-Smoke/1.0'}
        if os.environ.get('REMOTEIMPACT_API_KEY'):
            headers['Authorization']='Bearer '+os.environ['REMOTEIMPACT_API_KEY']
        req=urllib.request.Request(base.rstrip('/')+'/mcp/',data=body,headers=headers)
        with urllib.request.urlopen(req,timeout=45) as response:
            data=json.load(response)
        if 'error' in data:
            raise RuntimeError(data['error']['message'])
        return data['result']
    init=rpc('initialize',{'protocolVersion':'2025-06-18','capabilities':{},'clientInfo':{'name':'remoteimpact-agent-smoke','version':'1.0'}})
    tools=rpc('tools/list',{})['tools']
    assert all(t['annotations']['readOnlyHint'] for t in tools)
    found=rpc('tools/call',{'name':'search_jobs','arguments':{'worldwide_only':True,'limit':1}})
    assert not found['isError']
    jobs=found['structuredContent']['jobs']
    assert jobs, 'No worldwide result to exercise detail retrieval; workflow incomplete'
    role=jobs[0]
    assert role['remote_scope']=='worldwide' and role['data_quality']['eligibility']=='source_backed'
    detail=rpc('tools/call',{'name':'get_job','arguments':{'id':role['slug']}})['structuredContent']['job']
    assert detail['id']==role['id'] and detail['url'].startswith(base.rstrip('/')+'/jobs/')
    assert detail['data_quality']['salary'] in ('unknown','source_backed')
    if detail['salary']:
        assert detail['salary']['period']=='year' and detail['salary']['currency']
    return {'ok':True,'protocol':init['protocolVersion'],'tools':len(tools),'workflow':'initialize → tools/list → worldwide search → get_job',
            'listing_url':detail['url'],'data_quality':detail['data_quality']}


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-url',default='https://remoteimpact.org')
    print(json.dumps(run(parser.parse_args().base_url),indent=2))
