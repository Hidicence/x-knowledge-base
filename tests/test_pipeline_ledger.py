"""管線帳本要記得住那八天。

2026-09-01 到 09-09，書籤轉卡每晚跑、每晚以 404 失敗、每晚回報成功，八天 0
產出。當時所有「現況」的數字都正常：卡片 1,603 張、索引 1,632 筆。那八個晚上
在系統裡不存在，因為沒有任何地方記著「昨晚收了 9 筆、產出 0 筆、失敗 9 筆、
原因是找不到模型」。

所以這裡最重要的一個測試是 test_eight_quiet_nights_are_a_fault：把那段歷史
餵進帳本，健檢必須說出來。其餘的測試是為了讓它不要用誤報換到這個能力。
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

import xkb_ledger  # noqa: E402
import health_check_pipeline as hc  # noqa: E402

DAY = 86400.0


class _LedgerSandbox(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.path = Path(self._tmp.name) / "pipeline-ledger.jsonl"
        patch = mock.patch.object(xkb_ledger, "LEDGER_PATH", self.path)
        patch.start()
        self.addCleanup(patch.stop)
        self.addCleanup(self._tmp.cleanup)

    def write(self, rows: list[dict]) -> None:
        self.path.write_text(
            "\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n",
            encoding="utf-8",
        )

    @staticmethod
    def nights(stage: str, count: int, *, produced: int, failed: int,
               pending_from: int, reason: str = "", newest_age_hours: float = 2) -> list[dict]:
        """一晚一筆，最新的那筆在 newest_age_hours 小時前。"""
        rows = []
        now = datetime.now(timezone.utc)
        for i in range(count):
            age = timedelta(hours=newest_age_hours) + timedelta(days=count - 1 - i)
            row = {
                "ts": (now - age).isoformat(),
                "stage": stage,
                "ok": True,
                "intake": produced + failed,
                "produced": produced,
                "failed": failed,
                "pending": pending_from + i * failed,
            }
            if reason and failed:
                row["reason"] = reason
            rows.append(row)
        return rows

    @staticmethod
    def _msgs(section: dict) -> str:
        return "\n".join(c["msg"] for c in section["checks"])


class Recording(_LedgerSandbox):
    def test_a_run_is_written_as_one_line(self):
        xkb_ledger.record("bookmark-batch", intake=9, produced=0, failed=9,
                          pending=57, reason="404 model not found")
        rows = xkb_ledger.read()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["stage"], "bookmark-batch")
        self.assertEqual(rows[0]["produced"], 0)
        self.assertEqual(rows[0]["failed"], 9)
        self.assertIn("404", rows[0]["reason"])

    def test_reading_filters_by_stage(self):
        xkb_ledger.record("bookmark-batch", produced=1)
        xkb_ledger.record("ingestion-batch", produced=4)
        self.assertEqual(len(xkb_ledger.read("bookmark-batch")), 1)
        self.assertEqual(xkb_ledger.read("ingestion-batch")[0]["produced"], 4)

    def test_a_corrupt_line_does_not_lose_the_rest(self):
        xkb_ledger.record("bookmark-batch", produced=1)
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write("{ not json\n")
        xkb_ledger.record("bookmark-batch", produced=2)
        self.assertEqual([r["produced"] for r in xkb_ledger.read()], [1, 2])

    def test_an_unwritable_ledger_does_not_raise(self):
        """帳本不能把管線弄掛——但也不會沉默，xkb_failures 會在 stderr 說。"""
        blocked = Path(self._tmp.name) / "nope" / "x.jsonl"
        with mock.patch.object(xkb_ledger, "LEDGER_PATH", blocked), \
             mock.patch.object(Path, "mkdir", side_effect=OSError("read-only")):
            entry = xkb_ledger.record("bookmark-batch", produced=1)
        self.assertEqual(entry["stage"], "bookmark-batch")


class TypicalGap(_LedgerSandbox):
    def test_needs_at_least_three_runs(self):
        rows = self.nights("s", 2, produced=1, failed=0, pending_from=0)
        self.assertIsNone(xkb_ledger.typical_gap_seconds(rows))

    def test_one_catch_up_run_does_not_move_the_median(self):
        """手動補跑會製造一個很短的間隔。門檻不該因為它就變鬆或變緊。"""
        rows = self.nights("s", 6, produced=1, failed=0, pending_from=0)
        rows.append({**rows[-1], "ts": rows[-1]["ts"]})  # 同一刻再跑一次
        gap = xkb_ledger.typical_gap_seconds(rows)
        self.assertAlmostEqual(gap, DAY, delta=DAY * 0.1)

    def test_a_burst_of_manual_runs_does_not_move_the_median(self):
        """中位數只擋得住「一次」離群。

        2026-09-10 我為了驗證連續手動跑了六次書籤批次，每次相隔幾分鐘，中位
        間隔被壓成 0 小時——健檢於是說「平常每 0h 跑一次，已經 11h 沒有紀錄」，
        把一個正常的階段報成排程死掉。上一版的註解寫著「一次補跑不會把它拉
        歪」，而上一版的測試也只測了一次。
        """
        from datetime import datetime, timedelta, timezone
        now = datetime.now(timezone.utc)
        rows = [{"ts": (now - timedelta(days=d)).isoformat(), "stage": "s"}
                for d in range(8, 0, -1)]
        rows += [{"ts": (now - timedelta(minutes=m)).isoformat(), "stage": "s"}
                 for m in (40, 32, 25, 18, 9, 2)]

        gap = xkb_ledger.typical_gap_seconds(rows)

        self.assertIsNotNone(gap)
        self.assertAlmostEqual(gap, DAY, delta=DAY * 0.2)

    def test_a_history_with_no_rhythm_yet_says_so(self):
        """全部都在同一段操作裡——沒有節奏可比，就不要拿假節奏去判斷。"""
        from datetime import datetime, timedelta, timezone
        now = datetime.now(timezone.utc)
        rows = [{"ts": (now - timedelta(minutes=m)).isoformat(), "stage": "s"}
                for m in (30, 20, 10, 4)]
        self.assertIsNone(xkb_ledger.typical_gap_seconds(rows))


class HealthCheckReadsIt(_LedgerSandbox):
    def test_eight_quiet_nights_are_a_fault(self):
        """這一項存在的理由。"""
        self.write(self.nights("bookmark-batch", 8, produced=0, failed=9,
                               pending_from=13, reason="404 model not found"))

        section = hc.check_pipeline_ledger()

        self.assertFalse(all(c["ok"] for c in section["checks"]))
        msgs = self._msgs(section)
        self.assertIn("沒有產出", msgs)
        self.assertIn("404", msgs)

    def test_a_working_stage_is_not_a_fault(self):
        self.write(self.nights("bookmark-batch", 8, produced=20, failed=0,
                               pending_from=40))

        section = hc.check_pipeline_ledger()

        self.assertTrue(all(c["ok"] for c in section["checks"]), self._msgs(section))

    def test_a_quiet_stage_with_an_empty_queue_is_not_a_fault(self):
        """沒有新書籤時，產出 0 是正常的，不是故障。"""
        self.write(self.nights("bookmark-batch", 8, produced=0, failed=0,
                               pending_from=0))

        section = hc.check_pipeline_ledger()

        self.assertTrue(all(c["ok"] for c in section["checks"]), self._msgs(section))

    def test_a_stage_that_stopped_reporting_is_a_fault(self):
        """排程整個死掉時，帳本會安靜——安靜要看得見。"""
        rows = self.nights("bookmark-batch", 8, produced=20, failed=0,
                           pending_from=40, newest_age_hours=24 * 5)
        self.write(rows)

        section = hc.check_pipeline_ledger()

        self.assertFalse(all(c["ok"] for c in section["checks"]))
        self.assertIn("沒有紀錄", self._msgs(section))

    def test_the_threshold_follows_the_stage_own_rhythm(self):
        """每 12 小時跑一次的階段，停 20 小時不算停。

        寫死 24h 的話，改排程就會變成每天誤報，而每天誤報等於沒有檢查。
        """
        now = datetime.now(timezone.utc)
        rows = [{
            "ts": (now - timedelta(hours=20 + 12 * i)).isoformat(),
            "stage": "twice-daily", "ok": True, "produced": 3, "pending": 0,
        } for i in range(8)][::-1]
        self.write(rows)

        section = hc.check_pipeline_ledger()

        self.assertTrue(all(c["ok"] for c in section["checks"]), self._msgs(section))

    def test_a_batch_that_failed_outright_is_named(self):
        rows = self.nights("ingestion-batch", 5, produced=4, failed=0, pending_from=0)
        rows[-1] = {**rows[-1], "ok": False, "reason": "向量索引建置離開碼 137"}
        self.write(rows)

        section = hc.check_pipeline_ledger()

        self.assertFalse(all(c["ok"] for c in section["checks"]))
        self.assertIn("137", self._msgs(section))

    def test_an_empty_ledger_is_not_a_fault(self):
        """剛裝上去、還沒跑過任何排程，不是故障。"""
        section = hc.check_pipeline_ledger()
        self.assertTrue(all(c["ok"] for c in section["checks"]), self._msgs(section))


class BatchSummary(unittest.TestCase):
    """帳本接上去的第一次實跑就照出這個舊 bug。

    worker 的輸出裡有兩行含 done=：佇列同步的累計總數，和這一次的結果。
    原本的 sed 抓「最後一個 done=」，平常剛好對；佇列沒事做時 worker 不印
    結果那行，於是這次的產出被讀成佇列總數——實測報過「產了 1575 張卡」，
    還因此多跑了一次向量索引。
    """

    QUEUE_LINE = "✅ queue synced: /x/tiege-queue.json (1591 items, todo=0, done=1575)"

    def setUp(self):
        sys.path.insert(0, str(ROOT / "scripts"))
        import xkb_batch_summary
        self.parse = xkb_batch_summary.parse

    def test_the_queue_total_is_not_this_run_output(self):
        result = self.parse(self.QUEUE_LINE + "\n✅ No todo items found\n")
        self.assertEqual(result, (0, 0, "idle"))

    def test_a_real_run_is_read_from_the_worker_summary(self):
        text = (self.QUEUE_LINE
                + "\n📊 done=7  skipped=0  failed=2  remaining todo=0\n")
        self.assertEqual(self.parse(text), (7, 2, "ran"))

    def test_an_unreadable_run_is_not_reported_as_zero(self):
        """讀不出結果和「產出 0」是兩件事，混在一起就是這條管線八天沒被發現的原因。"""
        self.assertIsNone(self.parse(self.QUEUE_LINE + "\nTraceback (most recent call last):\n"))
        self.assertIsNone(self.parse(""))

    def test_the_batch_script_treats_unreadable_as_a_fault(self):
        text = (ROOT / "scripts" / "run_bookmark_batch.sh").read_text(encoding="utf-8")
        self.assertIn("xkb_batch_summary.py", text)
        self.assertIn("讀不出 worker 的執行結果", text)
        # 舊的抓法不能留著，否則哪天有人改回去也不會有人發現。
        self.assertNotIn('sed -n \'s/.*done=', text)


class Wiring(unittest.TestCase):
    def test_the_check_is_registered(self):
        source = (ROOT / "scripts" / "health_check_pipeline.py").read_text(encoding="utf-8")
        self.assertIn("check_pipeline_ledger(),", source)

    def test_the_daily_message_can_name_it(self):
        sys.path.insert(0, str(ROOT / "scripts"))
        import health_check_notify as notify
        self.assertIn("pipeline_ledger", notify.FAULT_LABELS)

    def test_the_batch_stages_write_to_it(self):
        """階段不寫，帳本就永遠是空的——而空帳本不會報錯。"""
        for name, stage in [("run_bookmark_batch.sh", "bookmark-batch"),
                            ("run_ingestion_batch.sh", "ingestion-batch")]:
            text = (ROOT / "scripts" / name).read_text(encoding="utf-8")
            with self.subTest(script=name):
                self.assertIn("xkb_ledger.py", text)
                self.assertIn(stage, text)


if __name__ == "__main__":
    unittest.main()
