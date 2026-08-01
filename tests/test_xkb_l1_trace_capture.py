from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "xkb_l1_trace_capture.py"
spec = importlib.util.spec_from_file_location("xkb_l1_trace_capture", SCRIPT)
assert spec and spec.loader
capture = importlib.util.module_from_spec(spec)
spec.loader.exec_module(capture)


class L1TraceCaptureTests(unittest.TestCase):
    def test_requires_explicit_episode_or_session(self) -> None:
        with self.assertRaisesRegex(ValueError, "episode_id or session_id"):
            capture.capture_trace({"content": "Pan prefers evidence-first work"})

    def test_capture_preserves_provenance_and_is_deterministic(self) -> None:
        payload = {
            "session_id": "session-123",
            "agent_id": "openclaw",
            "namespace": "pan-private",
            "source_type": "conversation",
            "source_id": "turn-9",
            "content": "Pan prefers evidence-first work.",
            "raw_source_refs": ["turn-9", "tool:read"],
            "observed_at": "2026-08-01T04:44:00+00:00",
        }
        first = capture.capture_trace(payload)
        second = capture.capture_trace(payload)
        self.assertEqual(first, second)
        self.assertEqual(first["schema"], "xkb-l1-trace.v1")
        self.assertEqual(first["memory_layer"], "L1")
        self.assertEqual(first["status"], "observed")
        self.assertEqual(first["session_id"], "session-123")
        self.assertEqual(first["raw_source_refs"], ["turn-9", "tool:read"])
        self.assertTrue(first["trace_id"].startswith("trace:"))
        self.assertEqual(len(first["provenance_sha256"]), 64)

    def test_episode_and_session_can_coexist(self) -> None:
        trace = capture.capture_trace(
            {
                "episode_id": "episode-1",
                "session_id": "session-1",
                "content": "A verified observation",
                "observed_at": "2026-08-01T04:44:00+00:00",
            }
        )
        self.assertEqual(trace["episode_id"], "episode-1")
        self.assertEqual(trace["session_id"], "session-1")


if __name__ == "__main__":
    unittest.main()
