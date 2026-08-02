from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"


def load(name: str):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


hook = load("xkb_agent_hook")
installer = load("xkb_install_agent_hook")


class SessionIdentityTests(unittest.TestCase):
    def test_session_id_is_preferred_and_cwd_is_the_fallback(self) -> None:
        self.assertEqual(hook.session_key({"session_id": "abc", "cwd": "/tmp"}), "abc")
        self.assertEqual(hook.session_key({"cwd": "/work/project"}), "/work/project")
        self.assertEqual(hook.session_key({}), "default")

    def test_turn_id_is_stable_for_the_same_prompt(self) -> None:
        first = hook.turn_id("session", "same prompt")
        self.assertEqual(first, hook.turn_id("session", "same prompt"))
        self.assertNotEqual(first, hook.turn_id("session", "other prompt"))


class FailOpenTests(unittest.TestCase):
    def test_service_being_down_never_blocks_the_agent(self) -> None:
        """A knowledge base that cannot be reached must not stop the user working."""
        payload = json.dumps({"hook_event_name": "UserPromptSubmit", "prompt": "hi", "session_id": "s"})
        with mock.patch.object(hook, "call", side_effect=OSError("connection refused")), \
             mock.patch("sys.stdin", new=__import__("io").StringIO(payload)):
            self.assertEqual(hook.main(), 0)

    def test_malformed_event_is_ignored(self) -> None:
        with mock.patch("sys.stdin", new=__import__("io").StringIO("{not json")):
            self.assertEqual(hook.main(), 0)


class EncodingTests(unittest.TestCase):
    def test_output_is_utf8_regardless_of_console_codepage(self) -> None:
        """Chinese output must not depend on the console encoding.

        print() would encode with the console codepage (cp950 on a zh-TW
        Windows box) and raise UnicodeEncodeError, which — being a ValueError —
        the fail-open handler swallows, silently disabling the whole hook.
        """
        captured = __import__("io").BytesIO()
        stdout = mock.Mock()
        stdout.buffer = captured
        with mock.patch.object(hook.sys, "stdout", stdout):
            hook.emit({"text": "語意召回"})
        self.assertEqual(json.loads(captured.getvalue().decode("utf-8"))["text"], "語意召回")


class TranscriptTests(unittest.TestCase):
    def test_reads_the_last_assistant_message(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "t.jsonl"
            path.write_text("\n".join([
                json.dumps({"message": {"role": "user", "content": "q"}}),
                json.dumps({"message": {"role": "assistant", "content": "first"}}),
                json.dumps({"message": {"role": "assistant", "content": [{"type": "text", "text": "final"}]}}),
            ]), encoding="utf-8")
            self.assertEqual(hook.last_assistant_message(str(path)), "final")

    def test_missing_transcript_is_not_an_error(self) -> None:
        self.assertEqual(hook.last_assistant_message("/no/such/file"), "")


class RenderTests(unittest.TestCase):
    def test_recalled_knowledge_is_labelled_as_history(self) -> None:
        rendered = hook.render([{"record_type": "knowledge_card", "title": "T", "summary": "S", "source_url": "u"}])
        self.assertIn("xkb_recalled_knowledge", rendered)
        self.assertIn("可能已經過時", rendered)
        self.assertIn("T", rendered)


class InstallerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        patcher = mock.patch.object(installer, "agent_home", return_value=self.home)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.config = self.home / "hook-config.json"
        config_patcher = mock.patch.object(installer, "HOOK_CONFIG", self.config)
        config_patcher.start()
        self.addCleanup(config_patcher.stop)
        self.addCleanup(self.tmp.cleanup)

    def settings(self) -> dict:
        return json.loads((self.home / "settings.json").read_text(encoding="utf-8"))

    def test_install_wires_both_events(self) -> None:
        installer.install("http://127.0.0.1:18972", "", "claude-code")
        hooks = self.settings()["hooks"]
        for event in installer.EVENTS:
            commands = [h["command"] for g in hooks[event] for h in g["hooks"]]
            self.assertTrue(any(installer.HOOK_SCRIPT.name in c for c in commands))

    def test_reinstall_replaces_rather_than_duplicates(self) -> None:
        installer.install("http://a", "", "claude-code")
        installer.install("http://b", "", "claude-code")
        hooks = self.settings()["hooks"]["UserPromptSubmit"]
        ours = [h for g in hooks for h in g["hooks"] if installer.HOOK_SCRIPT.name in h["command"]]
        self.assertEqual(len(ours), 1)
        self.assertEqual(json.loads(self.config.read_text(encoding="utf-8"))["url"], "http://b")

    def test_existing_unrelated_hooks_are_preserved(self) -> None:
        (self.home / "settings.json").write_text(json.dumps({
            "hooks": {"UserPromptSubmit": [{"matcher": "", "hooks": [{"type": "command", "command": "other-tool"}]}]},
            "model": "keep-me",
        }), encoding="utf-8")
        installer.install("http://127.0.0.1:18972", "", "claude-code")
        settings = self.settings()
        self.assertEqual(settings["model"], "keep-me")
        commands = [h["command"] for g in settings["hooks"]["UserPromptSubmit"] for h in g["hooks"]]
        self.assertIn("other-tool", commands)

    def test_uninstall_removes_only_ours(self) -> None:
        (self.home / "settings.json").write_text(json.dumps({
            "hooks": {"Stop": [{"matcher": "", "hooks": [{"type": "command", "command": "other-tool"}]}]},
        }), encoding="utf-8")
        installer.install("http://127.0.0.1:18972", "", "claude-code")
        installer.uninstall()
        settings = self.settings()
        commands = [h["command"] for g in settings.get("hooks", {}).get("Stop", []) for h in g["hooks"]]
        self.assertEqual(commands, ["other-tool"])
        self.assertNotIn("UserPromptSubmit", settings.get("hooks", {}))
        self.assertFalse(self.config.exists())

    def test_corrupt_settings_are_never_overwritten(self) -> None:
        (self.home / "settings.json").write_text("{not json", encoding="utf-8")
        with self.assertRaises(SystemExit):
            installer.install("http://127.0.0.1:18972", "", "claude-code")


if __name__ == "__main__":
    unittest.main()
