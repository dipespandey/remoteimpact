from unittest.mock import Mock, patch
from django.test import SimpleTestCase
from jobs.services.html_sanitizer import sanitize_html
from jobs.services.safe_fetch import UnsafeURL, resolve_public_url, fetch_public_html, _request_pinned
from jobs.templatetags.job_extras import render_html


def answer(ip):
    return (2, 1, 6, '', (ip, 443))


def response(status=200, headers=None, body=b'<p>Hello</p>'):
    obj = Mock(status=status, headers=headers or {'Content-Type': 'text/html'}, connection=None)
    obj.read1.side_effect = [body, b'']
    return obj


class ImportedHTMLSecurityTests(SimpleTestCase):
    def test_removes_active_content_attributes_and_url_schemes(self):
        rendered = str(render_html('''<h1 onclick="attack()">Role</h1><p style="background:url(x)" onmouseover="attack()">Details</p><script>alert(1)</script><svg><a href="javascript:attack()">bad</a></svg><iframe src="https://bad.example"></iframe><a href="jav&#x61;script:attack()">link</a><a href="data:text/html,attack">data</a><img src=x onerror=attack()>'''))
        self.assertIn('<h2>Role</h2>', rendered)
        for forbidden in ('onclick', 'onmouseover', 'style=', 'script', '<svg', '<iframe', '<img', 'javascript:', 'data:'):
            self.assertNotIn(forbidden, rendered)

    def test_markup_bearing_attributes_are_escaped(self):
        value = sanitize_html('<a href="https://example.org" title="&quot; onmouseover=&quot;alert(1)">OK</a>')
        self.assertIn('href="https://example.org"', value)
        self.assertIn('nofollow noopener noreferrer', value)
        from bs4 import BeautifulSoup
        self.assertEqual(set(BeautifulSoup(value, 'html.parser').a.attrs), {'href', 'title', 'rel'})

    def test_markdown_links_and_raw_html_use_same_allowlist(self):
        self.assertNotIn('javascript:', str(render_html('[bad](javascript:alert)')))
        self.assertNotIn('onerror', str(render_html('<img src=x onerror=alert(1)>')))
        self.assertNotIn('<script>', str(render_html('&lt;script&gt;')))

    def test_protocol_relative_credentials_controls_and_backslashes_removed(self):
        for url in ('//evil.example/x', 'https://user:pass@example.org', 'java\nscript:alert(1)', 'https:\\evil.example', 'vbscript:x'):
            self.assertNotIn('href=', sanitize_html(f'<a href="{url}">Link</a>'))


