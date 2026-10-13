from datetime import timedelta
from io import StringIO
from unittest import mock

from allauth.socialaccount.models import SocialApp
from django.contrib.sites.models import Site
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from jobs.models import Job, Organization
from jobs.services import ats_boards
from jobs.services.ats_boards import AtsRef, BoardResult, Check, Posting
from jobs.services.importers.common import _mark_job_seen, retire_unseen_listings
from jobs.services.job_service import JobService


class AtsUrlParsingTests(TestCase):
    def test_parses_common_ats_urls(self):
        cases = {
            "https://job-boards.greenhouse.io/planetlabs/jobs/7564777?gh_jid=7564777": ("greenhouse", "planetlabs", "7564777"),
            "https://jobs.lever.co/octoenergy/75807017-9713-4be5-8a62-46c35431fee8/apply": (
                "lever", "octoenergy", "75807017-9713-4be5-8a62-46c35431fee8"),
            "https://jobs.ashbyhq.com/ramp/34413F8D-26bf-4bbc-8ade-eb309a0e2245/application": (
                "ashby", "ramp", "34413f8d-26bf-4bbc-8ade-eb309a0e2245"),
            "https://apply.workable.com/fuseenergy/j/e58fb761cf/": ("workable", "fuseenergy", "E58FB761CF"),
            "https://apply.workable.com/j/E58FB761CF/apply?utm_source=x": ("workable", "", "E58FB761CF"),
            "https://fika.bamboohr.com/careers/108?source=x": ("bamboohr", "fika", "108"),
            "https://envelio.jobs.personio.de/job/2398720?language=en#apply": ("personio", "envelio", "2398720"),
            "https://deepsea.recruitee.com/o/senior-front-end-developer/c/new": ("recruitee", "deepsea", "senior-front-end-developer"),
            "https://clean-air-task-force.breezy.hr/p/d05ed170cf26-senior-director/apply": ("breezy", "clean-air-task-force", "d05ed170cf26"),
            "https://hitachi.wd1.myworkdayjobs.com/en-US/hitachi/job/Remote-US/Leader_R0138067?source=x": (
                "workday", "hitachi/hitachi", "Remote-US/Leader_R0138067"),
            "https://jobs.smartrecruiters.com/AECOM2/744000147555119-senior-engineer?oga=true": (
                "smartrecruiters", "AECOM2", "744000147555119"),
        }
        for url, expected in cases.items():
            ref = ats_boards.parse(url)
            self.assertIsNotNone(ref, url)
            self.assertEqual((ref.ats, ref.board, ref.job), expected, url)

    def test_unknown_hosts_and_custom_greenhouse_domains(self):
        self.assertIsNone(ats_boards.parse("https://www.idealist.org/en/nonprofit-job/abc-role"))
        self.assertIsNone(ats_boards.parse("https://sbenergy.com/careers/?gh_jid=5986874004"))
        ref = ats_boards.parse("https://sbenergy.com/careers/?gh_jid=5986874004", {"sbenergy.com": "sbenergy"})
        self.assertEqual((ref.ats, ref.board, ref.job), ("greenhouse", "sbenergy", "5986874004"))


class WorkplaceTests(TestCase):
    def test_only_clear_office_requirements_exclude_a_role(self):
        self.assertTrue(ats_boards.excludes_remote(Check("open", "onsite", "Bengaluru, India")))
        self.assertTrue(ats_boards.excludes_remote(Check("open", "hybrid", "New York, NY (HQ)")))
        self.assertFalse(ats_boards.excludes_remote(Check("open", "hybrid", "United Kingdom - Remote")))
        self.assertFalse(ats_boards.excludes_remote(Check("open", None, "London")))
        self.assertFalse(ats_boards.excludes_remote(Check("closed", "onsite", "")))

    def test_remote_flag_wins_over_hybrid_flag(self):
        self.assertEqual(ats_boards._workplace(remote=True, hybrid=True), "remote")
        self.assertEqual(ats_boards._workplace(remote=False, hybrid=False), "onsite")
        self.assertIsNone(ats_boards._workplace())

    def test_board_membership(self):
        ref = AtsRef("greenhouse", "acme", "2")
        board = BoardResult("ok", {"1": Posting()})
        self.assertEqual(ats_boards.check_against_board(ref, board).status, "closed")
        self.assertEqual(ats_boards.check_against_board(ref, BoardResult("error", error="timeout")).status, "unknown")
        self.assertEqual(ats_boards.check_against_board(ref, BoardResult("gone")).status, "closed")
        self.assertEqual(
            ats_boards.check_against_board(AtsRef("personio", "x", "2"), BoardResult("gone")).status, "unknown"
        )


def make_job(org, slug, url, source=Job.Source.CLIMATEBASE, **extra):
    return Job.objects.create(
        title=slug.replace("-", " ").title(), slug=slug, organization=org, description="x",
        application_url=url, source=source, external_id=slug, **extra,
    )


class VerifyAtsJobsCommandTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(name="Acme Climate", slug="acme-climate")
        old = timezone.now() - timedelta(days=5)
        self.open_job = make_job(self.org, "open-role", "https://boards.greenhouse.io/acme/jobs/1", last_seen_at=old)
        self.closed_job = make_job(self.org, "closed-role", "https://boards.greenhouse.io/acme/jobs/2", last_seen_at=old)
        self.office_job = make_job(self.org, "office-role", "https://boards.greenhouse.io/acme/jobs/3", last_seen_at=old)
        self.board = BoardResult("ok", {"1": Posting(None, "Remote"), "3": Posting("onsite", "Berlin"), "9": Posting()})

    def run_command(self, *args):
        out = StringIO()
        with mock.patch.object(ats_boards, "fetch_board", return_value=self.board) as fetch:
            call_command("verify_ats_jobs", *args, stdout=out)
        return out.getvalue(), fetch

    def test_closes_missing_and_office_roles_and_refreshes_open_ones(self):
        output, fetch = self.run_command()
        self.assertEqual(fetch.call_count, 1)  # one request per board, not per job
        for job in (self.open_job, self.closed_job, self.office_job):
            job.refresh_from_db()
        self.assertTrue(self.open_job.is_active)
        self.assertGreater(self.open_job.last_seen_at, timezone.now() - timedelta(minutes=1))
        self.assertEqual(self.open_job.raw_data["source_verification"]["status"], "open")
        self.assertFalse(self.closed_job.is_active)
        self.assertEqual(self.closed_job.raw_data["source_verification"]["status"], "closed")
        self.assertFalse(self.office_job.is_active)
        self.assertEqual(self.office_job.raw_data["source_verification"]["status"], "not_remote")
        self.assertIn("open=1", output)

    def test_dry_run_changes_nothing(self):
        self.run_command("--dry-run")
        self.closed_job.refresh_from_db()
        self.assertTrue(self.closed_job.is_active)
        self.assertNotIn("source_verification", self.closed_job.raw_data)

    def test_ratio_guard_blocks_suspicious_mass_closures(self):
        for i in range(40):
            make_job(self.org, f"bulk-{i}", f"https://boards.greenhouse.io/acme/jobs/{100 + i}")
        with self.assertRaises(CommandError):
            self.run_command()
        self.assertEqual(Job.objects.filter(is_active=False).count(), 0)

    def test_feed_sighting_does_not_resurrect_an_employer_closed_role(self):
        self.run_command()
        _mark_job_seen(Job.Source.CLIMATEBASE, "closed-role")
        self.closed_job.refresh_from_db()
        self.assertFalse(self.closed_job.is_active)

    def test_job_page_says_where_it_was_confirmed(self):
        site = Site.objects.get_current()
        for provider, provider_id in (("google", ""), ("openid_connect", "linkedin")):
            app = SocialApp.objects.create(
                provider=provider, provider_id=provider_id, name=(provider_id or provider).title(),
                client_id="x", secret="x",
                settings={"server_url": "https://www.linkedin.com/oauth"} if provider_id else {},
            )
            app.sites.add(site)
        self.run_command()
        response = self.client.get(reverse("jobs:job_detail", kwargs={"slug": "open-role"}))
        self.assertContains(response, "Confirmed open on Acme Climate's own job board")
        response = self.client.get(reverse("jobs:job_detail", kwargs={"slug": "closed-role"}))
        self.assertContains(response, "Acme Climate has closed this role")


class FeedRetirementTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(name="Feed Org", slug="feed-org")
        self.now = timezone.now()
        for i in range(30):
            make_job(self.org, f"seen-{i}", f"https://example.org/{i}", source=Job.Source.IDEALIST, last_seen_at=self.now)
        self.gone = make_job(self.org, "gone", "https://example.org/gone", source=Job.Source.IDEALIST,
                             last_seen_at=self.now - timedelta(days=4))
        self.ats_open = make_job(
            self.org, "ats-open", "https://example.org/ats", source=Job.Source.IDEALIST,
            last_seen_at=self.now - timedelta(days=4),
            raw_data={"source_verification": {"status": "open", "method": "ats_api",
                                               "checked_at": self.now.isoformat()}},
        )

    def test_retires_listings_the_feed_dropped(self):
        result = retire_unseen_listings(Job.Source.IDEALIST, self.now, fetched=30)
        self.assertEqual(result["retired"], 1)
        self.gone.refresh_from_db()
        self.ats_open.refresh_from_db()
        self.assertFalse(self.gone.is_active)
        self.assertTrue(self.ats_open.is_active)
        # Seen again later: back on the site.
        _mark_job_seen(Job.Source.IDEALIST, "gone")
        self.gone.refresh_from_db()
        self.assertTrue(self.gone.is_active)

    def test_partial_fetch_retires_nothing(self):
        result = retire_unseen_listings(Job.Source.IDEALIST, self.now, fetched=10)
        self.assertEqual(result["retired"], 0)
        self.assertTrue(Job.objects.get(pk=self.gone.pk).is_active)


class ImportedFreshnessTests(TestCase):
    def test_imported_listing_unseen_for_a_month_is_hidden(self):
        org = Organization.objects.create(name="Fresh Org", slug="fresh-org")
        now = timezone.now()
        stale = make_job(org, "stale", "https://example.org/a", last_seen_at=now - timedelta(days=40))
        fresh = make_job(org, "fresh", "https://example.org/b", last_seen_at=now - timedelta(days=2))
        manual = Job.objects.create(title="Posted Here", slug="posted-here", organization=org, description="x",
                                    posted_at=now - timedelta(days=40))
        available = set(JobService.get_available_jobs().values_list("slug", flat=True))
        self.assertNotIn("stale", available)
        self.assertIn("fresh", available)
        self.assertIn("posted-here", available)
        self.assertFalse(stale.is_available)
        self.assertTrue(fresh.is_available)
        self.assertTrue(manual.is_available)


class ThinLandingPageTests(TestCase):
    def setUp(self):
        site = Site.objects.get_current()
        for provider, provider_id in (("google", ""), ("openid_connect", "linkedin")):
            app = SocialApp.objects.create(
                provider=provider, provider_id=provider_id, name=(provider_id or provider).title(),
                client_id="x", secret="x",
                settings={"server_url": "https://www.linkedin.com/oauth"} if provider_id else {},
            )
            app.sites.add(site)
        from jobs.models import Category
        self.category = Category.objects.create(name="Ocean Policy", slug="ocean-policy")
        self.org = Organization.objects.create(name="Thin Org", slug="thin-org")

    def add_jobs(self, n):
        for i in range(n):
            Job.objects.create(title=f"Ocean Role {Job.objects.count()}", slug=f"ocean-role-{Job.objects.count()}",
                               organization=self.org, category=self.category, description="x")

    def robots(self, url):
        html = self.client.get(url).content.decode()
        return html.split('name="robots" content="', 1)[1].split('"', 1)[0]

    def test_category_page_is_noindexed_until_it_has_enough_live_jobs(self):
        from jobs.sitemaps import CategorySitemap
        url = reverse("jobs:category_landing", args=["ocean-policy"])
        self.add_jobs(2)
        self.assertEqual(self.robots(url), "noindex, follow")
        self.assertNotIn(self.category, list(CategorySitemap().items()))
        self.add_jobs(1)
        self.assertEqual(self.robots(url), "index, follow")
        self.assertIn(self.category, list(CategorySitemap().items()))
        self.assertEqual(self.robots(url + "?page=1"), "noindex, follow")

    def test_organization_without_live_roles_is_noindexed(self):
        url = reverse("jobs:organization_profile", args=["thin-org"])
        self.assertEqual(self.robots(url), "noindex, follow")
        self.add_jobs(1)
        self.assertEqual(self.robots(url), "index, follow")


class LongTitleSlugTests(TestCase):
    def test_slug_fits_the_column_for_very_long_titles(self):
        from jobs.utils import unique_slug
        org = Organization.objects.create(name="Long Org", slug="long-org")
        title = "Program Officer " * 20
        first = unique_slug(Job, title)
        self.assertLessEqual(len(first), 249)
        Job.objects.create(title=title.strip(), slug=first, organization=org, description="x")
        second = unique_slug(Job, title)
        self.assertNotEqual(first, second)
        self.assertLessEqual(len(second), 255)


class EmployerBoardImportTests(TestCase):
    def setUp(self):
        self.curated = Organization.objects.create(name="Curated Climate", slug="curated-climate")
        self.searched = Organization.objects.create(name="Search Only Co", slug="search-only-co")
        from jobs.models import Category
        climate = Category.objects.create(name="Climate & Environment", slug="climate-environment")
        make_job(self.curated, "seed-a", "https://boards.greenhouse.io/curated/jobs/1", category=climate)
        make_job(self.searched, "seed-b", "https://boards.greenhouse.io/searchonly/jobs/2", source=Job.Source.GREENHOUSE)

    def listing(self, job, location, workplace=None, title="Program Manager"):
        return ats_boards.Listing(job=job, title=title, url=f"https://boards.greenhouse.io/curated/jobs/{job}",
                                  description_html=("<p>Lead grant programs for our climate policy team. Requires 5+ years of "
                                                    "experience and a bachelor's degree. Skills: project management, "
                                                    "budgeting, stakeholder engagement. Salary: $90,000 - $110,000 per year.</p>"
                                                    + "<p>" + "Real responsibilities. " * 30 + "</p>"),
                                  location=location, workplace=workplace)

    def test_imports_remote_roles_from_curated_employers_only(self):
        listings = [self.listing("10", "Remote - US"), self.listing("11", "Berlin office"),
                    self.listing("12", "London", workplace="remote"), self.listing("13", "Remote", workplace="hybrid")]
        fetched = []

        def fake_fetch(ref, session=None):
            fetched.append(ref.board)
            return listings
        upsert = mock.AsyncMock(return_value={"created": 0})
        with mock.patch.object(ats_boards, "fetch_listings", side_effect=fake_fetch), \
                mock.patch("jobs.management.commands.import_employer_boards.batch_upsert_jobs", upsert):
            call_command("import_employer_boards", stdout=StringIO())
        self.assertEqual(fetched, ["curated"])
        from jobs.services.importers.common import _upsert_job
        for payload in upsert.call_args.args[0]:
            _upsert_job(payload)
        imported = Job.objects.filter(source=Job.Source.GREENHOUSE, organization=self.curated)
        self.assertEqual(set(imported.values_list("external_id", flat=True)), {"10", "12"})
        job = imported.get(external_id="10")
        self.assertEqual(job.raw_data["source_verification"]["status"], "open")
        self.assertTrue(job.is_available, (job.is_active, job.quality_score, job.quality_issues, job.last_seen_at))

    def test_rejected_organizations_are_hidden_everywhere(self):
        self.curated.verification_status = Organization.VerificationStatus.REJECTED
        self.curated.save()
        self.assertFalse(JobService.get_available_jobs().filter(organization=self.curated).exists())
        self.assertFalse(Job.objects.get(slug="seed-a").is_available)


class TrustStatsTests(TestCase):
    def test_counts_confirmed_and_recently_removed_listings(self):
        from django.core.cache import cache
        from jobs.services.trust_stats import trust_stats
        cache.clear()
        org = Organization.objects.create(name="Stats Org", slug="stats-org")
        now = timezone.now()
        make_job(org, "ok", "https://x.org/1", last_seen_at=now, salary_min=50000,
                 raw_data={"source_verification": {"method": "ats_api", "status": "open", "checked_at": now.isoformat()}})
        make_job(org, "plain", "https://x.org/2", last_seen_at=now)
        make_job(org, "closed", "https://x.org/3", is_active=False,
                 raw_data={"source_verification": {"status": "closed", "checked_at": now.isoformat()}})
        make_job(org, "office", "https://x.org/4", is_active=False,
                 raw_data={"source_verification": {"status": "not_remote", "checked_at": now.isoformat()}})
        make_job(org, "old", "https://x.org/5", is_active=False,
                 raw_data={"source_verification": {"status": "closed", "checked_at": (now - timedelta(days=20)).isoformat()}})
        make_job(org, "dropped", "https://x.org/6", is_active=False, raw_data={"removed_from_source_at": now.isoformat()})
        stats = trust_stats(refresh=True)
        self.assertEqual((stats["live"], stats["confirmed_open"], stats["confirmed_share"]), (2, 1, 50))
        self.assertEqual((stats["removed_7d"], stats["removed_not_remote_7d"]), (3, 1))
        self.assertEqual(stats["salary_share"], 50)

    def test_how_we_check_page_renders(self):
        site = Site.objects.get_current()
        for provider, provider_id in (("google", ""), ("openid_connect", "linkedin")):
            app = SocialApp.objects.create(provider=provider, provider_id=provider_id, name=provider_id or provider,
                                           client_id="x", secret="x",
                                           settings={"server_url": "https://www.linkedin.com/oauth"} if provider_id else {})
            app.sites.add(site)
        response = self.client.get(reverse("jobs:how_we_check"))
        self.assertContains(response, "How we check job listings")


