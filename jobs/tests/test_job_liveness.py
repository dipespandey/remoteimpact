from datetime import timedelta
from io import StringIO
from unittest import mock

from allauth.socialaccount.models import SocialApp
from django.contrib.sites.models import Site
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase
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
        self.assertContains(response, "How we check every job")


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
