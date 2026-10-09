"""Exercise deployed views inside a rolled-back transaction, with email transport disabled."""

import io
import json
import time
import uuid
from datetime import timedelta
from unittest.mock import patch

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.db import transaction
from django.test import Client
from django.utils import timezone

from jobs.models import AssistantSubscription, JobAlert, JobAlertDelivery, JobInterest, SeekerProfile, UserProfile
from jobs.services.email_service import email_service
from jobs.services.job_service import JobService
from jobs.services.job_scout_service import JobScoutService


checks = []


def check(name, ok, **details):
    row = {"name": name, "ok": bool(ok), **details}
    checks.append(row)
    print(json.dumps(row), flush=True)


username = "qa-e2e-" + uuid.uuid4().hex[:12]
intent = "Remote software engineering roles using Python in Europe"
preferences = JobScoutService.parse_intent(intent)
with transaction.atomic(), patch.object(email_service, "send_email", return_value=True) as email_transport:
    user = get_user_model().objects.create_user(username=username, email=username + "@example.com")
    user.set_unusable_password()
    user.save(update_fields=["password"])
    client = Client(HTTP_HOST="remoteimpact.org")
    client.force_login(user)
    profile = SeekerProfile.objects.create(user=user, wizard_completed=True, headline="Python Software Engineer", skills=["python"], location_preferences=["Europe"])
    UserProfile.objects.update_or_create(user=user, defaults={"account_type": "seeker", "headline": "Python Software Engineer"})
    for path in ("/account/", "/impact-profile/", "/my-matches/", "/shortlist/", "/alerts/", "/alerts/create/", "/alerts/pro/subscribe/"):
        start = time.monotonic()
        response = client.get(path, secure=True)
        check("authenticated_" + path, response.status_code == 200, status=response.status_code, seconds=round(time.monotonic() - start, 3))
    with patch.object(JobScoutService, "parse_intent", return_value=preferences):
        payload = {"intent_text": intent, "name": "Isolated QA Scout", "frequency": "once", "source": "qa_e2e"}
        response = client.post("/alerts/create/", payload, secure=True)
        alert = JobAlert.objects.filter(user=user).first()
        check("free_scout_create_and_initial_email_render", response.status_code == 302 and alert is not None and alert.deliveries.exists() and email_transport.called)
        if alert:
            subscription = AssistantSubscription.objects.get(user=user)
            check("free_scout_once_only", alert.frequency == "once" and not alert.is_active and subscription.free_job_scout_used_at is not None)
            emails_before = email_transport.call_count
            client.post("/alerts/create/", payload, secure=True)
            check("duplicate_scout_idempotent", JobAlert.objects.filter(user=user).count() == 1 and email_transport.call_count == emails_before)
            client.post("/alerts/create/", {**payload, "intent_text": "Remote senior Python software engineer jobs in Europe"}, secure=True)
            check("free_second_scout_blocked", JobAlert.objects.filter(user=user).count() == 1)
            delivery = alert.deliveries.first()
            response = client.post(f"/alerts/deliveries/{delivery.pk}/feedback/", {"feedback": "not_relevant"}, secure=True)
            delivery.refresh_from_db()
            check("feedback_saved", response.status_code == 302 and delivery.feedback == "not_relevant")
            matches = JobScoutService.get_matches_for_preferences(preferences, alert=alert, limit=10)
            check("feedback_excludes_rejected_job", delivery.job_id not in {j.pk for j in matches})
            subscription.is_subscribed = True
            subscription.expires_at = timezone.now() + timedelta(days=1)
            subscription.save(update_fields=["is_subscribed", "expires_at"])
            response = client.post("/alerts/create/", {**payload, "frequency": "daily"}, secure=True)
            pro_alert = JobAlert.objects.filter(user=user, frequency="daily").first()
            check("pro_daily_scout_create", response.status_code == 302 and pro_alert is not None and pro_alert.is_active)
            if pro_alert:
                client.post(f"/alerts/{pro_alert.pk}/toggle/", secure=True)
                pro_alert.refresh_from_db()
                check("pro_scout_pause", not pro_alert.is_active)
                client.post(f"/alerts/{pro_alert.pk}/toggle/", secure=True)
                pro_alert.refresh_from_db()
                check("pro_scout_resume", pro_alert.is_active)
            outsider = get_user_model().objects.create_user(username=username + "-other")
            other_client = Client(HTTP_HOST="remoteimpact.org")
            other_client.force_login(outsider)
            response = other_client.post(f"/alerts/{alert.pk}/toggle/", secure=True)
            check("scout_owner_authorization", response.status_code == 404)
    job = JobService.get_available_jobs().order_by("-posted_at", "-pk").first()
    response = client.post(f"/jobs/{job.slug}/interest/", {"status": "saved"}, secure=True)
    check("shortlist_save", response.status_code == 302 and JobInterest.objects.filter(user=user, job=job, status="saved").exists())
    response = client.get("/shortlist/", secure=True)
    check("shortlist_saved_job_visible", job.title.encode() in response.content)
    before = JobAlertDelivery.objects.count()
    emails_before = email_transport.call_count
    command_output = io.StringIO()
    call_command("send_job_alerts", dry_run=True, stdout=command_output)
    check("recurring_sender_dry_run_no_delivery_or_email", before == JobAlertDelivery.objects.count() and emails_before == email_transport.call_count)
    generators = [
        ("cover_letter", "/resources/applicant-assistant/generate/", {"job_description": "Remote Python software engineer building climate data APIs with Django and PostgreSQL.", "resume": "Synthetic QA candidate: five years of Python and Django experience."}),
        ("interview", "/tools/interview-prep/generate/", {"job_title": "Python Software Engineer", "focus_area": "technical"}),
        ("job_description", "/tools/job-description-generator/generate/", {"job_title": "Python Software Engineer", "skills": "Python, Django", "company": "QA Example Organization"}),
        ("negotiation", "/tools/salary-negotiation-script/generate/", {"job_title": "Python Software Engineer", "company": "QA Example Organization", "current_salary": "80000", "target_salary": "100000", "achievements": "Synthetic QA example: improved API performance."}),
        ("skills_gap", "/tools/skills-gap-analyzer/analyze/", {"resume": "Synthetic QA candidate with Python and Django experience.", "job_description": "Python engineer with Django, PostgreSQL, and Kubernetes experience."}),
        ("thank_you", "/tools/thank-you-email-generator/generate/", {"interviewer": "QA Interviewer", "company": "QA Example Organization", "position": "Python Software Engineer", "topics": "Python API reliability and remote collaboration"}),
        ("resignation", "/tools/resignation-letter-generator/generate/", {"name": "QA Candidate", "manager": "QA Manager", "company": "QA Example", "position": "Engineer", "last_day": "2026-11-01"}),
    ]
    for name, path, data in generators:
        start = time.monotonic()
        response = client.post(path, data, secure=True)
        try:
            payload = response.json()
        except ValueError:
            payload = {"error": "Not JSON"}
        content = payload.get("result", payload.get("content", ""))
        if name == "skills_gap":
            ok = "match_score" in payload and "action_plan" in payload
        else:
            ok = len(content) >= 80
        check("live_deepseek_" + name, response.status_code == 200 and "error" not in payload and ok, status=response.status_code, seconds=round(time.monotonic() - start, 3), output_keys=list(payload), error=payload.get("error"))
    check("email_transport_configured", bool(getattr(settings, "RESEND_API_KEY", "")))
    check("stripe_live_configured", str(getattr(settings, "STRIPE_SECRET_KEY", "")).startswith("sk_live_"))
    transaction.set_rollback(True)
check("qa_records_rolled_back", not get_user_model().objects.filter(username=username).exists())
print("FLOW_REPORT=" + json.dumps({"checks": checks, "summary": {"passed": sum(c["ok"] for c in checks), "failed": sum(not c["ok"] for c in checks)}, "email_transport": "mocked: templates and bookkeeping tested, no real email sent", "payments": "configuration only: no real checkout or charge"}), flush=True)
