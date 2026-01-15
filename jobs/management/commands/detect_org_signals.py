from django.core.management.base import BaseCommand

from jobs.models import Organization
from jobs.services.org_signals_service import OrgSignalsService


class Command(BaseCommand):
    help = 'Detect and update signals (80K, GiveWell, B Corp) for all organizations'

    def add_arguments(self, parser):
        parser.add_argument('--force', action='store_true',
                            help='Re-detect even if signals already set')

    def handle(self, *args, **options):
        qs = Organization.objects.all()

        if not options['force']:
            # Only check orgs without any signals detected
            qs = qs.filter(
                is_80k_recommended=False,
                is_givewell_top_charity=False,
                is_bcorp_certified=False,
            )

        total = qs.count()
        if total == 0:
            self.stdout.write('No organizations need signal detection.')
            return

        self.stdout.write(f'Detecting signals for {total} organizations...')

        updated_count = 0
        for i, org in enumerate(qs.iterator()):
            signals = OrgSignalsService.detect_all_signals(org)

            # Check if any signal was found
            has_signal = any([
                signals.get('is_80k_recommended'),
                signals.get('is_givewell_top_charity'),
                signals.get('is_bcorp_certified'),
            ])

            if has_signal:
                OrgSignalsService.update_org_signals(org)
                updated_count += 1
                self.stdout.write(f'  Found signals for: {org.name}')

            if (i + 1) % 50 == 0:
                self.stdout.write(f'  Processed {i + 1}/{total}')

        # Also run batch mark for 80K orgs
        batch_count = OrgSignalsService.mark_80k_orgs_from_imports()

        self.stdout.write(self.style.SUCCESS(
            f'Done. Updated {updated_count} orgs, batch-marked {batch_count} as 80K.'
        ))
