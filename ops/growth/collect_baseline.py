#!/usr/bin/env python3
"""Collect comparable, read-only organic evidence with the existing free Google login.

Run from the repository root: python3 ops/growth/collect_baseline.py
Private aggregate exports go under .audit/, never into a committed report.
GSC's latest four days are omitted to avoid comparing partial daily data.
This makes no paid provider requests, sends no messages, and changes no site data.
"""
import argparse
from datetime import date, datetime, timedelta
import json
from pathlib import Path
import shutil
import subprocess
from zoneinfo import ZoneInfo

SITE = 'sc-domain:remoteimpact.org'
PROPERTY = '518242262'
HOSTS = ['remoteimpact.org', 'www.remoteimpact.org']


def periods(end, days):
    start = end - timedelta(days=days - 1)
    return [('current', start, end),
            ('previous', start - timedelta(days=days), start - timedelta(days=1))]


def run_json(command, target):
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=180)
    except (subprocess.TimeoutExpired, OSError) as error:
        target.with_suffix('.error.txt').write_text(type(error).__name__ + ': provider request did not complete')
        return {'status': 'unavailable', 'evidence': str(target.with_suffix('.error.txt'))}
    if result.returncode:
        # Persist the failed response privately; never interpret failure as zero.
        target.with_suffix('.error.txt').write_text(result.stdout + result.stderr)
        return {'status': 'unavailable', 'evidence': str(target.with_suffix('.error.txt'))}
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError:
        target.with_suffix('.error.txt').write_text(result.stdout)
        return {'status': 'unavailable', 'evidence': str(target.with_suffix('.error.txt'))}
    target.write_text(json.dumps(payload, indent=2))
    if isinstance(payload, dict) and (payload.get('error') or payload.get('ok') is False):
        return {'status': 'unavailable', 'evidence': str(target)}
    return {'status': 'available', 'evidence': str(target), 'data': payload}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--end', type=date.fromisoformat)
    parser.add_argument('--days', type=int, default=28, choices=range(7, 91), metavar='7..90')
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    cli = shutil.which('seo')
    if not cli:
        parser.error('The existing seo CLI is required; run command -v seo to check it.')
    end = args.end or (datetime.now(ZoneInfo('America/Los_Angeles')).date() - timedelta(days=4))
    output = args.output or Path('.audit') / ('organic-baseline-' + datetime.now().strftime('%Y%m%dT%H%M%S'))
    output.mkdir(parents=True, exist_ok=False)
    report = {'site': SITE, 'property': PROPERTY, 'periods': {}, 'limits': [
        'GA4 uses its property timezone; Search Console dates use Pacific time. Verify the property timezone before comparing.',
        'GA4 sessions and engagement do not establish unique humans. Keep suspicious and staff/test traffic separate.',
        'GSC query rows omit anonymized queries; a query table is not a property total.',
        'GA4 apply clicks are outbound actions, not completed applications or hires.',
        'These observations establish a baseline, not causal lift from a release.',
    ]}
    host_filter = {'filter': {'fieldName': 'hostName', 'inListFilter': {'values': HOSTS}}}
    for label, start, finish in periods(end, args.days):
        span = {'startDate': start.isoformat(), 'endDate': finish.isoformat()}
        section = {'range': span}
        gsc_body = {**span, 'dimensions': ['date'], 'dataState': 'final', 'rowLimit': 1000}
        source = run_json([cli, 'gsc-query', '--site', SITE, '--body', json.dumps(gsc_body), '--refresh', '--json'],
                          output / f'gsc-{label}.json')
        section['search_console'] = {k: v for k, v in source.items() if k != 'data'}
        if source['status'] == 'available':
            rows = source['data'].get('rows', [])
            section['search_console'].update(clicks=sum(r['clicks'] for r in rows),
                                             impressions=sum(r['impressions'] for r in rows), days_returned=len(rows))
        body = {'dateRanges': [span], 'dimensions': [{'name': 'sessionDefaultChannelGroup'}],
                'metrics': [{'name': m} for m in ['sessions', 'engagedSessions', 'totalUsers']],
                'dimensionFilter': host_filter, 'limit': 100}
        source = run_json([cli, 'analytics', 'google', 'report', '--property', PROPERTY,
                           '--body', json.dumps(body), '--refresh', '--json'], output / f'ga-{label}.json')
        section['analytics'] = {k: v for k, v in source.items() if k != 'data'}
        if source['status'] == 'available':
            section['analytics']['timezone'] = source['data'].get('metadata', {}).get('timeZone')
            section['analytics']['channels'] = [
                {'channel': row['dimensionValues'][0]['value'],
                 **dict(zip(['sessions', 'engaged_sessions', 'users'], [int(v['value']) for v in row['metricValues']]))}
                for row in source['data'].get('rows', [])]
        report['periods'][label] = section
    target = output / 'summary.json'
    target.write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
