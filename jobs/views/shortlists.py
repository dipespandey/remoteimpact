from datetime import timedelta
from urllib.parse import urlencode

from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth import get_user_model
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Q
from django.http import Http404, HttpResponseBadRequest
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_safe, require_POST, require_http_methods

from jobs.models import (
    AssistantSubscription, ConsumerFunnelEvent, EmployerFunnelEvent, Job,
    JobInterest, Referral, SharedShortlist,
)
from jobs.services.candidate_suggestions import suggested_candidates
from jobs.services.funnel import record_consumer_event, record_employer_event
from jobs.services.shortlists import candidate_jobs, live_jobs, visible_interests


class InterestForm(forms.ModelForm):
    class Meta:
        model = JobInterest
        fields = ["status", "full_name", "headline", "evidence", "portfolio_url", "eligibility", "availability", "share_with_employer"]
        labels = {
            "status": "My progress", "headline": "Your role or specialism",
            "evidence": "Relevant experience", "eligibility": "Where can you legally work?",
            "availability": "When could you start?", "portfolio_url": "Portfolio or LinkedIn URL",
        }
        widgets = {"evidence": forms.Textarea(attrs={"rows": 4})}

    def __init__(self, *args, can_share=False, **kwargs):
        super().__init__(*args, **kwargs)
        self.can_share = can_share
        if not can_share:
            for key in list(self.fields):
                if key != "status":
                    del self.fields[key]

    def clean(self):
        data = super().clean()
        sharing = data.get("share_with_employer")
        if sharing and data.get("status") in (JobInterest.Status.ARCHIVED, JobInterest.Status.NOT_RELEVANT):
            self.add_error("share_with_employer", "Turn off sharing before archiving this role.")
        if sharing:
            for field in ("full_name", "headline", "evidence", "eligibility"):
                if not data.get(field):
                    self.add_error(field, "Add this before sharing with the employer.")
        return data

    def clean_portfolio_url(self):
        value = self.cleaned_data.get("portfolio_url", "")
        if value and not value.lower().startswith(("https://", "http://")):
            raise forms.ValidationError("Use an https:// or http:// URL.")
        return value


@never_cache
@login_required
@require_safe
def shortlist(request):
    interests = {item.job_id: item for item in JobInterest.objects.filter(user=request.user)}
    status = request.GET.get("status", "all")
    jobs = candidate_jobs(request.user).select_related("organization", "category")
    if status in JobInterest.Status.values:
        jobs = jobs.filter(pk__in=[pk for pk, item in interests.items() if item.status == status])
    elif status != "all":
        status = "all"
    else:
        jobs = jobs.exclude(pk__in=[pk for pk, item in interests.items() if item.status in ("archived", "not_relevant")])
    page = Paginator(jobs.order_by("-posted_at", "-pk"), 20).get_page(request.GET.get("page"))
    for job in page:
        job.interest = interests.get(job.pk)
    record_consumer_event(ConsumerFunnelEvent.Event.SHORTLIST_VIEWED, user=request.user,
                          dedupe_key=f"shortlist:{request.user.pk}:{timezone.localdate()}")
    return render(request, "jobs/shortlists/workspace.html", {
        "page_obj": page, "status": status, "statuses": JobInterest.Status.choices,
        "is_pro": AssistantSubscription.get_or_create_for_user(request.user).is_pro_active,
        "shares": SharedShortlist.objects.filter(owner=request.user, revoked=False).order_by("-created_at")[:20],
    })


@never_cache
@login_required
@require_http_methods(["GET", "POST"])
def interest(request, slug):
    job = get_object_or_404(Job.objects.select_related("organization"), slug=slug)
    existing = JobInterest.objects.filter(user=request.user, job=job).first()
    if not job.is_available and not existing:
        raise Http404
    can_share = bool(job.poster_id and job.is_paid and job.is_available and job.poster_id != request.user.pk)
    initial = {}
    if not existing:
        initial["full_name"] = request.user.get_full_name()
        profile = getattr(request.user, "seeker_profile", None)
        if profile:
            initial.update(headline=profile.headline, portfolio_url=profile.linkedin_url)
    form = InterestForm(request.POST if request.method == "POST" else None,
                        instance=existing, initial=initial, can_share=can_share)
    if request.method == "POST" and form.is_valid():
        with transaction.atomic():
            # Serialize writes for this candidate, including simultaneous first saves.
            get_user_model().objects.select_for_update().get(pk=request.user.pk)
            obj, _ = JobInterest.objects.get_or_create(user=request.user, job=job)
            was_sharing = obj.share_with_employer
            for key, value in form.cleaned_data.items():
                setattr(obj, key, value)
            if not can_share:
                obj.share_with_employer = False
            if obj.share_with_employer and (not was_sharing or not obj.consented_at or obj.consented_at < timezone.now() - timedelta(days=90)):
                obj.consented_at = timezone.now()
                obj.employer_stage = JobInterest.Stage.NEW
            if not obj.share_with_employer:
                obj.consented_at = None
                obj.employer_stage = JobInterest.Stage.NEW
            obj.save()
        record_consumer_event(ConsumerFunnelEvent.Event.INTEREST_SAVED, user=request.user,
                              metadata={"job_id": job.pk, "status": obj.status})
        if obj.share_with_employer and not was_sharing:
            record_consumer_event(ConsumerFunnelEvent.Event.EMPLOYER_OPT_IN, user=request.user, metadata={"job_id": job.pk})
        messages.success(request, "Updated your shortlist." if existing else "Added to your shortlist.")
        return redirect("jobs:shortlist")
    return render(request, "jobs/shortlists/interest.html", {"job": job, "form": form, "can_share": can_share, "interest": existing})


