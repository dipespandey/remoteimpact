import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from django.core.cache import caches
from django.test import SimpleTestCase, TestCase
from jobs.services.job_facts import VERSION, validate, legacy_fields, source_hash
from jobs.services.llm_parser import JobParser
from jobs.services.extraction_source import compact_source, evidence_source, resolve_evidence
from jobs.services.extraction_contract import FIELDS, messages_for, CONTRACT_HASH
from jobs.services import extraction_cache


def parser():
    instance = JobParser.__new__(JobParser)
    instance.config = {'model': 'test', 'base_url': 'test'}
    instance._semaphore = asyncio.Semaphore(3)
    instance._parse_fn = instance._parse_openai_compat
    return instance


def response(data):
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(data)))],
                           usage=SimpleNamespace(prompt_tokens=100, completion_tokens=30))


def empty():
    return {field: [] if 'skills' in field else None for field in FIELDS}


class ExtractionSourceTests(SimpleTestCase):
    def test_duplicate_paragraphs_removed_without_losing_restrictions(self):
        paragraph = 'We are an international organization committed to improving health through research and practical action.'
        text = f'<p>{paragraph}</p><p>Only residents of Germany.</p><p>{paragraph}</p><p>Except Berlin.</p>'
        compact = compact_source(text)
        self.assertEqual(compact.count(paragraph), 1)
        self.assertIn('Only residents of Germany.', compact)
        self.assertIn('Except Berlin.', compact)

    def test_hidden_content_and_html_removed_but_short_headings_repeat(self):
        value = compact_source('<script>Required Python</script><p>Required</p><p>SQL</p><p>Required</p><p>Java</p>')
        self.assertNotIn('Python', value)
        self.assertEqual(value.count('Required'), 2)

    def test_source_references_roundtrip_and_unknown_ids_fail(self):
        spans = evidence_source('Engineer', 'Org', 'Required Python. SQL preferred.')
        result = resolve_evidence({'skills': [{'value': 'python', 'evidence': 'S2'}, {'value': 'sql', 'evidence': 'INVENTED'}]}, spans)
        self.assertEqual(result['skills'][0]['evidence'], 'Required Python.')
        self.assertEqual(result['skills'][1]['evidence'], '')

    def test_multiple_geography_sentences_preserve_exclusions(self):
        spans = evidence_source('Engineer', 'Org', 'Remote worldwide. Except Germany.')
        geo = {'scope': 'restricted', 'countries': [], 'excluded_countries': ['DE'], 'regions': [], 'restrictions': [], 'evidence': ['S2', 'S3']}
        result = validate(resolve_evidence({'geography': geo}, spans), 'Engineer', 'Org', 'Remote worldwide. Except Germany.')
        self.assertEqual(result['geography']['excluded_countries'], ['DE'])
        self.assertEqual(len(result['geography']['evidence']), 2)

    def test_instructions_identical_across_listings_and_review_omits_unneeded_taxonomies(self):
        first, _ = messages_for('A', 'Org', 'Job one')
        second, _ = messages_for('B', 'Else', 'Job two')
        self.assertEqual(first[0], second[0])
        review, _ = messages_for('A', 'Org', 'Job one', ['education_level'])
        self.assertNotIn('Allowed skill labels', review[0]['content'])
        self.assertLess(len(review[0]['content']), len(first[0]['content']))


