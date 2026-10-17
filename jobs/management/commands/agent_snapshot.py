import json
from django.core.management.base import BaseCommand
from jobs.services.agent_snapshot import build_snapshot


class Command(BaseCommand):
    help = 'Print an aggregate-only catalogue snapshot as JSON; no data writes or messages.'

    def handle(self, *args, **options):
        self.stdout.write(json.dumps(build_snapshot(), ensure_ascii=False, indent=2))
