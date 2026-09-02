from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "setup_xbrain.sh"

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _isolation import isolated_env, needs_posix_exec


class SetupXbrainContractTests(unittest.TestCase):
    def run_setup(
        self,
        root: Path,
        *,
        env_file: Path | None = None,
        process_key: str | None = "process-placeholder",
        args: list[str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        if os.name == "nt":
            # fixture 用 shebang 腳本假裝成 bun / git。Windows 沒有那個 exec
            # 語意，跑不起來的是假裝的那一層，不是被測的契約——所以這裡明講
            # 跳過，不要讓它以「紅燈」的樣子存在，久了就沒人看了。
            self.skipTest("需要 POSIX 的 shebang 執行語意；這條契約在 VPS / CI 上驗")
        fake_bin = root / "bin"
        fake_bin.mkdir()
        (fake_bin / "bun").write_text(
            "#!/usr/bin/env bash\n"
            "case \"$1\" in\n"
            "  --version) printf 'fixture-bun\\n' ;;\n"
            "  install) exit 0 ;;\n"
            "  run) [[ \"$3\" == health ]] && printf 'pages\\n'; exit 0 ;;\n"
            "  *) exit 0 ;;\n"
            "esac\n",
            encoding="utf-8",
        )
        (fake_bin / "bun").chmod(0o755)
        (fake_bin / "git").write_text(
            "#!/usr/bin/env bash\n"
            "[[ \"$1\" == -C ]] && exit 0\n"
            "exit 0\n",
            encoding="utf-8",
        )
        (fake_bin / "git").chmod(0o755)
        gbrain = root / "portable" / "gbrain"
        (gbrain / "src").mkdir(parents=True)
        (gbrain / "src" / "cli.ts").write_text("// fixture\n", encoding="utf-8")
        isolated_home = root / "home"
        isolated_home.mkdir()
        env = isolated_env(
            home=isolated_home,
            PATH=str(fake_bin) + os.pathsep + os.environ.get("PATH", ""),
            PYTHONPATH="",
        )
        if env_file is not None:
            env["XKB_ENV_FILE"] = str(env_file)
        if process_key is not None:
            env["GEMINI_API_KEY"] = process_key
        command = ["bash", str(SCRIPT), "--dir", str(gbrain)]
        command.extend(args or [])
        return subprocess.run(command, cwd=ROOT, env=env, capture_output=True, text=True, check=False)

    def test_active_source_has_no_private_config_fallback(self) -> None:
        source = SCRIPT.read_text(encoding="utf-8")
        for forbidden in ("OPENCLAW_JSON", "openclaw.json", "$HOME/.openclaw", "/root/.openclaw"):
            self.assertNotIn(forbidden, source)

    def test_process_env_wins_over_xkb_env_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            env_file = root / "runtime.env"
            env_file.write_text("GEMINI_API_KEY=file-placeholder\n", encoding="utf-8")
            result = self.run_setup(root, env_file=env_file)
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_explicit_env_file_wins_over_xkb_env_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            inherited = root / "inherited.env"
            explicit = root / "explicit.env"
            inherited.write_text("# inherited fixture intentionally omits the credential\n", encoding="utf-8")
            explicit.write_text("GEMINI_API_KEY=explicit-placeholder\n", encoding="utf-8")
            result = self.run_setup(
                root,
                env_file=inherited,
                process_key=None,
                args=["--env-file", str(explicit)],
            )
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_missing_credential_fails_before_install_and_is_actionable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            result = self.run_setup(root, process_key=None)
            self.assertEqual(result.returncode, 1)
            self.assertIn("GEMINI_API_KEY", result.stderr)
            self.assertIn("process environment", result.stderr)
            self.assertFalse((root / "home" / ".openclaw").exists())

    def test_missing_env_file_fails_before_install(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            missing = root / "missing.env"
            result = self.run_setup(root, process_key=None, env_file=missing)
            self.assertEqual(result.returncode, 1)
            self.assertIn("XKB env file not found", result.stderr)
            self.assertFalse((root / "home" / ".openclaw").exists())

    def test_portable_custom_path_runs_without_host_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            result = self.run_setup(root)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("GBRAIN_AVAILABLE: True", result.stdout)
            self.assertIn("gbrain_dir = " + str(root / "portable" / "gbrain"), result.stdout)
            self.assertFalse((root / "home" / ".openclaw").exists())


if __name__ == "__main__":
    unittest.main()
