"""Consistent Scout tiers, including results restored from persisted reasons."""


def tier_for_job(job):
    reasons = getattr(job, 'scout_reasons', []) or []
    uncertain = (getattr(job, 'scout_needs_confirmation', False) or
                 getattr(job, 'scout_close_match', False) or getattr(job, 'scout_caveats', []) or
                 any(str(reason).startswith(('Check:', 'Close match, not exact', 'Worth considering:'))
                     or 'needs confirmation' in str(reason).lower() for reason in reasons))
    return 'consider' if uncertain else 'good'


def groups(jobs):
    jobs = list(jobs or [])
    return [
        {'key': 'good', 'heading': 'Good matches',
         'description': 'Roles that match your requested work and pass our eligibility checks.',
         'jobs': [job for job in jobs if tier_for_job(job) == 'good']},
        {'key': 'consider', 'heading': 'Worth considering',
         'description': 'Related roles or listings with details to confirm. Each result explains the difference.',
         'jobs': [job for job in jobs if tier_for_job(job) == 'consider']},
    ]
