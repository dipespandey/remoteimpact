"""Follow up once with people who looked at Pro (plan page or Stripe checkout) but didn't finish.

Plan-page visitors only qualify if their Scout has already delivered matches; for an empty Scout the
plan page itself points them to fixing their brief.

Dry run by default: prints who would get an email. Pass --send to actually send.
Meant to run hourly once enabled; each person gets at most one follow-up, ever.
"""
from datetime import timedelta

from django.conf import settings
from django.core.management.base import BaseCommand
from django.urls import reverse
from django.utils import timezone

from jobs.models import AssistantSubscription, ConsumerFunnelEvent, JobAlertDelivery, UserProfile
from jobs.services.email_service import email_service
from jobs.services.payment_service import normalize_pro_plan, pro_trial_eligible

CE = ConsumerFunnelEvent.Event


class Command(BaseCommand):
    help = "Email people who left Pro checkout without paying (one follow-up each). Dry run unless --send."

    def add_arguments(self, parser):
        parser.add_argument("--send", action="store_true", help="Actually send the emails.")
        parser.add_argument("--min-hours", type=int, default=2, help="Wait at least this long after checkout.")
        parser.add_argument("--max-hours", type=int, default=48, help="Ignore checkouts older than this.")

    def handle(self, *args, **options):
        now = timezone.now()
        site = getattr(settings, "SITE_URL", "https://remoteimpact.org").rstrip("/")
        started = (
            ConsumerFunnelEvent.objects.filter(
                event__in=[CE.CHECKOUT_CREATED, CE.CHECKOUT_STARTED, CE.UPGRADE_CLICKED],
                user__isnull=False, user__is_active=True,
                created_at__gte=now - timedelta(hours=options["max_hours"]),
                created_at__lte=now - timedelta(hours=options["min_hours"]),
            ).select_related("user").order_by("user_id", "-created_at")
        )
        latest = {}
        for event in started:
            latest.setdefault(event.user_id, event)
        sent = skipped = 0
        for event in latest.values():
            user = event.user
            subscription = AssistantSubscription.get_or_create_for_user(user)
            unsubscribed = UserProfile.objects.filter(user=user, email_newsletter_unsubscribed_at__isnull=False).exists()
            already = ConsumerFunnelEvent.objects.filter(user=user, event=CE.CHECKOUT_RECOVERY_SENT).exists()
            paid = ConsumerFunnelEvent.objects.filter(
                user=user, event=CE.PAYMENT_COMPLETED, created_at__gte=event.created_at).exists()
            plan_page_only = event.event == CE.UPGRADE_CLICKED
            no_matches = plan_page_only and not JobAlertDelivery.objects.filter(alert__user=user).exclude(
                feedback=JobAlertDelivery.Feedback.NOT_RELEVANT).exists()
            if subscription.is_pro_active() or unsubscribed or already or paid or no_matches:
                skipped += 1
                continue
            plan = normalize_pro_plan((event.metadata or {}).get("plan"))
            trial = plan == "monthly" and pro_trial_eligible(subscription)
            resume_url = f"{site}{reverse('jobs:job_scout_subscribe')}?source=checkout_recovery&plan={plan}"
            if not options["send"]:
                self.stdout.write(f"Would email {user.email} (plan={plan}, trial={trial}, stage={event.event})")
                sent += 1
                continue
            if email_service.send_checkout_recovery(user, trial=trial, resume_url=resume_url,
                                                  reached_checkout=not plan_page_only):
                ConsumerFunnelEvent.objects.create(
                    user=user, event=CE.CHECKOUT_RECOVERY_SENT, metadata={"plan": plan},
                    dedupe_key=f"checkout-recovery:{user.pk}",
                )
                sent += 1
        verb = "Sent" if options["send"] else "Dry run: would send"
        self.stdout.write(f"{verb} {sent} follow-up(s); skipped {skipped}.")
