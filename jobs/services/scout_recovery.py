"""Conservative, source-backed possibilities when a model review cannot finish.

This is deliberately a small set of recognizable professions. Title AND actual
role duties must agree; a shared sector, skill or company mission is insufficient.
Unsupported requests stay empty rather than being assigned an invented function.
"""
import re

from django.conf import settings

from jobs.services.scout_constraints import evaluate_job, positive_text, plain_job_field
from jobs.services.scout_source_profile import profile_for

# Request, title, applicant-owned duty. Keep specialized functions separate.
SIGNATURES = (
    (r'\b(?:software|backend|front[- ]?end|full[- ]?stack|web|python)\s+(?:engineer\w*|develop\w*)\b',
     r'\b(?:software engineer|software developer|backend|front[- ]?end|full[- ]?stack|web developer)\b',
     r'\b(?:build|develop|design|implement|maintain|write)\b.{0,90}\b(?:software|APIs?|applications?|backend|services|code)\b'),
    (r'\bdata engineer\w*\b', r'\bdata engineer\w*\b',
     r'\b(?:build|develop|design|maintain|implement)\b.{0,90}\b(?:data pipelines?|ETL|data infrastructure|data warehouses?)\b'),
    (r'\b(?:data analys\w*|analytics)\b', r'\b(?:data analyst|analytics)\b',
     r'\b(?:analy[sz]e|visuali[sz]e|interpret)\b.{0,65}\bdata\b|\b(?:build|develop|create)\b.{0,65}\b(?:dashboards?|analytical reports?)\b'),
    (r'\b(?:product manag\w*|product owner)\b', r'\b(?:product manager|product owner)\b',
     r'\b(?:own|define|manage|prioriti[sz]e|develop)\b.{0,70}\b(?:product roadmap|product strategy|product backlog)\b'),
    (r'\bpolicy\b', r'\b(?:policy|advocacy)\b',
     r'\b(?:conduct|develop|analy[sz]e|research|lead|shape|influence)\b.{0,70}\b(?:policy|policies|legislation|advocacy|regulatory)\b'),
    (r'\b(?:UX research|user research|design research)\b', r'\b(?:UX|user|design) researcher?\b',
     r'\b(?:conduct|plan|lead|design)\b.{0,70}\b(?:user research|user interviews?|usability (?:studies|testing))\b'),
    (r'\b(?:accountant|accounting|bookkeep\w*)\b', r'\b(?:accountant|accounting|bookkeep\w*)\b',
     r'\b(?:prepare|manage|reconcile|maintain|oversee)\b.{0,70}\b(?:financial statements?|general ledger|accounts payable|accounts receivable|bookkeeping)\b'),
    (r'\b(?:grant writ\w*|fundrais\w*)\b', r'\b(?:grant writ\w*|fundrais\w*|development (?:manager|officer|director))\b',
     r'\b(?:write|prepare|develop|lead|manage|secure|cultivate)\b.{0,70}\b(?:grant proposals?|fundraising|donor relationships?|donations?)\b'),
    (r'\b(?:customer support|customer service|technical support)\b', r'\b(?:customer support|customer service|technical support)\b',
     r'\b(?:respond|resolve|troubleshoot|answer|assist|support)\b.{0,70}\b(?:customer (?:inquiries|issues|questions|requests)|support tickets?|technical issues)\b'),
    (r'\b(?:RevOps|revenue operations)\b', r'\b(?:RevOps|revenue operations)\b',
     r'\b(?:own|build|manage|optimi[sz]e|develop|maintain)\b.{0,70}\b(?:revenue operations|revenue processes|sales operations|sales pipeline|CRM)\b'),
    (r'\b(?:CRM|customer relationship management)\b', r'\b(?:CRM|salesforce|customer relationship management)\b',
     r'\b(?:own|build|manage|configure|administer|maintain)\b.{0,70}\b(?:CRM|salesforce|customer relationship management)\b'),
    (r'\bcommunications?\b', r'\b(?:communications?|editorial|press|content|copywriter)\b',
     r'\b(?:develop|create|manage|lead|execute|produce|write|design)\b.{0,70}\b(?:communications? (?:strategy|strategies|campaigns?)|press releases?|media relations|content (?:strategy|campaigns?))\b'),
    (r'\b(?:marketing|media)\b', r'\b(?:marketing|media)\b',
     r'\b(?:develop|create|manage|lead|execute|produce|write|design)\b.{0,70}\b(?:marketing campaigns?|social media|content (?:strategy|campaigns?))\b'),
    (r'\b(?:content writ\w*|writer|editor|copywriter)\b', r'\b(?:content|writer|editor|copywriter)\b',
     r'\b(?:write|edit|produce|create)\b.{0,70}\b(?:articles?|content|publications?|copy|editorials?|reports?)\b'),
    (r'\bcommunity\b', r'\bcommunity\b',
     r'\b(?:build|manage|lead|develop|coordinate|foster|engage|grow)\b.{0,70}\b(?:community|communities|member engagement|community engagement)\b'),
    (r'\b(?:law|legal|lawyer|paralegal|attorney)\b', r'\b(?:legal|lawyer|paralegal|attorney|counsel)\b',
     r'\b(?:provide|conduct|draft|review|represent|prepare|advise)\b.{0,70}\b(?:legal (?:advice|research|services|documents)|contracts?|litigation|court proceedings|clients in court)\b'),
    (r'\b(?:victim|survivor) advocacy\b', r'\b(?:victim|survivor|domestic violence|sexual assault)\b',
     r'\b(?:provide|support|advocate|assist|coordinate)\b.{0,70}\b(?:victims?|survivors?|safety planning|crisis intervention)\b'),
)


