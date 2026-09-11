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
    """訊息第一行就要回答「這是我要處理的嗎」。

    原本的分法是「故障 vs 待辦」，而待辦 = 堆積。2026-09-12 發現那個分法是錯的：
    堆積有兩種。治理追不上是**故障**（系統自己該做的事做不完），而「要不要開一個
    新的 wiki 主題」才是**只有 Pan 能決定**的那種。

    舊分法把整個 governance_actionable section 當成決定類，於是「吸收追不上」每天
    被寫成「請你審核 180 筆」——實測那 180 筆裡 106 筆是 safe_promotion，一筆都不
    需要人。叫人決定一件系統自己會做的事，是這則訊息會被停止閱讀的直接原因。

    現在：追不上算故障，proposal / overdue 才進「等你決定」。
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

    def test_proposals_alone_are_a_decision_not_a_fault(self) -> None:
        """只有「要不要開新主題」時，不該說壞掉——那真的只有 Pan 能決定。"""
        message = self._build(
            [self._governance(pending=226, proposal=153, quarantine=23)], [])

        self.assertTrue(message.startswith("XKB 運作正常，有事情等你決定"))
        self.assertIn("153 條想開新的 wiki 主題", message)
        self.assertNotIn("壞掉了", message)

    def test_falling_behind_is_a_fault_not_a_decision(self) -> None:
        """治理吸收追不上是故障，不是待辦。

        這個測試原本斷言相反的事（「堆積不算壞掉」），而那個分法讓「追不上」
        每天被寫成「請你審核」。追不上是系統做不完自己該做的事，那是故障；
        要人決定的只有提案。
        """
        message = self._build(
            [self._governance(pending=226, proposal=0)],
            [("governance_actionable", "治理每天吸收 20 筆、進來 35 筆——追不上")],
        )

        self.assertTrue(message.startswith("XKB 有 1 個地方壞了"), message)
        self.assertIn("治理吸收追不上", message)      # 可讀標籤，不是內部識別字
        self.assertNotIn("等你決定", message)

    def test_a_fault_and_a_decision_are_both_reported(self) -> None:
        message = self._build(
            [self._governance(pending=226, proposal=153)],
            [("conversation_capture", "0/5 sessions recorded turns")],
        )

        self.assertTrue(message.startswith("XKB 有 1 個地方壞了"), message)
        self.assertIn("對話沒有被記錄下來", message)
        self.assertIn("等你決定", message)
        self.assertIn("153 條想開新的 wiki 主題", message)

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
