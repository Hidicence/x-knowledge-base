from __future__ import annotations

import contextlib
import importlib
import io
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))


class XkbAskRuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.xkb_ask = importlib.import_module("xkb_ask")

    def test_process_environment_takes_precedence_over_explicit_env_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            env_file = Path(tmp) / "explicit.env"
            env_file.write_text("GEMINI_API_KEY=file-placeholder\n", encoding="utf-8")
            with mock.patch.dict(
                os.environ,
                {"GEMINI_API_KEY": "process-placeholder"},
                clear=True,
            ):
                self.assertEqual(self.xkb_ask.load_env_key(env_file), "process-placeholder")

    def test_explicit_env_file_supplies_credential(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            env_file = Path(tmp) / "explicit.env"
            env_file.write_text("GEMINI_API_KEY=file-placeholder\n", encoding="utf-8")
            with mock.patch.dict(os.environ, {}, clear=True):
                self.assertEqual(self.xkb_ask.load_env_key(env_file), "file-placeholder")

    def test_missing_explicit_env_file_is_actionable_before_search(self) -> None:
        with mock.patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(FileNotFoundError, "XKB env file not found"):
                self.xkb_ask.load_env_key("/missing/xkb-runtime.env")

    def test_cli_keeps_noncredential_gbrain_path_and_forwards_env_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            env_file = root / "runtime.env"
            env_file.write_text("GEMINI_API_KEY=fixture-key\n", encoding="utf-8")
            gbrain = root / "portable-gbrain"
            (gbrain / "src").mkdir(parents=True)
            (gbrain / "src" / "cli.ts").write_text("// isolated fixture\n", encoding="utf-8")
            observed: dict[str, object] = {}

            def fake_gbrain(query: str, limit: int, env_file: str | Path | None = None):
                observed["query"] = query
                observed["limit"] = limit
                observed["env_file"] = env_file
                return []

            with mock.patch.dict(os.environ, {}, clear=True), mock.patch.object(
                self.xkb_ask, "_resolve_gbrain_dir", return_value=gbrain
            ) as resolve_gbrain_dir, mock.patch.object(
                self.xkb_ask, "search_wiki_topics", return_value=([], 0.0)
            ), mock.patch.object(
                self.xkb_ask, "search_cards_gbrain", side_effect=fake_gbrain
            ), mock.patch.object(
                self.xkb_ask, "build_answer", return_value="fixture answer"
            ), mock.patch.object(
                sys, "argv", ["xkb_ask.py", "fixture query", "--json", "--env-file", str(env_file)]
            ), contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(self.xkb_ask.main(), 0)

            self.assertEqual(observed["env_file"], str(env_file))
            self.assertEqual(
                resolve_gbrain_dir.call_args.args,
                ({"GEMINI_API_KEY": "fixture-key"},),
            )

    def test_cli_fails_fast_without_credential_before_search_or_llm(self) -> None:
        with mock.patch.dict(os.environ, {}, clear=True), mock.patch.object(
            self.xkb_ask, "search_wiki_topics", side_effect=AssertionError("search must not run")
        ), mock.patch.object(
            self.xkb_ask, "search_cards", side_effect=AssertionError("cards must not run")
        ), mock.patch.object(
            self.xkb_ask, "search_cards_gbrain", side_effect=AssertionError("gbrain search must not run")
        ), mock.patch.object(
            self.xkb_ask, "build_answer", side_effect=AssertionError("LLM must not run")
        ), mock.patch.object(
            sys, "argv", ["xkb_ask.py", "fixture query"]
        ), contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()) as stderr:
            self.assertEqual(self.xkb_ask.main(), 2)

        self.assertIn("GEMINI_API_KEY", stderr.getvalue())

    def test_active_source_has_no_private_host_credential_fallback(self) -> None:
        source = (SCRIPTS / "xkb_ask.py").read_text(encoding="utf-8")
        for forbidden in ("OPENCLAW_JSON", "openclaw.json", "$HOME/.openclaw", "/root/.openclaw"):
            self.assertNotIn(forbidden, source)


if __name__ == "__main__":
    unittest.main()
