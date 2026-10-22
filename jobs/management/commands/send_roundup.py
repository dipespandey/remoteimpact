"""Manually approved, bounded weekly sends with durable duplicate prevention."""
import hashlib
import json
from datetime import timedelta
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.template.loader import render_to_string
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from jobs.models import Job, RoundupDelivery
from jobs.services.email_service import email_service
from jobs.services.roundup import active_subscribers, public_url, token_for, share_url, issue_week


class Command(BaseCommand):
    help = "Preview by default. Sending requires a reviewed manifest, exact approval hash and --send. No automatic scheduling."
    def add_arguments(self, parser):
        parser.add_argument("--manifest", required=True)
        parser.add_argument("--approve-sha256")
        parser.add_argument("--send", action="store_true")
        parser.add_argument("--limit", type=int, default=50)

    def handle(self, *args, **options):
        if not 1 <= options['limit'] <= 100:
            raise CommandError("Limit must be 1–100; start with a small reviewed batch.")
        raw = Path(options['manifest']).read_bytes()
        digest = hashlib.sha256(raw).hexdigest()
        try:
            issue = json.loads(raw)
        except ValueError as exc:
            raise CommandError("Invalid manifest JSON") from exc
        week = issue_week()
        if issue.get('issue_week') != week:
            raise CommandError("Prepare/review an issue for the current ISO week.")
        picks = issue.get('picks', [])
        if not isinstance(picks, list) or not 1 <= len(picks) <= 10:
            raise CommandError("An issue needs 1–10 reviewed picks.")
        if options['send']:
            if options['approve_sha256'] != digest or issue.get('status') != 'approved':
                raise CommandError("Review the manifest, mark approved, and pass its exact SHA-256.")
            if getattr(settings,'EMAIL_PROVIDER','none') not in ('cloudflare','resend'):
                raise CommandError("A configured mail provider is required.")
            seen = set()
            for pick in picks:
                try:
                    checked = parse_datetime(pick.get('employer_source_reviewed_at') or '')
                    job = Job.objects.filter(Job.availability_filter()).get(pk=pick.get('job_id'))
                    reviewed = checked and timezone.is_aware(checked) and timezone.now()-timedelta(hours=24) <= checked <= timezone.now()
                    correct = (job.title == pick.get('title') and job.organization.name == pick.get('employer')
                        and job.application_url == pick.get('employer_source')
                        and pick.get('url','').split('?')[0] == settings.SITE_URL.rstrip('/') + job.get_absolute_url())
                    if not reviewed or not correct or not pick.get('eligible_countries_review') or not pick.get('salary_review') or job.pk in seen:
                        raise ValueError('Missing review or changed listing')
                    seen.add(job.pk)
                except (Job.DoesNotExist, ValueError, TypeError) as exc:
                    raise CommandError("Every pick needs a current employer-source, location and pay review; listing details must match.") from exc
        eligible = active_subscribers().exclude(roundupdelivery__issue_week=week).order_by('pk')
        result = {'issue_week':week,'content_sha256':digest,'eligible':eligible.count(),'batch_limit':options['limit'],
            'accepted_by_provider':0,'unknown_or_rejected':0,'already_claimed':0,'mode':'send' if options['send'] else 'preview'}
        if not options['send']:
            self.stdout.write(json.dumps(result,indent=2)); return
        for sub in list(eligible[:options['limit']]):
            claim,created = RoundupDelivery.objects.get_or_create(subscriber=sub,issue_week=week,defaults={'content_sha256':digest})
            if not created:
                result['already_claimed'] += 1; continue
            # Recheck permission after claiming and before any network operation.
            if not active_subscribers().filter(pk=sub.pk).exists():
                claim.status='suppressed';claim.save(update_fields=['status']);continue
            stop=public_url('jobs:roundup_unsubscribe',token=token_for(sub,'unsubscribe'))
            html=render_to_string('emails/roundup_issue.html',{'picks':picks,'stop_url':stop,'share_url':share_url(sub)})
            text='Remote Impact weekly picks\n\n'+'\n\n'.join(f"{p['title']} — {p['employer']}\n{p['eligible_countries_review']}\n{p['salary_review']}\n{p['url']}" for p in picks)+f'\n\nUnsubscribe: {stop}'
            headers={'List-Unsubscribe':f'<{stop}>','List-Unsubscribe-Post':'List-Unsubscribe=One-Click'}
            try:
                # One configured provider only. Never fallback after an ambiguous timeout.
                if settings.EMAIL_PROVIDER=='cloudflare':
                    accepted=email_service._send_cloudflare(sub.email,'Your Remote Impact weekly job picks',html,text,headers)=='sent'
                else:
                    accepted=email_service._send_resend(sub.email,'Your Remote Impact weekly job picks',html,text,headers)
            except Exception:
                accepted=False
            claim.status='accepted' if accepted else 'unknown'
            claim.accepted_at=timezone.now() if accepted else None
            claim.save(update_fields=['status','accepted_at'])
            result['accepted_by_provider' if accepted else 'unknown_or_rejected'] += 1
        self.stdout.write(json.dumps(result,indent=2))
