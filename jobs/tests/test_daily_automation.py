import io
import json
from datetime import timedelta
from unittest.mock import patch

from django.core.cache import cache
from django.core.management import call_command
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone

from jobs.models import (Category, ConsumerFunnelEvent, Job, JobDailyStat, JobFeaturePurchase, Organization,
                         OrganizationClaim, RoundupDelivery, RoundupSubscriber)
from jobs.services.impact_metrics import competition
from jobs.tests.test_employer_product import DESCRIPTION, EmployerTestBase


def verified(hours_ago=1):
    checked = (timezone.now() - timedelta(hours=hours_ago)).isoformat()
    return {"source_verification": {"status": "open", "checked_at": checked}, "ats_check": {"location": "Remote - US"}}


@override_settings(SECURE_SSL_REDIRECT=False, SITE_URL="https://remoteimpact.org", OWNER_REPORT_EMAIL="owner@example.org")
class DailyAutomationTests(EmployerTestBase):
    def setUp(self):
        super().setUp()
        cache.clear()
        self.climate = Category.objects.create(name="Climate & Environment", slug="climate-environment")
        self.health = Category.objects.create(name="Global Health", slug="global-health")

    def role(self, n, category, *, raw=None, salary=60000, level="mid", org=None, description=DESCRIPTION):
        org = org or Organization.objects.create(name=f"Org {n}", slug=f"org-{n}")
        return Job.objects.create(title=f"Role {n}", slug=f"role-{n}", organization=org, category=category, description=description,
                                  application_url=f"https://jobs.example.org/{n}", quality_score=80, salary_min=salary,
                                  salary_max=salary + 10000, location="Remote (US)", experience_level=level, raw_data=raw or {})

    def test_competition_divides_clicks_by_open_roles(self):
        for n in range(20):
            job = self.role(n, self.climate)
            JobDailyStat.objects.create(job=job, date=timezone.localdate(), views=10, apply_clicks=3 if n < 10 else 0)
        row = next(r for r in competition("category") if r.label == "Climate & Environment")
        self.assertEqual((row.roles, row.clicks, row.per_role), (20, 30, 1.5))

    def test_daily_signal_reports_yesterday_and_flags_sales(self):
        yesterday = timezone.now() - timedelta(days=1)
        JobDailyStat.objects.create(job=self.job, date=yesterday.date(), views=40, apply_clicks=12)
        purchase = JobFeaturePurchase.objects.create(job=self.job, stripe_checkout_session_id="cs_1", amount_cents=4900,
                                                     featured_until=timezone.now() + timedelta(days=30))
        JobFeaturePurchase.objects.filter(pk=purchase.pk).update(created_at=yesterday)
        claim = OrganizationClaim.objects.create(organization=self.org, email="a@climate.example.org")
        OrganizationClaim.objects.filter(pk=claim.pk).update(verified_at=yesterday)
        with patch("jobs.management.commands.send_daily_signal.email_service.send_email", return_value=True) as send:
            call_command("send_daily_signal", send=True, stdout=io.StringIO())
        to, subject, html = send.call_args.args
        text = send.call_args.kwargs["text"]
        self.assertEqual(to, "owner@example.org")
        self.assertIn("12 apply clicks", subject)
        self.assertIn("1 sale", subject)
        self.assertIn("Features sold ($49): 1", text)
        self.assertIn("An employer claimed their page", text)

    def test_click_alert_compares_with_the_same_weekday(self):
        day = timezone.localdate() - timedelta(days=1)
        JobDailyStat.objects.create(job=self.job, date=day, apply_clicks=90)
        JobDailyStat.objects.create(job=self.role(50, self.climate), date=day - timedelta(days=1), apply_clicks=400)
        JobDailyStat.objects.create(job=self.role(51, self.climate), date=day - timedelta(days=7), apply_clicks=100)
        from jobs.management.commands.send_daily_signal import build_signal
        self.assertFalse([a for a in build_signal(day)["alerts"] if "Apply clicks fell" in a])

    def test_friday_signal_carries_a_linkedin_post_built_from_the_data(self):
        from jobs.management.commands.send_daily_signal import build_signal
        global_health = Category.objects.create(name="Global Health", slug="global-health") if not Category.objects.filter(slug="global-health").exists() else Category.objects.get(slug="global-health")
        for n in range(10):
            JobDailyStat.objects.create(job=self.role(200 + n, self.climate), date=timezone.localdate(), apply_clicks=1)
            JobDailyStat.objects.create(job=self.role(300 + n, global_health), date=timezone.localdate(), apply_clicks=5)
        thursday = timezone.localdate() - timedelta(days=(timezone.localdate().weekday() - 3) % 7 or 7)
        post = build_signal(thursday)["linkedin_post"]
        self.assertIn("Global Health roles drew 5.0 apply clicks each", post)
        self.assertIn("Climate & Environment roles drew 1.0, across 10 open roles", post)
        self.assertIn("/insights/ai-and-impact-work/", post)
        self.assertEqual(build_signal(thursday - timedelta(days=1))["linkedin_post"], "")

    def test_daily_signal_dry_run_sends_nothing(self):
        with patch("jobs.management.commands.send_daily_signal.email_service.send_email") as send:
            out = io.StringIO()
            call_command("send_daily_signal", stdout=out)
        send.assert_not_called()
        self.assertIn("Apply clicks sent to employers", out.getvalue())

    def auto_roundup(self, *args):
        out = io.StringIO()
        with override_settings(EMAIL_PROVIDER="resend"), \
                patch("jobs.management.commands.send_roundup.email_service._send_resend", return_value=True) as provider:
            call_command("send_auto_roundup", *args, stdout=out)
        return provider, out.getvalue()

    def test_auto_roundup_sends_freshly_verified_roles_and_only_states_annual_pay(self):
        good = [self.role(n, self.climate if n % 2 else self.health, raw=verified()) for n in range(4)]
        self.role(10, self.climate, raw=verified(hours_ago=30))
        self.role(11, self.climate, raw={"source_verification": {"status": "closed", "checked_at": timezone.now().isoformat()}})
        hourly = self.role(12, self.climate, raw={**verified(), "salaryPeriod": "HOUR"})
        self.role(14, self.climate, raw=["not", "a", "dict"])
        RoundupSubscriber.objects.create(email="reader@example.org", confirmed_at=timezone.now())
        provider, out = self.auto_roundup("--send")
        self.assertEqual(provider.call_count, 1)
        _, subject, html, text, _ = provider.call_args.args
        self.assertEqual(subject, "This week's verified remote impact roles")
        for job in good:
            self.assertIn(job.title, text)
        for title in ("Role 10", "Role 11", "Role 14"):
            self.assertNotIn(title, text)
        hourly_block = text.split(hourly.title + " —")[1].split("\n\n")[0]
        self.assertIn("Pay: check the employer's posting.", hourly_block)
        self.assertNotIn("a year", hourly_block)
        self.assertIn("The employer lists: Remote - US.", text)
        self.assertIn("USD 60,000–70,000 a year", text)

    def test_auto_roundup_skips_recruiting_pages_and_cleans_locations(self):
        from jobs.management.commands.send_auto_roundup import where
        for n in range(4):
            self.role(n, self.climate, raw=verified())
        generic = self.role(9, self.climate, raw=verified())
        generic.title = "Early Career Opportunities"
        generic.save()
        RoundupSubscriber.objects.create(email="reader@example.org", confirmed_at=timezone.now())
        provider, _ = self.auto_roundup("--send")
        self.assertNotIn("Early Career Opportunities", provider.call_args.args[3])
        messy = Job(location="Remote (US)", raw_data={"ats_check": {"location": ",, ,, United States"}})
        self.assertIn("The employer lists: United States.", where(messy))
        vague = Job(location="Remote", raw_data={"ats_check": {"location": "Remote job"}})
        self.assertNotIn("employer lists", where(vague))

    def test_auto_roundup_never_sends_twice_in_a_week(self):
        for n in range(4):
            self.role(n, self.climate, raw=verified())
        RoundupSubscriber.objects.create(email="reader@example.org", confirmed_at=timezone.now())
        self.auto_roundup("--send")
        provider, out = self.auto_roundup("--send")
        provider.assert_not_called()
        self.assertIn("already went out", out)
        self.assertEqual(RoundupDelivery.objects.count(), 1)

    def test_auto_roundup_holds_a_thin_issue(self):
        self.role(1, self.climate, raw=verified())
        RoundupSubscriber.objects.create(email="reader@example.org", confirmed_at=timezone.now())
        provider, out = self.auto_roundup("--send")
        provider.assert_not_called()
        self.assertIn("Only 1 verified roles qualified", out)

    def test_monthly_report_does_not_pitch_a_role_that_is_already_featured(self):
        self.job.featured_until = timezone.now() + timedelta(days=10)
        self.job.save()
        JobDailyStat.objects.create(job=self.job, date=timezone.localdate(), views=30, apply_clicks=5)
        OrganizationClaim.objects.create(organization=self.org, email="a@climate.example.org", verified_at=timezone.now())
        with patch("jobs.services.email_service.EmailService.send_email", return_value=True) as send:
            call_command("send_employer_monthly_reports", send=True, stdout=io.StringIO())
        self.assertNotIn("/feature/", send.call_args.kwargs["text"])

    def test_insight_only_claims_a_real_gap(self):
        from jobs.management.commands.send_auto_roundup import insight
        for n in range(10):
            JobDailyStat.objects.create(job=self.role(n, self.climate, level="entry"), date=timezone.localdate(), apply_clicks=2)
            JobDailyStat.objects.create(job=self.role(100 + n, self.climate, level="senior"), date=timezone.localdate(), apply_clicks=2)
        self.assertNotIn("times as many", insight())

    def test_monthly_report_offers_a_feature_for_the_most_clicked_role(self):
        self.org.homepage_url = "https://climate.example.org"
        self.org.save()
        JobDailyStat.objects.create(job=self.job, date=timezone.localdate(), views=30, apply_clicks=5)
        OrganizationClaim.objects.create(organization=self.org, email="a@climate.example.org", verified_at=timezone.now())
        with patch("jobs.services.email_service.EmailService.send_email", return_value=True) as send:
            call_command("send_employer_monthly_reports", send=True, stdout=io.StringIO())
        self.assertIn(f"/jobs/{self.job.slug}/feature/?utm_source=email&utm_campaign=employer_monthly", send.call_args.kwargs["text"])

    def test_index_page_shows_demand_and_ai_sections(self):
        for n in range(20):
            job = self.role(n, self.climate)
            JobDailyStat.objects.create(job=job, date=timezone.localdate(), views=10, apply_clicks=2)
        response = self.client.get(reverse("jobs:impact_jobs_snapshot"))
        self.assertContains(response, "Where applicants are going")
        self.assertContains(response, "AI and impact work")
        self.assertContains(response, "2.0 · 20 roles")


