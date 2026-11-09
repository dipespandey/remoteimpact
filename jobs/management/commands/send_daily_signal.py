"""Email the owner yesterday's numbers against the previous 7 days, with anything that needs attention.

Prints instead of sending unless --send. Days are UTC, matching the stats tables.
"""
from datetime import timedelta

from django.conf import settings
from django.core.management.base import BaseCommand
from django.template.loader import render_to_string
from django.utils import timezone

from jobs.services.email_service import email_service
from jobs.services.impact_metrics import day_signal, weekly_audience

ROWS = [
    ("apply_clicks", "Apply clicks sent to employers"),
    ("job_views", "Job page views"),
    ("roles_seen", "Live roles re-seen at their source"),
    ("accounts", "New accounts"),
    ("weekly_requests", "Weekly roundup signups requested"),
    ("weekly_requests_from_apply_prompt", "... of which from the apply prompt"),
    ("weekly_confirmed", "Weekly roundup signups confirmed"),
    ("claims_verified", "Employer pages claimed"),
    ("employer_offers_sent", "Employer offers sent"),
    ("features_sold", "Features sold ($49)"),
    ("posts_sold", "Job posts sold"),
    ("pro_payments", "Pro payments"),
]
MONEY = ("features_sold", "posts_sold", "pro_payments")


def build_signal(day):
    today = day_signal(day)
    history = [day_signal(day - timedelta(days=n)) for n in range(1, 8)]
    rows = [{"key": key, "label": label, "value": today[key],
             "average": round(sum(h[key] for h in history) / len(history), 1)} for key, label in ROWS]
    alerts = []
    sales = sum(today[k] for k in MONEY)
    if sales:
        alerts.append(f"{sales} paid event{'s' if sales != 1 else ''} yesterday. Check Stripe and thank the customer.")
    if today["roles_seen"] == 0:
        alerts.append("No live role was re-seen at its source. Imports or verify_ats_jobs may be failing: check /var/log/easyclaw_import*.log and easyclaw_ats_verify.log.")
    clicks_avg = next(r["average"] for r in rows if r["key"] == "apply_clicks")
    if clicks_avg >= 50 and today["apply_clicks"] < 0.6 * clicks_avg:
        alerts.append(f"Apply clicks fell to {today['apply_clicks']} from a 7-day average of {clicks_avg:.0f}.")
    if today["claims_verified"]:
        alerts.append("An employer claimed their page. They are your warmest lead this week.")
    audience = weekly_audience()
    subject = (f"Remote Impact {day:%a %d %b}: {today['apply_clicks']} apply clicks, "
               f"{today['weekly_confirmed']} new subscribers, {sales} sale{'s' if sales != 1 else ''}")
    return {"day": day, "rows": rows, "alerts": alerts, "audience": audience, "subject": subject}


class Command(BaseCommand):
    help = "Email the owner yesterday's Remote Impact signal (prints unless --send)."

    def add_arguments(self, parser):
        parser.add_argument("--send", action="store_true")

    def handle(self, *args, **options):
        signal = build_signal(timezone.localdate() - timedelta(days=1))
        text = render_to_string("emails/daily_signal.txt", signal)
        if not options["send"]:
            self.stdout.write(signal["subject"] + "\n\n" + text)
            return
        sent = email_service.send_email(settings.OWNER_REPORT_EMAIL, signal["subject"],
                                        render_to_string("emails/daily_signal.html", signal), text=text)
        self.stdout.write(("Sent" if sent else "Failed to send") + f": {signal['subject']}")
