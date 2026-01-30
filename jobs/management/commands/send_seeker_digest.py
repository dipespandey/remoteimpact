"""Management command to send weekly seeker digest emails."""
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from jobs.models import SeekerProfile, ProfileView, TalentInvitation
from jobs.services.email_service import email_service


class Command(BaseCommand):
    help = "Send weekly digest emails to seekers with profile views/invitations"

    def handle(self, *args, **options):
        week_ago = timezone.now() - timedelta(days=7)
        seekers = SeekerProfile.objects.filter(wizard_completed=True).select_related("user")
        sent = 0
        for seeker in seekers:
            views = ProfileView.objects.filter(seeker=seeker, viewed_at__gte=week_ago).count()
            invitations = TalentInvitation.objects.filter(
                seeker=seeker, sent_at__gte=week_ago
            ).count()
            if views > 0 or invitations > 0:
                if email_service.send_seeker_weekly_digest(seeker, views, invitations):
                    sent += 1
        self.stdout.write(self.style.SUCCESS(f"Sent {sent} seeker digest emails"))
