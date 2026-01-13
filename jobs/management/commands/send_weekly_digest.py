"""
Management command to send weekly job digest emails.

Usage:
    python manage.py send_weekly_digest                    # Send to all subscribed users
    python manage.py send_weekly_digest --dry-run          # Preview without sending
    python manage.py send_weekly_digest --limit 10         # Send to first 10 users only
    python manage.py send_weekly_digest --user user@example.com  # Send to specific user
"""
from __future__ import annotations

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand

from jobs.models import Job, SeekerProfile
from jobs.services.email_service import email_service
from jobs.services.vector_search import search_jobs_for_seeker

User = get_user_model()


class Command(BaseCommand):
    help = "Send weekly job digest emails to subscribed users."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Preview what would be sent without actually sending.",
        )
        parser.add_argument(
            "--limit",
            type=int,
            default=None,
            help="Limit the number of users to send to.",
        )
        parser.add_argument(
            "--user",
            type=str,
            default=None,
            help="Send to a specific user by email address.",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        limit = options["limit"]
        specific_user = options["user"]

        if dry_run:
            self.stdout.write(self.style.WARNING("DRY RUN - no emails will be sent"))

        # Get users to send to
        if specific_user:
            users = User.objects.filter(email=specific_user)
            if not users.exists():
                self.stdout.write(self.style.ERROR(f"User not found: {specific_user}"))
                return
        else:
            # Get users with completed impact profiles (seeker profiles with embeddings)
            users = User.objects.filter(
                seeker_profile__wizard_completed=True,
                seeker_profile__embedding__isnull=False,
                is_active=True,
            ).select_related("seeker_profile")

        if limit:
            users = users[:limit]

        total_users = users.count()
        self.stdout.write(f"Found {total_users} users to send digests to")

        # Get active jobs for users without profiles
        # Exclude jobs with placeholder titles or missing descriptions
        recent_jobs = list(
            Job.objects.filter(is_active=True)
            .exclude(title__startswith="Job at ")
            .exclude(description__isnull=True)
            .exclude(description="")
            .select_related("organization", "category")
            .order_by("-posted_at")[:20]
        )

        sent_count = 0
        skipped_count = 0
        error_count = 0

        for user in users:
            try:
                # Check if user has a completed seeker profile
                seeker_profile = None
                has_profile = False

                try:
                    seeker_profile = user.seeker_profile
                    if seeker_profile and seeker_profile.wizard_completed:
                        has_profile = True
                except SeekerProfile.DoesNotExist:
                    pass

                if has_profile and seeker_profile.embedding is not None:
                    # Get personalized matches via vector search
                    results = search_jobs_for_seeker(seeker_profile, limit=20)
                    if results:
                        jobs = [job for job, *_ in results]
                        match_scores = {job.id: int(score * 100) for job, score, *_ in results}
                    else:
                        jobs = recent_jobs[:20]
                        match_scores = None
                else:
                    # No profile or no embedding - use recent jobs
                    jobs = recent_jobs[:20]
                    match_scores = None

                if not jobs:
                    self.stdout.write(f"  Skipped {user.email} - no jobs available")
                    skipped_count += 1
                    continue

                if dry_run:
                    profile_status = "personalized" if has_profile else "generic"
                    self.stdout.write(
                        f"  Would send to {user.email} ({profile_status}, {len(jobs)} jobs)"
                    )
                    sent_count += 1
                else:
                    success = email_service.send_weekly_digest(
                        user=user,
                        jobs=jobs,
                        match_scores=match_scores,
                    )
                    if success:
                        self.stdout.write(
                            self.style.SUCCESS(f"  Sent to {user.email}")
                        )
                        sent_count += 1
                    else:
                        self.stdout.write(
                            self.style.WARNING(f"  Failed to send to {user.email}")
                        )
                        error_count += 1

            except Exception as e:
                self.stdout.write(
                    self.style.ERROR(f"  Error processing {user.email}: {e}")
                )
                error_count += 1

        # Summary
        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS(f"Sent: {sent_count}"))
        self.stdout.write(f"Skipped: {skipped_count}")
        if error_count:
            self.stdout.write(self.style.ERROR(f"Errors: {error_count}"))
