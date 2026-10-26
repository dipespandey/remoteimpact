"""Source-offset profiles for duties-first retrieval. No generated job facts.

Profiles contain offsets into canonical source text, never model summaries.
They may prioritize candidates for review; they cannot establish eligibility.
"""
import hashlib
import json
import re

VERSION = 6
KEY = 'scout_source_profile'
MAX_SPANS = 16
MAX_DUTIES = 6000
START = re.compile(
    r'\b(?:core responsibilities|primary responsibilities|key responsibilities|'
    r'key duties|essential duties|responsibilities|what you(?:[’\']ll| will) do|'
    r'what you get to do|what you(?:[’\']ll| will) be doing|'
    r'about (?:this|the) role|position overview|position summary|job summary|'
    r'role overview|your (?:role|responsibilities)|the opportunity)\b\s*[:!*]*', re.I)
STOP = re.compile(
    r'\b(?:Qualifications|Requirements|Required Qualifications|Preferred Qualifications|'
    r'What You Bring|What You Will Bring|What We(?:[’\']re| Are) Looking For|'
    r'Who You Are|Benefits|Compensation|About (?:Us|The Company|Our Company)|'
    r'Equal Opportunity|How To Apply)\b|\b(?:qualifications|requirements|benefits)\s*:', re.I)
WORDS = re.compile(r'[a-z][a-z0-9+#]{2,}', re.I)
CANONICAL = {'accountant':'accounting','accountants':'accounting',
    'engineer':'engineering','engineers':'engineering',
    'researcher':'research','researchers':'research',
    'policies':'policy','fundraiser':'fundraising','fundraisers':'fundraising',
    'designer':'design','designers':'design','designing':'design',
    'communications':'communication','operational':'operations',
    'audits':'audit','auditing':'audit','controls':'control',
    'assessments':'assessment','workflows':'workflow','toolkits':'toolkit',
    'regulations':'regulatory'}
GENERIC = frozenset('remote worldwide global europe european jobs job roles role positions position '
    'work working career experience years year using use with and the for from that this '
    'senior junior entry mid level least salary paid annual per internships internship '
    'manager management director lead head officer associate analyst specialist coordinator '
    'mission impact driven nonprofit nonprofits organization organizations '.split())


def source_fields(job):
    from jobs.services.scout_constraints import plain_job_field
    from jobs.services.scout_evidence import source_location
    return {**{field: plain_job_field(job,field) for field in ('title','description','requirements','location','country')},
        '_source_location':str(source_location(job) or '')}


def source_hash(fields):
    return hashlib.sha256(json.dumps(fields,sort_keys=True,ensure_ascii=False).encode()).hexdigest()


def duty_spans(fields):
    spans={}
    for field in ('description','requirements'):
        text=fields[field]
        found=[]
        for heading in START.finditer(text):
            start=heading.end()
            # Stop only at a plausible heading. Lowercase "identify user
            # requirements" in a duty is not the Qualifications section.
            end=len(text)
            for stop in STOP.finditer(text,start):
                token=stop[0]
                if token[0].isupper() or token.rstrip().endswith(':'):
                    end=stop.start();break
            end=min(end,start+MAX_DUTIES)
            if end-start<20:continue
            if any(start>=a and end<=b for a,b in found):continue
            found.append([start,end])
            if len(found)>=MAX_SPANS:break
        spans[field]=found
    return spans


def build_profile(fields,*,geography=True,workplace=True,spans=True):
    spans = duty_spans(fields) if spans else None
    from jobs.services.scout_constraints import extract_location_evidence
    from jobs.services.scout_review import workplace_conflict
    return {'version':VERSION,'source_hash':source_hash(fields),'spans':spans,
        'geography':extract_location_evidence(fields) if geography else None,
        'mandatory_workplace':workplace_conflict(None,{'intent_text':'remote work'},' '.join(fields[f] for f in ('title','description','requirements')), title=fields['title']) if workplace else None}


