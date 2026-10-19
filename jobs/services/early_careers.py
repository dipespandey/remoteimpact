"""Reviewed programme directory, separate from live vacancies and job schema."""
import json
from datetime import date, timedelta
from functools import lru_cache
from pathlib import Path

from django.utils import timezone

FILTERS = {
    'kind': {'fellowship': 'Fellowships', 'internship': 'Internships', 'mentorship': 'Mentorships', 'training': 'Courses & career programmes', 'graduate': 'Graduate programmes'},
    'audience': {'first-step': 'First career step', 'research-ready': 'Technical / research preparation', 'career-switcher': 'Changing sectors'},
    'funding': {'paid': 'Paid / stipend', 'free': 'Free learning / costs covered', 'unpaid': 'Unpaid placement', 'fee': 'Fees apply', 'unknown': 'Funding to confirm'},
    'mode': {'remote': 'Fully online / remote', 'onsite': 'In person', 'hybrid': 'Remote with required travel', 'mixed': 'Varies by placement', 'unknown': 'Format to confirm'},
    'status': {'open': 'Applications open', 'listings': 'Browse vacancies / projects', 'check': 'Check next intake', 'closed': 'Closed round'},
}
REVIEW_DAYS = 14


@lru_cache(maxsize=1)
def registry():
    path = Path(__file__).resolve().parents[1] / 'data' / 'early_careers.json'
    return json.loads(path.read_text())


def opportunities(today=None):
    today = today or timezone.localdate()
    rows = []
    for raw in registry():
        row = dict(raw)
        checked = date.fromisoformat(row['checked_on'])
        deadline = date.fromisoformat(row['deadline']) if row.get('deadline') else None
        row['review_due'] = today > checked + timedelta(days=REVIEW_DAYS)
        if deadline and today > deadline:
            row['status'] = 'closed'
        elif row['review_due'] and row['status'] in {'open', 'listings'}:
            row['status'] = 'check'
        row['checked_date'] = checked
        row['deadline_date'] = deadline
        row['closing_soon'] = row['status'] == 'open' and deadline is not None and today <= deadline <= today + timedelta(days=7)
        for key, choices in FILTERS.items():
            row[key + '_label'] = choices[row[key]]
        row['action_label'] = 'View application details' if row['status'] == 'open' else 'View official programme'
        rows.append(row)
    return rows
