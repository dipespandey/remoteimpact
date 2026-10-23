"""Dated editorial selections, separate from automated catalogue previews."""
import json
import re
from datetime import date, timedelta
from functools import lru_cache
from pathlib import Path
from urllib.parse import urlsplit

from django.utils import timezone

from jobs.models import Job

REVIEW_DAYS = 7
KEY = re.compile(r'^[a-z0-9]+(?:-[a-z0-9]+)*$')
PICK_TEXT_FIELDS = ('job_slug', 'title', 'organization', 'location_summary',
                    'experience_summary', 'pay_summary', 'editorial_note')


def _https(value):
    parsed = urlsplit(value)
    return parsed.scheme == 'https' and bool(parsed.hostname) and not parsed.username and not parsed.password


def validate_issues(rows, today):
    seen = set()
    for issue in rows:
        published = date.fromisoformat(issue['published_on'])
        if issue['slug'] != published.isoformat() or issue['slug'] in seen or published > today:
            raise ValueError('Issue dates must be unique and not in the future')
        seen.add(issue['slug'])
        if not issue['title'] or not issue['introduction'] or not issue['picks']:
            raise ValueError('Issue needs a title, introduction and picks')
        job_ids = set()
        for key, pick in issue['picks'].items():
            checked = date.fromisoformat(pick['checked_on'])
            if not KEY.fullmatch(key) or checked.isoformat() != pick['checked_on'] or checked > published:
                raise ValueError('Invalid pick key or source-check date')
            for field in ('job_id', 'organization_id'):
                if type(pick[field]) is not int or pick[field] < 1:
                    raise ValueError('Pick identities must be positive integers')
            if pick['job_id'] in job_ids:
                raise ValueError('A reviewed job belongs in the registry once')
            job_ids.add(pick['job_id'])
            if any(not isinstance(pick[field], str) or not pick[field].strip() for field in PICK_TEXT_FIELDS):
                raise ValueError('Pick descriptions must be nonempty strings')
            if not KEY.fullmatch(pick['job_slug']) or any(not _https(pick[field]) for field in ('source_url', 'expected_catalogue_application_url')):
                raise ValueError('Pick needs a slug and HTTPS source URLs')
        section_slugs = set()
        referenced = set()
        for section in issue['sections']:
            keys = section['pick_keys']
            path = urlsplit(section['search_path'])
            if (not KEY.fullmatch(section['slug']) or section['slug'] in section_slugs
                    or not section['title'] or not section['description']
                    or not keys or len(keys) != len(set(keys)) or not set(keys) <= issue['picks'].keys()
                    or not section['search_path'].startswith('/') or section['search_path'].startswith('//')
                    or path.scheme or path.netloc or '\\' in section['search_path']):
                raise ValueError('Invalid issue section')
            section_slugs.add(section['slug'])
            referenced.update(keys)
        if referenced != issue['picks'].keys():
            raise ValueError('Every pick must appear in a section')
    return rows


@lru_cache(maxsize=1)
def registry():
    path = Path(__file__).resolve().parents[1] / 'data' / 'roundup_issues.json'
    return json.loads(path.read_text())


def issue(slug, today=None):
    today = today or timezone.localdate()
    rows = validate_issues(registry(), today)
    raw = next((row for row in rows if row['slug'] == slug), None)
    if raw is None:
        return None
    jobs = {job.pk: job for job in Job.objects.filter(
        Job.availability_filter(), pk__in=[pick['job_id'] for pick in raw['picks'].values()]
    ).select_related('organization')}
    cards = {}
    for key, pick in raw['picks'].items():
        job = jobs.get(pick['job_id'])
        identity_matches = bool(job and job.slug == pick['job_slug']
            and job.organization_id == pick['organization_id']
            and job.title == pick['title'] and job.organization.name == pick['organization']
            and job.application_url == pick['expected_catalogue_application_url'])
        checked = date.fromisoformat(pick['checked_on'])
        cards[key] = {**pick, 'key': key, 'checked_date': checked,
                      'review_due': today > checked + timedelta(days=REVIEW_DAYS),
                      'job_url': job.get_absolute_url() if identity_matches else '',
                      'available': identity_matches}
    published = date.fromisoformat(raw['published_on'])
    return {**raw, 'published_date': published,
            'review_due': any(card['review_due'] for card in cards.values()),
            'sections': [{**section, 'cards': [cards[key] for key in section['pick_keys']]}
                         for section in raw['sections']],
            'pick_count': len(cards), 'review_days': REVIEW_DAYS}
