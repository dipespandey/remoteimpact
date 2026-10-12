"""One-time "Remote Impact got better" email.

--audience seekers: opted-in users and newsletter subscribers who haven't unsubscribed.
--audience employers: the organizations with the most live roles, at a public email found on their own website.

Dry run by default: prints who would get it. Pass --send (and --limit for daily batches).
Each address gets each campaign at most once.
"""
import re
from collections import Counter

from django.conf import settings
from django.core.management.base import BaseCommand
from django.core.signing import TimestampSigner
from django.db import IntegrityError
from django.db.models import Count
from django.template.loader import render_to_string
from django.urls import reverse

from jobs.models import Job, NewsletterSubscriber, Organization, OutreachSend, UserProfile
from jobs.services.email_service import email_service
from jobs.services.org_sites import host_of

SEEKER_CAMPAIGN = "relaunch-2026-10-seekers"
EMPLOYER_CAMPAIGN = "relaunch-2026-10-employers"
EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
# Best inbox for a hiring note first; personal-looking addresses are never used.
PREFERRED = ("jobs", "careers", "career", "recruitment", "recruiting", "hiring", "hr", "people", "talent",
             "hello", "info", "contact", "office", "admin", "team", "general", "enquiries", "inquiries")
CONTACT_PATHS = ("", "contact", "contact-us", "about", "careers", "jobs")


def site_email(org) -> str:
    """A role-based email on the organization's own domain, published on its own website."""
    from jobs.services.safe_fetch import fetch_public_html

    home = org.homepage_url
    domain = host_of(home)
    if not domain:
        return ""
    found = Counter()
    for path in CONTACT_PATHS:
        page = fetch_public_html(f"https://{domain}/{path}", max_bytes=600_000, total_timeout=8, max_redirects=2)
        if page.status != "ok":
            continue
        for address in EMAIL.findall(page.text):
            address = address.lower().rstrip(".")
            local, _, host = address.partition("@")
            if (host == domain or host.endswith("." + domain)) and local in PREFERRED:
                found[address] += 1
        best = sorted(found, key=lambda a: PREFERRED.index(a.split("@")[0]))
        if best and best[0].split("@")[0] in PREFERRED[:9]:
            break
    best = sorted(found, key=lambda a: PREFERRED.index(a.split("@")[0]))
    return best[0] if best else ""


class Command(BaseCommand):
    help = "Send the one-time relaunch email to seekers or employers. Dry run unless --send."

    def add_arguments(self, parser):
        parser.add_argument("--audience", choices=["seekers", "employers"], required=True)
        parser.add_argument("--send", action="store_true")
        parser.add_argument("--limit", type=int, default=0)
        parser.add_argument("--top", type=int, default=30, help="Employers: how many organizations to consider.")

    def handle(self, *args, **options):
        self.site = getattr(settings, "SITE_URL", "https://remoteimpact.org").rstrip("/")
        if options["audience"] == "seekers":
            self.seekers(options)
        else:
            self.employers(options)

    def record(self, campaign, email, org=None) -> bool:
        try:
            OutreachSend.objects.create(campaign=campaign, email=email.lower(), organization=org)
            return True
        except IntegrityError:
            return False

    def seekers(self, options):
        done = set(OutreachSend.objects.filter(campaign=SEEKER_CAMPAIGN).values_list("email", flat=True))
        unsubscribed = {e.lower() for e in NewsletterSubscriber.objects.filter(unsubscribed=True)
                        .values_list("email", flat=True)}
        unsubscribed |= {e.lower() for e in UserProfile.objects.filter(email_newsletter_unsubscribed_at__isnull=False)
                         .values_list("user__email", flat=True) if e}
        people = []  # (email, first_name, unsubscribe_url)
        for profile in UserProfile.objects.filter(email_newsletter=True, user__is_active=True).select_related("user"):
            if profile.user.email:
                people.append((profile.user.email.lower(), profile.user.first_name,
                               email_service._get_unsubscribe_url(profile.user_id)))
        signer = TimestampSigner(salt="subscriber-unsubscribe")
        for email in NewsletterSubscriber.objects.filter(unsubscribed=False).values_list("email", flat=True):
            path = reverse("jobs:newsletter_unsubscribe", kwargs={"token": signer.sign(email.lower())})
            people.append((email.lower(), "", f"{self.site}{path}"))
        jobs_url = f"{self.site}/jobs/?utm_source=email&utm_campaign=relaunch"
        sent = 0
        seen = set()
        for email, first_name, unsubscribe_url in people:
            if email in seen or email in done or email in unsubscribed:
                continue
            seen.add(email)
            if not options["send"]:
                self.stdout.write(f"Would email {email}")
            else:
                context = {"first_name": first_name, "jobs_url": jobs_url, "unsubscribe_url": unsubscribe_url}
                if not self.record(SEEKER_CAMPAIGN, email):
                    continue
                if not email_service.send_email(
                    to=email, subject="Remote Impact got a lot better",
                    html=render_to_string("emails/relaunch_seeker.html", context),
                    text=render_to_string("emails/relaunch_seeker.txt", context),
                    unsubscribe_url=unsubscribe_url,
                ):
                    OutreachSend.objects.filter(campaign=SEEKER_CAMPAIGN, email=email).delete()
                    continue
            sent += 1
            if options["limit"] and sent >= options["limit"]:
                break
        verb = "Sent" if options["send"] else "Dry run: would send"
        self.stdout.write(f"{verb} {sent} seeker email(s); {len(seen) - sent if options['limit'] else 0} not reached.")

    def employers(self, options):
        done = set(OutreachSend.objects.filter(campaign=EMPLOYER_CAMPAIGN).values_list("organization_id", flat=True))
        counts = (Job.objects.filter(Job.availability_filter()).values("organization_id")
                  .annotate(live=Count("id")).order_by("-live"))
        live = {row["organization_id"]: row["live"] for row in counts}
        candidates = Organization.objects.filter(pk__in=live).exclude(homepage_url="").exclude(slug="") \
            .filter(members__isnull=True)
        orgs = sorted(candidates, key=lambda o: -live[o.pk])[: options["top"]]
        for org in orgs:
            org.live = live[org.pk]
        sent = 0
        for org in orgs:
            if org.pk in done:
                continue
            email = site_email(org)
            if not email:
                self.stdout.write(f"No public email: {org.name}")
                continue
            org_url = f"{self.site}{reverse('jobs:organization_profile', kwargs={'slug': org.slug})}"
            claim_url = f"{self.site}{reverse('jobs:claim_organization', kwargs={'slug': org.slug})}"
            context = {"org": org, "count": org.live, "org_url": org_url, "claim_url": claim_url}
            if not options["send"]:
                self.stdout.write(f"Would email {org.name} ({org.live} roles) at {email}")
            else:
                if not self.record(EMPLOYER_CAMPAIGN, email, org):
                    continue
                if not email_service.send_email(
                    to=email, subject=f"{org.name} has {org.live} role{'s' if org.live != 1 else ''} on Remote Impact",
                    html=render_to_string("emails/relaunch_employer.html", context),
                    text=render_to_string("emails/relaunch_employer.txt", context),
                ):
                    OutreachSend.objects.filter(campaign=EMPLOYER_CAMPAIGN, email=email).delete()
                    continue
            sent += 1
            if options["limit"] and sent >= options["limit"]:
                break
        verb = "Sent" if options["send"] else "Dry run: would send"
        self.stdout.write(f"{verb} {sent} employer email(s).")