class ExtractionRequestTests(SimpleTestCase):
    def test_accepted_result_needs_one_call(self):
        instance = parser()
        create = AsyncMock(return_value=response(empty()))
        instance.client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
        result = asyncio.run(instance._parse_openai_compat('Engineer', 'Org', 'Nothing specified.'))
        self.assertEqual(create.await_count, 1)
        self.assertEqual(result['job_facts']['usage']['requests'], 1)

    def test_rejected_field_only_is_repaired_and_usage_is_totaled(self):
        first = empty()
        first['education_level'] = {'value': 'bachelor', 'evidence': 'S999'}
        create = AsyncMock(side_effect=[response(first), response({'education_level': None})])
        instance = parser()
        instance.client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
        result = asyncio.run(instance._parse_openai_compat('Counsel', 'Org', 'Juris Doctor (JD) required.'))
        facts = result['job_facts']
        self.assertIsNone(facts['education_level'])
        self.assertEqual(facts['reviewed_fields'], ['education_level'])
        self.assertEqual(facts['usage']['input_tokens'], 200)
        self.assertEqual(create.await_count, 2)
        self.assertNotIn('Allowed skill labels', create.call_args_list[1].kwargs['messages'][0]['content'])

    def test_empty_provider_object_is_repaired_once_and_never_published_as_unknown(self):
        instance = parser()
        create = AsyncMock(return_value=response({}))
        instance.client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
        self.assertEqual(asyncio.run(instance._parse_openai_compat('A', 'B', 'Source')), {})
        self.assertEqual(create.await_count, 2)

    def test_unknown_sentence_reference_is_not_accepted(self):
        first = empty()
        first['required_skills'] = [{'value': 'python', 'evidence': 'S999'}]
        create = AsyncMock(side_effect=[response(first), response({'required_skills': []})])
        instance = parser()
        instance.client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
        result = asyncio.run(instance._parse_openai_compat('A', 'B', 'No required skills.'))
        self.assertEqual(result['skills'], [])
        self.assertEqual(create.await_count, 2)

    def test_shared_cache_skips_other_instance_and_avoids_aliasing(self):
        caches['job_extraction'].clear()
        facts = validate(empty(), 'A', 'B', 'Source')
        facts['model'] = 'test'
        facts['contract_hash'] = CONTRACT_HASH
        first, second = parser(), parser()
        first._parse_fn = AsyncMock(return_value=legacy_fields(facts))
        second._parse_fn = AsyncMock(side_effect=AssertionError('Cache should have prevented API call'))
        with patch('jobs.services.extraction_cache.get_facts', wraps=extraction_cache.get_facts), patch('jobs.models.Job.objects.filter', side_effect=RuntimeError('No database in unit test')):
            result = asyncio.run(first.parse_single('A', 'B', 'Source'))
            result['job_facts']['geography'] = 'mutated'
            reused = asyncio.run(second.parse_single('A', 'B', 'Source'))
        self.assertIsNone(reused['job_facts']['geography'])
        self.assertEqual(first._parse_fn.await_count, 1)
        self.assertEqual(second._parse_fn.await_count, 0)

    def test_concurrent_identical_requests_use_one_api_call(self):
        instance = parser()
        facts = validate(empty(), 'A', 'B', 'Concurrent source')
        facts['model'] = 'test'
        facts['contract_hash'] = CONTRACT_HASH
        async def generate(*args):
            await asyncio.sleep(.01)
            return legacy_fields(facts)
        instance._parse_fn = AsyncMock(side_effect=generate)
        async def run():
            return await asyncio.gather(*(instance.parse_single('A', 'B', 'Concurrent source') for _ in range(12)))
        with patch('jobs.services.extraction_cache.get_facts', new=AsyncMock(return_value=None)), patch('jobs.services.extraction_cache.set_facts', new=AsyncMock()):
            results = asyncio.run(run())
        self.assertEqual(len(results), 12)
        self.assertEqual(instance._parse_fn.await_count, 1)

    def test_cache_changes_with_prompt_model_endpoint_and_source(self):
        base = extraction_cache.cache_key(VERSION, 'm', 'e', 's')
        for args in [('old', 'm', 'e', 's'), (VERSION, 'new', 'e', 's'), (VERSION, 'm', 'new', 's'), (VERSION, 'm', 'e', 'new')]:
            self.assertNotEqual(base, extraction_cache.cache_key(*args))

    def test_failures_are_not_cached(self):
        instance = parser()
        instance._parse_fn = AsyncMock(return_value={})
        with patch('jobs.services.extraction_cache.get_facts', new=AsyncMock(return_value=None)), patch('jobs.services.extraction_cache.set_facts', new=AsyncMock()) as write:
            asyncio.run(instance.parse_single('A', 'B', 'Source'))
            asyncio.run(instance.parse_single('A', 'B', 'Source'))
        self.assertEqual(instance._parse_fn.await_count, 2)
        self.assertEqual(write.await_count, 0)

    def test_review_failure_preserves_accepted_first_pass_fields(self):
        first = empty()
        first['job_type'] = {'value': 'contract', 'evidence': 'S2'}
        first['education_level'] = {'value': 'bachelor', 'evidence': 'S999'}
        create = AsyncMock(side_effect=[response(first), RuntimeError('Provider unavailable')])
        instance = parser()
        instance.client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
        result = asyncio.run(instance._parse_openai_compat('Counsel', 'Org', 'Contract position. Juris Doctor (JD) required.'))
        self.assertEqual(result['job_type'], 'contract')
        self.assertIsNone(result['job_facts']['education_level'])
        self.assertEqual(create.await_count, 2)

    def test_formatting_and_repeated_paragraphs_share_hash_but_changed_restrictions_do_not(self):
        paragraph = 'We are an international organization committed to improving health through research and practical action.'
        first = f'<p>{paragraph}</p><p>Residents of Germany only.</p><p>{paragraph}</p>'
        second = f'{paragraph}\nResidents of Germany only.'
        self.assertEqual(source_hash('A', 'B', first), source_hash('A', 'B', second))
        self.assertNotEqual(source_hash('A', 'B', first), source_hash('A', 'B', second.replace('Germany', 'France')))