class CandidateSuggestionTests(TestCase):
    def setUp(self):
        from django.contrib.auth import get_user_model
        from django.core.cache import cache
        cache.clear()
        from jobs.models import Category, SeekerProfile
        site = Site.objects.get_current()
        for provider, provider_id in (("google", ""), ("openid_connect", "linkedin")):
            app = SocialApp.objects.create(provider=provider, provider_id=provider_id, name=provider_id or provider,
                                           client_id="x", secret="x",
                                           settings={"server_url": "https://www.linkedin.com/oauth"} if provider_id else {})
            app.sites.add(site)
        User = get_user_model()
        self.employer = User.objects.create_user("boss", "boss@example.org", "pw-test-123")
        self.area = Category.objects.create(name="Global Health", slug="global-health")
        org = Organization.objects.create(name="Health Org", slug="health-org")
        org.members.add(self.employer)
        vec = [1.0] + [0.0] * 383
        self.job = Job.objects.create(
            title="Data Analyst", slug="data-analyst", organization=org, category=self.area, description="x",
            poster=self.employer, is_paid=True, skills=["sql", "python", "data-visualization"],
            experience_level="mid",
        )
        # Saving recomputes embeddings from text; pin known vectors for the test.
        Job.objects.filter(pk=self.job.pk).update(embedding=vec, embedding_content_hash="pinned")
        self.job.refresh_from_db()

        def seeker(name, visibility, embedding, skills, areas=(), remote="remote"):
            user = User.objects.create_user(name, f"{name}@example.org", "pw-test-123", first_name=name.title())
            profile = SeekerProfile.objects.create(
                user=user, visibility=visibility, is_actively_looking=True, wizard_completed=True,
                skills=skills, remote_preference=remote, experience_level="mid",
            )
            SeekerProfile.objects.filter(pk=profile.pk).update(embedding=embedding)
            profile.impact_areas.set(areas)
            return profile
        close = [0.9, 0.43] + [0.0] * 382
        far = [0.0, 1.0] + [0.0] * 382
        self.good = seeker("ana", SeekerProfile.Visibility.PUBLIC, close, ["sql", "python"], [self.area])
        self.private_match = seeker("bo", SeekerProfile.Visibility.MATCHING, close, ["sql", "data-visualization"])
        self.one_skill = seeker("gu", SeekerProfile.Visibility.PUBLIC, close, ["sql"])
        self.hidden = seeker("cy", SeekerProfile.Visibility.HIDDEN, close, ["sql", "python"])
        self.unrelated = seeker("di", SeekerProfile.Visibility.PUBLIC, far, ["sql"])
        self.no_overlap = seeker("ed", SeekerProfile.Visibility.PUBLIC, close, ["sales"])
        self.office = seeker("fa", SeekerProfile.Visibility.PUBLIC, close, ["sql"], remote="onsite")

    def test_suggests_visible_overlapping_people_with_reasons(self):
        from jobs.services.candidate_suggestions import suggested_candidates
        suggestions = suggested_candidates(Job.objects.get(pk=self.job.pk))
        self.assertEqual([s.profile for s in suggestions], [self.good, self.private_match])
        best = suggestions[0]
        self.assertEqual(best.shared_skills, ["python", "sql"])
        self.assertEqual(best.shared_area, "Global Health")
        self.assertEqual(suggestions[1].display_name, "Candidate (shares details when invited)")

    def test_employer_can_invite_a_matching_only_profile_to_a_paid_role(self):
        from jobs.models import TalentInvitation
        self.client.force_login(self.employer)
        page = self.client.get(reverse("jobs:employer_shortlist", args=[self.job.slug]))
        self.assertContains(page, "Suggested from our talent pool")
        self.assertContains(page, "SQL")
        url = reverse("jobs:talent_invite", args=[self.private_match.user_id])
        self.assertEqual(self.client.get(url + f"?job={self.job.pk}").status_code, 200)
        with mock.patch("jobs.signals.send_mail", create=True):
            response = self.client.post(url, {"job_id": self.job.pk, "message": "Your SQL work fits this role."})
        self.assertRedirects(response, reverse("jobs:employer_shortlist", args=[self.job.slug]),
                             fetch_redirect_response=False)
        self.assertTrue(TalentInvitation.objects.filter(job=self.job, seeker=self.private_match).exists())
        # Hidden profiles can never be invited.
        self.assertEqual(self.client.get(reverse("jobs:talent_invite", args=[self.hidden.user_id])).status_code, 404)

    def test_skill_label(self):
        from jobs.templatetags.job_extras import skill_label
        self.assertEqual(skill_label("system-design"), "System design")
        self.assertEqual(skill_label("sql"), "SQL")
        self.assertEqual(skill_label("crm-admin"), "CRM admin")


class AdminAnalyticsTests(TestCase):
    def test_staff_can_open_analytics_and_others_cannot(self):
        from django.contrib.auth import get_user_model
        User = get_user_model()
        org = Organization.objects.create(name="Metric Org", slug="metric-org")
        make_job(org, "metric-job", "https://x.org/m", last_seen_at=timezone.now())
        url = reverse("admin_analytics")
        self.assertEqual(self.client.get(url).status_code, 302)  # to admin login
        member = User.objects.create_user("member", "m@example.org", "pw-test-123")
        self.client.force_login(member)
        self.assertEqual(self.client.get(url).status_code, 302)
        staff = User.objects.create_user("staff", "s@example.org", "pw-test-123", is_staff=True)
        self.client.force_login(staff)
        for days in ("7", "30", "90", "bogus"):
            response = self.client.get(url, {"days": days, "refresh": "1"})
            self.assertEqual(response.status_code, 200)
            self.assertContains(response, "Seeker funnel")


class ReferralTaggingTests(TestCase):
    def test_replaces_aggregator_attribution_with_ours(self):
        from urllib.parse import parse_qs, urlsplit
        from jobs.services.job_stats import with_referral
        url = with_referral("https://tesvolt.jobs.personio.de/job/2832457?language=en&gh_src=Climatebase&utm_source=climatebase#apply")
        parts = urlsplit(url)
        query = parse_qs(parts.query)
        self.assertEqual(query["utm_source"], ["remoteimpact"])
        self.assertEqual(query["language"], ["en"])
        self.assertNotIn("gh_src", query)
        self.assertEqual(parts.fragment, "apply")
        lever = parse_qs(urlsplit(with_referral("https://jobs.lever.co/acme/123")).query)
        self.assertEqual(lever["lever-source"], ["RemoteImpact"])
        self.assertEqual(with_referral("mailto:jobs@example.org"), "mailto:jobs@example.org")
        # Employer-specific tokens that aren't another board's name stay.
        self.assertIn("gh_src=a1b2c3", with_referral("https://boards.greenhouse.io/acme/jobs/1?gh_src=a1b2c3"))

    def test_apply_redirect_is_tagged(self):
        org = Organization.objects.create(name="Tag Org", slug="tag-org")
        make_job(org, "tag-job", "https://boards.greenhouse.io/acme/jobs/9?utm_source=climatebase", last_seen_at=timezone.now())
        response = self.client.get(reverse("jobs:job_apply_redirect", args=["tag-job"]), HTTP_USER_AGENT="Mozilla/5.0")
        self.assertEqual(response.status_code, 302)
        self.assertIn("utm_source=remoteimpact", response["Location"])
        self.assertNotIn("climatebase", response["Location"])


