"""One-to-one partnership emails to impact-aligned paid contributor programmes.

Reads a JSON list of {"org", "email", "reason"} and sends one plain email to each,
skipping anyone already in the log. Dry run unless --send.
"""
import json
import os
from django.core.management.base import BaseCommand
from django.utils import timezone

from jobs.services.email_service import EmailService

SENDER = "Dipesh from Remote Impact <hello@remoteimpact.org>"
ASKS = {
    "fee": "If the people we send work out, we'd ask for a referral fee per approved contributor, at a rate you think is fair. Happy to start with a free month so you can judge the quality first.",
    "free": "As you're a nonprofit, there's no fee: we'd simply like your contributor roles in front of the right people.",
}
SUBJECT = "Featuring {org} in Remote Impact's paid remote work section"
BODY = """Hi {org} team,

I run Remote Impact (remoteimpact.org), a job board for remote roles at climate, global health, AI safety and other public-interest organizations. Listings link to their sources and carry recorded verification details when available; we only list organizations with a clear mission.

Next to our full-time listings, we're opening a small section for paid, flexible remote work at mission-driven organizations. We'd like {org} to be one of the first programmes in it: {reason}.

What I'd propose: we feature your contributor programme on Remote Impact and send interested people straight to your own application page, tagged utm_source=remoteimpact so you can see who came from us. {ask}

{users} people have signed up to Remote Impact so far, and {scouts} of them have set up an AI Scout that emails them matching roles.

Who would be the right person to talk to about this? If it isn't relevant, just say so and I won't follow up.

Dipesh
Remote Impact · remoteimpact.org
"""


class Command(BaseCommand):
    help = "Send partnership emails to impact-aligned contributor programmes (dry run unless --send)."

    def add_arguments(self, parser):
        parser.add_argument("path")
        parser.add_argument("--log", default="/app/reports/partner_outreach_log.json")
        parser.add_argument("--send", action="store_true")

    def handle(self, *args, **options):
        partners = json.load(open(options["path"]))
        log = json.load(open(options["log"])) if os.path.exists(options["log"]) else []
        done = {entry["email"].lower() for entry in log}
        from django.contrib.auth import get_user_model
        from jobs.models import JobAlert
        users = get_user_model().objects.filter(is_staff=False).count()
        scouts = JobAlert.objects.exclude(intent_text="").values("user").distinct().count()
        service = EmailService()
        service.from_email = SENDER
        for partner in partners:
            email = partner["email"].strip()
            if email.lower() in done:
                self.stdout.write(f"skip (already sent) {partner['org']}")
                continue
            body = BODY.format(org=partner["org"], reason=partner["reason"].rstrip("."), ask=ASKS[partner.get("ask", "fee")],
                               users=f"{users:,}", scouts=f"{scouts:,}")
            subject = SUBJECT.format(org=partner["org"])
            if not options["send"]:
                self.stdout.write(f"--- to {email}\nSubject: {subject}\n\n{body}")
                continue
            html = "".join(f"<p>{p.replace(chr(10), '<br>')}</p>" for p in body.strip().split("\n\n"))
            ok = service.send_email(to=email, subject=subject, html=html, text=body)
            self.stdout.write(f"{'sent' if ok else 'FAILED'} {partner['org']} <{email}>")
            if ok:
                log.append({"org": partner["org"], "email": email, "sent_at": timezone.now().isoformat()})
                with open(options["log"], "w") as fh:
                    json.dump(log, fh, indent=1)
