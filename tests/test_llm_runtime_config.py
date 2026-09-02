from __future__ import annotations

import importlib
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _isolation import clean_env
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))


class LLMRuntimeConfigurationTests(unittest.TestCase):
    def test_explicit_env_file_supplies_direct_llm_settings(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            env_file = Path(tmp) / "runtime.env"
            env_file.write_text(
                "LLM_API_URL=https://mock.example/v1\n"
                "LLM_API_KEY=file-key\nLLM_MODEL=file-model\n",
                encoding="utf-8",
            )
            with mock.patch.dict(os.environ, {**clean_env(), "XKB_ENV_FILE": str(env_file)}, clear=True):
                llm = importlib.import_module("_llm")
                with mock.patch.object(llm, "_direct_api_call", return_value="ok") as direct:
                    self.assertEqual(llm.call("system", "user"), "ok")
                # max_tokens 現在會一路傳到 API payload。原本 _card_prompt.llm_call
                # 收下它就丟掉，於是 condense_long_content 每段傳 600、實際拿到 4096。
                # （目前的供應商不理這個欄位，但參數確實傳到底了——換供應商就會生效。）
                direct.assert_called_once_with("system", "user", timeout=120, max_tokens=4096)

    def test_missing_direct_credential_fails_actionably_without_private_fallback(self) -> None:
        with mock.patch.dict(os.environ, {**clean_env(), }, clear=True):
            llm = importlib.import_module("_llm")
            with self.assertRaisesRegex(RuntimeError, "LLM_API_URL / LLM_API_KEY"):
                llm._direct_api_call("system", "user")

    def test_missing_explicit_env_file_fails_before_llm_call(self) -> None:
        with mock.patch.dict(os.environ, {**clean_env(), "XKB_ENV_FILE": "/missing/runtime.env"}, clear=True):
            llm = importlib.import_module("_llm")
            with self.assertRaisesRegex(FileNotFoundError, "XKB env file"):
                llm.call("system", "user")

    def test_github_card_entrypoint_reads_explicit_env_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            env_file = Path(tmp) / "runtime.env"
            env_file.write_text("LLM_API_KEY=card-file-key\n", encoding="utf-8")
            with mock.patch.dict(os.environ, {**clean_env(), "XKB_ENV_FILE": str(env_file)}, clear=True):
                github = importlib.import_module("fetch_github_repos")
                self.assertEqual(github.load_env_key(), "card-file-key")

    def test_process_environment_wins_over_explicit_env_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            env_file = Path(tmp) / "runtime.env"
            env_file.write_text("LLM_API_KEY=file-key\n", encoding="utf-8")
            with mock.patch.dict(os.environ, {**clean_env(), 
                "XKB_ENV_FILE": str(env_file), "LLM_API_KEY": "process-key"
            }, clear=True):
                github = importlib.import_module("fetch_github_repos")
                self.assertEqual(github.load_env_key(), "process-key")

    def test_active_llm_and_github_entrypoints_have_no_private_runtime_lookup(self) -> None:
        forbidden = ('Path.home() / ".openclaw"', "openclaw.json", "OPENCLAW_JSON")
        active_entrypoints = (
            "_llm.py", "fetch_github_repos.py", "xbrain_recall.py", "xkb_ask.py",
            "run_scan_worker.py", "pdf_ingest.py",
            "health_check_notify.py", "topic_guide_generator.py",
        )
        for name in active_entrypoints:
            source = (SCRIPTS / name).read_text(encoding="utf-8")
            for token in forbidden:
                self.assertNotIn(token, source, f"{name} must use portable runtime config")

    def test_active_entrypoints_import_shared_runtime_loader(self) -> None:
        active_entrypoints = (
            "xbrain_recall.py", "xkb_ask.py", "run_scan_worker.py", "pdf_ingest.py", "health_check_notify.py",
            "topic_guide_generator.py",
        )
        for name in active_entrypoints:
            source = (SCRIPTS / name).read_text(encoding="utf-8")
            self.assertIn("from runtime_config import runtime_env", source, name)

    def test_openai_compatible_branch_uses_runtime_key_without_leaking_it(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            env_file = Path(tmp) / "runtime.env"
            env_file.write_text(
                "LLM_API_URL=https://mock.example/v1\n"
                "LLM_API_KEY=runtime-secret-placeholder\n"
                "LLM_MODEL=mock-model\n",
                encoding="utf-8",
            )
            captured: dict[str, Any] = {}

            class Response:
                def __enter__(self):
                    return self

                def __exit__(self, *args):
                    return False

                def read(self):
                    return json.dumps({
                        "choices": [{"message": {"content": "generated"}}]
                    }).encode("utf-8")

            def fake_urlopen(request, timeout):
                captured["url"] = request.full_url
                captured["authorization"] = request.get_header("Authorization")
                captured["body"] = json.loads(request.data.decode("utf-8"))
                return Response()

            with mock.patch.dict(os.environ, {**clean_env(), "XKB_ENV_FILE": str(env_file)}, clear=True):
                llm = importlib.import_module("_llm")
                with mock.patch.object(llm.urllib.request, "urlopen", fake_urlopen):
                    result = llm._direct_api_call("system", "user")

            self.assertEqual(result, "generated")
            self.assertEqual(captured["url"], "https://mock.example/v1/chat/completions")
            self.assertEqual(captured["authorization"], "Bearer runtime-secret-placeholder")
            self.assertEqual(captured["body"]["model"], "mock-model")
            self.assertNotIn("runtime-secret-placeholder", json.dumps(captured["body"]))


if __name__ == "__main__":
    unittest.main()