def duty_evidence(job, preferences):
    request = ' '.join(preferences.get('titles') or [])
    if not request:
        request = positive_text(preferences.get('intent_text', ''))
        if re.search(r'\b(?:background|experience|worked|skills in|previously|used to|have been)\b', request, re.I):
            return None
    title = plain_job_field(job, 'title')
    signatures = [duty for wanted, role, duty in SIGNATURES
                  if re.search(wanted, request, re.I) and re.search(role, title, re.I)]
    if not signatures:
        return None
    fields, profile = profile_for(job)
    text = fields['description']
    # Headed role sections are preferred; otherwise only a short role overview.
    spans = profile['spans']['description'] or [[0, min(len(text), 700)]]
    for start, end in spans:
        for clause in re.split(r'[.!?;•]|\s[–—]\s', text[start:end]):
            clause = clause.strip(' *:-')
            # Qualifications, company descriptions and incidental duties cannot
            # serve as affirmative role evidence during an outage.
            if re.search(r'\b(?:experience|qualifications?|familiarity|proficiency|preferred|nice to have|our company|we (?:build|develop|design)|collaborate with|assist the|support the)\b', clause, re.I):
                continue
            for pattern in signatures:
                match = re.search(pattern, clause, re.I)
                if match and re.fullmatch(r"(?:(?:you (?:will|'ll)|in this role you will)\s+)?", clause[:match.start()], re.I):
                    quote = clause[match.start():min(len(clause), match.end()+70)][:300]
                    if len(quote) >= 8 and quote in text:
                        return quote
    return None


def broad_search_evidence(job):
    """A real duty for browsing when the seeker has not specified a profession.

    This cannot establish personal suitability. The caller must retain the
    request-details and unreviewed caveats and all deterministic restrictions.
    """
    fields, profile = profile_for(job)
    text = fields['description']
    spans = profile['spans']['description'] or [[0, min(len(text), 700)]]
    for start, end in spans:
        for clause in re.split(r'[.!?;•]|\s[–—]\s', text[start:end]):
            clause = clause.strip(' *:-')
            if re.search(r'\b(?:experience|qualifications?|required|proficiency|familiarity|ability to|our company|we (?:build|develop|design))\b', clause, re.I):
                continue
            if re.match(r"(?:(?:you will|you.ll)\s+)?(?:write|edit|build|develop|design|manage|coordinate|maintain|provide|support|lead|analyze|analyse)\s+\S.{12,}", clause, re.I):
                return clause[:300]
    return None


def recover(candidates, preferences, selected, diagnostics, *, limit=3, rejected_ids=()):
    if limit <= 0 or not getattr(settings, 'SCOUT_RULE_RECOVERY_ENABLED', True):
        return []
    from jobs.services.job_scout_service import JobScoutService
    from jobs.services.scout_evidence import source_value
    from jobs.services.scout_review import requested_checks
    from jobs.services.scout_verification import needs_role_details
    broad = needs_role_details(preferences)
    blocked = set(rejected_ids)
    seen = set()
    def keys(job):
        req = source_value(job, 'scout_requisition', 'api_response', 'requisition_id')
        identity = ('req', job.organization_id, str(req)) if req else JobScoutService._repost_key(job)
        return ('job', job.pk), identity
    for job in selected:
        seen.update(keys(job))
    picked = []
    # Existing retrieval order is stable; no database scan or API request here.
    for job in candidates[:400]:
        if job.pk in blocked or any(key in seen for key in keys(job)):
            continue
        if getattr(job, 'scout_remote_policy', {}).get('label') == 'incompatible':
            continue
        eligibility = evaluate_job(job, preferences, relaxed=True)
        if not eligibility.allowed:
            continue
        quote = broad_search_evidence(job) if broad else duty_evidence(job, preferences)
        if not quote:
            continue
        job.scout_caveats = list(dict.fromkeys([*getattr(job, 'scout_caveats', []),
            *eligibility.caveats, 'source_review_pending',
            *(['request_details_needed'] if broad else []),
            *['review_' + name for name in requested_checks(preferences) if name != 'function']]))
        job.scout_needs_confirmation = True
        job.scout_reasons = ['Listing duties: ' + quote]
        seen.update(keys(job)); picked.append(job)
        if len(picked) >= limit:
            break
    diagnostics['source_rule_recovery'] = len(picked)
    return picked