class MoreAtsListingTests(TestCase):
    def fake(self, payload, status=200, content=None):
        resp = mock.Mock(status_code=status)
        resp.json.return_value = payload
        resp.content = content or b""
        resp.raise_for_status = mock.Mock()
        return resp

    def test_recruitee_listing(self):
        offers = {"offers": [{"slug": "ops-lead", "title": "Ops Lead", "careers_url": "https://x.recruitee.com/o/ops-lead",
                              "description": "<p>Run ops.</p>", "requirements": "<p>5 years.</p>", "remote": True,
                              "hybrid": False, "on_site": False, "location": "Berlin", "status": "published",
                              "published_at": "2026-10-02 19:27:54 UTC"}]}
        session = mock.Mock(get=mock.Mock(return_value=self.fake(offers)))
        [listing] = ats_boards.fetch_listings(AtsRef("recruitee", "x", "", host="x.recruitee.com"), session)
        self.assertEqual((listing.job, listing.workplace, listing.location), ("ops-lead", "remote", "Remote"))
        self.assertIn("5 years", listing.description_html)
        self.assertTrue(ats_boards.is_remote_listing(listing))

    def test_personio_listing(self):
        xml = (b"<workzag-jobs><position><id>7</id><office>Remote from Germany</office><name>Analyst</name>"
               b"<jobDescriptions><jobDescription><name>Role</name><value>Do analysis.</value></jobDescription></jobDescriptions>"
               b"<createdAt>2026-10-01T10:00:00+00:00</createdAt></position></workzag-jobs>")
        session = mock.Mock(get=mock.Mock(return_value=self.fake({}, content=xml)))
        [listing] = ats_boards.fetch_listings(AtsRef("personio", "acme", "", host="acme.jobs.personio.de"), session)
        self.assertEqual(listing.url, "https://acme.jobs.personio.de/job/7")
        self.assertTrue(ats_boards.is_remote_listing(listing))

    def test_workable_without_account_is_skipped(self):
        self.assertIsNone(ats_boards.fetch_listings(AtsRef("workable", "", "ABC"), mock.Mock()))


class PostedDateTests(TestCase):
    def test_naive_and_date_only_posting_dates_become_aware(self):
        from jobs.management.commands.import_employer_boards import Command
        entry = {"ref": AtsRef("bamboohr", "x", "1", host="x.bamboohr.com"), "org_name": "X", "category_name": None}
        for raw in ("2024-11-06 00:00:00", "2024-11-06"):
            listing = ats_boards.Listing(job="1", title="Role", url="https://x.bamboohr.com/careers/1",
                                         description_html="", location="Remote", workplace="remote", posted_at=raw)
            posted = Command._payload(entry, listing, "desc", timezone.now().isoformat())["posted_at"]
            self.assertFalse(timezone.is_naive(posted))
            self.assertEqual(posted.date().isoformat(), "2024-11-06")


class UnsubscribeTests(TestCase):
    def setUp(self):
        from django.contrib.auth import get_user_model
        from jobs.services.email_service import email_service
        self.user = get_user_model().objects.create_user("reader", "Reader@Example.org", "pw-test-123")
        self.url = email_service._get_unsubscribe_url(self.user.id).split("remoteimpact.org", 1)[-1]
        site = Site.objects.get_current()
        for provider, provider_id in (("google", ""), ("openid_connect", "linkedin")):
            app = SocialApp.objects.create(provider=provider, provider_id=provider_id, name=provider_id or provider,
                                           client_id="x", secret="x",
                                           settings={"server_url": "https://www.linkedin.com/oauth"} if provider_id else {})
            app.sites.add(site)

    def test_user_without_profile_can_unsubscribe_and_drips_stop(self):
        from jobs.models import NewsletterSubscriber, UserProfile
        NewsletterSubscriber.objects.create(email="reader@example.org", confirmed=True)
        self.assertFalse(UserProfile.objects.filter(user=self.user).exists())
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "Unable to process")
        self.assertIsNotNone(UserProfile.objects.get(user=self.user).email_newsletter_unsubscribed_at)
        self.assertTrue(NewsletterSubscriber.objects.get(email="reader@example.org").unsubscribed)

    def test_one_click_post_and_old_links_work(self):
        from unittest import mock as m
        from django.core.signing import TimestampSigner
        from jobs.models import UserProfile
        with m.patch("django.core.signing.time.time", return_value=__import__("time").time() - 400 * 86400):
            old_token = TimestampSigner(salt="newsletter-unsubscribe").sign(str(self.user.id))
        response = self.client.post(reverse("jobs:newsletter_unsubscribe", args=[old_token]))
        self.assertEqual(response.status_code, 200)
        self.assertIsNotNone(UserProfile.objects.get(user=self.user).email_newsletter_unsubscribed_at)

    @override_settings(EMAIL_PROVIDER="resend")
    def test_marketing_emails_carry_list_unsubscribe_headers(self):
        from jobs.services.email_service import EmailService
        service = EmailService()
        with mock.patch("jobs.services.email_service.resend") as resend:
            resend.api_key = "test"
            resend.Emails.send.return_value = {"id": "x"}
            service.send_email(to="a@example.org", subject="s", html="<p>h</p>", unsubscribe_url="https://x/u/1/")
            params = resend.Emails.send.call_args.args[0]
        self.assertEqual(params["headers"]["List-Unsubscribe"], "<https://x/u/1/>")
        self.assertEqual(params["headers"]["List-Unsubscribe-Post"], "List-Unsubscribe=One-Click")


class OutreachQueueTests(TestCase):
    def setUp(self):
        from django.contrib.auth import get_user_model
        from jobs.models import JobDailyStat
        self.staff = get_user_model().objects.create_user("ops", "ops@example.org", "pw-test-123", is_staff=True,
                                                          first_name="Dipesh")
        self.busy = Organization.objects.create(name="Busy Climate", slug="busy-climate")
        self.quiet = Organization.objects.create(name="Quiet Health", slug="quiet-health")
        now = timezone.now()
        job = make_job(self.busy, "busy-role", "https://boards.greenhouse.io/busy/jobs/1", last_seen_at=now,
                       application_email="jobs@busy.example")
        make_job(self.quiet, "quiet-role", "https://boards.greenhouse.io/quiet/jobs/2", last_seen_at=now)
        JobDailyStat.objects.create(job=job, date=timezone.localdate(), views=40, apply_clicks=9)

    def test_ranks_by_engagement_and_drafts_an_email_with_real_numbers(self):
        from jobs.services.outreach import outreach_targets
        rows = outreach_targets(days=7, sender="Dipesh")
        self.assertEqual(rows[0]["org"], self.busy)
        self.assertIn("9 people clicked to apply", rows[0]["subject"])
        self.assertIn("viewed 40 times", rows[0]["body"])
        self.assertIn("/employers/report/", rows[0]["body"])
        self.assertIn("/feature/?source=outreach", rows[0]["body"])
        self.assertTrue(rows[0]["mailto"].startswith("mailto:jobs%40busy.example?"))

    def test_staff_page_and_status_tracking(self):
        from jobs.models import EmployerOutreach
        url = reverse("admin_outreach")
        self.assertEqual(self.client.get(url).status_code, 302)
        self.client.force_login(self.staff)
        page = self.client.get(url)
        self.assertContains(page, "Busy Climate")
        self.client.post(reverse("admin_outreach_update", args=[self.busy.pk]), {"status": "contacted", "contact": "ana@busy.example"})
        record = EmployerOutreach.objects.get(organization=self.busy)
        self.assertEqual((record.status, record.contact), ("contacted", "ana@busy.example"))
        self.assertIsNotNone(record.contacted_at)
        self.assertContains(self.client.get(url, {"status": "contacted"}), "Busy Climate")
        self.assertNotContains(self.client.get(url, {"status": "contacted"}), "Quiet Health")


