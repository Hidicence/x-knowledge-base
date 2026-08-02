from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "xkb_job_runner.py"
spec = importlib.util.spec_from_file_location("xkb_job_runner", SCRIPT)
assert spec and spec.loader
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class XKBJobRunnerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.worker = Path(self.tmp.name) / "worker.py"

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def write_worker(self, code: str) -> None:
        self.worker.write_text(code, encoding="utf-8")

    def test_success_reports_queued_running_succeeded_and_preserves_args(self) -> None:
        self.write_worker(
            "import pathlib, sys\n"
            "pathlib.Path(sys.argv[1]).write_text(' '.join(sys.argv[2:]))\n"
        )
        output = Path(self.tmp.name) / "output.txt"
        events = []

        with patch.object(module, "post_event", side_effect=lambda _url, payload: events.append(payload) or True):
            result = module.main([
                "--worker", str(self.worker), "--service-url", "http://fixture",
                "--job-id", "job-success", "--", str(output), "alpha", "beta",
            ])

        self.assertEqual(result, 0)
        self.assertEqual([event["status"] for event in events], ["queued", "running", "succeeded"])
        self.assertEqual(output.read_text(encoding="utf-8"), "alpha beta")
        self.assertEqual(events[-1]["output_ref"], "worker_exit:0")

    def test_failure_reports_failed_and_is_retryable(self) -> None:
        self.write_worker("raise SystemExit(7)\n")
        events = []

        with patch.object(module, "post_event", side_effect=lambda _url, payload: events.append(payload) or True):
            result = module.main([
                "--worker", str(self.worker), "--service-url", "http://fixture",
                "--job-id", "job-failure",
            ])

        self.assertEqual(result, 7)
        self.assertEqual([event["status"] for event in events], ["queued", "running", "failed"])
        self.assertTrue(events[-1]["retryable"])
        self.assertIn("code 7", events[-1]["error"])

    def test_dry_run_does_not_execute_worker_but_reports_terminal_success(self) -> None:
        self.write_worker("raise SystemExit(99)\n")
        events = []

        with patch.object(module, "post_event", side_effect=lambda _url, payload: events.append(payload) or True):
            result = module.main([
                "--worker", str(self.worker), "--service-url", "http://fixture",
                "--job-id", "job-dry", "--dry-run",
            ])

        self.assertEqual(result, 0)
        self.assertEqual([event["status"] for event in events], ["queued", "succeeded"])
        self.assertEqual(events[-1]["output_ref"], "dry-run")


if __name__ == "__main__":
    unittest.main()
