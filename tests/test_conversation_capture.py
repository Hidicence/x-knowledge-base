from __future__ import annotations

import importlib
import sqlite3
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))


def _store(path: Path, sessions: int, with_turns: int, age_days: int = 0) -> None:
    when = (datetime.now(timezone.utc) - timedelta(days=age_days)).isoformat()
    db = sqlite3.connect(path)
    db.execute("CREATE TABLE sessions (session_id TEXT PRIMARY KEY, created_at TEXT)")
    db.execute("CREATE TABLE turns (turn_id TEXT PRIMARY KEY, session_id TEXT)")
    for index in range(sessions):
        db.execute("INSERT INTO sessions VALUES (?, ?)", (f"sess:{index}", when))
        if index < with_turns:
            db.execute("INSERT INTO turns VALUES (?, ?)", (f"turn:{index}", f"sess:{index}"))
    db.commit()
    db.close()


class ConversationCaptureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.hc = importlib.import_module("health_check_pipeline")

    def _run(self, **store):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "knowledge.sqlite"
            if store:
                _store(path, **store)
            with mock.patch.dict("os.environ", {"XKB_SERVICE_DB": str(path)}):
                return self.hc.check_conversation_capture()["checks"][0]

    def test_sessions_recording_nothing_is_a_failure(self) -> None:
        """The signature of the 2026-08-24 outage: sessions open, no turns."""
        self.assertFalse(self._run(sessions=5, with_turns=0)["ok"])

    def test_a_mix_is_normal(self) -> None:
        """A session can legitimately open and go unused."""
        self.assertTrue(self._run(sessions=5, with_turns=2)["ok"])

    def test_quiet_period_is_not_a_failure(self) -> None:
        self.assertTrue(self._run(sessions=0, with_turns=0)["ok"])

    def test_old_breakage_outside_the_window_is_not_reported(self) -> None:
        self.assertTrue(self._run(sessions=5, with_turns=0, age_days=30)["ok"])

    def test_absent_store_is_not_a_failure(self) -> None:
        """Not every host runs the knowledge service."""
        self.assertTrue(self._run()["ok"])


if __name__ == "__main__":
    unittest.main()
