from __future__ import annotations

import importlib
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))


class DailySummaryTests(unittest.TestCase):
    """The message has to answer "is this mine to deal with?" on line one.

    A fault is the system being broken and nobody's decision. A backlog is
    work waiting on a judgement only Pan can make. They were reported in one
    list, in the vocabulary of whichever check produced them, which is why
    the daily message stopped being read.
    """

    @classmethod
    def setUpClass(cls) -> None:
        cls.notify = importlib.import_module("health_check_notify")

    def _build(self, sections, failures):
        # Inventory reads the live workspace; not what these tests are about.
        with mock.patch.object(self.notify, "_inventory_lines", return_value=[]):
            return self.notify.build_message(sections, failures)

    def _governance(self, **counts):
        return {"name": "governance_actionable", "checks": [], "actionable_counts": counts}

    def test_all_clear(self) -> None:
        self.assertTrue(self._build([], []).startswith("XKB 一切正常"))

    def test_a_backlog_is_not_reported_as_breakage(self) -> None:
        message = self._build(
            [self._governance(pending=226, proposal=153, quarantine=23)],
            [("governance_actionable", "governance actionable counts: {...}")],
        )
        self.assertTrue(message.startswith("XKB 運作正常，有事情等你決定"))
        self.assertIn("153 條想開新的 wiki 主題", message)
        self.assertNotIn("壞掉了", message)

    def test_a_fault_leads(self) -> None:
        message = self._build(
            [self._governance(pending=226, proposal=153)],
            [
                ("conversation_capture", "0/5 sessions recorded turns"),
                ("governance_actionable", "counts: {...}"),
            ],
        )
        self.assertTrue(message.startswith("XKB 有 1 個地方壞了"))
        self.assertIn("對話沒有被記錄下來", message)
        # The backlog is still listed, but under its own heading.
        self.assertIn("等你決定", message)

    def test_raw_section_names_do_not_reach_the_reader(self) -> None:
        message = self._build([], [("conversation_capture", "0/5 sessions recorded turns")])
        self.assertNotIn("conversation_capture", message.split("\\n")[0])
        self.assertIn("對話沒有被記錄下來", message)

    def test_an_unknown_check_still_reports_something(self) -> None:
        """A check added later must not vanish from the message."""
        message = self._build([], [("something_new", "詳細說明")])
        self.assertIn("something_new", message)
        self.assertIn("詳細說明", message)

    def test_counts_that_do_not_apply_are_omitted(self) -> None:
        message = self._build([self._governance(pending=0, proposal=0, quarantine=0)], [])
        self.assertTrue(message.startswith("XKB 一切正常"))


if __name__ == "__main__":
    unittest.main()
