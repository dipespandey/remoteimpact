"""Bounded search and evidence-based qualification. Never authorizes outreach.

Page text is untrusted evidence; no page instructions are executed. The rule
score measures research fit, not traffic, editorial permission or revenue.
"""
from collections import Counter
from datetime import date
import ipaddress
import re
import time
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from bs4 import BeautifulSoup

VERSION = 'partner-research-v2'
SECTOR_TERMS = {
    'climate': ('climate', 'sustainability', 'renewable', 'environmental'),
    'health': ('global health', 'public health', 'health equity', 'humanitarian'),
    'ai': ('ai safety', 'ai governance', 'ai alignment', 'responsible ai'),
    'impact': ('social impact', 'nonprofit', 'non-profit', 'social change', 'mission-driven'),
}
# Intent changes on successive visits to a sector, not merely word order.
QUERY_PATTERNS = (
    '{topic} careers resources university job boards',
    '{topic} career community newsletter resources',
    '{topic} careers "suggest a resource"',
    '{topic} career resources association',
    '{topic} careers "submit a resource"',
    '{topic} careers newsletter partnerships',
)
TOPICS = {'climate': 'climate sustainability', 'health': 'global public health',
          'ai': 'AI safety governance', 'impact': 'nonprofit social impact'}
SEARCH_HOSTS = ('bing.com', 'google.com', 'duckduckgo.com', 'search.yahoo.com', 'yandex.com')
EXCLUDED_HOSTS = SEARCH_HOSTS + ('reddit.com', 'redd.it', 'tiktok.com', 'facebook.com', 'instagram.com')
COMPETITOR_HOSTS = ('climatebase.org', 'climatechangejobs.com', 'aisafetycareers.com', 'aisafetycareers.org')


def host_key(url):
    try:
        host = (urlsplit(url).hostname or '').lower().rstrip('.')
        return host.removeprefix('www.')
    except (ValueError, TypeError):
        return ''


def belongs_to(host, root):
    return host == root or host.endswith('.' + root)


def seen_site(host, sites):
    # Collapse www and parent/subdomain variants only when that parent is
    # actually present; do not conflate unrelated university/hosted subdomains.
    return any(belongs_to(host, old) or belongs_to(old, host) for old in sites)


def clean_candidate_url(url, own_host='remoteimpact.org'):
    from django.core.exceptions import ValidationError
    from jobs.link_models import canonical_prospect_url
    try:
        if not isinstance(url, str) or len(url) > 1000 or '\\' in url or any(ord(c) <= 32 for c in url):
            return '', 'invalid_url'
        normalized = canonical_prospect_url(url)
        host = host_key(normalized)
        if any(belongs_to(host, blocked) for blocked in EXCLUDED_HOSTS) or belongs_to(host, own_host):
            return '', 'excluded_host'
        if host == 'localhost' or '.' not in host:
            return '', 'invalid_url'
        try:
            if not ipaddress.ip_address(host).is_global:
                return '', 'invalid_url'
        except ValueError:
            pass
        parsed = urlsplit(normalized)
        if parsed.path.lower().rstrip('/') in ('/aclick', '/pagead/aclk'):
            return '', 'advertising_redirect'
        params = [(k,v) for k,v in parse_qsl(parsed.query, keep_blank_values=True)
                  if not k.lower().startswith('utm_') and k.lower() not in ('gclid', 'fbclid', 'msclkid')]
        return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, urlencode(params), '')), ''
    except (ValidationError, ValueError, UnicodeError, AttributeError):
        return '', 'invalid_url'