class PublicFetchSecurityTests(SimpleTestCase):
    def test_rejects_all_nonpublic_ip_families_and_mixed_dns(self):
        for ip in ('127.0.0.1', '10.0.0.1', '169.254.169.254', '100.64.0.1', '192.0.2.1', '224.0.0.1', '::1', 'fc00::1', 'fe80::1', '::ffff:127.0.0.1', '2002:0808:0808::1', '64:ff9b::a00:1'):
            with self.subTest(ip=ip), patch('jobs.services.safe_fetch.socket.getaddrinfo', return_value=[answer('8.8.8.8'), answer(ip)]):
                with self.assertRaises(UnsafeURL):
                    resolve_public_url('https://jobs.example.org')

    def test_rejects_malformed_credentials_ports_and_nonhttp(self):
        for url in ('file:///etc/passwd', 'http://user@public.example', 'http://public.example:22', 'http://public.example:bad', 'http://public.example/\n', 'http:\\public.example', 'http://[bad'):
            with self.subTest(url=url):
                self.assertEqual(fetch_public_html(url).status, 'unsafe_or_unresolvable_url')

    @patch('jobs.services.safe_fetch.socket.getaddrinfo', return_value=[answer('8.8.8.8')])
    def test_tcp_uses_numeric_ip_while_tls_keeps_original_hostname(self, dns):
        with patch('jobs.services.safe_fetch.urllib3.HTTPSConnectionPool') as pool_cls, patch('jobs.services.safe_fetch.socket.socket') as sock:
            pool, result = _request_pinned(*resolve_public_url('https://jobs.example.org/path?q=1'), {}, 5)
            self.assertEqual(pool_cls.call_args.args[:2], ('jobs.example.org', 443))
            connection = pool.ConnectionCls('jobs.example.org', timeout=5)
            connection._new_conn()
            sock.return_value.connect.assert_called_once_with(('8.8.8.8', 443))
            dns.assert_called_once()
            self.assertEqual(pool.urlopen.call_args.args, ('GET', '/path?q=1'))
            self.assertFalse(pool.urlopen.call_args.kwargs['retries'])

    def test_public_redirect_to_private_is_rejected_without_connect(self):
        redirect = response(302, {'Location': 'http://internal.example/latest/meta-data'})
        with patch('jobs.services.safe_fetch.socket.getaddrinfo', side_effect=[[answer('8.8.8.8')], [answer('169.254.169.254')]]), patch('jobs.services.safe_fetch._request_pinned', return_value=(Mock(), redirect)) as request:
            self.assertEqual(fetch_public_html('https://jobs.example.org').status, 'unsafe_or_unresolvable_url')
            self.assertEqual(request.call_count, 1)
            redirect.close.assert_called_once()

    def test_redirect_chain_public_success_and_limit(self):
        with patch('jobs.services.safe_fetch.socket.getaddrinfo', return_value=[answer('8.8.8.8')]), patch('jobs.services.safe_fetch._request_pinned', side_effect=[(Mock(), response(302, {'Location': '/detail'})), (Mock(), response())]) as request:
            result = fetch_public_html('https://jobs.example.org/start')
            self.assertEqual(result.status, 'ok')
            self.assertEqual(request.call_args.args[0].path, '/detail')
        with patch('jobs.services.safe_fetch.socket.getaddrinfo', return_value=[answer('8.8.8.8')]), patch('jobs.services.safe_fetch._request_pinned', return_value=(Mock(), response(302, {'Location': '/again'}))) as request:
            self.assertEqual(fetch_public_html('https://jobs.example.org/start', max_redirects=1).status, 'too_many_redirects')
            self.assertEqual(request.call_count, 2)

    def test_body_encoding_and_size_limits(self):
        fixtures = [(response(headers={'Content-Type': 'text/html', 'Content-Length': '9999999'}), 'page_too_large'), (response(body=b'x'*11), 'page_too_large'), (response(headers={'Content-Type': 'text/html', 'Content-Encoding': 'gzip'}), 'unsupported_encoding'), (response(headers={'Content-Type': 'application/json'}), 'non_html')]
        for page, expected in fixtures:
            with self.subTest(expected=expected), patch('jobs.services.safe_fetch.socket.getaddrinfo', return_value=[answer('8.8.8.8')]), patch('jobs.services.safe_fetch._request_pinned', return_value=(Mock(), page)):
                self.assertEqual(fetch_public_html('https://jobs.example.org', max_bytes=10).status, expected)
                page.close.assert_called_once()

    def test_transport_failures_are_safe(self):
        with patch('jobs.services.safe_fetch.socket.getaddrinfo', return_value=[answer('8.8.8.8')]), patch('jobs.services.safe_fetch._request_pinned', side_effect=OSError('timeout')):
            self.assertEqual(fetch_public_html('https://jobs.example.org').status, 'request_failed')

    def test_dns_timeout_and_capacity_fail_closed(self):
        from concurrent.futures import TimeoutError
        future = Mock()
        future.result.side_effect = TimeoutError()
        with patch('jobs.services.safe_fetch._DNS_EXECUTOR.submit', return_value=future), patch('jobs.services.safe_fetch._DNS_SLOTS') as slots, patch('jobs.services.safe_fetch._request_pinned') as request:
            slots.acquire.return_value = True
            self.assertEqual(fetch_public_html('https://jobs.example.org').status, 'unsafe_or_unresolvable_url')
            request.assert_not_called()
            self.assertLessEqual(future.result.call_args.kwargs['timeout'], 5)
            slots.acquire.return_value = False
            self.assertEqual(fetch_public_html('https://jobs.example.org').status, 'unsafe_or_unresolvable_url')

    def test_expired_total_deadline_does_not_start_dns_or_request(self):
        with patch('jobs.services.safe_fetch.time.monotonic', side_effect=[0, 11]), patch('jobs.services.safe_fetch.socket.getaddrinfo') as dns, patch('jobs.services.safe_fetch._request_pinned') as request:
            self.assertEqual(fetch_public_html('https://jobs.example.org', total_timeout=10).status, 'request_failed')
            dns.assert_not_called()
            request.assert_not_called()

    def test_resource_description_uses_guarded_boundary(self):
        from jobs.views.resources import _fetch_job_description
        with patch('jobs.views.resources.fetch_public_html', return_value=Mock(status='unsafe_or_unresolvable_url')) as guarded:
            self.assertEqual(_fetch_job_description('http://169.254.169.254/secret'), '')
            guarded.assert_called_once_with('http://169.254.169.254/secret', total_timeout=10)