class SeekerConversionEmailTests(TestCase):
    def setUp(self):
        from django.contrib.auth import get_user_model
        from jobs.models import ConsumerFunnelEvent, JobAlert
        User = get_user_model()
        self.left = User.objects.create_user("left", "left@example.org", "pw-test-123", first_name="Lee")
        self.unsub = User.objects.create_user("unsub", "unsub@example.org", "pw-test-123")
        old = timezone.now() - timedelta(hours=5)
        for user in (self.left, self.unsub):
            event = ConsumerFunnelEvent.objects.create(user=user, event="checkout_created", metadata={"plan": "monthly"})
            ConsumerFunnelEvent.objects.filter(pk=event.pk).update(created_at=old)
            alert = JobAlert.objects.create(user=user, name="Climate policy roles", frequency="once")
            JobAlert.objects.filter(pk=alert.pk).update(created_at=timezone.now() - timedelta(days=3))
        from jobs.models import UserProfile
        UserProfile.objects.create(user=self.unsub, email_newsletter_unsubscribed_at=timezone.now())

    def run_cmd(self, name, *args):
        out = StringIO()
        with mock.patch("jobs.services.email_service.EmailService.send_email", return_value=True) as send:
            call_command(name, *args, stdout=out)
        return out.getvalue(), send

    def test_checkout_recovery_is_dry_run_by_default_and_sends_once(self):
        from jobs.models import ConsumerFunnelEvent
        out, send = self.run_cmd("send_checkout_recovery")
        self.assertIn("would send 1", out)
        send.assert_not_called()
        out, send = self.run_cmd("send_checkout_recovery", "--send")
        self.assertEqual(send.call_count, 1)
        self.assertEqual(send.call_args.kwargs["to"], "left@example.org")
        self.assertIn("one click away", send.call_args.kwargs["subject"])
        self.assertTrue(ConsumerFunnelEvent.objects.filter(user=self.left, event="checkout_recovery_sent").exists())
        _, send = self.run_cmd("send_checkout_recovery", "--send")
        send.assert_not_called()

    def test_winback_skips_unsubscribed_and_sends_once(self):
        out, send = self.run_cmd("send_scout_winback")
        self.assertIn("would send 1", out)
        send.assert_not_called()
        _, send = self.run_cmd("send_scout_winback", "--send")
        self.assertEqual(send.call_count, 1)
        self.assertIn("Climate policy roles", send.call_args.kwargs["html"])
        _, send = self.run_cmd("send_scout_winback", "--send")
        send.assert_not_called()


class LocationBlockedScoutTests(TestCase):
    def test_empty_scout_blocked_by_location_gets_honest_advice_not_a_pro_pitch(self):
        from django.contrib.auth import get_user_model
        from jobs.models import ConsumerFunnelEvent, JobAlert
        site = Site.objects.get_current()
        for provider, provider_id in (("google", ""), ("openid_connect", "linkedin")):
            app = SocialApp.objects.create(provider=provider, provider_id=provider_id, name=provider_id or provider,
                                           client_id="x", secret="x",
                                           settings={"server_url": "https://www.linkedin.com/oauth"} if provider_id else {})
            app.sites.add(site)
        user = get_user_model().objects.create_user("pk", "pk@example.org", "pw-test-123")
        alert = JobAlert.objects.create(user=user, intent_text="Social media roles, I'm in Pakistan", frequency="once")
        ConsumerFunnelEvent.objects.create(user=user, alert=alert, event="initial_matches_empty",
                                           metadata={"matching": {"hard_rejections": {"location": 118}}})
        self.client.force_login(user)
        with mock.patch("jobs.services.job_scout_service.JobScoutService.get_matches", return_value=[]):
            page = self.client.get(reverse("jobs:job_alerts"), {"created": alert.pk})
        self.assertContains(page, "limited to other countries")
        self.assertContains(page, "/gigs/ai-evaluation/")
        self.assertNotContains(page, "Upgrade to Pro to keep watching")


from django.test import override_settings


@override_settings(EMAIL_PROVIDER="cloudflare", CLOUDFLARE_ACCOUNT_ID="acc123", CLOUDFLARE_EMAIL_TOKEN="tok",
                   DEFAULT_FROM_EMAIL="Remote Impact Jobs <hello@remoteimpact.org>")
class CloudflareEmailTests(TestCase):
    def response(self, status=200, payload=None):
        import json as _json
        resp = mock.Mock(status_code=status, ok=status < 400)
        resp.content = b"{}"
        resp.json.return_value = payload if payload is not None else {
            "success": True, "errors": [], "result": {"delivered": ["a@example.org"], "permanent_bounces": [], "queued": []}}
        return resp

    def service(self):
        from jobs.services.email_service import EmailService
        return EmailService()

    def test_sends_through_cloudflare_with_unsubscribe_headers(self):
        with mock.patch("requests.post", return_value=self.response()) as post, \
                mock.patch("jobs.services.email_service.resend") as resend:
            ok = self.service().send_email(to="a@example.org", subject="s", html="<p>h</p>", text="h",
                                            unsubscribe_url="https://x/u/1/")
        self.assertTrue(ok)
        url = post.call_args.args[0]
        body = post.call_args.kwargs["json"]
        self.assertEqual(url, "https://api.cloudflare.com/client/v4/accounts/acc123/email/sending/send")
        self.assertEqual(post.call_args.kwargs["headers"]["Authorization"], "Bearer tok")
        self.assertEqual(body["to"], ["a@example.org"])
        self.assertEqual(body["from"], "Remote Impact Jobs <hello@remoteimpact.org>")
        self.assertEqual(body["headers"]["List-Unsubscribe-Post"], "List-Unsubscribe=One-Click")
        resend.Emails.send.assert_not_called()

    def test_retries_with_bare_sender_address_when_display_name_is_rejected(self):
        rejected = self.response(400, {"success": False, "errors": [{"message": "Invalid from address"}]})
        with mock.patch("requests.post", side_effect=[rejected, self.response()]) as post:
            self.assertTrue(self.service().send_email(to="a@example.org", subject="s", html="h"))
        self.assertEqual(post.call_args_list[1].kwargs["json"]["from"], "hello@remoteimpact.org")

    def test_falls_back_to_resend_when_cloudflare_fails(self):
        with mock.patch("requests.post", return_value=self.response(403, {"success": False, "errors": [{"message": "not onboarded"}]})), \
                mock.patch("jobs.services.email_service.resend") as resend:
            service = self.service()  # __init__ sets resend.api_key from settings
            resend.api_key = "re_test"
            resend.Emails.send.return_value = {"id": "1"}
            self.assertTrue(service.send_email(to="a@example.org", subject="s", html="h"))
        resend.Emails.send.assert_called_once()

    def test_hard_bounce_is_not_resent_through_resend(self):
        bounced = self.response(payload={"success": True, "errors": [],
                                         "result": {"delivered": [], "permanent_bounces": ["a@example.org"], "queued": []}})
        with mock.patch("requests.post", return_value=bounced), \
                mock.patch("jobs.services.email_service.resend") as resend:
            self.assertFalse(self.service().send_email(to="a@example.org", subject="s", html="h"))
        resend.Emails.send.assert_not_called()


class OrganizationClaimTests(TestCase):
    def setUp(self):
        from jobs.models import Organization
        self.org = Organization.objects.create(name="Claimable Org", slug="claimable-org", website="https://www.claimable.org")
        from allauth.socialaccount.models import SocialApp
        from django.contrib.sites.models import Site
        site = Site.objects.get_current()
        for provider, provider_id in (("google", ""), ("openid_connect", "linkedin")):
            app = SocialApp.objects.create(
                provider=provider, provider_id=provider_id, name=(provider_id or provider).title(),
                client_id="x", secret="y",
                settings={"server_url": "https://www.linkedin.com/oauth"} if provider_id else {},
            )
            app.sites.add(site)

    def test_claim_sends_verification_and_verify_marks_claim(self):
        from unittest import mock
        from django.core import signing
        from jobs.models import OrganizationClaim
        from jobs.views.employers import CLAIM_SALT
        url = reverse("jobs:claim_organization", kwargs={"slug": self.org.slug})
        self.assertEqual(self.client.get(url).status_code, 200)
        with mock.patch("jobs.services.email_service.email_service.send_claim_verification", return_value=True) as send:
            response = self.client.post(url, {"email": "Ana@Claimable.org", "name": "Ana"})
        self.assertRedirects(response, f"{url}?sent=1", fetch_redirect_response=False)
        claim = OrganizationClaim.objects.get()
        self.assertEqual(claim.email, "ana@claimable.org")
        self.assertTrue(claim.domain_match)
        send.assert_called_once()
        token = signing.dumps(claim.pk, salt=CLAIM_SALT)
        response = self.client.get(reverse("jobs:claim_verify", kwargs={"token": token}))
        self.assertEqual(response.status_code, 302)
        self.assertIn("/employers/report/", response["Location"])
        claim.refresh_from_db()
        self.assertIsNotNone(claim.verified_at)

    def test_non_matching_domain_and_bad_email(self):
        from unittest import mock
        from jobs.models import OrganizationClaim
        url = reverse("jobs:claim_organization", kwargs={"slug": self.org.slug})
        with mock.patch("jobs.services.email_service.email_service.send_claim_verification", return_value=True):
            self.client.post(url, {"email": "someone@gmail.com"})
            self.client.post(url, {"email": "not-an-email"})
        self.assertEqual(OrganizationClaim.objects.count(), 1)
        self.assertFalse(OrganizationClaim.objects.get().domain_match)

    def test_bad_verify_token(self):
        response = self.client.get(reverse("jobs:claim_verify", kwargs={"token": "nope"}))
        self.assertEqual(response.status_code, 302)


