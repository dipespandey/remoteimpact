"""Email service: newsletters and notifications via Cloudflare Email Service or Resend."""

import json
import logging
from typing import Optional
from urllib.parse import urlencode

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

    def _get_job_alert_unsubscribe_url(self, alert) -> str:
        """Get a permanent signed unsubscribe URL for one Job Scout."""
        signer = TimestampSigner(salt="job-alert-unsubscribe")
        token = signer.sign(str(alert.pk))
        path = reverse("jobs:job_alert_unsubscribe", kwargs={"token": token})
        return f"{self.site_url}{path}"

    def send_email(
        self,
        to: str,
        subject: str,
        html: str,
        text: Optional[str] = None,
        unsubscribe_url: Optional[str] = None,
    ) -> bool:
        """
        Send an email via the configured provider (Cloudflare Email Service or Resend).

        If Cloudflare is configured but a send fails for a reason other than a hard
        bounce, falls back to Resend so a misconfiguration never silently drops mail.
        Returns True if a provider accepted the message.
        """
        if getattr(settings, "EMAIL_PROVIDER", "resend") == "none":
            return False
        from jobs.services.outbound_links import attribute_html_links
        html = attribute_html_links(html, medium="email", campaign="remote_impact_emails")
        headers = {}
        if unsubscribe_url:
            # One-click unsubscribe (RFC 8058), required by Gmail/Yahoo for bulk mail.
            headers = {
                "List-Unsubscribe": f"<{unsubscribe_url}>",
                "List-Unsubscribe-Post": "List-Unsubscribe=One-Click",
            }
        if getattr(settings, "EMAIL_PROVIDER", "resend") == "cloudflare":
            outcome = self._send_cloudflare(to, subject, html, text, headers)
            if outcome != "retry":
                return outcome == "sent"
            logger.warning("Cloudflare email failed for %s; falling back to Resend", to)
        return self._send_resend(to, subject, html, text, headers)

    def _send_resend(self, to, subject, html, text, headers) -> bool:
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
            if headers:
                params["headers"] = headers
            response = resend.Emails.send(params)
            logger.info(f"Email sent to {to}: {response.get('id', 'unknown')}")
            return True
        except Exception as e:
            logger.error(f"Failed to send email to {to}: {e}")
            return False

    def _send_cloudflare(self, to, subject, html, text, headers) -> str:
        """POST to Cloudflare Email Service. Returns "sent", "bounced" or "retry"."""
        import requests
        from email.utils import parseaddr

        account = getattr(settings, "CLOUDFLARE_ACCOUNT_ID", "")
        token = getattr(settings, "CLOUDFLARE_EMAIL_TOKEN", "")
        url = f"https://api.cloudflare.com/client/v4/accounts/{account}/email/sending/send"
        name, address = parseaddr(self.from_email)
        body = {"to": [to], "subject": subject, "html": html}
        if text:
            body["text"] = text
        if headers:
            body["headers"] = headers
        # The display name form isn't documented; try "Name <address>", then the bare address.
        senders = [self.from_email, address] if name and address else [self.from_email]
        for sender in senders:
            try:
                response = requests.post(
                    url, json={**body, "from": sender}, timeout=20,
                    headers={"Authorization": f"Bearer {token}"},
                )
                data = response.json() if response.content else {}
            except (requests.RequestException, ValueError) as exc:
                logger.error("Cloudflare email request failed for %s: %s", to, exc)
                return "retry"
            if response.ok and data.get("success"):
                result = data.get("result") or {}
                if result.get("permanent_bounces"):
                    logger.warning("Cloudflare reported a permanent bounce for %s", to)
                    return "bounced"
                logger.info("Email sent to %s via Cloudflare", to)
                return "sent"
            errors = json.dumps(data.get("errors") or [])[:300]
            if response.status_code in (400, 422) and "from" in errors.lower() and sender != senders[-1]:
                continue  # sender format rejected; try the bare address
            logger.error("Cloudflare email rejected for %s: HTTP %s %s", to, response.status_code, errors)
            return "retry"
        return "retry"

    def send_pro_trial_ending(self, user, trial_end, match_count: int = 0) -> bool:
        """Remind a trialing subscriber before the first charge."""
        if not user.email:
            return False
        context = {
            "user": user,
            "trial_end": trial_end,
            "match_count": match_count,
            "account_url": f"{self.site_url}{reverse('jobs:account')}",
        }
        return self.send_email(
            to=user.email,
            subject="Your Remote Impact Pro trial ends soon",
            html=render_to_string("emails/pro_trial_ending.html", context),
            text=render_to_string("emails/pro_trial_ending.txt", context),
        )

    def send_claim_verification(self, claim, verify_url: str) -> bool:
        """Confirm the email of someone following their organization's stats."""
        context = {"claim": claim, "verify_url": verify_url}
        return self.send_email(
            to=claim.email,
            subject=f"Confirm your email for {claim.organization.name} on Remote Impact",
            html=render_to_string("emails/claim_verify.html", context),
            text=render_to_string("emails/claim_verify.txt", context),
        )

    def send_scout_contact_verification(self, contact, verify_url: str) -> bool:
        """Confirm an anonymous visitor's explicitly requested Scout follow-up."""
        context = {"contact": contact, "verify_url": verify_url, "site_url": self.site_url}
        return self.send_email(
            to=contact.email,
            subject="Confirm your Remote Impact follow-up",
            html=render_to_string("emails/scout_contact_verify.html", context),
            text=render_to_string("emails/scout_contact_verify.txt", context),
        )

    def send_employer_monthly_report(self, claim, stats: dict, report_link: str) -> bool:
        """Monthly 'applicants we sent you' summary for a verified employer contact."""
        context = {"claim": claim, "stats": stats, "report_link": report_link}
        return self.send_email(
            to=claim.email,
            subject=f"{stats['clicks']} applicants from Remote Impact to {claim.organization.name} this month",
            html=render_to_string("emails/employer_monthly.html", context),
            text=render_to_string("emails/employer_monthly.txt", context),
        )

    def send_checkout_recovery(self, user, *, trial: bool, resume_url: str, sender: str = "Dipesh",
                               reached_checkout: bool = True) -> bool:
        """One follow-up to someone who looked at Pro (plan page or checkout) and left without paying."""
        if not user.email:
            return False
        unsubscribe_url = self._get_unsubscribe_url(user.id)
        context = {"user": user, "trial": trial, "resume_url": resume_url, "sender": sender,
                   "unsubscribe_url": unsubscribe_url, "reached_checkout": reached_checkout}
        subject = "Your 7-day Pro trial is one click away" if trial else "Finish setting up Remote Impact Pro"
        return self.send_email(
            to=user.email,
            subject=subject,
            html=render_to_string("emails/checkout_recovery.html", context),
            text=render_to_string("emails/checkout_recovery.txt", context),
            unsubscribe_url=unsubscribe_url,
        )

    def send_scout_winback(self, user, *, brief: str, resume_url: str, sender: str = "Dipesh") -> bool:
        """One email inviting someone whose free Scout ran once to keep it running with Pro."""
        if not user.email:
            return False
        unsubscribe_url = self._get_unsubscribe_url(user.id)
        context = {"user": user, "brief": brief, "resume_url": resume_url, "sender": sender,
                   "unsubscribe_url": unsubscribe_url}
        return self.send_email(
            to=user.email,
            subject="Keep your job search running, free for 7 days",
            html=render_to_string("emails/scout_winback.html", context),
            text=render_to_string("emails/scout_winback.txt", context),
            unsubscribe_url=unsubscribe_url,
        )

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

        # This digest is not tied to a single Job Scout, so it uses the
        # account-level newsletter preference link.
        unsubscribe_url = self._get_unsubscribe_url(user.id)

        # Describe the digest as personalized only when matched results were
        # actually supplied; a completed profile can have no current matches.
        has_profile = bool(match_scores)

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
            unsubscribe_url=unsubscribe_url,
        )



    def send_talent_invitation_notification(self, invitation) -> bool:
        """Send notification when a seeker receives a talent invitation."""
        seeker_user = invitation.seeker.user
        context = {
            "organization_name": invitation.organization.name,
            "job_title": invitation.job.title,
            "message": invitation.message,
            "site_url": self.site_url,
        }
        html = render_to_string("emails/talent_invitation.html", context)
        subject = f"{invitation.organization.name} thinks you'd be great for {invitation.job.title}"
        return self.send_email(to=seeker_user.email, subject=subject, html=html)

    def send_job_alert(self, alert, jobs: list, is_initial: bool = False) -> bool:
        """
        Send job alert email to a user.

        Args:
            alert: JobAlert object
            jobs: List of matching Job objects
        
        Returns:
            True if sent successfully
        """
        if not jobs and not is_initial:
            return False

        user = alert.user
        from jobs.services.scout_email_identity import scout_email_label, scout_email_schedule
        alert_label = scout_email_label(alert)
        job_count = len(jobs)
        display_jobs = list(jobs[:10])  # Show max 10 jobs
        more_jobs = max(0, job_count - 10)

        # Keep alert traffic attributable without putting user data in links.
        # The source is also useful for analytics when a recipient is logged out.
        for job in display_jobs:
            job.alert_email_url = (
                f"{self.site_url}{job.get_absolute_url()}?"
                f"{urlencode({'source': 'scout_email', 'alert_id': alert.pk})}"
            )

        # Generate URLs
        unsubscribe_url = self._get_job_alert_unsubscribe_url(alert)
        alerts_url = f"{self.site_url}/alerts/"
        review_url = f"{alerts_url}?created={alert.pk}&source=scout_email_feedback"
        upgrade_url = (
            f"{self.site_url}{reverse('jobs:job_scout_subscribe')}"
            "?source=free_scout_email"
        )

        # Build context
        context = {
            "user": user,
            "jobs": display_jobs,
            "job_count": job_count,
            "more_jobs": more_jobs,
            "alert_name": alert_label,
            "schedule_description": scout_email_schedule(alert),
            "keywords": alert.keywords.strip() if alert.keywords else "",
            "frequency": alert.get_frequency_display().lower(),
            "site_url": self.site_url,
            "alerts_url": alerts_url,
            "review_url": review_url,
            "upgrade_url": upgrade_url,
            "unsubscribe_url": unsubscribe_url,
            "is_initial": is_initial,
            "is_one_time": alert.frequency == alert.Frequency.ONCE,
        }

        html = render_to_string("emails/job_alert.html", context)
        text = render_to_string("emails/job_alert.txt", context)

        # Build subject
        subject = (
            f"{job_count} job match{'es' if job_count != 1 else ''}"
            if job_count
            else "No strong current job matches"
        )
        subject += f' for "{alert_label}"'
        if display_jobs and not is_initial:
            from django.utils.text import Truncator
            subject += f" — {Truncator(display_jobs[0].title).chars(75)}"

        return self.send_email(
            to=user.email,
            subject=subject,
            html=html,
            text=text,
            unsubscribe_url=unsubscribe_url,
        )

    def send_seeker_weekly_digest(self, seeker_profile, profile_views: int, new_invitations: int) -> bool:
        """Send weekly digest to a seeker with profile views and invitation counts."""
        if profile_views == 0 and new_invitations == 0:
            return False
        user = seeker_profile.user
        unsubscribe_url = self._get_unsubscribe_url(user.id)
        context = {
            "user": user,
            "profile_views": profile_views,
            "new_invitations": new_invitations,
            "unsubscribe_url": unsubscribe_url,
            "site_url": self.site_url,
        }
        html = render_to_string("emails/seeker_weekly_digest.html", context)
        subject = f"You had {profile_views} profile views this week and {new_invitations} new invitations"
        return self.send_email(to=user.email, subject=subject, html=html, unsubscribe_url=unsubscribe_url)

# Singleton instance
email_service = EmailService()