def employer_job(request, slug):
    return get_object_or_404(Job.objects.select_related("organization"), slug=slug, poster=request.user)


@never_cache
@login_required
@require_safe
def employer_shortlist(request, slug):
    job = employer_job(request, slug)
    candidates = visible_interests(job).select_related("user").order_by("-consented_at") if job.is_paid else JobInterest.objects.none()
    page = Paginator(candidates, 20).get_page(request.GET.get("page"))
    suggestions = suggested_candidates(job) if job.is_paid and job.is_available else []
    record_employer_event(EmployerFunnelEvent.Event.SHORTLIST_VIEWED, user=request.user, job=job,
                          metadata={"suggestions": len(suggestions)})
    return render(request, "jobs/shortlists/employer.html", {
        "job": job, "page_obj": page, "stages": JobInterest.Stage.choices, "suggestions": suggestions,
    })


@never_cache
@login_required
@require_POST
def employer_stage(request, slug, pk):
    job = employer_job(request, slug)
    if not job.is_paid:
        raise Http404
    stage = request.POST.get("stage")
    if stage not in JobInterest.Stage.values:
        return HttpResponseBadRequest("Invalid stage")
    if not visible_interests(job).filter(pk=pk).update(employer_stage=stage):
        raise Http404
    record_employer_event(EmployerFunnelEvent.Event.CANDIDATE_REVIEWED, user=request.user, job=job, metadata={"stage": stage})
    messages.success(request, "Review stage updated. No email has been sent.")
    return redirect("jobs:employer_shortlist", slug=job.slug)


@never_cache
@login_required
@require_POST
def share(request):
    ids = request.POST.getlist("jobs")
    if not ids or len(ids) > 10 or any(not value.isdigit() for value in ids):
        messages.error(request, "Select between 1 and 10 available jobs to share.")
        return redirect("jobs:shortlist")
    selected = live_jobs().filter(pk__in=ids)
    selected = list(selected.filter(pk__in=candidate_jobs(request.user).values("pk")))
    if len(selected) != len(set(ids)):
        return HttpResponseBadRequest("Only available jobs from your shortlist can be shared.")
    with transaction.atomic():
        get_user_model().objects.select_for_update().get(pk=request.user.pk)
        if SharedShortlist.objects.filter(owner=request.user, revoked=False).count() >= 20:
            messages.error(request, "Revoke an older collection before sharing another.")
            return redirect("jobs:shortlist")
        collection = SharedShortlist.objects.create(owner=request.user)
        collection.jobs.set(selected)
        Referral.get_or_create_for_user(request.user)
    record_consumer_event(ConsumerFunnelEvent.Event.COLLECTION_CREATED, user=request.user, metadata={"job_count": len(selected)})
    return redirect("jobs:shared_shortlist", token=collection.token)


@never_cache
@require_safe
def shared(request, token):
    collection = get_object_or_404(SharedShortlist, token=token, revoked=False)
    jobs = live_jobs().filter(shared_shortlists=collection).select_related("organization", "category").order_by("-posted_at", "-pk")
    # A random referral code attributes signups; no owner data is rendered.
    referral = Referral.get_or_create_for_user(collection.owner)
    if not request.user.is_authenticated or request.user.pk != collection.owner_id:
        request.session["referral_code"] = referral.code
        if not request.session.session_key:
            request.session.create()
        record_consumer_event(ConsumerFunnelEvent.Event.COLLECTION_VIEWED, user=request.user,
            metadata={"collection": str(token)}, dedupe_key=f"collection:{token}:{request.session.session_key}")
    response = render(request, "jobs/shortlists/public.html", {"collection": collection, "jobs": jobs,
        "is_owner": request.user.is_authenticated and request.user.pk == collection.owner_id,
        "share_url": request.build_absolute_uri(reverse("jobs:shared_shortlist", args=[token])),
        "scout_url": reverse("jobs:job_alerts") + "?" + urlencode({"source": "shared_shortlist", "ref": referral.code}),
        "collection_title": "Remote impact job picks", "private_collection": True})
    response["X-Robots-Tag"] = "noindex, nofollow"
    return response


@never_cache
@login_required
@require_POST
def revoke(request, token):
    collection = get_object_or_404(SharedShortlist, token=token, owner=request.user)
    collection.revoked = True
    collection.save(update_fields=["revoked"])
    messages.success(request, "Sharing link revoked.")
    return redirect("jobs:shortlist")


@require_safe
def tech_collection(request):
    jobs = live_jobs().filter(posted_at__gte=timezone.now() - timedelta(days=30)).filter(
        Q(title__icontains="software") | Q(title__icontains="data engineer") | Q(title__icontains="data scientist")
        | Q(title__icontains="data analyst") | Q(title__icontains="python")
    ).select_related("organization", "category").order_by("-posted_at", "-pk")[:30]
    return render(request, "jobs/shortlists/public.html", {"jobs": jobs,
        "collection_title": "Software & data jobs with impact",
        "share_url": request.build_absolute_uri(reverse("jobs:tech_collection")),
        "scout_url": reverse("jobs:job_scout_preview") + "?" + urlencode({
            "intent": "Remote software engineering or data engineering roles", "source": "tech_collection"})})