class ScoutCloseMatchTests(TestCase):
    def test_validate_keeps_grounded_picks_only(self):
        import json
        from jobs.services.scout_close_matches import validate
        evidence = {1: {"title": "Energy Policy Analyst", "description": "Analyse energy policy across the EU.", "requirements": ""},
                    2: {"title": "Office Manager", "description": "Run the office.", "requirements": ""}}
        output = json.dumps({"picks": [
            {"id": 1, "same_kind_of_work": True, "difference": "Energy rather than climate policy", "evidence": {"field": "description", "quote": "Analyse energy policy"}},
            {"id": 2, "same_kind_of_work": True, "difference": "Operations, not policy work", "evidence": {"field": "description", "quote": "made up quote here"}},
            {"id": 1, "same_kind_of_work": False, "difference": "Duplicate pick of the same job",
             "evidence": {"field": "title", "quote": "Energy Policy Analyst"}},
            {"id": 99, "same_kind_of_work": True, "difference": "Not a candidate at all", "evidence": {"field": "title", "quote": "Energy Policy Analyst"}},
        ]})
        self.assertEqual(validate(output, evidence, 5), [(1, "Energy rather than climate policy")])

    def test_disabled_by_default(self):
        from jobs.models import Job
        from jobs.services.scout_close_matches import close_matches
        self.assertEqual(close_matches(Job.objects.all(), {"intent_text": "climate policy"}, ["climate"], {}), [])

    def test_loose_preferences_keep_hard_limits(self):
        from jobs.services.scout_close_matches import loose_preferences
        loose = loose_preferences({"titles": ["Policy Researcher"], "required_domains": ["climate"],
                                   "locations": ["Spain"], "exclude_keywords": ["sales"], "job_types": ["full_time"]})
        self.assertEqual(loose["titles"], [])
        self.assertNotIn("required_domains", loose)
        self.assertEqual((loose["locations"], loose["exclude_keywords"], loose["job_types"]), (["Spain"], ["sales"], ["full_time"]))


class CountryActivityTests(TestCase):
    def test_records_country_and_hides_small_countries(self):
        from django.core.cache import cache
        from django.test import RequestFactory
        from jobs.services import country_activity
        cache.clear()
        factory = RequestFactory()
        for code, n in (("NP", 4), ("KE", 3), ("FR", 2), ("XX", 9)):
            for _ in range(n):
                request = factory.get("/", HTTP_CF_IPCOUNTRY=code)
                country_activity.record(request, "views")
                country_activity.record(request, "apply_clicks")
        summary = country_activity.weekly_summary()
        self.assertEqual(summary["countries"], 2)
        self.assertEqual([r["name"] for r in summary["top"]], ["Nepal", "Kenya"])
        self.assertEqual(summary["clicks"], 7)


class ScoutRepostTests(TestCase):
    def test_identical_reposts_collapse(self):
        from types import SimpleNamespace
        from jobs.services.job_scout_service import JobScoutService
        a = SimpleNamespace(organization_id=1, title="Senior Data Analyst", description="Analyse data.")
        b = SimpleNamespace(organization_id=1, title="Senior  data analyst", description="Analyse   data.")
        c = SimpleNamespace(organization_id=1, title="Senior Data Analyst", description="Different team.")
        self.assertEqual(JobScoutService._repost_key(a), JobScoutService._repost_key(b))
        self.assertNotEqual(JobScoutService._repost_key(a), JobScoutService._repost_key(c))


class ScoutTitleFamilyTests(TestCase):
    def check(self, title, wanted):
        from jobs.services.job_scout_service import JobScoutService
        return JobScoutService._title_matches_intent(title, wanted)

    def test_compound_titles_must_stay_together(self):
        self.assertTrue(self.check("Executive Assistant to the CEO", ["Executive Assistant"]))
        self.assertFalse(self.check("Assistant General Counsel", ["Executive Assistant"]))
        self.assertTrue(self.check("Data Entry Clerk", ["Data Entry"]))
        self.assertFalse(self.check("Senior Data Analyst", ["Data Entry"]))

    def test_specialist_operations_need_the_same_specialism(self):
        self.assertTrue(self.check("Director of Operations", ["Operations Director"]))
        self.assertFalse(self.check("Senior People Operations Associate", ["Operations Director"]))
        self.assertFalse(self.check("Legal Operations Associate", ["Operations Manager"]))
        self.assertTrue(self.check("People Operations Manager", ["People Operations Manager"]))


class ScoutJobTypeTests(TestCase):
    def test_permanent_counts_as_full_time(self):
        from jobs.services.job_scout_service import JobScoutService
        types = JobScoutService._explicit_job_types("Open to permanent, freelance, and fractional roles.")
        # "fractional" is open to part-time and contract roles too (job types are an allow-list).
        self.assertEqual(set(types), {"full-time", "freelance", "part-time", "contract"})
        self.assertEqual(JobScoutService._explicit_job_types("Permanent roles only"), ["full-time"])
        self.assertEqual(JobScoutService._explicit_job_types("A permanent impact on climate"), [])


class OrganizationLogoTests(TestCase):
    def png(self, size=(200, 200)):
        import io
        from PIL import Image
        out = io.BytesIO()
        Image.new("RGBA", size, (20, 120, 60, 255)).save(out, format="PNG")
        return out.getvalue()

    def test_icon_candidates_prefer_apple_touch_and_skip_svg(self):
        from jobs.services.org_logos import icon_candidates
        html = ('<link rel="icon" href="/favicon-32.png" sizes="32x32"><link rel="icon" href="/logo.svg">'
                '<link rel="apple-touch-icon" href="/apple.png">')
        urls = icon_candidates(html, "https://org.example/about/")
        self.assertEqual(urls[0], "https://org.example/apple.png")
        self.assertNotIn("https://org.example/logo.svg", urls)
        self.assertIn("https://org.example/favicon.ico", urls)

    def test_square_icons_are_reencoded_and_banners_rejected(self):
        from jobs.services.org_logos import to_square_png
        out = to_square_png(self.png())
        self.assertTrue(out.startswith(b"\x89PNG"))
        self.assertIsNone(to_square_png(self.png((600, 200))))
        self.assertIsNone(to_square_png(self.png((16, 16))))
        self.assertIsNone(to_square_png(b"<svg onload=alert(1)></svg>"))

    def test_logo_is_served_and_used_in_schema(self):
        import json
        from jobs.models import Job, Organization, OrganizationLogo
        from jobs.services.job_quality import job_posting_schema_json
        org = Organization.objects.create(name="Logo Org", slug="logo-org", logo_version="abc123")
        OrganizationLogo.objects.create(organization=org, png=self.png())
        response = self.client.get(reverse("jobs:organization_logo", args=["logo-org"]))
        self.assertEqual(response["Content-Type"], "image/png")
        self.assertIn("immutable", response["Cache-Control"])
        self.assertEqual(org.logo_src, "/org/logo-org/logo.png?v=abc123")
        job = Job.objects.create(title="Analyst", slug="analyst-logo", organization=org, location="Remote",
                                 description="Analyse.", application_url="https://example.org/a")
        data = json.loads(job_posting_schema_json(job, "https://remoteimpact.org"))
        self.assertEqual(data["hiringOrganization"]["logo"], "https://remoteimpact.org/org/logo-org/logo.png?v=abc123")
        self.assertEqual(self.client.get(reverse("jobs:organization_logo", args=["missing"])).status_code, 404)


class EmployerSiteTests(TestCase):
    def test_platform_profiles_are_not_employer_sites(self):
        from jobs.management.commands.fetch_org_logos import employer_site
        self.assertFalse(employer_site("https://www.idealist.org/en/nonprofit/abc"))
        self.assertFalse(employer_site("https://jobs.lever.co/acme"))
        self.assertTrue(employer_site("https://www.actalliance.org/"))
        self.assertTrue(employer_site("80000hours.org"))