class PersistedExtractionTests(TestCase):
    def setUp(self):
        caches['job_extraction'].clear()

    def test_persisted_job_restores_cache_after_eviction_without_model_call(self):
        from asgiref.sync import async_to_sync
        from jobs.models import Job, Organization
        title, org, text = 'Engineer', 'Cache Org', 'Build reliable systems for health research.'
        facts = validate(empty(), title, org, text)
        facts['model'] = 'test'
        facts['contract_hash'] = CONTRACT_HASH
        key = extraction_cache.cache_key(VERSION + CONTRACT_HASH, 'test', 'test', source_hash(title, org, text))
        facts['cache_key'] = key
        organization = Organization.objects.create(name=org)
        Job.objects.create(title=title, organization=organization, slug='cache-restoration', description=text,
                           is_active=False, raw_data={'job_facts': facts})
        instance = parser()
        instance._parse_fn = AsyncMock(side_effect=AssertionError('Persisted facts should skip the model'))
        result = async_to_sync(instance.parse_single)(title, org, text)
        self.assertEqual(instance._parse_fn.await_count, 0)
        self.assertEqual(result['job_facts']['cache_key'], key)
        self.assertEqual(caches['job_extraction'].get(key)['source_hash'], source_hash(title, org, text))

    def test_cache_service_failure_still_allows_extraction(self):
        from asgiref.sync import async_to_sync
        instance = parser()
        facts = validate(empty(), 'A', 'B', 'Source')
        facts['model'] = 'test'
        facts['contract_hash'] = CONTRACT_HASH
        instance._parse_fn = AsyncMock(return_value=legacy_fields(facts))
        with patch.object(caches['job_extraction'], 'aget', new=AsyncMock(side_effect=RuntimeError('Cache down'))), patch.object(caches['job_extraction'], 'aset', new=AsyncMock(side_effect=RuntimeError('Cache down'))):
            result = async_to_sync(instance.parse_single)('A', 'B', 'Source')
        self.assertTrue(result)
        self.assertEqual(instance._parse_fn.await_count, 1)

    def test_import_preserves_cached_facts_on_identical_non_ai_refresh(self):
        from jobs.services.importers.common import _upsert_job
        text = 'Build reliable Python services for climate research. ' * 8
        payload = {'title': 'Engineer', 'organization_name': 'Cache Org', 'description': text,
                   'source': 'climatebase', 'external_id': 'cache-refresh', 'location': 'Remote (Germany)',
                   'application_url': 'https://example.org/jobs/cache-refresh', 'is_active': False}
        from jobs.services.job_facts import source_description
        facts = validate(empty(), payload['title'], payload['organization_name'], source_description(payload))
        payload['raw_data'] = {'job_facts': facts}
        _upsert_job(payload)
        refresh = dict(payload)
        refresh.pop('raw_data')
        job, _ = _upsert_job(refresh)
        self.assertEqual(job.raw_data['job_facts']['source_hash'], facts['source_hash'])
        refresh['description'] = text + 'Only applicants residing in France can apply.'
        job, _ = _upsert_job(refresh)
        self.assertNotIn('job_facts', job.raw_data)


