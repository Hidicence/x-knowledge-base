from __future__ import annotations

import importlib
import io
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))


class QueueSyncTests(unittest.TestCase):
    """The worker must not depend on someone else having filled the queue.

    Fetching writes a bookmark to the inbox; a separate step queues it. When
    the pipeline holding both was retired, the scheduler job that runs this
    worker did not inherit the queue step, so the worker ran nightly, found
    nothing, reported success, and bookmarks accumulated in the inbox at
    about eight a day with every check green.
    """

    @classmethod
    def setUpClass(cls) -> None:
        cls.worker = importlib.import_module("run_bookmark_worker")

    def _main_with(self, argv: list[str]):
        calls: list[bool] = []
        with mock.patch.object(self.worker, "_sync_queue", side_effect=lambda: calls.append(True) or True), \
             mock.patch.object(self.worker, "_load_queue", return_value={"items": []}), \
             mock.patch.object(sys, "argv", ["run_bookmark_worker.py", *argv]), \
             redirect_stdout(io.StringIO()):
            self.worker.main()
        return calls

    def test_a_real_run_refreshes_the_queue_first(self) -> None:
        self.assertEqual(len(self._main_with(["--limit", "1"])), 1)

    def test_dry_run_does_not_write(self) -> None:
        """--dry-run promises the queue is not mutated; syncing writes it."""
        self.assertEqual(self._main_with(["--dry-run", "--limit", "1"]), [])

    def test_local_only_implies_dry_run(self) -> None:
        self.assertEqual(self._main_with(["--local-only", "--limit", "1"]), [])

    def test_a_failing_sync_does_not_stop_the_worker(self) -> None:
        """A stale queue is worse than no refresh, but not worth aborting on."""
        with mock.patch.object(self.worker, "_sync_queue", return_value=False), \
             mock.patch.object(self.worker, "_load_queue", return_value={"items": []}), \
             mock.patch.object(sys, "argv", ["run_bookmark_worker.py", "--limit", "1"]), \
             redirect_stdout(io.StringIO()):
            self.assertEqual(self.worker.main(), 0)


if __name__ == "__main__":
    unittest.main()
