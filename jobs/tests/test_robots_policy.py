from urllib.robotparser import RobotFileParser

from django.test import SimpleTestCase, override_settings


@override_settings(SITE_URL='https://remoteimpact.org')
class RobotsPolicyTests(SimpleTestCase):
    def setUp(self):
        response = self.client.get('/robots.txt')
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response['Content-Type'].startswith('text/plain'))
        self.body = response.content.decode()
        self.groups = {}
        for block in self.body.split('\n\n'):
            agents = []
            rules = []
            for line in block.splitlines():
                if line.startswith('User-agent: '):
                    agents.append(line.split(': ', 1)[1])
                elif line.startswith(('Allow: ', 'Disallow: ')):
                    rules.append(line)
            for agent in agents:
                self.assertNotIn(agent, self.groups)
                self.groups[agent] = rules

    def test_public_crawlers_retain_every_sensitive_path_exclusion(self):
        agents = ('* Googlebot bingbot Applebot OAI-SearchBot ChatGPT-User '
                  'Claude-SearchBot Claude-User PerplexityBot Perplexity-User '
                  'DuckAssistBot Bravebot YouBot ExaSearchBot LinkupBot ShapBot '
                  'GPTBot ClaudeBot facebookexternalhit Twitterbot '
                  'Claude-Web Anthropic-ai Google-Extended').split()
        for agent in agents:
            with self.subTest(agent=agent):
                self.assertEqual(set(self.groups[agent]), {
                    'Allow: /', 'Disallow: /admin/', 'Disallow: /accounts/',
                    'Disallow: /api/', 'Disallow: /checkout/',
                    'Disallow: /jobs/*/feature/', 'Disallow: /jobs/*/go/',
                })

    def test_requested_and_existing_blocks_deny_all_paths(self):
        parser = RobotFileParser()
        parser.parse(self.body.splitlines())
        agents = ('meta-externalagent meta-externalads Reflectionbot ElevenlabsCrawler '
                  'AionBot TokenFoundryBot Bytespider CCBot AwarioBot QlyzeBot '
                  'Barkrowler SERankingBacklinksBot SenderBot TernCrawler SQLSpider '
                  'SpiderLing DataForSeoBot SemrushBot AhrefsBot PetalBot').split()
        for agent in agents:
            with self.subTest(agent=agent):
                self.assertEqual(self.groups[agent], ['Disallow: /'])
                for path in ['/', '/jobs/', '/blog/', '/employers/']:
                    self.assertFalse(parser.can_fetch(agent, 'https://remoteimpact.org' + path))

    def test_canonical_sitemap_is_preserved(self):
        self.assertIn('Sitemap: https://remoteimpact.org/sitemap.xml', self.body)