class CandidateVocabularyTests(SimpleTestCase):
    def test_shortlist_preserves_explicit_tools_and_related_label_words(self):
        from jobs.services.extraction_contract import skill_candidates
        candidates = skill_candidates('Required Python and SQL. Writing grant proposals. Preferred AWS.')
        for label in ('python', 'sql', 'aws', 'grant-writing'):
            self.assertIn(label, candidates)
        self.assertNotIn('java', candidates)

    def test_empty_candidate_vocabulary_does_not_create_guessed_skills(self):
        from jobs.services.extraction_contract import skill_candidates
        self.assertEqual(skill_candidates('Enjoy your day.'), [])

    def test_customer_tool_mention_is_kept_for_model_to_disambiguate(self):
        from jobs.services.extraction_contract import skill_candidates
        self.assertIn('python', skill_candidates('Our customers use Python. No candidate tools required.'))


class ExtractionImportFlowTests(TestCase):
    def test_extract_persist_filter_schema_and_reuse_without_second_call(self):
        from asgiref.sync import async_to_sync
        from django.http import QueryDict
        from jobs.models import Category
        from jobs.services.importers.common import _upsert_job
        from jobs.services.job_facts import source_description
        from jobs.services.job_service import JobService
        from jobs.services.job_quality import job_posting_schema_json
        caches['job_extraction'].clear()
        Category.objects.create(name='Climate & Environment', slug='climate-environment')
        payload = {
            'title': 'Software Engineer', 'organization_name': 'Flow Test Org',
            'description': 'Our mission is to prevent climate change. Full-time. Candidates must reside in Germany. Salary USD 80000 to 100000 per year. '
                           'We build reliable services for researchers and work closely with partners on practical solutions to environmental challenges.',
            'requirements': 'Required Python. Minimum 4 years of experience required.',
            'location': 'Remote (Germany)', 'source': 'climatebase', 'external_id': 'facts-flow-e2e',
            'application_url': 'https://example.org/jobs/facts-flow',
        }
        desc = source_description(payload)
        spans = evidence_source(payload['title'], payload['organization_name'], desc)
        def ref(phrase):
            return next(key for key, text in spans.items() if phrase in text)
        raw = empty()
        raw.update(impact_area={'value': 'climate-environment', 'evidence': ref('mission')},
                   job_type={'value': 'full-time', 'evidence': ref('Full-time')},
                   experience_level={'value': 'mid', 'evidence': ref('4 years')},
                   required_skills=[{'value': 'python', 'evidence': ref('Required Python')}],
                   geography={'scope': 'restricted', 'countries': ['DE'], 'excluded_countries': [], 'regions': [], 'restrictions': [], 'evidence': [ref('must reside')]},
                   salary={'min': 80000, 'max': 100000, 'currency': 'USD', 'period': 'year', 'evidence': ref('Salary')})
        instance = parser()
        create = AsyncMock(return_value=response(raw))
        instance.client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
        enriched = async_to_sync(instance._parse_and_enrich)(payload, payload['title'], payload['organization_name'], desc)
        with patch('jobs.services.embedding_service.embed_job', return_value=None):
            job, _ = _upsert_job(enriched)
        self.assertEqual(job.skills, ['python'])
        self.assertEqual(job.country, 'Germany')
        self.assertEqual(job.salary_min, 80000)
        self.assertEqual(job.experience_level, 'mid')
        self.assertEqual(job.description, payload['description'])
        self.assertEqual(job.raw_data['job_facts']['version'], VERSION)
        filters = QueryDict(mutable=True)
        filters.setlist('country', ['Germany'])
        filters.setlist('skill', ['python'])
        filters.setlist('experience', ['mid'])
        self.assertIn(job.pk, JobService.get_filtered_jobs(filters).values_list('pk', flat=True))
        schema = json.loads(job_posting_schema_json(job, 'https://remoteimpact.org'))
        self.assertEqual(schema['baseSalary']['currency'], 'USD')
        self.assertEqual(schema['applicantLocationRequirements']['name'], 'Germany')
        again = async_to_sync(instance._parse_and_enrich)(payload, payload['title'], payload['organization_name'], desc)
        self.assertEqual(create.await_count, 1)
        self.assertEqual(again['skills'], ['python'])


