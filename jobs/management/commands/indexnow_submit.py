"""Tell IndexNow engines (Bing and partners) about job pages that opened or closed recently."""
from urllib.parse import urlsplit

import requests
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from jobs.services.search_indexing import candidates, pending, fingerprint, acknowledge

ENDPOINT = "https://api.indexnow.org/indexnow"


class Command(BaseCommand):
    help = "Submit recently opened/closed job URLs to IndexNow (dry run unless --send)."

    def add_arguments(self, parser):
        parser.add_argument("--hours", type=int, default=6)
        parser.add_argument("--send", action="store_true")
        parser.add_argument('--limit', type=int, default=1000)

    def handle(self, *args, **options):
        site = settings.SITE_URL.rstrip("/")
        if not 1 <= options['limit'] <= 10000 or options['hours'] < 1:
            raise CommandError('Invalid indexing bounds')
        if not settings.INDEXNOW_ENABLED:
            self.stdout.write('IndexNow is disabled; no requests sent.')
            return
        jobs = []
        for job in candidates('indexnow', options['hours']).order_by('-updated_at').iterator(chunk_size=200):
            if pending(job, 'indexnow'):
                jobs.append((job, fingerprint(job)))
            if len(jobs) >= options['limit']:
                break
        urls = [site + job.get_absolute_url() for job, signature in jobs]
        self.stdout.write(f"{len(urls)} URLs")
        if not options["send"] or not urls:
            return
        for start in range(0, len(urls), 10000):
            try:
                response = requests.post(ENDPOINT, json={
                "host": urlsplit(site).hostname, "key": settings.INDEXNOW_KEY,
                "keyLocation": f"{site}/{settings.INDEXNOW_KEY}.txt", "urlList": urls[start:start + 10000],
                }, timeout=30)
            except requests.RequestException as exc:
                raise CommandError(f'IndexNow request failed ({type(exc).__name__}); URLs remain pending.') from None
            self.stdout.write(f"submitted {len(urls[start:start + 10000])}: HTTP {response.status_code}")
            if response.status_code not in {200, 202}:
                raise CommandError(f'IndexNow rejected the batch (HTTP {response.status_code}); retry on the next scheduled run.')
            for job, signature in jobs[start:start + 10000]:
                acknowledge(job, 'indexnow', signature)
