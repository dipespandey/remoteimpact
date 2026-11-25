"""Consistent Scout tiers, including results restored from persisted reasons."""


CLOSE_LABEL = 'Close match, not exact'
ORDER = {'good': 0, 'consider': 1, 'also': 2}


def tier_for_job(job):
    reasons = getattr(job, 'scout_reasons', []) or []
    if getattr(job, 'scout_close_match', False) or any(str(reason).startswith(CLOSE_LABEL) for reason in reasons):
        return 'also'
    uncertain = (getattr(job, 'scout_needs_confirmation', False) or
                 getattr(job, 'scout_close_match', False) or getattr(job, 'scout_caveats', []) or
                 any(str(reason).startswith(('Check:', 'Worth considering:'))
                     or 'needs confirmation' in str(reason).lower() for reason in reasons))
    return 'consider' if uncertain else 'good'


def groups(jobs):
    jobs = list(jobs or [])
    return [
        {'key': 'good', 'heading': 'Good matches',
         'description': 'Roles that match your requested work and pass our eligibility checks.',
         'jobs': [job for job in jobs if tier_for_job(job) == 'good']},
        {'key': 'consider', 'heading': 'Worth considering',
         'description': 'Relevant work with differences or details to confirm. Review the checks before applying.',
         'jobs': [job for job in jobs if tier_for_job(job) == 'consider']},
        {'key': 'also', 'heading': 'We also found these',
         'description': 'Close to what you asked for, but not an exact match. Each one says how it differs.',
         'jobs': [job for job in jobs if tier_for_job(job) == 'also']},
    ]
