from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "xkb_turn_complete.py"
spec = importlib.util.spec_from_file_location("xkb_turn_complete", SCRIPT)
assert spec and spec.loader
adapter = importlib.util.module_from_spec(spec)
spec.loader.exec_module(adapter)


class TurnCompleteTests(unittest.TestCase):
    def test_requires_turn_identity(self) -> None:
        with self.assertRaisesRegex(ValueError, "session_id or episode_id"):
            adapter.turn_to_trace_payload({"user_message": "hello"})

    def test_maps_completed_turn_and_is_idempotent(self) -> None:
        turn = {
            "session_id": "session-1",
            "episode_id": "episode-1",
            "turn_id": "turn-1",
            "agent_id": "openclaw",
            "namespace": "team-private",
            "user_message": "請查證這個決策",
            "assistant_message": "已查證，保留來源證據。",
            "tool_calls": [{"name": "read"}],
            "tool_results": [{"ok": True}],
            "completed_at": "2026-08-01T04:48:00+00:00",
            "status": "completed",
        }
        with tempfile.TemporaryDirectory() as tmp:
            first = adapter.write_turn_trace(turn, Path(tmp))
            second = adapter.write_turn_trace(turn, Path(tmp))
            self.assertTrue(first["created"])
            self.assertFalse(second["created"])
            self.assertEqual(first["trace_id"], second["trace_id"])
            artifact = __import__("json").loads(Path(first["path"]).read_text(encoding="utf-8"))
            self.assertEqual(artifact["episode_id"], "episode-1")
            self.assertEqual(artifact["session_id"], "session-1")
            self.assertEqual(artifact["raw_source_refs"], ["turn:turn-1"])
            self.assertEqual(artifact["metadata"]["status"], "completed")

    def test_existing_content_is_preserved(self) -> None:
        payload = adapter.turn_to_trace_payload({
            "session_id": "session-2",
            "content": {"custom": "raw result"},
            "turn_id": "turn-2",
        })
        self.assertEqual(payload["content"], {"custom": "raw result"})


if __name__ == "__main__":
    unittest.main()