class GeographyConflictTests(SimpleTestCase):
    def test_conflicting_requirements_remain_unknown_unresolved_and_uncacheable(self):
        text = 'Applicants must reside in France. Only residents of Germany are eligible.'
        raw = empty()
        raw['geography'] = {'scope': 'restricted', 'countries': ['FR', 'DE'], 'excluded_countries': [], 'regions': [], 'restrictions': [], 'evidence': ['S2', 'S3']}
        create = AsyncMock(return_value=response(raw))
        instance = parser()
        instance.client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
        with patch('jobs.services.extraction_cache.get_facts', new=AsyncMock(return_value=None)), patch('jobs.services.extraction_cache.set_facts', new=AsyncMock()) as write:
            result = asyncio.run(instance.parse_single('Analyst', 'Org', text))
        self.assertEqual(result['job_facts']['geography']['countries'], [])
        self.assertEqual(result['job_facts']['geography']['scope'], 'unknown')
        self.assertEqual(result['job_facts']['unresolved_fields'], ['geography'])
        self.assertEqual(write.await_count, 0)
        self.assertEqual(create.await_count, 1)
        self.assertNotIn('country', result)

    def test_legal_alternatives_and_nested_country_sets_are_not_conflicts(self):
        from jobs.services.extraction_geography import conflicting_residency
        for text in (
            'Applicants must reside in France or Germany.',
            'Applicants must reside in France or Germany. Candidates must be based in Germany.',
            'Our offices are in France. Applicants must reside in Germany.',
            'Applicants must reside in France. Alternatively, candidates must reside in Germany.',
            'Applicants must reside in Europe. Only residents of Germany are eligible.',
        ):
            self.assertEqual(conflicting_residency(text), [], text)

    def test_conflict_cannot_be_hidden_by_single_quote_or_null_response(self):
        text = 'Candidates must live in Canada. Applicants must be based in Australia.'
        for geo in (None, {'scope': 'restricted', 'countries': ['CA'], 'excluded_countries': [], 'regions': [], 'restrictions': [], 'evidence': 'Candidates must live in Canada'}):
            result = validate({'geography': geo}, 'Analyst', 'Org', text)
            self.assertEqual(result['blocked_fields'], ['geography'])
            self.assertEqual(legacy_fields(result)['job_facts']['geography']['countries'], [])


class RolloutGateTests(SimpleTestCase):
    def test_normal_imports_use_legacy_parser_unless_explicitly_enabled(self):
        from django.test import override_settings
        with override_settings(JOB_EXTRACTION_OPTIMIZED_ENABLED=False):
            with patch('jobs.services.legacy_job_parser.JobParser') as legacy:
                instance = JobParser()
                self.assertIs(instance._legacy_parser, legacy.return_value)
        with override_settings(DEEPSEEK_API_KEY='test', JOB_EXTRACTION_OPTIMIZED_ENABLED=False):
            with patch('openai.AsyncOpenAI'):
                instance = JobParser(optimized=True)
                self.assertFalse(hasattr(instance, '_legacy_parser'))



