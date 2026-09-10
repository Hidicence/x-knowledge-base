"""空手而回有兩種原因，紀錄裡要分得出來。

2026-05-04 的清理移掉了 recall_router 依賴的模組，召回從此每次都崩潰。整整
十二週沒有人發現，因為它一直有禮貌地回答「我不知道」——而在 telemetry 裡，
「這個主題確實沒有東西」和「語意後端整個掛了」是同一行：

    recalled=false, result_count=0

各層的失敗其實都有經過 xkb_failures.note()，只是那些話寫在 stderr，而召回跑
在 MCP 伺服器裡，stderr 沒有人讀。

這裡測的是：那些痕跡有被收進紀錄，而且健檢分得出「抖了一下」和「壞著」。
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import xkb_failures  # noqa: E402
import health_check_pipeline as hc  # noqa: E402


class FailuresLeaveATrace(unittest.TestCase):
    def setUp(self):
        xkb_failures.reset()
        self.addCleanup(xkb_failures.reset)

    def test_a_fallback_is_recorded_not_only_printed(self):
        xkb_failures.note("semantic recall (xbrain)", TimeoutError("timed out"))
        self.assertIn("semantic recall (xbrain)", xkb_failures.taken())

    def test_it_is_recorded_even_when_nothing_is_printed(self):
        """安靜模式或同一個地方第二次，仍然要留下「發生過」。

        印不印是給人看的事，記不記是給紀錄看的事。原本這兩件事綁在一起，
        於是最容易被忽略的那些情況，連發生過都留不下來。
        """
        with mock.patch.object(xkb_failures, "QUIET", True):
            xkb_failures.note("relevance filter", RuntimeError("index broken"))
        self.assertIn("relevance filter", xkb_failures.taken())

        xkb_failures.note("relevance filter", RuntimeError("index broken"))
        self.assertEqual(xkb_failures.taken().count("relevance filter"), 1)

    def test_a_clean_run_records_nothing(self):
        self.assertEqual(xkb_failures.taken(), [])


class TelemetryCarriesIt(unittest.TestCase):
    def setUp(self):
        xkb_failures.reset()
        self.addCleanup(xkb_failures.reset)

    def _telemetry(self, result_count: int) -> dict:
        import recall_router
        from conversation_state_parser import parse as parse_state
        parsed = parse_state("XKB 的知識管線是怎麼設計的")
        return recall_router._build_telemetry(
            "XKB 的知識管線是怎麼設計的", parsed, result_count, "none", 12)

    def test_an_empty_result_with_a_broken_layer_is_marked(self):
        xkb_failures.note("semantic recall (xbrain)", TimeoutError("timed out"))
        entry = self._telemetry(0)
        self.assertEqual(entry["degraded"], ["semantic recall (xbrain)"])
        self.assertTrue(entry["empty_because_broken"])

    def test_an_empty_result_with_nothing_broken_is_not_marked(self):
        """真的沒有資料——這一種不該被當成故障。"""
        entry = self._telemetry(0)
        self.assertEqual(entry["degraded"], [])
        self.assertFalse(entry["empty_because_broken"])

    def test_results_despite_a_degraded_layer_are_not_marked_as_empty(self):
        xkb_failures.note("bm25 leg", RuntimeError("index missing"))
        entry = self._telemetry(5)
        self.assertEqual(entry["degraded"], ["bm25 leg"])
        self.assertFalse(entry["empty_because_broken"])


class HealthCheckTellsThemApart(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)

    @staticmethod
    def _rows(layers_by_age_hours: dict[float, list[str]]) -> list[dict]:
        now = datetime.now(timezone.utc)
        rows = []
        for hours in sorted(layers_by_age_hours, reverse=True):
            rows.append({
                "ts": (now - timedelta(hours=hours)).isoformat(),
                "recalled": False,
                "result_count": 0,
                "degraded": layers_by_age_hours[hours],
                "empty_because_broken": bool(layers_by_age_hours[hours]),
            })
        return rows

    @staticmethod
    def _msgs(checks: list[dict]) -> str:
        return "\n".join(c["msg"] for c in checks)

    def test_a_layer_broken_for_hours_is_a_fault(self):
        rows = self._rows({24: ["semantic recall (xbrain)"],
                           12: ["semantic recall (xbrain)"],
                           0.2: ["semantic recall (xbrain)"]})

        checks = hc._degraded_recall_checks(rows)

        self.assertFalse(all(c["ok"] for c in checks))
        msgs = self._msgs(checks)
        self.assertIn("semantic recall (xbrain)", msgs)
        self.assertIn("不能當成真的沒資料", msgs)

    def test_a_single_blip_is_not_a_fault(self):
        """偶爾逾時一次是抖動。比率會被使用頻率騙——一天查三次的話，
        一次逾時就是 33%——所以看的是持續多久，不是佔幾成。"""
        rows = self._rows({24: [], 12: [], 0.2: ["semantic recall (xbrain)"]})

        checks = hc._degraded_recall_checks(rows)

        self.assertTrue(all(c["ok"] for c in checks), self._msgs(checks))

    def test_a_recovered_layer_is_not_a_fault(self):
        rows = self._rows({24: ["semantic recall (xbrain)"],
                           12: ["semantic recall (xbrain)"],
                           0.2: []})

        checks = hc._degraded_recall_checks(rows)

        self.assertTrue(all(c["ok"] for c in checks), self._msgs(checks))
        self.assertIn("因為壞掉", self._msgs(checks))

    def test_old_rows_without_the_field_are_not_claimed_to_be_green(self):
        """沒有欄位不等於沒壞掉，只是那時候還沒在記。"""
        rows = [{"ts": datetime.now(timezone.utc).isoformat(),
                 "recalled": False, "result_count": 0}]

        checks = hc._degraded_recall_checks(rows)

        self.assertTrue(all(c["ok"] for c in checks))
        self.assertIn("還沒有 degraded 欄位", self._msgs(checks))


if __name__ == "__main__":
    unittest.main()
