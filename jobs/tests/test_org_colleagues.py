from allauth.account.models import EmailAddress
from django.contrib.auth import get_user_model
from django.test import TestCase

from jobs.models import Organization
from jobs.services.job_service import JobService


class ColleagueOrgTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.first = User.objects.create_user("sarah", "sarah@rtt.org", "pw")
        self.org = Organization.objects.create(name="RTT", slug="rtt", website="https://www.rtt.org/")
        self.org.members.add(self.first)

    def _user(self, email, verified=True):
        user = get_user_model().objects.create_user(email.split("@")[0] + email[-6:], email, "pw")
        EmailAddress.objects.create(user=user, email=email, verified=verified, primary=True)
        return user

    def test_verified_colleague_joins_existing_org(self):
        colleague = self._user("jobs@rtt.org")
        org = JobService._organization_for_poster("RTT", {}, colleague)
        self.assertEqual(org.pk, self.org.pk)
        self.assertTrue(org.members.filter(pk=colleague.pk).exists())

    def test_outsider_or_unverified_gets_new_org(self):
        for user in (self._user("x@gmail.com"), self._user("y@rtt.org", verified=False)):
            org = JobService._organization_for_poster("RTT", {}, user)
            self.assertNotEqual(org.pk, self.org.pk)


class ColleagueCanManagePaidRoleTests(TestCase):
    def test_colleague_sees_paid_role_but_not_others_drafts(self):
        from django.utils import timezone

        from jobs.models import Category, Job
        from jobs.services.employer_access import hiring_jobs

        User = get_user_model()
        poster, colleague, outsider = (User.objects.create_user(n, f"{n}@rtt.org", "pw") for n in ("jobs", "sarah", "x"))
        org = Organization.objects.create(name="RTT", slug="rtt", website="https://rtt.org/")
        org.members.add(poster, colleague)
        cat = Category.objects.create(name="Impact", slug="impact")
        now = timezone.now()
        common = dict(organization=org, category=cat, description="d", location="Remote", job_type="full-time",
                      source=Job.Source.MANUAL, poster=poster, posted_at=now)
        paid = Job.objects.create(title="Paid", slug="paid", is_paid=True, is_active=True, **common)
        draft = Job.objects.create(title="Draft", slug="draft", is_paid=False, is_active=False, **common)
        self.assertEqual(set(hiring_jobs(colleague)), {paid})
        self.assertEqual(set(hiring_jobs(poster)), {paid, draft})
        self.assertFalse(hiring_jobs(outsider).exists())
