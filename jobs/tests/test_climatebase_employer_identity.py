from io import StringIO

from django.core.management import call_command
from django.test import SimpleTestCase, TestCase

from jobs.models import Job, Organization
from jobs.services.importers.climatebase import employer_identity


class ClimatebaseEmployerIdentityTests(SimpleTestCase):
    def test_explicit_hitachi_group_company_is_not_attributed_to_energy(self):
        examples = [
            ('<p><b>Company Name:</b></p>HITACHI GLOBAL AIR POWER US, LLC', 'Hitachi Global Air Power'),
            ('<p><b>Company Name:</b></p>HITACHI HIGH-TECH AMERICA, INC.', 'Hitachi High-Tech'),
            ('<h1>Our Company</h1><p>We’re Hitachi Vantara, the data foundation.</p>', 'Hitachi Vantara'),
            ('<h1>Our Company</h1><p>We’re Hitachi Digital Services, a technology company.</p>', 'Hitachi Digital Services'),
        ]
        for description, expected in examples:
            with self.subTest(expected=expected):
                self.assertEqual(employer_identity('HITACHI ENERGY', description), expected)

    def test_no_identity_inference_from_generic_or_other_company_copy(self):
        self.assertEqual(employer_identity('HITACHI ENERGY', '<p>Work across the Hitachi group.</p>'), 'HITACHI ENERGY')
        self.assertEqual(employer_identity('HITACHI ENERGY', '<b>Company Name:</b> HITACHI ENERGY USA INC'), 'HITACHI ENERGY')
        self.assertEqual(employer_identity('Other Company', 'We’re Hitachi Vantara'), 'Other Company')


class HitachiIdentityRepairTests(TestCase):
    def test_dry_run_then_reassigns_only_explicitly_named_company(self):
        energy = Organization.objects.create(name='HITACHI ENERGY', slug='hitachi-energy')
        other = Job.objects.create(
            organization=energy, title='Security engineer', slug='security-engineer-hitachi',
            source=Job.Source.CLIMATEBASE, external_id='one',
            description='<p>We’re Hitachi Vantara, the data foundation.</p>',
        )
        energy_job = Job.objects.create(
            organization=energy, title='Grid engineer', slug='grid-engineer-hitachi',
            source=Job.Source.CLIMATEBASE, external_id='two',
            description='<b>Company Name:</b><p>HITACHI ENERGY USA INC</p>',
        )
        output = StringIO()
        call_command('repair_hitachi_company_identity', stdout=output)
        other.refresh_from_db()
        self.assertEqual(other.organization, energy)
        self.assertIn('Hitachi Vantara: 1', output.getvalue())
        call_command('repair_hitachi_company_identity', '--apply', stdout=StringIO())
        other.refresh_from_db()
        energy_job.refresh_from_db()
        self.assertEqual(other.organization.name, 'Hitachi Vantara')
        self.assertEqual(energy_job.organization, energy)
