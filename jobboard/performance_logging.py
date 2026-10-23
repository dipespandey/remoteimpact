"""Stdlib-only privacy-safe rotating JSONL logging.

This module is safe to import while Django is configuring logging, before its
application registry is ready. Performance events are JSON-serializable dicts
attached to ``LogRecord.performance`` and contain no request objects.
"""
import json
import logging
import os


class ConcurrentJSONLHandler(logging.Handler):
    """Bounded JSONL handler safe across multiple web and worker processes.

    An OS advisory lock protects both rotation and append. The base log, rotated
    logs, and lock file are owner-only; the parent directory must already exist.
    """

    def __init__(self, filename, max_bytes=10 * 1024 * 1024, backup_count=3):
        super().__init__()
        self.filename = os.fspath(filename)
        self.max_bytes = max(1024, int(max_bytes))
        self.backup_count = max(0, int(backup_count))
        self.lock_filename = self.filename + ".lock"

    def emit(self, record):
        try:
            payload = getattr(record, "performance", None)
            if not isinstance(payload, dict):
                return
            line = (json.dumps(payload, separators=(",", ":"), sort_keys=True) + "\n").encode("utf-8")
            lock_fd = os.open(self.lock_filename, os.O_CREAT | os.O_RDWR, 0o600)
            try:
                os.chmod(self.lock_filename, 0o600)
                import fcntl
                fcntl.flock(lock_fd, fcntl.LOCK_EX)
                try:
                    self._rotate_if_needed(len(line))
                    fd = os.open(self.filename, os.O_CREAT | os.O_APPEND | os.O_WRONLY, 0o600)
                    try:
                        os.fchmod(fd, 0o600)
                        with os.fdopen(fd, "ab", closefd=False) as stream:
                            stream.write(line)
                    finally:
                        os.close(fd)
                finally:
                    fcntl.flock(lock_fd, fcntl.LOCK_UN)
            finally:
                os.close(lock_fd)
        except Exception:
            self.handleError(record)

    def _rotate_if_needed(self, incoming):
        try:
            size = os.stat(self.filename).st_size
        except FileNotFoundError:
            return
        if size + incoming <= self.max_bytes:
            return
        if self.backup_count:
            oldest = "{}.{}".format(self.filename, self.backup_count)
            try:
                os.unlink(oldest)
            except FileNotFoundError:
                pass
            for index in range(self.backup_count - 1, 0, -1):
                source = "{}.{}".format(self.filename, index)
                target = "{}.{}".format(self.filename, index + 1)
                try:
                    os.replace(source, target)
                except FileNotFoundError:
                    pass
            os.replace(self.filename, self.filename + ".1")
        else:
            os.unlink(self.filename)


class PerformanceJSONFormatter(logging.Formatter):
    """Serialize only the pre-sanitized performance event attached to a record."""

    def format(self, record):
        payload = getattr(record, "performance", None)
        if not isinstance(payload, dict):
            return "{}"
        return json.dumps(payload, separators=(",", ":"), sort_keys=True)