def profile_for(job,*,geography=False,workplace=False):
    from django.conf import settings
    needs_spans = not (geography or workplace) or not getattr(settings, 'SCOUT_LAZY_DUTY_SPANS', True)
    fields=source_fields(job)
    local=getattr(job,'_scout_source_profile',None)
    inputs=(VERSION,*fields.values())
    if (isinstance(local,dict) and local.get('version')==VERSION
            and getattr(job,'_scout_source_profile_inputs',None)==inputs):
        if needs_spans and local.get('spans') is None:
            local['spans'] = duty_spans(fields)
        if geography and local.get('geography') is None:
            from jobs.services.scout_constraints import extract_location_evidence
            local['geography']=extract_location_evidence(fields)
        if workplace and local.get('mandatory_workplace') is None:
            from jobs.services.scout_review import workplace_conflict
            local['mandatory_workplace']=workplace_conflict(None,{'intent_text':'remote work'},' '.join(fields[f] for f in ('title','description','requirements')), title=fields['title'])
        return fields,local
    digest=source_hash(fields)
    data=getattr(job,'__dict__',{})
    stored=data.get('scout_source_profile')
    if stored is None:stored=(data.get('raw_data') or {}).get(KEY)
    valid=isinstance(stored,dict) and stored.get('version')==VERSION and stored.get('source_hash')==digest
    valid=valid and type(stored.get('mandatory_workplace')) is bool
    if valid:
        geo=stored.get('geography')
        valid=(isinstance(geo,dict) and set(geo)=={'offered','offered_states','denied','explicit_global','title_region'}
            and type(geo.get('explicit_global')) is bool and geo.get('title_region') in (None,'africa','europe','asia'))
        if valid:
            for name in ('offered','offered_states','denied'):
                if not isinstance(geo[name],list) or len(geo[name])>250 or any(not isinstance(v,str) for v in geo[name]):
                    valid=False;break
    if valid:
        spans=stored.get('spans')
        valid=isinstance(spans,dict) and set(spans)=={'description','requirements'}
        if valid:
            for field,values in spans.items():
                if not isinstance(values,list) or len(values)>MAX_SPANS:
                    valid=False;break
                if any(not isinstance(v,list) or len(v)!=2 or any(type(n) is not int for n in v)
                       or not 0<=v[0]<v[1]<=len(fields[field]) or v[1]-v[0]>MAX_DUTIES for v in values):
                    valid=False;break
    from jobs.services.scout_workflow import source_profile_memo
    cached = source_profile_memo()
    memo, diagnostics = cached if cached is not None else (None, None)
    key = (VERSION, digest)
    profile = stored if valid else memo.get(key) if memo is not None else None
    if not valid and profile is not None:
        diagnostics['source_profile_cache_hits'] = diagnostics.get('source_profile_cache_hits', 0) + 1
    if profile is None:
        profile = build_profile(fields, geography=geography, workplace=workplace, spans=needs_spans)
        if memo is not None:
            if len(memo) >= 4096:
                memo.clear()
            memo[key] = profile
    else:
        if needs_spans and profile.get('spans') is None:
            profile['spans'] = duty_spans(fields)
        if geography and profile.get('geography') is None:
            from jobs.services.scout_constraints import extract_location_evidence
            profile['geography'] = extract_location_evidence(fields)
        if workplace and profile.get('mandatory_workplace') is None:
            from jobs.services.scout_review import workplace_conflict
            profile['mandatory_workplace'] = workplace_conflict(None, {'intent_text':'remote work'}, ' '.join(fields[f] for f in ('title','description','requirements')), title=fields['title'])

    job._scout_source_profile=profile
    job._scout_source_profile_inputs=inputs
    job._scout_source_profile_prepared=valid
    return fields,profile


def duties(job,field='description'):
    fields,profile=profile_for(job)
    return _duties_from_profile(fields, profile, field)


def _duties_from_profile(fields, profile, field):
    text=fields[field]
    spans=profile['spans'][field]
    # Unstructured short listings generally start with a role summary; keep
    # the fallback modest so a long company introduction cannot dominate.
    return ' […] '.join(text[a:b] for a,b in spans) if spans else text[:450]


def request_terms(preferences):
    from jobs.services.scout_constraints import positive_text
    text=' '.join(preferences.get('titles') or []) or positive_text(preferences.get('intent_text',''))
    text+=' '+' '.join(preferences.get('required_skills') or [])
    # Stable, query-grounded stems. This ranks evidence, not professional fit.
    return list(dict.fromkeys(CANONICAL.get(w.lower(),w.lower()) for w in WORDS.findall(text)
        if w.lower() not in GENERIC))[:24]


def function_priority(job,preferences,*,terms=None):
    if terms is None:terms=request_terms(preferences)
    if not terms:return 0
    fields,profile=profile_for(job)
    title=fields['title'].lower()
    body=_duties_from_profile(fields,profile,'description').lower()
    if fields['requirements']!=fields['description'] and profile['spans']['requirements']:
        body+=' '+_duties_from_profile(fields,profile,'requirements').lower()
    title_words={CANONICAL.get(w,w) for w in WORDS.findall(title)}
    body_words={CANONICAL.get(w,w) for w in WORDS.findall(body)}
    return sum(4*(term in title_words)+2*(term in body_words) for term in terms)


def duty_excerpt(job,preferences,limit=700):
    text=duties(job)
    from jobs.services.scout_review import excerpt
    return excerpt(text,' '.join(request_terms(preferences)),limit)


def shortlist(candidates,preferences,*,limit=12,anchors=()):
    """Order source-backed candidates, keeping distinct requisitions distinct."""
    from jobs.services.scout_evidence import source_value
    terms=request_terms(preferences)
    ordered=sorted(candidates,key=lambda j:(-function_priority(j,preferences,terms=terms),
        -getattr(j,'scout_score',0),len(getattr(j,'scout_caveats',[])),j.pk))
    # Retain a few strongest original matches while adding duty-led recovery;
    # a new lexical projection must not erase the existing retrieval signal.
    ordered=[*anchors,*ordered]
    selected=[];seen=set()
    for job in ordered:
        req=source_value(job,'scout_requisition','api_response','requisition_id')
        # A regional copy must not consume multiple scarce review slots.
        key=('req',job.organization_id,str(req)) if req else ('job',job.pk)
        if key in seen:continue
        seen.add(key);selected.append(job)
        if len(selected)>=limit:break
    return selected
