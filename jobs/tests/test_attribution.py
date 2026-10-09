from django.contrib.auth import get_user_model
from django.contrib.sessions.backends.db import SessionStore
from django.test import RequestFactory, TestCase

from allauth.account.signals import user_signed_up

from jobs.models import ConsumerFunnelEvent
from jobs.middleware import AcquisitionAttributionMiddleware


class AcquisitionAttributionTests(TestCase):
    def test_campaign_parameters_are_kept_in_session(self):
        request = RequestFactory().get(
            "/jobs/",
            {
                "utm_source": "remote-list",
                "utm_medium": "referral",
                "utm_campaign": "partner-listings",
            },
        )
        request.session = SessionStore()
        response = AcquisitionAttributionMiddleware(lambda req: None)(request)

        self.assertIsNone(response)
        self.assertEqual(
            request.session[AcquisitionAttributionMiddleware.SESSION_KEY],
            {
                "utm_source": "remote-list",
                "utm_medium": "referral",
                "utm_campaign": "partner-listings",
            },
        )

    def test_signup_event_receives_campaign_context(self):
        user = get_user_model().objects.create_user(
            username="attribution-user",
            email="attribution@example.com",
            password="password",
        )
        request = RequestFactory().get("/accounts/signup/")
        request.session = SessionStore()
        request.session[AcquisitionAttributionMiddleware.SESSION_KEY] = {
            "utm_source": "awesome-job-boards",
            "utm_medium": "referral",
            "utm_campaign": "community-lists",
        }

        user_signed_up.send(sender=type(user), request=request, user=user)

        event = ConsumerFunnelEvent.objects.get(
            dedupe_key=f"account-created:{user.pk}"
        )
        self.assertEqual(event.metadata["utm_source"], "awesome-job-boards")
        self.assertEqual(event.metadata["utm_medium"], "referral")
        self.assertEqual(event.metadata["utm_campaign"], "community-lists")