def plan_queries(sector, on_date):
    topic = TOPICS[sector]
    start = (on_date.toordinal() // 4) % len(QUERY_PATTERNS)
    return [QUERY_PATTERNS[(start+i) % len(QUERY_PATTERNS)].format(topic=topic) for i in range(3)]


def assess_page(url, sector, result, *, today=None):
    today = today or date.today()
    assessment = {'version': VERSION, 'sector':sector, 'source_url': url, 'checked_on': today.isoformat(),
                  'status': 'unverified', 'score': 0, 'reasons': [], 'signals': [],
                  'freshness': 'unknown', 'permission': 'Not established; human review required',
                  'fetch_status': result.status, 'page_date': None}
    if result.status != 'ok':
        assessment['reasons'] = ['Page could not be verified: ' + result.status]
        return assessment
    soup = BeautifulSoup(result.text, 'html.parser')
    title = soup.title.get_text(' ', strip=True) if soup.title else ''
    assessment['title'] = title[:255]
    dates = []
    for tag in soup.find_all('meta'):
        if (tag.get('property') or tag.get('name') or '').lower() in ('article:modified_time', 'article:published_time', 'date', 'datemodified', 'datepublished'):
            dates.append(tag.get('content', '')[:10])
    for tag in soup.find_all('time', datetime=True):
        dates.append(tag['datetime'][:10])
    valid_dates = []
    for value in dates:
        try:
            parsed = date.fromisoformat(value)
            if parsed <= today:
                valid_dates.append(parsed)
        except (ValueError, TypeError):
            pass
    if valid_dates:
        latest = max(valid_dates)
        assessment['page_date'] = latest.isoformat()
        assessment['freshness'] = 'older_than_two_years' if (today-latest).days > 730 else 'dated_within_two_years'
    for tag in soup(['script', 'style', 'noscript', 'nav', 'footer', 'header']):
        tag.decompose()
    main = soup.find('main') or soup.find('article') or soup
    text = main.get_text(' ', strip=True)
    content = (title + ' ' + text).lower()
    heading = ' '.join(x.get_text(' ', strip=True) for x in main.find_all(['h1','h2'])).lower()
    signals = []
    def signal(label, terms):
        found = next((term for term in terms if re.search(r'(?<!\w)' + re.escape(term) + r'(?!\w)', content)), None)
        if found:
            signals.append({'label':label, 'matched':found})
        return bool(found)
    sector_fit = signal('Sector audience', SECTOR_TERMS[sector])
    career_fit = signal('Career audience', ('careers','career','job seekers','jobseekers','job boards','employment'))
    resource_fit = signal('Resource or distribution page', ('career resources','career resource','job boards','useful resources','resource library','newsletter','career community','career services'))
    newsletter_only = resource_fit and signals[-1]['matched'] == 'newsletter'
    career_newsletter = bool(re.search(r'(?:career|job|employment)[^.\n]{0,60}newsletter|newsletter[^.\n]{0,60}(?:career|job|employment)', content))
    editorial = signal('Editorial route mentioned', ('suggest a resource','submit a resource','recommend a resource','resource suggestions','newsletter submissions'))
    restriction = signal('Contact restriction', ('no unsolicited','do not solicit','no solicitation','not accepting submissions'))
    external = set()
    for a in main.find_all('a', href=True):
        href = a['href']
        if href.startswith(('https://','http://')):
            host = host_key(href)
            if host and host != host_key(url) and not any(belongs_to(host,x) for x in EXCLUDED_HOSTS):
                external.add(host)
    competitor = any(belongs_to(host_key(url), x) for x in COMPETITOR_HOSTS)
    board_heading = bool(re.search(r'\b(job board|find jobs|search jobs|browse jobs)\b', heading))
    competitor = competitor or (board_heading and 'post a job' in content and not editorial and not resource_fit)
    score = 25*sector_fit + 15*career_fit + 20*resource_fit + 10*(len(external)>=2) + 15*editorial
    if assessment['freshness'] == 'dated_within_two_years':
        score += 5
    reasons = []
    if restriction: reasons.append('Page contains a contact/submission restriction; review it before any outreach.')
    if competitor: reasons.append('Competing job-board destination; no distinct editorial placement established.')
    if not sector_fit: reasons.append('Sector audience not established by page text.')
    if not career_fit: reasons.append('Career-seeker audience not established by page text.')
    if not resource_fit and not editorial: reasons.append('No resource, newsletter or editorial route established.')
    if assessment['freshness'] == 'older_than_two_years': reasons.append('Dated content is over two years old; current editorial relevance needs checking.')
    if newsletter_only and not career_newsletter and not editorial:
        reasons.append('A general newsletter and careers are mentioned separately; a career distribution audience needs review.')
    if not reasons:
        assessment['status'] = 'promising' if score >= 65 else 'needs_review'
        reasons.append('Relevant career audience and resource/distribution signals observed.')
    else:
        assessment['status'] = 'unsuitable' if restriction or competitor or not sector_fit or not career_fit or not (resource_fit or editorial) else 'needs_review'
    if assessment['freshness'] == 'unknown': reasons.append('No reliable page date observed; freshness remains unknown.')
    assessment.update(score=score, reasons=reasons, signals=signals, external_resource_sites=len(external))
    return assessment


def discover_prospects(sector, *, limit=5, on_date=None, active=lambda: True, persist=True, search=None):
    from django.conf import settings
    from django.core.exceptions import ValidationError
    from django.utils import timezone
    from jobs.link_models import LinkProspect
    from jobs.services import link_prospecting
    from jobs.services.safe_fetch import fetch_public_html
    if search is None:
        from ddgs import DDGS
        search = DDGS(timeout=10).text
    on_date = on_date or timezone.localdate()
    own_host = host_key(settings.SITE_URL)
    limit = min(max(limit, 1), 5)
    sites = {host_key(url) for url in LinkProspect.objects.values_list('url',flat=True)} if persist else set()
    counts = Counter(searches=0, search_failures=0, search_hits=0, pages_checked=0,
                     promising=0, needs_review=0, imported=0, duplicates=0, filtered=0, fetch_failures=0)
    report = {'version':VERSION, 'sector':sector, 'observed_at':timezone.now().isoformat(), 'queries':[], 'candidates':[], 'counts':{}, 'interrupted':False}
    started = time.monotonic()
    def available(): return time.monotonic()-started < 95 and active()
    for query in plan_queries(sector,on_date):
        if not available():
            report['interrupted']=True; break
        counts['searches']+=1; report['queries'].append(query)
        try:
            results = list(search(query, max_results=5))[:5]
        except Exception:
            counts['search_failures']+=1; continue
        counts['search_hits'] += len(results)
        for item in results:
            if not isinstance(item, dict):
                counts['filtered'] += 1
                report['candidates'].append({'decision': 'invalid_search_result', 'query': query})
                continue
            url, reason = clean_candidate_url(item.get('href'), own_host)
            row = {'url':url, 'title':str(item.get('title',''))[:255], 'query':query}
            if reason:
                counts['filtered']+=1; row['decision']=reason; report['candidates'].append(row); continue
            host = host_key(url)
            if seen_site(host, sites):
                counts['duplicates']+=1; row['decision']='duplicate_site'; report['candidates'].append(row); continue
            if counts['pages_checked'] >= 8 or counts['imported'] >= limit or not available():
                report['interrupted']=True
                row['decision']='not_checked_run_limit'
                report['candidates'].append(row)
                continue
            sites.add(host)
            fetched = fetch_public_html(url,total_timeout=6)
            counts['pages_checked']+=1
            final_url, final_reason = clean_candidate_url(fetched.final_url or url, own_host)
            if fetched.status=='ok' and final_reason:
                counts['filtered']+=1; row['decision']='excluded_redirect'; report['candidates'].append(row); continue
            final_host = host_key(final_url) if final_url else host
            if fetched.status=='ok' and final_host != host and seen_site(final_host, sites-{host}):
                counts['duplicates']+=1; row['decision']='duplicate_redirect'; report['candidates'].append(row); continue
            sites.add(final_host)
            assessment = assess_page(final_url or url,sector,fetched,today=on_date)
            row.update(decision=assessment['status'], assessment=assessment)
            report['candidates'].append(row)
            if assessment['status']=='unverified': counts['fetch_failures']+=1
            if assessment['status'] not in ('promising','needs_review'): continue
            counts[assessment['status']]+=1
            if persist and available():
                try:
                    prospect, created = link_prospecting.import_prospect(
                        {'url':final_url, 'name':assessment.get('title') or row['title'], 'sector':sector},
                        source=f'Growth discovery: {query}')
                    if created:
                        link_prospecting.inspect_page(prospect, fetched=fetched)
                        counts['imported']+=1
                except (ValidationError, ValueError):
                    counts['filtered']+=1; row['decision']='invalid_prospect'
        if counts['pages_checked']>=8 or counts['imported']>=limit or not available():
            report['interrupted']=True; break
    report['counts']=dict(counts)
    report['seconds']=round(time.monotonic()-started,2)
    return report
