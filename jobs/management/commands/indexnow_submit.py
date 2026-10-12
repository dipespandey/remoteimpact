"""Tell IndexNow engines (Bing and partners) about job pages that opened or closed recently."""
from datetime import timedelta

import requests
from django.conf import settings
from django.core.management.base import BaseCommand
from django.db.models import Q
from django.utils import timezone

from jobs.models import Job

ENDPOINT = "https://api.indexnow.org/indexnow"


class Command(BaseCommand):
    help = "Submit recently opened/closed job URLs to IndexNow (dry run unless --send)."

    def add_arguments(self, parser):
        parser.add_argument("--hours", type=int, default=6)
        parser.add_argument("--send", action="store_true")

    def handle(self, *args, **options):
        site = settings.SITE_URL.rstrip("/")
        since = timezone.now() - timedelta(hours=options["hours"])
        jobs = Job.objects.filter(Q(posted_at__gte=since) | Q(updated_at__gte=since)).only("slug")[:10000]
        urls = sorted({site + job.get_absolute_url() for job in jobs})
        self.stdout.write(f"{len(urls)} URLs")
        if not options["send"] or not urls:
            return
        for start in range(0, len(urls), 10000):
            response = requests.post(ENDPOINT, json={
                "host": site.split("//", 1)[1], "key": settings.INDEXNOW_KEY,
                "keyLocation": f"{site}/{settings.INDEXNOW_KEY}.txt", "urlList": urls[start:start + 10000],
            }, timeout=30)
            self.stdout.write(f"submitted {len(urls[start:start + 10000])}: HTTP {response.status_code}")
