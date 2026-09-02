from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _isolation import clean_env

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
TOOLS = ROOT / "tools"
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(TOOLS))
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
            with mock.patch.dict(os.environ, {**clean_env(), 
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

    def test_missing_env_file_fails_before_child_execution(self):
        with mock.patch.dict(os.environ, {**clean_env(), "XKB_ENV_FILE": "/missing/runtime.env"}, clear=True):
            with self.assertRaisesRegex(FileNotFoundError, "XKB env file"):
                runtime_env()


if __name__ == "__main__":
    unittest.main()
