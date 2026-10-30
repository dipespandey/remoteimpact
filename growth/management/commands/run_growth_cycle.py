from django.core.management.base import BaseCommand
from growth.acquisition import run_cycle


class Command(BaseCommand):
    help = 'Run bounded acquisition research, drafting and measurement. Never sends or publishes.'

    def add_arguments(self, parser):
        parser.add_argument('--due', action='store_true', help='Only run daily or after a staff refresh request.')

    def handle(self, *args, **options):
        run = run_cycle(force=not options['due'])
        self.stdout.write(f'Growth: {run.status}; {run.summary}' if run else 'Growth: paused, already running, or not due.')
