"""Run the durable Scout preview queue as a dedicated process."""

import json
import os
import signal
import time
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import connection

from jobs.models import ScoutPreviewTask
from jobs.services.scout_tasks import claim_task, execute_claimed_task, prune_tasks


def release_is_active():
    """Fail closed during release transitions when the host state file is configured."""
    state_file = os.getenv("SCOUT_PREVIEW_ACTIVE_STATE_FILE", "").strip()
    if not state_file:
        return True
    release_id = os.getenv("RELEASE_ID", "").strip()
    if not release_id:
        return False
    try:
        state = json.loads(Path(state_file).read_text(encoding="utf-8"))
        return state.get("active", {}).get("release") == release_id
    except (OSError, ValueError, AttributeError, TypeError):
        return False


class BaseCommandWithStop(BaseCommand):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.stop_requested = False

    def _request_stop(self, *_args):
        self.stop_requested = True


class Command(BaseCommandWithStop):
    help = "Process asynchronous Job Scout preview tasks."

    def add_arguments(self, parser):
        parser.add_argument("--check", action="store_true", help="Check queue DB and release gate without claiming work.")
        parser.add_argument("--idle-exit-seconds", type=int, default=0, help="Exit after this many idle seconds (0 runs continuously).")
        parser.add_argument("--poll-seconds", type=float, default=1.0)

    def handle(self, *args, **options):
        # Touch the table during readiness checks, and fail startup if a release
        # reached traffic before its additive queue migration was applied.
        try:
            ScoutPreviewTask.objects.only("pk").first()
        except Exception as exc:  # noqa: BLE001 - management command should explain startup failure
            raise CommandError(f"Scout preview queue is unavailable: {type(exc).__name__}") from exc
        if options["check"]:
            self.stdout.write(self.style.SUCCESS("Scout preview worker checks passed."))
            return

        previous_term = signal.signal(signal.SIGTERM, self._request_stop)
        previous_int = signal.signal(signal.SIGINT, self._request_stop)
        idle_since = time.monotonic()
        last_prune = 0.0
        try:
            while not self.stop_requested:
                # Old and candidate containers can overlap. Only the release
                # currently serving traffic may lease additional work.
                if not release_is_active():
                    time.sleep(max(0.1, options["poll_seconds"]))
                    continue
                now = time.monotonic()
                if now - last_prune >= 300:
                    prune_tasks()
                    last_prune = now
                claim = claim_task()
                if claim is None:
                    if options["idle_exit_seconds"] and now - idle_since >= options["idle_exit_seconds"]:
                        break
                    time.sleep(max(0.1, options["poll_seconds"]))
                    continue
                idle_since = time.monotonic()
                task_id, lease_token = claim
                execute_claimed_task(task_id, lease_token)
        finally:
            signal.signal(signal.SIGTERM, previous_term)
            signal.signal(signal.SIGINT, previous_int)
            connection.close()
