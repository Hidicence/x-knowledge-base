"""MCP 邊界要自己解析可攜的環境契約，不能指望繼承。

Hermes 只把 MCP 設定裡明文宣告的變數交給 child。2026-09-05 實測執行中的
recall server 程序，整個環境只有 11 個變數，其中跟 XKB 有關的只有
OPENCLAW_WORKSPACE——沒有 GEMINI_API_KEY，語意召回因此一直退回關鍵字。

修法是在 MCP 邊界呼叫 runtime_env()：設定裡只放 XKB_ENV_FILE 這個路徑，
金鑰本身不進 MCP 設定，而 child 拿得到。這裡釘的是那個行為，因為它在
本機看不出來——本機沒有 Hermes，繼承是通的，錯了也不會有人發現。
"""
from __future__ import annotations

import importlib.util
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from _isolation import clean_env  # noqa: E402


def _load_server():
    spec = importlib.util.spec_from_file_location(
        "xkb_recall_server_under_test", SCRIPTS / "xkb_recall_server.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class RecallServerEnvTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.server = _load_server()

    def _child_env(self, env: dict[str, str]) -> dict[str, str]:
        """跑一次 _run_recall_structured，回傳它交給 child 的環境。"""
        captured: dict[str, dict[str, str]] = {}

        def fake_run(cmd, **kwargs):
            captured["env"] = kwargs["env"]
            raise RuntimeError("stop here: 我們只要看環境")

        with tempfile.TemporaryDirectory() as tmp:
            router = Path(tmp) / "recall_router.py"
            router.write_text("# fixture\n", encoding="utf-8")
            with mock.patch.dict(os.environ, env, clear=True), \
                    mock.patch.object(self.server, "ROUTER_SCRIPT", router), \
                    mock.patch.object(self.server.subprocess, "run", side_effect=fake_run):
                self.server._run_recall_structured("fixture query")
        return captured["env"]

    def test_credential_reaches_the_child_through_the_env_file(self) -> None:
        """金鑰只寫在 XKB_ENV_FILE 指到的檔案裡，不寫進 MCP 設定。"""
        with tempfile.TemporaryDirectory() as tmp:
            env_file = Path(tmp) / "runtime.env"
            env_file.write_text("GEMINI_API_KEY=file-placeholder\n", encoding="utf-8")
            child = self._child_env({**clean_env(), "XKB_ENV_FILE": str(env_file)})
        self.assertEqual(child.get("GEMINI_API_KEY"), "file-placeholder")

    def test_process_environment_still_wins(self) -> None:
        """繼承來的值優先於檔案，順序跟其他進入點一致。"""
        with tempfile.TemporaryDirectory() as tmp:
            env_file = Path(tmp) / "runtime.env"
            env_file.write_text("GEMINI_API_KEY=file-placeholder\n", encoding="utf-8")
            child = self._child_env({
                **clean_env(),
                "XKB_ENV_FILE": str(env_file),
                "GEMINI_API_KEY": "process-placeholder",
            })
        self.assertEqual(child.get("GEMINI_API_KEY"), "process-placeholder")

    def test_a_broken_env_file_is_named_not_swallowed(self) -> None:
        """路徑打錯時要說出來，不能長得跟「知識庫裡沒東西」一樣。"""
        result = None
        with tempfile.TemporaryDirectory() as tmp:
            router = Path(tmp) / "recall_router.py"
            router.write_text("# fixture\n", encoding="utf-8")
            with mock.patch.dict(
                os.environ,
                {**clean_env(), "XKB_ENV_FILE": str(Path(tmp) / "does-not-exist.env")},
                clear=True,
            ), mock.patch.object(self.server, "ROUTER_SCRIPT", router):
                result = self.server._run_recall_structured("fixture query")
        self.assertNotEqual(result.get("status"), "ok")
        self.assertIn("does-not-exist.env", str(result))


if __name__ == "__main__":
    unittest.main()