class CanaryCommandTests(TestCase):
    def prepared(self):
        from django.conf import settings
        from jobs.services.job_facts import source_description
        payload = {'title': 'Software Engineer', 'organization_name': 'Canary Org', 'source': 'climatebase',
                   'external_id': 'canary-test', 'application_url': 'https://example.org/jobs/canary',
                   'location': 'Remote', 'country': 'Germany', 'job_type': 'full-time', 'experience_level': 'mid',
                   'category_slug': 'climate-environment', 'skills': ['python'],
                   'description': 'Our mission is to prevent climate change. Full-time. Applicants must reside in Germany. Required Python. Minimum 4 years of experience required. '
                                  'We build reliable software for researchers and work with partners to deliver practical environmental solutions.'}
        raw = empty()
        raw.update(impact_area={'value': 'climate-environment', 'evidence': 'Our mission is to prevent climate change'},
                   experience_level={'value': 'mid', 'evidence': 'Minimum 4 years of experience required'},
                   job_type={'value': 'full-time', 'evidence': 'Full-time'},
                   required_skills=[{'value': 'python', 'evidence': 'Required Python'}])
        facts = validate(raw, payload['title'], payload['organization_name'], source_description(payload))
        facts.update(model=settings.DEEPSEEK_MODEL, contract_hash=CONTRACT_HASH)
        facts['cache_key'] = extraction_cache.cache_key(VERSION + CONTRACT_HASH, settings.DEEPSEEK_MODEL, 'https://api.deepseek.com', facts['source_hash'])
        payload['raw_data'] = {'job_facts': facts}
        return {'phase': 'prepared', 'items': [{'payload': payload, 'eligible': True}]}

    def test_stage_is_inactive_and_repeat_apply_never_updates_existing_job(self):
        from jobs.management.commands.extraction_canary import Command
        from jobs.models import Job
        command = Command()
        prepared = self.prepared()
        with patch('jobs.services.embedding_service.get_model', side_effect=AssertionError('No model on server')):
            first = command.apply(prepared, 3)
            second = command.apply(prepared, 3)
        self.assertEqual(len(first['jobs']), 1)
        self.assertEqual(second['jobs'], [])
        job = Job.objects.get(pk=first['jobs'][0]['id'])
        self.assertFalse(job.is_active)
        self.assertIsNone(job.embedding)
        facts = job.raw_data['job_facts']
        self.assertEqual(caches['job_extraction'].get(facts['cache_key']), facts)
        self.assertEqual(job.description, prepared['items'][0]['payload']['description'])

    def test_publish_checks_model_fingerprint_and_vector_without_loading_ml(self):
        from django.conf import settings
        from django.core.management.base import CommandError
        from jobs.management.commands.extraction_canary import Command
        from jobs.models import Job
        command = Command()
        staged = command.apply(self.prepared(), 3)
        row = dict(staged['jobs'][0], embedding=[1.0] + [0.0] * 383)
        with self.assertRaises(CommandError):
            command.publish({'model': 'wrong-model', 'jobs': [row]}, 3)
        with self.assertRaises(CommandError):
            command.publish({'model': settings.EMBEDDING_MODEL, 'jobs': [dict(row, fingerprint='changed')]}, 3)
        with self.assertRaises(CommandError):
            command.publish({'model': settings.EMBEDDING_MODEL, 'jobs': [dict(row, embedding=[float('nan')] * 384)]}, 3)
        self.assertFalse(Job.objects.get(pk=row['id']).is_active)
        with patch('jobs.services.embedding_service.get_model', side_effect=AssertionError('No model on server')):
            result = command.publish({'model': settings.EMBEDDING_MODEL, 'jobs': [row]}, 3)
        self.assertEqual(len(result['jobs']), 1)
        self.assertTrue(Job.objects.get(pk=row['id']).is_active)

    def test_create_only_upsert_refuses_existing_record(self):
        from django.db import transaction
        from jobs.management.commands.extraction_canary import Command
        from jobs.services.importers.common import _upsert_job, ExistingJobError
        from jobs.models import Job
        prepared = self.prepared()
        result = Command().apply(prepared, 3)
        payload = dict(prepared['items'][0]['payload'], description='Changed source text.')
        with self.assertRaises(ExistingJobError), transaction.atomic():
            _upsert_job(payload, create_only=True)
        self.assertNotEqual(Job.objects.get(pk=result['jobs'][0]['id']).description, 'Changed source text.')


