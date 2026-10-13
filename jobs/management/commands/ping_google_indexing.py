"""
Notify Google's Indexing API about new, updated, or removed job URLs.

Environment:
    GOOGLE_APPLICATION_CREDENTIALS=/path/to/service-account.json
    or GOOGLE_INDEXING_CREDENTIALS_JSON='{"type":"service_account",...}'

Missing credentials and rejected requests are explicit failures. Accepted
notifications are persisted; feed sightings alone do not repeatedly resubmit.
"""

import json
import os
import time

import jwt
import requests
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from jobs.models import Job
from jobs.services.search_indexing import candidates, pending, fingerprint, acknowledge
from jobs.services.job_quality import google_job_posting_json


class Command(BaseCommand):
    help = "Notify Google Indexing API for job posting URLs."

    TOKEN_SCOPE = "https://www.googleapis.com/auth/indexing"
    TOKEN_URI = "https://oauth2.googleapis.com/token"
    PUBLISH_URL = "https://indexing.googleapis.com/v3/urlNotifications:publish"

    def add_arguments(self, parser):
        parser.add_argument("--hours", type=int, default=24, help="Jobs updated in the last N hours.")
        parser.add_argument("--job-slug", help="Notify one job by slug.")
        parser.add_argument("--limit", type=int, default=100, help="Maximum URLs to notify (leaves room for removals).")
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
            raise CommandError('Google Indexing API credentials are not configured; no notifications sent.')

        access_token = self.fetch_access_token(credentials)
        if not access_token:
            raise CommandError('Google OAuth failed; no notifications sent.')

        success = 0
        failed = 0
        for url in urls:
            if self.publish(access_token, url, notification_type):
                success += 1
                job, signature = self.queued_jobs[url]
                acknowledge(job, 'google', signature)
                self.stdout.write(f"  OK {url}")
            else:
                failed += 1
                self.stdout.write(self.style.ERROR(f"  FAIL {url}"))
                raise CommandError('Google rejected a notification; remaining URLs stay pending for the next run.')
            time.sleep(0.1)

        self.stdout.write(self.style.SUCCESS(f"Done. Success: {success}; failed: {failed}."))

    def build_urls(self, options):
        site_url = getattr(settings, "SITE_URL", "https://remoteimpact.org").rstrip("/")
        limit = options['limit']
        if not 1 <= limit <= 200 or options['hours'] < 1:
            raise CommandError('Invalid indexing bounds')

        if options["job_slug"]:
            qs = Job.objects.filter(slug=options["job_slug"])
        else:
            qs = candidates('google', options['hours'])

        now = timezone.now()
        if options["type"] == "URL_UPDATED":
            qs = qs.filter(Job.availability_filter(now=now))
        else:
            qs = qs.exclude(Job.availability_filter(now=now))

        self.queued_jobs = {}
        for job in qs.select_related('organization').defer('embedding', 'search_vector').order_by('-updated_at').iterator(chunk_size=200):
            if options['type'] == 'URL_UPDATED' and not google_job_posting_json(job, site_url):
                continue  # Ordinary indexable pages without JobPosting cannot use this API.
            if pending(job, 'google'):
                url = f'{site_url}{job.get_absolute_url()}'
                self.queued_jobs[url] = (job, fingerprint(job))
            if len(self.queued_jobs) >= limit:
                break
        return list(self.queued_jobs)

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
        try:
            response = requests.post(
            self.PUBLISH_URL,
            headers={"Authorization": f"Bearer {access_token}"},
            json={"url": url, "type": notification_type},
            timeout=20,
            )
        except requests.RequestException:
            return False
        return response.status_code in {200, 201}
