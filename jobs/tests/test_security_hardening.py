import time
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.http import HttpResponse
from django.test import Client, RequestFactory, TestCase, override_settings
from django.urls import reverse

from jobs.security import TrustedProxyMiddleware


class TrustedProxyTests(TestCase):
    def test_public_peer_cannot_choose_forwarded_identity(self):
        request = RequestFactory().get('/', REMOTE_ADDR='198.51.100.1',
            HTTP_CF_CONNECTING_IP='203.0.113.1', HTTP_X_REMOTEIMPACT_CLIENT_IP='203.0.113.2')
        TrustedProxyMiddleware(lambda r: HttpResponse())(request)
        self.assertEqual(request.client_ip, '198.51.100.1')
        self.assertNotIn('HTTP_CF_CONNECTING_IP', request.META)

    def test_trusted_proxy_accepts_only_valid_overwritten_ip(self):
        request = RequestFactory().get('/', REMOTE_ADDR='127.0.0.1', HTTP_X_REMOTEIMPACT_CLIENT_IP='203.0.113.2')
        TrustedProxyMiddleware(lambda r: HttpResponse())(request)
        self.assertEqual(request.META['REMOTE_ADDR'], '203.0.113.2')
        bad = RequestFactory().get('/', REMOTE_ADDR='127.0.0.1', HTTP_X_REMOTEIMPACT_CLIENT_IP='bad, forged')
        TrustedProxyMiddleware(lambda r: HttpResponse())(bad)
        self.assertEqual(bad.client_ip, '127.0.0.1')


@override_settings(ADMIN_REQUIRE_MFA=True)
class StaffMFATests(TestCase):
    def setUp(self):
        self.staff = get_user_model().objects.create_user('security-staff', 'staff@example.test', 'synthetic-test-password', is_staff=True)
        self.client.force_login(self.staff)

    def test_unenrolled_staff_cannot_use_admin_or_post(self):
        for method in ('get', 'post'):
            response = getattr(self.client, method)('/admin/analytics/')
            self.assertRedirects(response, reverse('mfa_activate_totp'), fetch_redirect_response=False)

    def test_native_admin_login_routes_anonymous_users_to_protected_login(self):
        self.client.logout()
        response = self.client.get('/admin/login/')
        self.assertEqual(response.status_code, 302)
        self.assertIn('/accounts/login/', response['Location'])

    def test_existing_password_session_needs_a_second_factor(self):
        from allauth.mfa.models import Authenticator
        Authenticator.objects.create(user=self.staff, type='totp', data={})
        response = self.client.get('/admin/analytics/')
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse('mfa_reauthenticate'), response['Location'])

    def test_current_factor_record_allows_admin_but_expired_record_does_not(self):
        from allauth.mfa.models import Authenticator
        auth = Authenticator.objects.create(user=self.staff, type='totp', data={})
        session = self.client.session
        session['account_authentication_methods'] = [{'method':'mfa','id':auth.pk,'type':'totp','at':time.time()}]
        session.save()
        self.assertEqual(self.client.get('/admin/').status_code, 200)
        session = self.client.session
        session['account_authentication_methods'][0]['at'] = time.time() - 9 * 3600
        session.save()
        self.assertEqual(self.client.get('/admin/').status_code, 302)

    def test_authenticator_secrets_are_encrypted_and_staff_cannot_remove_mfa(self):
        from jobs.mfa_adapter import StaffMFAAdapter
        from allauth.mfa.models import Authenticator
        adapter = StaffMFAAdapter()
        value = adapter.encrypt('SYNTHETICSECRETONLY')
        self.assertNotIn('SYNTHETICSECRETONLY', value)
        self.assertEqual(adapter.decrypt(value), 'SYNTHETICSECRETONLY')
        self.assertFalse(adapter.can_delete_authenticator(Authenticator(user=self.staff, type='totp')))


@override_settings(ADMIN_REQUIRE_MFA=False)
class OptionalStaffMFATests(TestCase):
    def setUp(self):
        self.staff = get_user_model().objects.create_user(
            'optional-staff', 'optional@example.test', 'synthetic-test-password', is_staff=True)
        self.client.force_login(self.staff)

    def test_staff_can_enter_admin_without_enrolling_when_enforcement_disabled(self):
        self.assertEqual(self.client.get('/admin/').status_code, 200)

    def test_mfa_management_and_setup_remain_available(self):
        response = self.client.get(reverse('mfa_index'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, reverse('mfa_activate_totp'))

    def test_optional_enrollment_remains_encrypted_and_can_be_disabled(self):
        from jobs.mfa_adapter import StaffMFAAdapter
        from allauth.mfa.models import Authenticator
        adapter = StaffMFAAdapter()
        value = adapter.encrypt('SYNTHETICOPTIONALSECRET')
        self.assertNotIn('SYNTHETICOPTIONALSECRET', value)
        self.assertEqual(adapter.decrypt(value), 'SYNTHETICOPTIONALSECRET')
        self.assertTrue(adapter.can_delete_authenticator(Authenticator(user=self.staff, type='totp')))


class ParserBoundaryTests(TestCase):
    def test_oversized_cv_is_rejected_before_starting_a_parser(self):
        from io import BytesIO
        from jobs.services.cv_reader import extract_text
        with patch('subprocess.run') as run:
            self.assertEqual(extract_text(BytesIO(b'x' * (5 * 1024 * 1024 + 1)), 'cv.pdf'), '')
            run.assert_not_called()

    def test_logo_decode_allowlist_excludes_complex_formats(self):
        from jobs.services.org_logos import to_square_png
        with patch('PIL.Image.open', side_effect=ValueError('synthetic')) as opened:
            self.assertIsNone(to_square_png(b'not-an-image'))
        self.assertNotIn('PSD', opened.call_args.kwargs['formats'])