class CanaryRefreshTests(TestCase):
    prepared = CanaryCommandTests.prepared

    def test_city_variant_preserves_reviewed_fields_and_quarantine(self):
        from jobs.management.commands.extraction_canary import Command
        from jobs.services.importers.common import _upsert_job, _mark_job_seen
        from jobs.models import Job
        prepared = self.prepared()
        original = prepared['items'][0]['payload']
        # Use a city label with the same normalized single-country location.
        original['location'] = 'Remote (Berlin, Germany)'
        from jobs.services.job_facts import source_description
        facts = original['raw_data']['job_facts']
        facts['source_hash'] = source_hash(original['title'], original['organization_name'], source_description(original))
        staged = Command().apply(prepared, 3)
        job = Job.objects.get(pk=staged['jobs'][0]['id'])
        refresh = dict(original, location='Remote (Munich, Germany)', external_id=original['external_id'])
        refresh['raw_data'] = {'experience_levels': ['Mid Level']}
        with self.captureOnCommitCallbacks(execute=True), patch('jobs.services.embedding_service.get_model', side_effect=AssertionError('No model needed')):
            refreshed, _ = _upsert_job(refresh)
        self.assertFalse(refreshed.is_active)
        self.assertEqual(refreshed.skills, ['python'])
        self.assertEqual(refreshed.raw_data['extraction_canary']['phase'], 'staged')
        self.assertIn('job_facts', refreshed.raw_data)
        self.assertEqual(refreshed.raw_data['job_facts']['source_hash'], source_hash(refresh['title'], refresh['organization_name'], source_description(refresh)))
        _mark_job_seen(refresh['source'], refresh['external_id'], refresh['raw_data'])
        refreshed.refresh_from_db()
        self.assertFalse(refreshed.is_active)

    def test_country_or_requirement_changes_do_not_reuse_facts(self):
        from jobs.management.commands.extraction_canary import Command
        from jobs.services.extraction_refresh import retained_facts
        from jobs.models import Job
        prepared = self.prepared();staged = Command().apply(prepared, 3)
        job = Job.objects.select_related('organization').get(pk=staged['jobs'][0]['id'])
        original = prepared['items'][0]['payload']
        self.assertIsNone(retained_facts(job, dict(original, location='Remote (France)')))
        self.assertIsNone(retained_facts(job, dict(original, requirements='Required Java instead.')))

    def test_changed_source_invalidates_facts_without_publishing_staged_job(self):
        from jobs.management.commands.extraction_canary import Command
        from jobs.services.importers.common import _upsert_job, _mark_job_seen
        from jobs.models import Job
        prepared = self.prepared();staged = Command().apply(prepared, 3)
        refresh = dict(prepared['items'][0]['payload'], description='Changed requirements and responsibilities. ' * 12)
        refresh['raw_data'] = {}
        job, _ = _upsert_job(refresh)
        self.assertFalse(job.is_active)
        self.assertNotIn('job_facts', job.raw_data)
        self.assertTrue(job.raw_data['extraction_canary']['needs_review'])
        _mark_job_seen(refresh['source'], refresh['external_id'])
        job.refresh_from_db()
        self.assertFalse(job.is_active)


class ExactSkillFilterTests(TestCase):
    def test_selected_skill_matches_whole_json_value(self):
        from django.http import QueryDict
        from jobs.models import Job, Organization
        from jobs.services.job_service import JobService
        org = Organization.objects.create(name='Skills Filter Org')
        wanted = Job.objects.create(title='SQL Analyst', slug='sql-exact', organization=org, skills=['sql'])
        Job.objects.create(title='NoSQL Analyst', slug='nosql-exact', organization=org, skills=['nosql'])
        Job.objects.create(title='SQL Server Analyst', slug='sql-server-exact', organization=org, skills=['sql server'])
        result = JobService.get_filtered_jobs(QueryDict('skill=sql'))
        self.assertEqual(set(result.values_list('pk', flat=True)), {wanted.pk})