class PlainTextEmailTemplateTests(EmployerTestBase):
    def test_plain_text_emails_are_never_html_escaped(self):
        from pathlib import Path
        from django.conf import settings
        folder = Path(settings.BASE_DIR) / "templates" / "emails"
        missing = [p.name for p in folder.glob("*.txt") if not p.read_text().startswith("{% autoescape off %}")]
        self.assertEqual(missing, [], "Plain-text emails must start with {% autoescape off %} or links and names arrive as &amp; and &#x27;")

    def test_names_and_links_survive_in_plain_text(self):
        from django.template.loader import render_to_string
        text = render_to_string("emails/employer_offer.txt", {
            "org": Organization(name="Huntington's Disease Youth Organization"), "stats": {"roles": 1, "views": 3, "clicks": 2, "top": "Programs & Support"},
            "feature_url": "https://remoteimpact.org/x/?a=1&b=2", "claim_url": "https://remoteimpact.org/y/?a=1&b=2"})
        self.assertIn("Huntington's Disease", text)
        self.assertIn("Programs & Support", text)
        self.assertIn("?a=1&b=2", text)


@override_settings(SECURE_SSL_REDIRECT=False, SITE_URL="https://remoteimpact.org")
class AIImpactWorkPageTests(EmployerTestBase):
    def test_page_shows_ai_safety_demand_and_ai_skill_roles_by_domain(self):
        cache.clear()
        safety = Category.objects.create(name="AI Safety & Governance", slug="ai-safety")
        climate = Category.objects.create(name="Climate & Environment", slug="climate-environment")
        for n, (title, category) in enumerate([("Research Engineer", safety), ("AI Program Lead", climate), ("Machine Learning Analyst", climate), ("Grant Writer", climate)]):
            org = Organization.objects.create(name=f"AI Org {n}", slug=f"ai-org-{n}")
            Job.objects.create(title=title, slug=f"ai-role-{n}", organization=org, category=category, description=DESCRIPTION,
                               application_url=f"https://jobs.example.org/ai/{n}", quality_score=80)
        response = self.client.get(reverse("jobs:ai_impact_work"))
        self.assertContains(response, "AI and the future of impact work")
        self.assertContains(response, '<a href="/jobs/category/climate-environment/" class="font-medium text-foreground hover:underline">Climate &amp; Environment</a><span class="text-xs tabular-nums text-muted-foreground">2 roles</span>', html=False)
        self.assertContains(response, '"@type": "Dataset"')
        self.assertContains(response, "High-impact areas")
        ea = Category.objects.create(name="Effective Altruism", slug="effective-altruism")
        Job.objects.create(title="Operations Lead", slug="ea-ops", organization=self.org, category=ea, description=DESCRIPTION,
                           application_url="https://jobs.example.org/ea", quality_score=80)
        cache.clear()
        self.assertContains(self.client.get(reverse("jobs:ai_impact_work")), ">Effective Altruism</a>")


