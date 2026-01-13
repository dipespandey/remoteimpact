"""Email service for sending newsletters and notifications via Resend."""

import logging
from typing import Optional

import resend
from django.conf import settings
from django.core.signing import TimestampSigner
from django.template.loader import render_to_string
from django.urls import reverse

logger = logging.getLogger(__name__)


class EmailService:
    """Service for sending emails via Resend API."""

    def __init__(self):
        resend.api_key = getattr(settings, "RESEND_API_KEY", None)
        self.from_email = getattr(
            settings, "DEFAULT_FROM_EMAIL", "Remote Impact <jobs@remoteimpact.org>"
        )
        self.site_url = getattr(settings, "SITE_URL", "https://remoteimpact.org")

    def _generate_unsubscribe_token(self, user_id: int) -> str:
        """Generate a signed token for one-click unsubscribe."""
        signer = TimestampSigner(salt="newsletter-unsubscribe")
        return signer.sign(str(user_id))

    def _get_unsubscribe_url(self, user_id: int) -> str:
        """Get the full unsubscribe URL for a user."""
        token = self._generate_unsubscribe_token(user_id)
        path = reverse("jobs:newsletter_unsubscribe", kwargs={"token": token})
        return f"{self.site_url}{path}"

    def send_email(
        self,
        to: str,
        subject: str,
        html: str,
        text: Optional[str] = None,
    ) -> bool:
        """
        Send an email via Resend.

        Returns True if successful, False otherwise.
        """
        if not resend.api_key:
            logger.warning("RESEND_API_KEY not configured, skipping email send")
            return False

        try:
            params = {
                "from": self.from_email,
                "to": [to],
                "subject": subject,
                "html": html,
            }
            if text:
                params["text"] = text

            response = resend.Emails.send(params)
            logger.info(f"Email sent to {to}: {response.get('id', 'unknown')}")
            return True
        except Exception as e:
            logger.error(f"Failed to send email to {to}: {e}")
            return False

    def send_weekly_digest(
        self,
        user,
        jobs: list,
        match_scores: Optional[dict] = None,
    ) -> bool:
        """
        Send weekly job digest email to a user.

        Args:
            user: The User object
            jobs: List of Job objects to include
            match_scores: Optional dict of job_id -> match_score for personalized scores
        """
        if not jobs:
            logger.info(f"No jobs to send to {user.email}, skipping")
            return False

        # Prepare job data with optional match scores
        job_data = []
        for job in jobs[:10]:  # Limit to 10 jobs
            data = {
                "job": job,
                "match_score": match_scores.get(job.id) if match_scores else None,
            }
            job_data.append(data)

        # Generate unsubscribe URL
        unsubscribe_url = self._get_unsubscribe_url(user.id)

        # Determine if user has personalized recommendations
        has_profile = hasattr(user, "seeker_profile") and user.seeker_profile.wizard_completed

        # Render templates
        context = {
            "user": user,
            "jobs": job_data,
            "has_profile": has_profile,
            "unsubscribe_url": unsubscribe_url,
            "site_url": self.site_url,
            "profile_url": f"{self.site_url}/impact-profile/wizard/",
        }

        html = render_to_string("emails/weekly_digest.html", context)
        text = render_to_string("emails/weekly_digest.txt", context)

        subject = "Your Weekly Impact Jobs Digest"
        if has_profile:
            subject = "Jobs Matched to Your Impact Profile"

        return self.send_email(
            to=user.email,
            subject=subject,
            html=html,
            text=text,
        )


# Singleton instance
email_service = EmailService()
