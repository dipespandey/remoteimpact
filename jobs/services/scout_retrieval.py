"""Bounded lexical/vector retrieval and reciprocal rank fusion for Scouts."""
from collections import Counter
import logging
import hashlib
import json
from datetime import datetime
from time import monotonic
import re

from django.conf import settings
from django.db import connection
from django.db.models import Case, FloatField, Value, When, Q
from django.core.cache import cache

from jobs.services.scout_constraints import evaluate_job,DOMAIN_VARIANTS,skill_requirement_groups

logger=logging.getLogger(__name__)


def reciprocal_rank_fusion(rankings, *, limit=400, constant=60):
    scores={};jobs={}
    for ranking in rankings:
        seen=set()
        for rank,job in enumerate(ranking,1):
            if job.pk in seen:continue
            seen.add(job.pk);jobs[job.pk]=job
            scores[job.pk]=scores.get(job.pk,0.0)+1.0/(constant+rank)
    return sorted(jobs.values(),key=lambda job:(-scores[job.pk],job.pk))[:limit]


def query_embedding(text):
    if not getattr(settings,'SCOUT_SEMANTIC_ENABLED',True) or not text:
        return None
    from jobs.services import embedding_service
    # Warmup is an operational requirement, not a blocking network download in
    # an anonymous web request. Lexical retrieval remains available on cold start.
    if embedding_service._model is None:
        return None
    import hashlib
    key='scout-query-v3:'+hashlib.sha256(text.encode()).hexdigest()
    vector=cache.get(key)
    if vector is None:
        vector=embedding_service.get_embedding(text)
        if vector is not None:cache.set(key,vector,300)
    return vector