class BoardLogoTests(TestCase):
    def test_board_root_and_logo_pick_skip_platform_logo(self):
        from jobs.services.org_logos import board_logo_url, board_root
        self.assertEqual(board_root("https://job-boards.greenhouse.io/givedirectly/jobs/47"), "https://job-boards.greenhouse.io/givedirectly")
        self.assertEqual(board_root("https://cet.bamboohr.com/careers/12"), "https://cet.bamboohr.com/")
        self.assertEqual(board_root("https://example.org/jobs/1"), "")
        html = ('<img alt="Lever logo" src="/img/lever-logo-refresh.svg">'
                '<img src="https://cdn.greenhouse.io/logos/400/original/thumb.jpg" alt="GiveDirectly Logo" class="logo"/>')
        self.assertEqual(board_logo_url(html, "https://job-boards.greenhouse.io/givedirectly"),
                         "https://cdn.greenhouse.io/logos/400/original/thumb.jpg")

    def test_slightly_wide_board_logos_are_padded(self):
        import io
        from PIL import Image
        from jobs.services.org_logos import to_square_png
        out = io.BytesIO(); Image.new("RGB", (300, 160), "white").save(out, format="PNG")
        self.assertIsNone(to_square_png(out.getvalue()))
        self.assertIsNotNone(to_square_png(out.getvalue(), max_ratio=2.0))


class OrgSitesTests(TestCase):
    def test_domain_must_carry_the_organization_name(self):
        from jobs.services.org_sites import domain_matches
        self.assertTrue(domain_matches("givedirectly.org", "GiveDirectly"))
        self.assertTrue(domain_matches("clintonhealthaccess.org", "Clinton Health Access Initiative (CHAI)"))
        self.assertTrue(domain_matches("ifrc.org", "International Federation of Red Cross (IFRC)"))
        self.assertTrue(domain_matches("rand.org", "RAND Corporation"))
        self.assertFalse(domain_matches("airtable.com", "Ocean Conservancy"))
        self.assertFalse(domain_matches("example.co.uk", "Climate Policy Lab"))

    def test_careers_page_from_ats_links_and_homepage_candidates(self):
        from types import SimpleNamespace
        from jobs.services.org_sites import careers_url_for, homepage_candidates, resolve_homepage
        urls = ["https://job-boards.greenhouse.io/givedirectly/jobs/4739348005",
                "https://job-boards.greenhouse.io/givedirectly/jobs/4738732005",
                "https://jobs.lever.co/other/abc"]
        self.assertEqual(careers_url_for(urls), "https://job-boards.greenhouse.io/givedirectly")
        org = SimpleNamespace(name="GiveDirectly", website="https://www.idealist.org/en/nonprofit/x")
        apply = ["https://careers.givedirectly.org/jobs/1"]
        self.assertEqual(homepage_candidates(org, apply), [("https://givedirectly.org/", "apply_domain")])
        self.assertEqual(homepage_candidates(org, ["https://airtable.com/form"]), [])
        claimed = homepage_candidates(org, [], claim_domains=["givedirectly.org", "gmail.com"])
        self.assertEqual(claimed, [("https://givedirectly.org/", "claim")])
        self.assertEqual(resolve_homepage(org, apply, fetch=lambda url: False), ("", ""))
        self.assertEqual(resolve_homepage(org, apply, fetch=lambda url: True), ("https://givedirectly.org/", "apply_domain"))

    def test_org_page_links_verified_sites_only(self):
        from jobs.models import Organization
        org = Organization.objects.create(name="Sites Org", slug="sites-org", website="https://www.idealist.org/x",
                                          homepage_url="https://sitesorg.org/", careers_url="https://jobs.lever.co/sitesorg")
        from allauth.socialaccount.models import SocialApp
        from django.contrib.sites.models import Site
        for provider, pid in (("google", ""), ("openid_connect", "linkedin")):
            SocialApp.objects.create(provider=provider, provider_id=pid, name=pid or provider, client_id="x", secret="y",
                                     settings={"server_url": "https://www.linkedin.com/oauth"} if pid else {}).sites.add(Site.objects.get_current())
        html = self.client.get(reverse("jobs:organization_profile", args=["sites-org"])).content.decode()
        self.assertIn('href="https://sitesorg.org/?utm_source=remoteimpact&amp;utm_medium=referral', html)
        self.assertIn("Careers page", html)
        self.assertNotIn("idealist.org/x", html)


class GuessedHomepageTests(TestCase):
    def test_guess_needs_the_page_to_name_the_organization(self):
        from types import SimpleNamespace
        from jobs.services.org_sites import guess_homepage, guessed_domains
        org = SimpleNamespace(name="Open Door Legal")
        self.assertIn("opendoorlegal.org", guessed_domains(org, ""))
        pages = {"https://opendoorlegal.org/": "<title>Domain for sale</title>",
                 "https://opendoorlegal.com/": "<title>Open Door Legal | Free legal help</title>"}
        self.assertEqual(guess_homepage(org, "", fetch_html=pages.get), "https://opendoorlegal.com/")
        self.assertEqual(guess_homepage(org, "", fetch_html=lambda url: "<title>Open Doors Realty</title>"), "")
        # one-word names are too ambiguous to guess
        self.assertEqual(guess_homepage(SimpleNamespace(name="Slipstream"), "", fetch_html=lambda url: "<title>Slipstream</title>"), "")
        intersect = SimpleNamespace(name="Intersect Power")
        self.assertEqual(guess_homepage(intersect, "", fetch_html=lambda url: "<title>Intersect</title>"), "")


class ReadingListTitleTests(TestCase):
    def test_reading_lists_are_not_openings(self):
        from jobs.services.importers.common import _is_generic_listing_title
        self.assertTrue(_is_generic_listing_title("List of Places to Find Roles at Top Startup Companies"))
        self.assertTrue(_is_generic_listing_title("List of Internship and Residency Programs, Machine Learning"))
        self.assertFalse(_is_generic_listing_title("Listings Operations Manager"))
        self.assertFalse(_is_generic_listing_title("Policy Analyst"))


class ParkedDomainTests(TestCase):
    def test_parked_pages_are_detected(self):
        from jobs.services.org_sites import looks_parked
        self.assertTrue(looks_parked("<title>American Indian College Fund</title> This domain is for sale! Make an offer"))
        self.assertTrue(looks_parked("Buy this domain at HugeDomains"))
        self.assertFalse(looks_parked("<title>American Indian College Fund</title> Scholarships for Native students"))
        self.assertFalse(looks_parked('<a href="https://www.jerrdan.com">Jerr-Dan</a>'))
        self.assertTrue(looks_parked("Listed on dan.com"))


class RegionFilterTests(TestCase):
    def test_europe_covers_eu_labels_and_countries_but_not_us(self):
        from django.http import QueryDict
        from jobs.models import Job, Organization
        from jobs.services.job_service import JobService
        org = Organization.objects.create(name="Region Org")
        def make(slug, **kw):
            return Job.objects.create(title=f"Analyst {slug}", slug=slug, organization=org, description="Analyse.",
                                      application_url=f"https://example.org/{slug}", **kw)
        eu = make("eu", location="Remote (EU)")
        europe = make("europe", country="Europe", location="Remote")
        germany = make("de", location="Remote (Germany)")
        us = make("us", location="Remote (US)", country="USA")
        params = QueryDict("country=Europe&country=Germany&country=Czech Republic")
        found = set(JobService.get_filtered_jobs(params).filter(organization=org).values_list("slug", flat=True))
        self.assertEqual(found, {"eu", "europe", "de"})


class MultiLocationTests(TestCase):
    def test_any_global_option_makes_it_worldwide(self):
        from jobs.services.location_normalizer import normalize_location
        self.assertEqual(normalize_location("Remote, USA; Remote, Global"), "Remote")
        self.assertEqual(normalize_location("Remote, Global"), "Remote")
        self.assertEqual(normalize_location("Remote, USA; Remote, UK"), "Remote (US)")
        self.assertEqual(normalize_location("Remote, Europe"), "Remote (EU)")


class BestMatchUnderFiltersTests(TestCase):
    def test_keep_all_ranks_matches_first_without_dropping_others(self):
        from unittest import mock
        from types import SimpleNamespace
        from jobs.models import Job, Organization
        from jobs.services.unified_matching_service import UnifiedMatchingService
        org = Organization.objects.create(name="Match Org")
        jobs = [Job.objects.create(title=f"Role {i}", slug=f"role-{i}", organization=org, description="x",
                                   application_url=f"https://example.org/{i}") for i in range(4)]
        matched = SimpleNamespace(job=jobs[0])
        with mock.patch.object(UnifiedMatchingService, "get_matches", return_value=[matched]):
            only = UnifiedMatchingService.order_jobs_for_seeker(Job.objects.all(), seeker=None)
            everything = UnifiedMatchingService.order_jobs_for_seeker(Job.objects.all(), seeker=None, keep_all=True)
        self.assertEqual([j.pk for j in only], [jobs[0].pk])
        self.assertEqual(len(everything), 4)
        self.assertEqual(everything[0].pk, jobs[0].pk)


