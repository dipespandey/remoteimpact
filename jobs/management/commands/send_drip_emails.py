"""
Management command to send onboarding drip emails.

Drip schedule (days since signup):
  Day 2:  "Complete your Impact Profile" (only if wizard not completed)
  Day 5:  "Top jobs for you" (personalized if seeker profile exists)
  Day 10: "Try the AI Application Assistant"

Usage:
    python manage.py send_drip_emails
    python manage.py send_drip_emails --dry-run
    python manage.py send_drip_emails --user user@example.com
"""
from __future__ import annotations

from datetime import timedelta

from django.conf import settings as django_settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.template.loader import render_to_string
from django.utils import timezone

from jobs.models import DripEmailLog, Job, SeekerProfile
from jobs.services.email_service import email_service

User = get_user_model()

DRIP_SCHEDULE = [
    {
        "drip_type": "day2_profile",
        "day": 2,
        "subject": "Complete your Impact Profile — it takes 2 minutes",
        "template": "emails/drip_day2_profile",
        "skip_if_wizard_completed": True,
    },
    {
        "drip_type": "day5_jobs",
        "day": 5,
        "subject": "Top impact jobs for you this week",
        "template": "emails/drip_day5_jobs",
    },
    {
        "drip_type": "day10_assistant",
        "day": 10,
        "subject": "Apply smarter with our AI Assistant ✨",
        "template": "emails/drip_day10_assistant",
    },
]


class Command(BaseCommand):
    help = "Send onboarding drip emails to users based on signup date."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true", help="Preview without sending.")
        parser.add_argument("--user", type=str, default=None, help="Send to a specific user email.")

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        specific_user = options.get("user")
        now = timezone.now()
        site_url = getattr(django_settings, "SITE_URL", "https://remoteimpact.org")

        if dry_run:
            self.stdout.write(self.style.WARNING("DRY RUN — no emails will be sent"))

        sent = 0
        skipped = 0
        errors = 0

        for drip in DRIP_SCHEDULE:
            drip_type = drip["drip_type"]
            target_date = now - timedelta(days=drip["day"])
            # Window: users who signed up on target day (±12 hours for cron drift)
            window_start = target_date - timedelta(hours=12)
            window_end = target_date + timedelta(hours=12)

            users_qs = User.objects.filter(
                date_joined__gte=window_start,
                date_joined__lte=window_end,
                is_active=True,
            ).exclude(
                drip_emails__drip_type=drip_type,
            )

            if specific_user:
                users_qs = users_qs.filter(email=specific_user)

            for user in users_qs:
                try:
                    # Skip day-2 email if user already completed wizard
                    if drip.get("skip_if_wizard_completed"):
                        try:
                            if user.seeker_profile.wizard_completed:
                                skipped += 1
                                continue
                        except SeekerProfile.DoesNotExist:
                            pass

                    # Build context
                    unsubscribe_url = email_service._get_unsubscribe_url(user.id)
                    context = {
                        "user": user,
                        "site_url": site_url,
                        "unsubscribe_url": unsubscribe_url,
                    }

                    # Day 5: add jobs
                    if drip_type == "day5_jobs":
                        has_profile = False
                        jobs_data = []
                        try:
                            sp = user.seeker_profile
                            if sp.wizard_completed and sp.embedding is not None:
                                has_profile = True
                                from jobs.services.vector_search import search_jobs_for_seeker
                                results = search_jobs_for_seeker(sp, limit=5)
                                jobs_data = [
                                    {"job": job, "match_score": int(score * 100)}
                                    for job, score, *_ in results
                                ]
                        except SeekerProfile.DoesNotExist:
                            pass

                        if not jobs_data:
                            recent = Job.objects.filter(is_active=True).exclude(
                                title__startswith="Job at "
                            ).exclude(description="").select_related(
                                "organization", "category"
                            ).order_by("-posted_at")[:5]
                            jobs_data = [{"job": j, "match_score": None} for j in recent]

                        context["has_profile"] = has_profile
                        context["jobs"] = jobs_data

                        if not jobs_data:
                            skipped += 1
                            continue

                    html = render_to_string(f"{drip['template']}.html", context)
                    text = render_to_string(f"{drip['template']}.txt", context)

                    if dry_run:
                        self.stdout.write(f"  Would send [{drip_type}] to {user.email}")
                        sent += 1
                    else:
                        ok = email_service.send_email(
                            to=user.email,
                            subject=drip["subject"],
                            html=html,
                            text=text,
                        )
                        if ok:
                            DripEmailLog.objects.create(user=user, drip_type=drip_type)
                            self.stdout.write(self.style.SUCCESS(f"  Sent [{drip_type}] → {user.email}"))
                            sent += 1
                        else:
                            errors += 1

                except Exception as e:
                    self.stdout.write(self.style.ERROR(f"  Error [{drip_type}] {user.email}: {e}"))
                    errors += 1

        self.stdout.write(f"\nDone — sent: {sent}, skipped: {skipped}, errors: {errors}")