def retrieve(qs,preferences,terms,diagnostics,relaxed=False,*,candidate_limit=200):
    retrieval_start=monotonic()
    from jobs.services.scout_workflow import retrieval_time_available
    if not retrieval_time_available():
        diagnostics['retrieval_deadline_reached']=True
        return []
    # Ranking uses vectors inside PostgreSQL; transferring the full vector and
    # tsvector for thousands of rejected rows provides no matching evidence.
    # Push only necessary conditions of the later Python gates into SQL.
    # Unknown seniority stays eligible and final source evidence still wins.
    levels=preferences.get('experience_levels') or []
    if levels:
        qs=qs.filter(Q(experience_level__in=levels)|Q(experience_level__isnull=True)|Q(experience_level=''))
    # Necessary conditions only. All required skills must occur in source
    # fields under the final evaluator. Checking an ASCII word from each skill
    # here avoids scanning thousands of unrelated long listings. Use a word,
    # rather than the whole phrase, so HTML between phrase words stays eligible.
    for group in skill_requirement_groups(preferences):
        evidence=Q();safe=True
        for skill in group:
            words=re.findall(r'[A-Za-z]{2,}',str(skill))
            if not words:safe=False;break
            word=max(words,key=len)
            evidence|=Q(title__icontains=word)|Q(description__icontains=word)|Q(requirements__icontains=word)
        if safe:qs=qs.filter(evidence)
    domains=preferences.get('required_domains') or []
    if domains:
        domain_evidence=Q();safe=True
        for domain in domains:
            for variant in DOMAIN_VARIANTS.get(str(domain).lower(),(str(domain),)):
                words=re.findall(r'[A-Za-z]{2,}',variant)
                if not words:safe=False;break
                word=max(words,key=len)
                domain_evidence|=(Q(title__icontains=word)|Q(description__icontains=word)
                    |Q(requirements__icontains=word)|Q(organization__name__icontains=word))
        if safe:qs=qs.filter(domain_evidence)
    skills={str(v).lower().replace('-',' ') for v in preferences.get('skills', [])}
    stack=skills & {'python','java','javascript','typescript','sql','c++','c#','rust','golang','ruby','php','react','django','node.js','pytorch','tensorflow','kubernetes','terraform','aws','azure'}
    if preferences.get('titles') and stack:
        evidence=Q()
        for skill in stack:
            evidence|=Q(title__icontains=skill)|Q(description__icontains=skill)|Q(requirements__icontains=skill)
        qs=qs.filter(evidence)
    requirement=preferences.get('salary_requirement')
    if requirement and requirement.get('currency') and requirement.get('period','year') in {'year','month'}:
        from decimal import Decimal, InvalidOperation
        try:amount=Decimal(str(requirement['amount'])) * (12 if requirement.get('period')=='month' else 1)
        except (KeyError,TypeError,InvalidOperation):return []
        if amount.is_finite() and amount>=0:
            if requirement.get('mode')=='floor':known=Q(salary_currency=requirement['currency'],salary_min__gte=amount)
            else:known=Q(salary_currency=requirement['currency'])&(Q(salary_max__gte=amount)|Q(salary_max__isnull=True,salary_min__gte=amount))
            if relaxed and not re.search(r'\bguaranteed\b',preferences.get('intent_text',''),re.I):
                # Unknown or non-comparable pay may be shown with a caveat;
                # a known lower salary in the requested currency never is.
                known|=Q(salary_min__isnull=True,salary_max__isnull=True)|~Q(salary_currency=requirement['currency'])
            qs=qs.filter(known)
    from django.db.models.fields.json import KeyTextTransform, KeyTransform
    qs=qs.annotate(scout_salary_period=KeyTextTransform('salary_period','raw_data'),
        scout_source_profile=KeyTransform('scout_source_profile','raw_data'),
        scout_source_period=KeyTextTransform('salaryPeriod','raw_data'),
        scout_source_pay_min=KeyTextTransform('salaryMinimum','raw_data'),
        scout_source_pay_max=KeyTextTransform('salaryMaximum','raw_data'),
        scout_source_location=KeyTextTransform('name',KeyTransform('location',KeyTransform('api_response','raw_data'))),
        scout_requisition=KeyTextTransform('requisition_id',KeyTransform('api_response','raw_data'))).defer('embedding','embedding_content_hash','search_vector','raw_data','custom_questions','company_description','how_to_apply_text','benefits')
    score=Value(0.0,output_field=FloatField())
    lexical_evidence=Q()
    for term in terms[:12]:
        lexical_evidence|=Q(title__icontains=term)|Q(skills__icontains=term)
        score+=Case(When(title__icontains=term,then=Value(8.0)),
                    When(skills__icontains=term,then=Value(4.0)),
                    default=Value(0.0),output_field=FloatField())
    if connection.vendor=='postgresql' and terms:
        from django.contrib.postgres.search import SearchQuery
        # ts_rank repeatedly scans very large imported vectors. Boolean FTS
        # evidence plus title/skill weights preserves lexical recall cheaply.
        full_text_query=SearchQuery(' OR '.join(terms[:12]),search_type='websearch')
        lexical_evidence|=Q(search_vector=full_text_query)|Q(search_vector__isnull=True)
        score+=Case(When(search_vector=full_text_query,then=Value(2.0)),default=Value(0.0),output_field=FloatField())
    lexical_qs=qs
    if connection.vendor=='postgresql' and terms and getattr(settings,'SCOUT_SOURCE_RANKING_ENABLED',True):
        # A zero-evidence row cannot contribute to lexical retrieval. Keep
        # not-yet-indexed rows and the independent semantic branch available.
        lexical_qs=qs.filter(lexical_evidence)
        diagnostics['lexical_evidence_only']=True
    lexical=lexical_qs.annotate(scout_retrieval_score=score).order_by('-scout_retrieval_score','-posted_at','-id')
    branches=[('lexical',lexical)]
    embedding_start=monotonic()
    if connection.vendor=='postgresql':
        try:
            vector=query_embedding(preferences.get('intent_text') or ' '.join(terms))
            if vector is not None:
                from pgvector.django import CosineDistance
                semantic=qs.filter(embedding__isnull=False).annotate(scout_distance=CosineDistance('embedding',vector)).order_by('scout_distance','-id')
                branches.append(('semantic',semantic))
        except Exception:
            # Neither a transient cache nor an embedding failure disables search.
            diagnostics['semantic_fallback']=True
            logger.warning('Scout semantic retrieval unavailable',exc_info=False)
    diagnostics['query_embedding_seconds']=round(monotonic()-embedding_start,4)
    # Cache ranked IDs only for initial previews, never source text or user
    # delivery history. Reload through the current availability/filter queryset
    # and rerun eligibility on every hit. Newly imported jobs may take <=60s.
    cache_key=None
    if preferences.get('_scout_preview_cache') and getattr(settings,'SCOUT_RETRIEVAL_CACHE_ENABLED',connection.vendor=='postgresql'):
        try:
            sql,params=qs.query.sql_with_params()
            canonical=[p.replace(minute=0,second=0,microsecond=0).isoformat() if isinstance(p,datetime) else p for p in params]
            payload=[sql,canonical,preferences,terms,relaxed,candidate_limit,len(branches),getattr(settings,'EMBEDDING_MODEL','')]
            cache_key='scout-candidates-v1:'+hashlib.sha256(json.dumps(payload,sort_keys=True,default=str).encode()).hexdigest()
            ids=cache.get(cache_key)
            if isinstance(ids,list) and len(ids)<=400 and all(type(pk) is int for pk in ids):
                by_id={job.pk:job for job in qs.filter(pk__in=ids)}
                result=[]
                for pk in ids:
                    job=by_id.get(pk)
                    if job is None:continue
                    eligibility=evaluate_job(job,preferences,relaxed=relaxed)
                    if eligibility.allowed:
                        job._scout_caveats=eligibility.caveats
                        result.append(job)
                diagnostics.update(retrieval_cache='hit',semantic_used=len(branches)>1,
                    retrieval_seconds=round(monotonic()-retrieval_start,4))
                return result
        except Exception:
            diagnostics['retrieval_cache']='unavailable'
    count=min(max(candidate_limit,1),400);scan=2000
    rejected=Counter();rankings=[];eligibility_cache={}
    diagnostics['retrieval']={}
    for name,query in branches:
        eligible=[];scanned=0
        load_seconds=eligibility_seconds=0.0
        # Keep SQL ordering, scan cap and eligibility semantics unchanged. A
        # server-side cursor avoids materializing 2,000 long listings when the
        # first few hundred already supply the eligible candidate limit.
        rows=query[:scan].iterator(chunk_size=100)
        try:
            while len(eligible)<count:
                if scanned % 25 == 0 and not retrieval_time_available():
                    diagnostics['retrieval_deadline_reached']=True
                    break
                load_start=monotonic()
                try:job=next(rows)
                except StopIteration:
                    load_seconds+=monotonic()-load_start
                    break
                load_seconds+=monotonic()-load_start
                eligibility_start=monotonic()
                scanned+=1
                if job.pk not in eligibility_cache:eligibility_cache[job.pk]=evaluate_job(job,preferences,relaxed=relaxed)
                result=eligibility_cache[job.pk]
                if result.allowed:
                    job._scout_caveats=result.caveats
                    eligible.append(job)
                else:rejected[result.reason]+=1
                eligibility_seconds+=monotonic()-eligibility_start
        finally:
            rows.close()
        diagnostics['retrieval'][name]={'scanned':scanned,'eligible':len(eligible),'scan_limit_reached':scanned==scan,'load_seconds':round(load_seconds,4),'eligibility_seconds':round(eligibility_seconds,4)}
        rankings.append(eligible)
    diagnostics['hard_rejections']=dict(rejected)
    diagnostics['semantic_used']=len(branches)>1
    result=reciprocal_rank_fusion(rankings)
    if cache_key and not diagnostics.get('retrieval_deadline_reached'):
        try:cache.set(cache_key,[job.pk for job in result],60)
        except Exception:diagnostics['retrieval_cache']='unavailable'
    diagnostics['retrieval_seconds']=round(monotonic()-retrieval_start,4)
    return result
