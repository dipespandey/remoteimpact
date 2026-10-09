"""Bounded lexical/vector retrieval and reciprocal rank fusion for Scouts."""
from collections import Counter
import logging
from time import monotonic

from django.conf import settings
from django.db import connection
from django.db.models import Case, FloatField, Value, When, Q
from django.core.cache import cache

from jobs.services.scout_constraints import evaluate_job

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


def retrieve(qs,preferences,terms,diagnostics,relaxed=False):
    retrieval_start=monotonic()
    # Ranking uses vectors inside PostgreSQL; transferring the full vector and
    # tsvector for thousands of rejected rows provides no matching evidence.
    # Push only necessary conditions of the later Python gates into SQL.
    # Unknown seniority stays eligible and final source evidence still wins.
    levels=preferences.get('experience_levels') or []
    if levels:
        qs=qs.filter(Q(experience_level__in=levels)|Q(experience_level__isnull=True)|Q(experience_level=''))
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
            if relaxed:
                # Unknown or non-comparable pay may be shown with a caveat;
                # a known lower salary in the requested currency never is.
                known|=Q(salary_min__isnull=True,salary_max__isnull=True)|~Q(salary_currency=requirement['currency'])
            qs=qs.filter(known)
    from django.db.models.fields.json import KeyTextTransform, KeyTransform
    qs=qs.annotate(scout_salary_period=KeyTextTransform('salary_period','raw_data'),
        scout_source_period=KeyTextTransform('salaryPeriod','raw_data'),
        scout_source_location=KeyTextTransform('name',KeyTransform('location',KeyTransform('api_response','raw_data'))),
        scout_requisition=KeyTextTransform('requisition_id',KeyTransform('api_response','raw_data'))).defer('embedding','embedding_content_hash','search_vector','raw_data','custom_questions','company_description','how_to_apply_text','benefits')
    score=Value(0.0,output_field=FloatField())
    for term in terms[:12]:
        score+=Case(When(title__icontains=term,then=Value(8.0)),
                    When(skills__icontains=term,then=Value(4.0)),
                    default=Value(0.0),output_field=FloatField())
    if connection.vendor=='postgresql' and terms:
        from django.contrib.postgres.search import SearchQuery
        # ts_rank repeatedly scans very large imported vectors. Boolean FTS
        # evidence plus title/skill weights preserves lexical recall cheaply.
        full_text_query=SearchQuery(' OR '.join(terms[:12]),search_type='websearch')
        score+=Case(When(search_vector=full_text_query,then=Value(2.0)),default=Value(0.0),output_field=FloatField())
    lexical=qs.annotate(scout_retrieval_score=score).order_by('-scout_retrieval_score','-posted_at','-id')
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
    count=200;scan=2000
    rejected=Counter();rankings=[];eligibility_cache={}
    diagnostics['retrieval']={}
    for name,query in branches:
        eligible=[];scanned=0
        load_start=monotonic()
        rows=list(query[:scan])
        load_seconds=monotonic()-load_start
        eligibility_start=monotonic()
        for job in rows:
            scanned+=1
            if job.pk not in eligibility_cache:eligibility_cache[job.pk]=evaluate_job(job,preferences,relaxed=relaxed)
            result=eligibility_cache[job.pk]
            if result.allowed:
                job._scout_caveats=result.caveats
                eligible.append(job)
            else:rejected[result.reason]+=1
            if len(eligible)>=count:break
        diagnostics['retrieval'][name]={'scanned':scanned,'eligible':len(eligible),'scan_limit_reached':scanned==scan,'load_seconds':round(load_seconds,4),'eligibility_seconds':round(monotonic()-eligibility_start,4)}
        rankings.append(eligible)
    diagnostics['hard_rejections']=dict(rejected)
    diagnostics['semantic_used']=len(branches)>1
    result=reciprocal_rank_fusion(rankings)
    diagnostics['retrieval_seconds']=round(monotonic()-retrieval_start,4)
    return result