class ScoutOutageSignalTests(EmployerTestBase):
    def searches(self, unreviewed, reviewed):
        yesterday = timezone.now() - timedelta(days=1)
        for n in range(unreviewed + reviewed):
            workflow = {"reranker": "review_unavailable", "review_incomplete": True} if n < unreviewed else {"reranker": "model"}
            event = ConsumerFunnelEvent.objects.create(event=ConsumerFunnelEvent.Event.SCOUT_PREVIEWED,
                                                       metadata={"match_count": 0, "workflow": workflow})
            ConsumerFunnelEvent.objects.filter(pk=event.pk).update(created_at=yesterday)

    def balance_response(self, payload):
        response = type("R", (), {"raise_for_status": lambda self: None, "json": lambda self: payload})()
        return patch("jobs.management.commands.send_daily_signal.requests.get", return_value=response)

    @override_settings(DEEPSEEK_API_KEY="sk-test")
    def test_signal_flags_searches_scout_could_not_review_and_an_empty_balance(self):
        self.searches(unreviewed=6, reviewed=4)
        from jobs.management.commands.send_daily_signal import build_signal
        with self.balance_response({"is_available": False, "balance_infos": [{"currency": "USD", "total_balance": "0.00"}]}):
            signal = build_signal(timezone.localdate() - timedelta(days=1))
        rows = {r["key"]: r["value"] for r in signal["rows"]}
        self.assertEqual((rows["scout_searches"], rows["scout_unreviewed"]), (10, 6))
        self.assertTrue(any("could not review 6 of 10" in a for a in signal["alerts"]))
        self.assertTrue(any("DeepSeek balance is $0.00" in a for a in signal["alerts"]))

    @override_settings(DEEPSEEK_API_KEY="sk-test")
    def test_healthy_scout_and_funded_account_raise_nothing(self):
        self.searches(unreviewed=1, reviewed=9)
        from jobs.management.commands.send_daily_signal import build_signal
        with self.balance_response({"is_available": True, "balance_infos": [{"currency": "USD", "total_balance": "42.10"}]}):
            alerts = build_signal(timezone.localdate() - timedelta(days=1))["alerts"]
        self.assertFalse([a for a in alerts if "Scout" in a or "DeepSeek" in a])


@override_settings(SCOUT_PREVIEW_ASYNC_ENABLED=True)
class ScoutCrawlerTests(EmployerTestBase):
    def test_crawlers_following_similar_roles_links_start_no_paid_search(self):
        from jobs.scout_task_models import ScoutPreviewTask
        url = reverse("jobs:job_scout_preview") + "?intent=Remote%20Program%20Manager%20roles%20similar%20to%20this%20one&source=job_detail"
        agent = "Mozilla/5.0 AppleWebKit/537.36 (KHTML, like Gecko); compatible; ShapBot/0.1.0"
        response = self.client.get(url, HTTP_USER_AGENT=agent)
        self.assertEqual(response["X-Robots-Tag"], "noindex, nofollow")
        self.assertFalse(ScoutPreviewTask.objects.exists())
        self.assertFalse(ConsumerFunnelEvent.objects.exists())
        self.client.get(url, HTTP_USER_AGENT="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) Safari/605.1.15")
        self.assertTrue(ScoutPreviewTask.objects.exists())
