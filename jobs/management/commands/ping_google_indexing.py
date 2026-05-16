"""
Notify Google's Indexing API about new, updated, or removed job URLs.

Environment:
    GOOGLE_APPLICATION_CREDENTIALS=/path/to/service-account.json
    or GOOGLE_INDEXING_CREDENTIALS_JSON='{"type":"service_account",...}'

The command exits cleanly when credentials are missing so it is safe to run
from cron before the Search Console service account is configured.
"""

import json
import os
import time
from datetime import timedelta

import jwt
import requests
from django.conf import settings
from django.core.management.base import BaseCommand
from django.utils import timezone

from jobs.models import Job


class Command(BaseCommand):
    help = "Notify Google Indexing API for job posting URLs."

    TOKEN_SCOPE = "https://www.googleapis.com/auth/indexing"
    TOKEN_URI = "https://oauth2.googleapis.com/token"
    PUBLISH_URL = "https://indexing.googleapis.com/v3/urlNotifications:publish"

    def add_arguments(self, parser):
        parser.add_argument("--hours", type=int, default=24, help="Jobs updated in the last N hours.")
        parser.add_argument("--job-slug", help="Notify one job by slug.")
        parser.add_argument("--limit", type=int, default=200, help="Maximum URLs to notify.")
        parser.add_argument("--dry-run", action="store_true", help="Print URLs without calling Google.")
        parser.add_argument(
            "--type",
            choices=["URL_UPDATED", "URL_DELETED"],
            default="URL_UPDATED",
            help="Indexing API notification type.",
        )

    def handle(self, *args, **options):
        notification_type = options["type"]
        urls = self.build_urls(options)

        if not urls:
            self.stdout.write(self.style.WARNING("No job URLs matched."))
            return

        self.stdout.write(f"Found {len(urls)} URL(s) for {notification_type}.")
        if options["dry_run"]:
            for url in urls:
                self.stdout.write(f"  {url}")
            return

        credentials = self.load_credentials()
        if not credentials:
            self.stdout.write(
                self.style.WARNING(
                    "Google Indexing API credentials are not configured; skipping."
                )
            )
            return

        access_token = self.fetch_access_token(credentials)
        if not access_token:
            return

        success = 0
        failed = 0
        for url in urls:
            if self.publish(access_token, url, notification_type):
                success += 1
                self.stdout.write(f"  OK {url}")
            else:
                failed += 1
                self.stdout.write(self.style.ERROR(f"  FAIL {url}"))
            time.sleep(0.1)

        self.stdout.write(self.style.SUCCESS(f"Done. Success: {success}; failed: {failed}."))

    def build_urls(self, options):
        site_url = getattr(settings, "SITE_URL", "https://remoteimpact.org").rstrip("/")
        limit = max(1, options["limit"])

        if options["job_slug"]:
            qs = Job.objects.filter(slug=options["job_slug"])
        else:
            cutoff = timezone.now() - timedelta(hours=options["hours"])
            qs = Job.objects.filter(updated_at__gte=cutoff)

        if options["type"] == "URL_UPDATED":
            qs = qs.filter(is_active=True).exclude(expires_at__lt=timezone.now())

        qs = qs.order_by("-updated_at").only("slug")[:limit]
        return [f"{site_url}{job.get_absolute_url()}" for job in qs]

    def load_credentials(self):
        raw_json = os.getenv("GOOGLE_INDEXING_CREDENTIALS_JSON")
        if raw_json:
            try:
                return json.loads(raw_json)
            except json.JSONDecodeError as exc:
                self.stdout.write(self.style.ERROR(f"Invalid GOOGLE_INDEXING_CREDENTIALS_JSON: {exc}"))
                return None

        path = os.getenv("GOOGLE_APPLICATION_CREDENTIALS")
        if not path:
            return None
        try:
            with open(path, encoding="utf-8") as handle:
                return json.load(handle)
        except OSError as exc:
            self.stdout.write(self.style.ERROR(f"Could not read GOOGLE_APPLICATION_CREDENTIALS: {exc}"))
            return None

    def fetch_access_token(self, credentials):
        now = int(time.time())
        token_uri = credentials.get("token_uri") or self.TOKEN_URI
        claims = {
            "iss": credentials.get("client_email"),
            "scope": self.TOKEN_SCOPE,
            "aud": token_uri,
            "iat": now,
            "exp": now + 3600,
        }
        try:
            assertion = jwt.encode(
                claims,
                credentials["private_key"],
                algorithm="RS256",
                headers={"kid": credentials.get("private_key_id")},
            )
            response = requests.post(
                token_uri,
                data={
                    "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
                    "assertion": assertion,
                },
                timeout=20,
            )
        except Exception as exc:
            self.stdout.write(self.style.ERROR(f"Could not request Google OAuth token: {exc}"))
            return None

        if response.status_code != 200:
            self.stdout.write(self.style.ERROR(f"Google OAuth token failed: {response.status_code} {response.text[:500]}"))
            return None
        return response.json().get("access_token")

    def publish(self, access_token, url, notification_type):
        response = requests.post(
            self.PUBLISH_URL,
            headers={"Authorization": f"Bearer {access_token}"},
            json={"url": url, "type": notification_type},
            timeout=20,
        )
        return response.status_code in {200, 201}
