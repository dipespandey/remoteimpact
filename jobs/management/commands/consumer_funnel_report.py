from collections import Counter
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.db.models import Count, Q
from django.utils import timezone

from jobs.models import AssistantSubscription, ConsumerFunnelEvent, JobAlert, SeekerProfile


class Command(BaseCommand):
    help = "Report the consumer acquisition and Remote Impact Pro funnel."

    def add_arguments(self, parser):
        parser.add_argument("--days", type=int, default=30)
        parser.add_argument(
            "--by-source",
            action="store_true",
            help="Show signup counts grouped by stored campaign source.",
        )

    def handle(self, *args, **options):
        days = max(1, options["days"])
        now = timezone.now()
        since = now - timedelta(days=days)
        period_events = ConsumerFunnelEvent.objects.filter(created_at__gte=since, created_at__lt=now)
        events = dict(
            period_events
            .values_list("event")
            .annotate(total=Count("id"))
        )
        new_accounts = get_user_model().objects.filter(date_joined__gte=since, date_joined__lt=now)
        users = new_accounts.count()
        cohort_profiles = SeekerProfile.objects.filter(user__in=new_accounts)
        profiles = cohort_profiles.count()
        completed_profiles = cohort_profiles.filter(wizard_completed=True).count()
        period_scouts = JobAlert.objects.filter(created_at__gte=since, created_at__lt=now)
        scouts = period_scouts.count()
        cohort_scout_users = period_scouts.filter(user__in=new_accounts).values('user_id').distinct().count()
        landings = events.get(ConsumerFunnelEvent.Event.SCOUT_LANDED, 0)
        job_views = events.get(ConsumerFunnelEvent.Event.SCOUT_JOB_VIEWED, 0)
        completions = period_events.filter(event=ConsumerFunnelEvent.Event.PAYMENT_COMPLETED)
        trial_starts = 0
        nontrial_events = 0
        cohort_pro_users = set()
        # The legacy event also records trial starts. Neither event type is a
        # payment ledger, so never turn these counts into cash revenue.
        cohort_ids = set(new_accounts.values_list('pk', flat=True))
        for user_id, metadata in completions.values_list('user_id', 'metadata'):
            metadata = metadata if isinstance(metadata, dict) else {}
            if metadata.get('trial') is True:
                trial_starts += 1
            else:
                nontrial_events += 1
            if user_id in cohort_ids:
                cohort_pro_users.add(user_id)
        checkout_created = (
            events.get(ConsumerFunnelEvent.Event.CHECKOUT_CREATED, 0)
            + events.get(ConsumerFunnelEvent.Event.CHECKOUT_STARTED, 0)
        )

        rows = [
            ("New accounts", users),
            ("Accounts with seeker profile", profiles),
            ("Completed seeker profiles", completed_profiles),
            ("Scout landings", landings),
            ("Scout job views", job_views),
            ("Scout previews", events.get(ConsumerFunnelEvent.Event.SCOUT_PREVIEWED, 0)),
            ("Shortlist user-days", events.get(ConsumerFunnelEvent.Event.SHORTLIST_VIEWED, 0)),
            ("Interest updates", events.get(ConsumerFunnelEvent.Event.INTEREST_SAVED, 0)),
            ("Employer opt-ins", events.get(ConsumerFunnelEvent.Event.EMPLOYER_OPT_IN, 0)),
            ("Collections shared", events.get(ConsumerFunnelEvent.Event.COLLECTION_CREATED, 0)),
            ("Collection visits", events.get(ConsumerFunnelEvent.Event.COLLECTION_VIEWED, 0)),
            ("Scouts created", events.get(ConsumerFunnelEvent.Event.SCOUT_CREATED, scouts)),
            ("Initial emails sent", events.get(ConsumerFunnelEvent.Event.INITIAL_EMAIL_SENT, 0)),
            ("Scouts with no matches", events.get(ConsumerFunnelEvent.Event.INITIAL_MATCHES_EMPTY, 0)),
            ("Upgrade clicks", events.get(ConsumerFunnelEvent.Event.UPGRADE_CLICKED, 0)),
            ("Stripe checkouts created", checkout_created),
            ("Checkouts expired", events.get(ConsumerFunnelEvent.Event.CHECKOUT_EXPIRED, 0)),
            ("Checkout failures", events.get(ConsumerFunnelEvent.Event.CHECKOUT_FAILED, 0)),
            ("Recorded trial starts", trial_starts),
            ("Non-trial completion events", nontrial_events),
            ("Active Pro entitlements (all accounts)", AssistantSubscription.objects.filter(
                is_subscribed=True
            ).filter(Q(expires_at__isnull=True) | Q(expires_at__gte=now)).count()),
        ]

        self.stdout.write(self.style.MIGRATE_HEADING(f"Consumer funnel: last {days} days"))
        for label, value in rows:
            self.stdout.write(f"{label:<24} {value:>7}")

        if options["by_source"]:
            source_counts = Counter()
            for event in period_events.filter(event=ConsumerFunnelEvent.Event.ACCOUNT_CREATED).only("metadata"):
                metadata = event.metadata or {}
                source = (
                    metadata.get("utm_source")
                    or metadata.get("source")
                    or "direct / unknown"
                )
                source_counts[str(source)] += 1
            self.stdout.write("")
            self.stdout.write("Recorded signup event sources (not organic attribution)")
            for source, total in source_counts.most_common():
                self.stdout.write(f"{source:<24} {total:>7}")

        def rate(numerator, denominator):
            return f"{100 * numerator / denominator:.2f}%" if denominator else "n/a"

        self.stdout.write("")
        self.stdout.write("New-account cohort outcomes observed by report time")
        self.stdout.write(f"Accounts with a new Scout: {cohort_scout_users} / {users} ({rate(cohort_scout_users, users)})")
        self.stdout.write(f"Accounts with seeker profile: {profiles} / {users} ({rate(profiles, users)})")
        self.stdout.write(f"Seeker profiles completed: {completed_profiles} / {profiles} ({rate(completed_profiles, profiles)})")
        self.stdout.write(f"Accounts with Pro start event (includes trials): {len(cohort_pro_users)} / {users} ({rate(len(cohort_pro_users), users)})")
        self.stdout.write("Event totals above can include older accounts and repeated actions; they are not a sequential conversion funnel.")
        self.stdout.write("Trial and non-trial completion events are not verified cash receipts. Organic acquisition and collected revenue require separate attribution/payment evidence.")
