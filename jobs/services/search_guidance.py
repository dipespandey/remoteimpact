"""Focused search routes and eligibility answers; no inferred applicant qualification."""
from urllib.parse import urlencode
from django.urls import reverse


def category_search_guidance(slug):
    profiles = {
        'climate-environment': {
            'heading': 'Find a climate role that fits',
            'summary': 'Compare the work, hiring location and experience required. Remote climate jobs can still involve site visits or fieldwork.',
            'links': [('Europe / EMEA location labels', {'country': 'Europe'}), ('Entry-level listings', {'experience': 'entry'}), ('Climate policy search', {'q': 'policy'})],
            'question': 'Can I do a remote climate job from Europe?',
            'answer': 'Check the hiring countries, work authorization, time-zone overlap and travel requirements in each employer posting. Europe and EMEA location labels help narrow a search, but do not establish that every country in those regions is eligible.',
            'snapshot': 'climate-hiring',
        },
        'global-health': {
            'heading': 'Find your route into global health',
            'summary': 'Explore research, data, communications and programme work. Home-based consultancy can still require travel, local residence or a professional licence.',
            'links': [('Entry-level listings', {'experience': 'entry'}), ('Communications search', {'q': 'communications'}), ('Worldwide location labels', {'country': 'Worldwide'})],
            'question': 'Are remote global health jobs open to international applicants?',
            'answer': 'Eligibility depends on the employer and role. Global health describes the work, not worldwide hiring. Check permitted countries, employment or consultancy terms, professional qualifications and any fieldwork before applying.',
            'snapshot': 'global-health-hiring',
        },
        'ai-safety': {
            'heading': 'Explore different routes into AI safety',
            'summary': 'Research, engineering, policy and operations have different requirements. The category includes wider roles at relevant organizations; check what the vacancy actually involves.',
            'links': [('Entry-level listings', {'experience': 'entry'}), ('Operations search', {'q': 'operations'}), ('Policy search', {'q': 'policy'})],
            'question': 'Do I need a PhD to work in AI safety?',
            'answer': 'There is no single qualification rule across these vacancies. Some research roles require advanced study, while engineering, policy, communications and operations roles can set different requirements. Read the actual qualifications; a non-research title does not guarantee that no degree or experience is required.',
            'snapshot': 'ai-safety-hiring',
        },
    }
    profile = profiles.get(slug)
    if not profile:
        return None
    profile = dict(profile)
    base = reverse('jobs:category_landing', args=[slug])
    profile['links'] = [{'label': label, 'url': base + '?' + urlencode(query)} for label, query in profile['links']]
    profile['snapshot_url'] = reverse('jobs:hiring_evidence', kwargs={'topic': profile['snapshot']})
    profile['early_careers_url'] = reverse('jobs:early_careers')
    return profile
