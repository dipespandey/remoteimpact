from django.template.loader import render_to_string
from django.test import SimpleTestCase


class EmailDesignTests(SimpleTestCase):
    def test_roundup_keeps_each_job_source_and_unsubscribe(self):
        html = render_to_string('emails/roundup_issue.html', {
            'picks': [{'employer': 'Example & Co', 'title': 'Engineer', 'url': 'https://example.com/job',
                       'employer_source': 'https://employer.example/job',
                       'eligible_countries_review': 'Remote in Canada', 'salary_review': 'Pay not listed'}],
            'stop_url': 'https://example.com/unsubscribe', 'share_url': 'https://example.com/share',
        })
        for value in ('Example &amp; Co', 'Engineer', 'Remote in Canada', 'Pay not listed',
                      'https://example.com/job', 'https://employer.example/job', 'https://example.com/unsubscribe'):
            self.assertIn(value, html)
        self.assertIn('Remote Impact', html)

    def test_confirmation_links_and_expiry_remain_visible(self):
        roundup = render_to_string('emails/roundup_confirm.html', {
            'confirm_url': 'https://example.com/confirm', 'stop_url': 'https://example.com/stop',
        })
        scout = render_to_string('emails/scout_contact_verify.html', {'verify_url': 'https://example.com/verify'})
        for value in ('https://example.com/confirm', 'https://example.com/stop', '48 hours'):
            self.assertIn(value, roundup)
        for value in ('https://example.com/verify', 'seven days'):
            self.assertIn(value, scout)
