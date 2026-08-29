from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "xbrain_recall.py"


class XbrainRecallRuntimeTests(unittest.TestCase):
    def make_fixture(self, root: Path) -> tuple[Path, Path]:
        gbrain = root / "gbrain"
        (gbrain / "src").mkdir(parents=True)
        (gbrain / "src" / "cli.ts").write_text("// fixture\n", encoding="utf-8")
        fake_bin = root / "bin"
        fake_bin.mkdir()
        bun = fake_bin / "bun"
        bun.write_text(
            "#!/usr/bin/env python3\n"
            "import json, os, sys\n"
            "if os.environ.get('FIXTURE_BUN_FAILURE'):\n"
            "    print(os.environ.get('GEMINI_API_KEY', ''), file=sys.stderr)\n"
            "    raise SystemExit(7)\n"
            "key = os.environ.get('GEMINI_API_KEY', '')\n"
            "source = {'process-placeholder': 'process', 'explicit-placeholder': 'explicit', 'inherited-placeholder': 'inherited'}.get(key, 'none')\n"
            "print(json.dumps([{'slug': source, 'chunk_text': 'fixture', 'score': 1.0}]))\n",
            encoding="utf-8",
        )
        bun.chmod(0o755)
        return gbrain, fake_bin

    def run_cli(
        self,
        root: Path,
        *,
        env_file: Path | None = None,
        explicit_env_file: Path | None = None,
        process_key: str | None = None,
        extra_env: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        gbrain, fake_bin = self.make_fixture(root)
        env = {
            "HOME": str(root / "isolated-home"),
            "PATH": str(fake_bin) + os.pathsep + os.environ.get("PATH", ""),
            "GBRAIN_DIR": str(gbrain),
            "PYTHONPATH": "",
        }
        if env_file is not None:
            env["XKB_ENV_FILE"] = str(env_file)
        if process_key is not None:
            env["GEMINI_API_KEY"] = process_key
        if extra_env:
            env.update(extra_env)
        command = [sys.executable, str(SCRIPT), "fixture query", "--json"]
        if explicit_env_file is not None:
            command.extend(["--env-file", str(explicit_env_file)])
        return subprocess.run(command, cwd=ROOT, env=env, capture_output=True, text=True, check=False)

    def test_process_environment_takes_precedence_over_xkb_env_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            env_file = root / "runtime.env"
            env_file.write_text("GEMINI_API_KEY=file-placeholder\n", encoding="utf-8")
            result = self.run_cli(root, env_file=env_file, process_key="process-placeholder")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout)[0]["slug"], "process")

    def test_explicit_env_file_is_used_instead_of_xkb_env_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            inherited = root / "inherited.env"
            explicit = root / "explicit.env"
            inherited.write_text("GEMINI_API_KEY=inherited-placeholder\n", encoding="utf-8")
            explicit.write_text("GEMINI_API_KEY=explicit-placeholder\n", encoding="utf-8")
            result = self.run_cli(root, env_file=inherited, explicit_env_file=explicit)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout)[0]["slug"], "explicit")

    def test_explicit_env_file_still_yields_to_process_environment(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            explicit = root / "explicit.env"
            explicit.write_text("GEMINI_API_KEY=explicit-placeholder\n", encoding="utf-8")
            result = self.run_cli(root, explicit_env_file=explicit, process_key="process-placeholder")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout)[0]["slug"], "process")

    def test_missing_credential_is_actionable_without_host_fallback_or_secret_leak(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            secret = "host-secret-placeholder"
            result = self.run_cli(
                root,
                extra_env={"OPENCLAW_JSON": secret},
            )
            combined = result.stdout + result.stderr
            self.assertEqual(result.returncode, 1)
            self.assertIn("GEMINI_API_KEY", result.stderr)
            self.assertIn("process environment", result.stderr)
            self.assertFalse(secret in combined)
            self.assertFalse((root / "isolated-home" / ".openclaw").exists())

    def test_active_recall_source_has_no_private_config_fallback(self) -> None:
        source = SCRIPT.read_text(encoding="utf-8")
        for forbidden in ("OPENCLAW_JSON", "openclaw.json", "$HOME/.openclaw", "/root/.openclaw"):
            self.assertNotIn(forbidden, source)

    def test_failed_backend_does_not_echo_credential(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            secret = "fixture-secret-that-must-not-leak"
            result = self.run_cli(
                root,
                process_key=secret,
                extra_env={"FIXTURE_BUN_FAILURE": "1"},
            )
            combined = result.stdout + result.stderr
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout), [])
            self.assertNotIn(secret, combined)

if __name__ == "__main__":
    unittest.main()
