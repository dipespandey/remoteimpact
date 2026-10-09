from collections import Counter
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.db.models import Count
from django.utils import timezone

from jobs.models import AssistantSubscription, ConsumerFunnelEvent, JobAlert, UserProfile, SeekerProfile


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
        since = timezone.now() - timedelta(days=days)
        events = dict(
            ConsumerFunnelEvent.objects.filter(created_at__gte=since)
            .values_list("event")
            .annotate(total=Count("id"))
        )
        users = get_user_model().objects.filter(date_joined__gte=since).count()
        profiles = UserProfile.objects.filter(user__date_joined__gte=since).count()
        completed_profiles = SeekerProfile.objects.filter(
            user__date_joined__gte=since, wizard_completed=True
        ).count()
        scouts = JobAlert.objects.filter(created_at__gte=since).count()
        landings = events.get(ConsumerFunnelEvent.Event.SCOUT_LANDED, 0)
        job_views = events.get(ConsumerFunnelEvent.Event.SCOUT_JOB_VIEWED, 0)
        paid = events.get(ConsumerFunnelEvent.Event.PAYMENT_COMPLETED, 0)
        checkout_created = (
            events.get(ConsumerFunnelEvent.Event.CHECKOUT_CREATED, 0)
            + events.get(ConsumerFunnelEvent.Event.CHECKOUT_STARTED, 0)
        )

        rows = [
            ("New accounts", users),
            ("Accounts with profile", profiles),
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
            ("Payments completed", paid),
            ("Currently Pro", AssistantSubscription.objects.filter(is_subscribed=True).count()),
        ]

        self.stdout.write(self.style.MIGRATE_HEADING(f"Consumer funnel: last {days} days"))
        for label, value in rows:
            self.stdout.write(f"{label:<24} {value:>7}")

        if options["by_source"]:
            source_counts = Counter()
            for event in ConsumerFunnelEvent.objects.filter(
                created_at__gte=since,
                event=ConsumerFunnelEvent.Event.ACCOUNT_CREATED,
            ).only("metadata"):
                metadata = event.metadata or {}
                source = (
                    metadata.get("utm_source")
                    or metadata.get("source")
                    or "direct / unknown"
                )
                source_counts[str(source)] += 1
            self.stdout.write("")
            self.stdout.write("Signup attribution")
            for source, total in source_counts.most_common():
                self.stdout.write(f"{source:<24} {total:>7}")

        def rate(numerator, denominator):
            return 100 * numerator / denominator if denominator else 0

        previews = events.get(ConsumerFunnelEvent.Event.SCOUT_PREVIEWED, 0)
        created = events.get(ConsumerFunnelEvent.Event.SCOUT_CREATED, scouts)
        checkout = checkout_created
        self.stdout.write("")
        self.stdout.write(f"Preview -> scout          {rate(created, previews):6.2f}%")
        self.stdout.write(f"Account -> scout          {rate(created, users):6.2f}%")
        self.stdout.write(f"Account -> profile        {rate(profiles, users):6.2f}%")
        self.stdout.write(f"Profile -> completed      {rate(completed_profiles, profiles):6.2f}%")
        self.stdout.write(f"Scout landing -> created  {rate(created, landings):6.2f}%")
        self.stdout.write(f"Scout job view -> paid     {rate(paid, job_views):6.2f}%")
        self.stdout.write(f"Scout -> checkout created {rate(checkout, created):6.2f}%")
        self.stdout.write(f"Checkout -> paid          {rate(paid, checkout):6.2f}%")
        self.stdout.write(f"Account -> paid           {rate(paid, users):6.2f}%")
