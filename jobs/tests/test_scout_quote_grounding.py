import html
import json
from unittest.mock import patch

from django.core.cache import cache
from django.test import SimpleTestCase, override_settings

from jobs.services.scout_reranker import rerank
from jobs.services.scout_workflow import bounded_workflow
from jobs.tests.test_scout_pipeline import job

LISTING_HTML = ('<div class="content-intro"><p>We fund clean water.</p></div>'
                '<h3><strong>Responsibilities</strong></h3><ul>'
                '<li>Lead monitoring &amp; evaluation for our grant portfolio</li>'
                '<li>Manage partner budgets&nbsp;and quarterly reporting</li></ul>'
                '<p>You&rsquo;ll report to the program director.</p>')


def response(*records):
    return json.dumps({'decisions': list(records)})


def selected(pk, quote):
    return {'id': pk, 'relevant': True, 'checks': {'function': ['confirmed', 'description', quote]}}


@override_settings(SCOUT_RERANK_ENABLED=True, SCOUT_RERANK_BACKEND='deepseek',
                   DEEPSEEK_API_KEY='test', DEEPSEEK_MODEL='quote-test')
class QuoteGroundingTests(SimpleTestCase):
    prefs = {'intent_text': 'Grants and program management roles'}

    def setUp(self):
        cache.clear()

    def review(self, listing, quote):
        candidate = job(1, title='Program Manager', requirements='', **listing)
        with patch('jobs.services.scout_workflow.generate', return_value=response(selected(1, quote))):
            return rerank([candidate], self.prefs, [], {})

    def test_true_quotes_of_shown_text_ground_despite_markup_and_typography(self):
        greenhouse = {'description': 'Catalogue summary.', 'raw_data': {'api_response': {'content': html.escape(LISTING_HTML)}}}
        ashby = {'description': 'Catalogue summary.', 'raw_data': {'api_response': {'descriptionHtml': LISTING_HTML}}}
        markdown = {'description': '## About\n**Responsibilities:** Develop and track program budgets.\n'
                                   '• Coordinate grant reporting… every quarter'}
        for name, listing, quote in [
                ('escaped greenhouse content', greenhouse, 'Lead monitoring & evaluation for our grant portfolio'),
                ('quote spanning list items', greenhouse, 'Manage partner budgets and quarterly reporting'),
                ('decoded &amp;', ashby, 'Lead monitoring & evaluation for our grant portfolio'),
                ('decoded &rsquo;', ashby, "You'll report to the program director"),
                ('markdown emphasis dropped', markdown, 'Responsibilities: Develop and track program budgets'),
                ('ellipsis typed as dots', markdown, 'Coordinate grant reporting... every quarter')]:
            with self.subTest(name):
                cache.clear()
                self.assertEqual([j.pk for j in self.review(listing, quote)], [1])

    def test_escaped_markup_is_not_shown_to_the_reviewer(self):
        candidate = job(1, description='Catalogue summary.', requirements='',
                        raw_data={'api_response': {'content': html.escape(LISTING_HTML)}})
        with patch('jobs.services.scout_workflow.generate', return_value=response({'id': 1, 'relevant': False})) as provider:
            rerank([candidate], self.prefs, [], {})
        shown = json.loads(provider.call_args.args[0])['candidates'][0]['description']
        self.assertIn('Lead monitoring & evaluation for our grant portfolio', shown)
        self.assertNotIn('&lt;', shown)
        self.assertNotIn('&amp;', shown)

    def test_invented_quotes_still_fail(self):
        ashby = {'description': 'Catalogue summary.', 'raw_data': {'api_response': {'descriptionHtml': LISTING_HTML}}}
        for quote in ['Lead fundraising strategy for our donors',
                      'Lead monitoring and evaluation for our grant portfolio',
                      'Lead monitoring & evaluation <b>for our donors</b>']:
            with self.subTest(quote):
                cache.clear()
                self.assertEqual(self.review(ashby, quote), [])

    def test_excerpts_do_not_cut_words(self):
        from jobs.services.scout_review import excerpt
        text = 'Intro filler sentence about the organisation. ' * 30 + 'Manage grant portfolios for partners. ' + 'Benefits text here. ' * 40
        shown = excerpt(text, 'grant', 400)
        words = set(text.split())
        for part in shown.split(' […] '):
            self.assertTrue(set(part.split()) <= words, part)


@override_settings(SCOUT_RERANK_ENABLED=True, SCOUT_RERANK_BACKEND='deepseek',
                   DEEPSEEK_API_KEY='test', DEEPSEEK_MODEL='quote-repair-test')
class QuoteRepairTests(SimpleTestCase):
    prefs = {'intent_text': 'Software engineering jobs'}

    def setUp(self):
        cache.clear()
        self.a = job(1, title='Software Engineer', description='Build Python software services.', requirements='')
        self.b = job(2, title='Backend Developer', description='Maintain software integrations.', requirements='')

    def test_repair_names_the_failed_quote_and_resolves_a_paraphrase(self):
        paraphrase = selected(2, 'Maintains integrations for software')
        with patch('jobs.services.scout_workflow.generate', side_effect=[
                response(selected(1, self.a.description), paraphrase),
                response(selected(2, self.b.description))]) as provider:
            d = {}
            self.assertEqual(rerank([self.a, self.b], self.prefs, [], d), [self.a, self.b])
        self.assertEqual(provider.call_count, 2)
        repair = provider.call_args
        payload = json.loads(repair.args[0])
        self.assertEqual([row['id'] for row in payload['candidates']], [2])
        self.assertIn('exact contiguous copy', payload['previous_issues']['2'])
        self.assertNotIn('previous_issues', payload['candidates'][0])
        self.assertIn('previous_issues', repair.kwargs['system_message'])
        self.assertEqual(d['review_decision_issues'], {'prompt_quote_ungrounded': 1})
        self.assertEqual(d['review_repair_resolved'], 1)
        self.assertEqual(d['reranker'], 'model')

    def test_repair_with_feedback_stays_inside_the_four_call_ceiling(self):
        from jobs.services.scout_workflow import generate
        paraphrase = selected(2, 'Maintains integrations for software')
        with patch('jobs.services.ai.AIClient') as client:
            client.return_value.generate.side_effect = [
                'setup', 'setup', 'setup', response(selected(1, self.a.description), paraphrase)]
            d = {}
            with bounded_workflow(d):
                for _ in range(3):
                    generate('setup')
                self.assertEqual(rerank([self.a, self.b], self.prefs, [], d), [self.a])
        self.assertEqual(client.return_value.generate.call_count, 4)
        self.assertNotIn('review_repair_attempted', d)
        self.assertEqual(d['reranker'], 'review_partial')