class SalaryPlausibilityTests(TestCase):
    def test_wide_ranges_are_dropped_and_recorded(self):
        from decimal import Decimal
        from jobs.services.importers.common import drop_implausible_salary
        wide = {"salary_min": Decimal("10000"), "salary_max": Decimal("600000"), "salary_currency": "USD", "raw_data": {}}
        drop_implausible_salary(wide)
        self.assertIsNone(wide["salary_min"])
        self.assertEqual(wide["raw_data"]["salary_rejected"]["reason"], "range_too_wide")
        normal = {"salary_min": Decimal("80000"), "salary_max": Decimal("120000"), "salary_currency": "USD", "raw_data": {}}
        drop_implausible_salary(normal)
        self.assertEqual(normal["salary_max"], Decimal("120000"))


class JsonFeedTests(TestCase):
    def test_json_feed_lists_open_roles(self):
        from jobs.models import Job, Organization
        org = Organization.objects.create(name="Feed Org", slug="feed-org")
        Job.objects.create(title="Feed Analyst", slug="feed-analyst", organization=org, description="Analyse things.",
                           application_url="https://example.org/f", location="Remote (US)")
        data = self.client.get(reverse("jobs:jobs_json_feed")).json()
        self.assertEqual(data["version"], "https://jsonfeed.org/version/1.1")
        self.assertIn("Feed Analyst at Feed Org", [i["title"] for i in data["items"]])


class RoleInsightsTests(TestCase):
    def test_pay_comparison_needs_enough_peers_and_page_renders_facts(self):
        from decimal import Decimal
        from django.core.cache import cache
        from jobs.models import Category, Job, Organization
        from jobs.services.role_insights import role_insights
        cache.clear()
        cat = Category.objects.create(name="Test Area", slug="test-area")
        org = Organization.objects.create(name="Insight Org", slug="insight-org")
        def make(i, lo, hi):
            return Job.objects.create(title=f"Insight Role {i}", slug=f"insight-{i}", organization=org, category=cat,
                                      description="A role with a distinct description " * 5, location="Remote (US)",
                                      application_url=f"https://example.org/{i}", salary_min=Decimal(lo), salary_max=Decimal(hi),
                                      salary_currency="USD", job_type="full-time")
        jobs = [make(i, 50000 + i * 5000, 60000 + i * 5000) for i in range(10)]
        facts = role_insights(jobs[9], "Test Area")
        self.assertEqual(facts["similar_open"], 10)
        self.assertEqual(facts["pay_percentile"], 90)
        self.assertEqual(facts["org_other_open"], 9)
        few = role_insights(Job(category=cat, job_type="part-time", organization=org, salary_currency="USD"), "Test Area")
        self.assertNotIn("pay_percentile", few)
        from allauth.socialaccount.models import SocialApp
        from django.contrib.sites.models import Site
        for provider, pid in (("google", ""), ("openid_connect", "linkedin")):
            SocialApp.objects.create(provider=provider, provider_id=pid, name=pid or provider, client_id="x", secret="y",
                                     settings={"server_url": "https://www.linkedin.com/oauth"} if pid else {}).sites.add(Site.objects.get_current())
        html = self.client.get(reverse("jobs:job_detail", args=[jobs[9].slug])).content.decode()
        self.assertIn("This role at a glance", html)
        # an organization without a page address must not break the page
        Organization.objects.filter(pk=org.pk).update(slug="")
        self.assertEqual(self.client.get(reverse("jobs:job_detail", args=[jobs[9].slug])).status_code, 200)
        self.assertIn("more than 90%", html)


class SeoTextTests(TestCase):
    def test_titles_and_descriptions_fit_search_results(self):
        from jobs.services.seo_text import DESCRIPTION_LIMIT, TITLE_LIMIT, job_meta_description, job_title_tag
        short = job_title_tag("Policy Analyst", "RAND")
        self.assertEqual(short, "Policy Analyst at RAND | Remote Impact")
        long_title = job_title_tag("Head of Litigation", "Legal Advocates for Safe Science and Technology")
        self.assertLessEqual(len(long_title), TITLE_LIMIT)
        self.assertTrue(long_title.startswith("Head of Litigation at Legal"))
        huge = job_title_tag("Principal Software Engineer, Infrastructure Security and Reliability", "OpenAI")
        self.assertLessEqual(len(huge), TITLE_LIMIT)
        desc = job_meta_description("Head of Litigation", "LASST", "Remote (US)", "Full-time", "$150,000 – $190,000 per year",
                                    "<p>" + "Lead the organization's litigation strategy. " * 20 + "</p>")
        self.assertLessEqual(len(desc), DESCRIPTION_LIMIT)
        self.assertIn("Remote (US), full-time, $150,000", desc)


class OrgHiringSummaryTests(TestCase):
    def test_summary_and_sitemap_agree_with_the_page(self):
        from decimal import Decimal
        from jobs.models import Category, Job, Organization
        from jobs.sitemaps import OrganizationSitemap
        from jobs.views.organizations import OrganizationProfileView
        cat = Category.objects.create(name="Org Area", slug="org-area")
        org = Organization.objects.create(name="Summary Org", slug="summary-org")
        for i in range(3):
            Job.objects.create(title=f"Org Role {i}", slug=f"org-role-{i}", organization=org, category=cat, description="x " * 80,
                               location="Remote" if i == 0 else "Remote (US)", application_url=f"https://example.org/{i}",
                               salary_min=Decimal("60000"), salary_max=Decimal("80000"), salary_currency="USD", job_type="full-time")
        jobs = Job.objects.filter(organization=org)
        summary = OrganizationProfileView._hiring_summary(org, jobs)
        self.assertEqual(summary["open"], 3)
        self.assertEqual(summary["areas"], ["Org Area"])
        self.assertEqual(summary["worldwide"], 1)
        self.assertEqual(summary["pay_count"], 3)
        self.assertIn(org, list(OrganizationSitemap().items()))
        self.assertEqual(OrganizationProfileView._hiring_summary(org, Job.objects.none()), {})


class GroundedLabelTests(TestCase):
    def test_quotes_must_appear_in_the_posting(self):
        from jobs.management.commands.label_job_fields import verified
        body = "We need 5+ years of experience. A Master's degree is required."
        self.assertTrue(verified("5+ years of experience", body))
        self.assertTrue(verified("A Master's degree is required", body))
        self.assertFalse(verified("ten years of experience", body))
        self.assertFalse(verified("", body))


class RestrictiveQuoteTests(TestCase):
    def test_only_explicit_requirements_naming_the_country_count(self):
        from jobs.management.commands.restrict_worldwide_jobs import restrictive
        self.assertTrue(restrictive("Candidates must have existing authorization to work in India", "India"))
        self.assertTrue(restrictive("This role is based in Brazil and you will support customer sites", "Brazil"))
        self.assertTrue(restrictive("Applicants must be currently authorized to work in the United States.", "USA"))
        self.assertFalse(restrictive("Must reside within 25 miles of Phoenix Valley, AZ", "Azerbaijan"))
        self.assertFalse(restrictive("Preferred work location is Carlsbad, California. However, remote candidates welcome in the US", "USA"))
        self.assertFalse(restrictive("Lead corporate engagement in Colombia to negotiate policies", "Colombia"))


class SeoTrimFilterTests(TestCase):
    def test_trims_at_word_boundary_without_breaking_entities(self):
        from jobs.templatetags.job_extras import seo_trim
        self.assertEqual(seo_trim("Climate &amp; Energy jobs", 160), "Climate &amp; Energy jobs")
        out = seo_trim("Free salary to hourly calculator. Convert annual salary to hourly rate and back, " * 3, 160)
        self.assertLessEqual(len(out), 160)
        self.assertTrue(out.endswith("…"))
        self.assertNotIn("&amp;amp;", seo_trim("A &amp; B " * 30, 60))


class RegionSitemapTests(TestCase):
    def test_regions_with_enough_open_roles_are_listed(self):
        from jobs.models import Job, Organization
        from jobs.sitemaps import RegionSitemap, StaticSitemap
        org = Organization.objects.create(name="Region SM Org", slug="region-sm-org")
        for i in range(3):
            Job.objects.create(title=f"EU Role {i}", slug=f"eu-role-{i}", organization=org, description="x " * 80,
                               location="Remote (Germany)", country="Germany", application_url=f"https://example.org/{i}")
        regions = RegionSitemap().items()
        self.assertIn("worldwide", regions)
        names = [item[0] for item in StaticSitemap().items()]
        self.assertNotIn("jobs:post_job", names)
        self.assertIn("jobs:all_role_pages", names)
