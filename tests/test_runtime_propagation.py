from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
TOOLS = ROOT / "tools"
import sys
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(TOOLS))
import xkb_job_runner
from runtime_config import runtime_env


class RuntimePropagationTests(unittest.TestCase):
    def test_process_env_beats_explicit_env_file_and_child_receives_file_values(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            env_file = root / "runtime.env"
            env_file.write_text(
                "XKB_TEST_PROVIDER=file-provider\n"
                "XKB_TEST_FILE_ONLY=file-only\n"
                "XKB_TEST_SECRET=fixture-secret\n",
                encoding="utf-8",
            )
            child = root / "child.py"
            child.write_text(
                "import os\n"
                "print('child.provider=' + os.environ.get('XKB_TEST_PROVIDER', ''))\n"
                "print('child.cwd=' + os.getcwd())\n"
                "print('child.file=' + os.environ.get('XKB_TEST_FILE_ONLY', ''))\n"
                "print('child.secret_present=' + str(bool(os.environ.get('XKB_TEST_SECRET'))).lower())\n",
                encoding="utf-8",
            )
            with mock.patch.dict(os.environ, {
                "XKB_ENV_FILE": str(env_file),
                "XKB_TEST_PROVIDER": "process-provider",
            }, clear=True):
                settings = runtime_env()
                self.assertEqual(settings["XKB_TEST_PROVIDER"], "process-provider")
                proc = subprocess.run(
                    [sys.executable, str(child)],
                    cwd=root,
                    env=settings,
                    capture_output=True, text=True, check=False,
                )
            self.assertEqual(proc.returncode, 0)
            self.assertIn("child.provider=process-provider", proc.stdout)
            self.assertIn("child.cwd=" + str(root), proc.stdout)
            self.assertIn("child.file=file-only", proc.stdout)
            self.assertIn("child.secret_present=true", proc.stdout)

    def test_job_runner_propagates_runtime_environment_to_worker(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            worker = root / "worker.py"
            worker.write_text(
                "import os, sys\n"
                "assert os.environ['XKB_RUNTIME_SENTINEL'] == 'from-process'\n"
                "raise SystemExit(0)\n",
                encoding="utf-8",
            )
            with mock.patch.object(xkb_job_runner, "post_event", return_value=True):
                with mock.patch.dict(os.environ, {"XKB_RUNTIME_SENTINEL": "from-process"}, clear=True):
                    self.assertEqual(xkb_job_runner.main([
                        "--service-url", "", "--worker", str(worker), "--", "--fixture-arg"
                    ]), 0)

    def test_job_runner_explicit_env_file_reaches_worker(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            env_file = root / "runtime.env"
            env_file.write_text("XKB_RUNTIME_SENTINEL=from-file\n", encoding="utf-8")
            worker = root / "worker.py"
            worker.write_text(
                "import os\n"
                "raise SystemExit(0 if os.environ.get('XKB_RUNTIME_SENTINEL') == 'from-file' else 9)\n",
                encoding="utf-8",
            )
            with mock.patch.object(xkb_job_runner, "post_event", return_value=True):
                with mock.patch.dict(os.environ, {}, clear=True):
                    self.assertEqual(xkb_job_runner.main([
                        "--service-url", "", "--worker", str(worker),
                        "--env-file", str(env_file)
                    ]), 0)

    def test_job_runner_preserves_worker_exit_code(self):
        with tempfile.TemporaryDirectory() as tmp:
            worker = Path(tmp) / "worker.py"
            worker.write_text("raise SystemExit(7)\n", encoding="utf-8")
            with mock.patch.object(xkb_job_runner, "post_event", return_value=True):
                self.assertEqual(xkb_job_runner.main(["--service-url", "", "--worker", str(worker)]), 7)

    def test_missing_env_file_fails_before_child_execution(self):
        with mock.patch.dict(os.environ, {"XKB_ENV_FILE": "/missing/runtime.env"}, clear=True):
            with self.assertRaisesRegex(FileNotFoundError, "XKB env file"):
                runtime_env()


if __name__ == "__main__":
    unittest.main()
