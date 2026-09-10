"""「排隊中」和「卡住」是兩件事，混在一個數字裡會給出錯的建議。

2026-09-10，佇列裡剩 17 筆待轉，組成是 failed 9、skipped 6、還沒進佇列 2。
沒有任何一筆在排隊——那 9 筆是模型設定壞掉那八天留下的，根因修好之後它們仍
然停在 failed，因為 sync_tiege_queue 明文寫著「保留 failed/skipped，由操作者
自行重設」，沒有任何排程會再碰它們。

而批次的摘要照樣說：「進來的比消化的快，limit 需要調高。」調高 limit 對這
17 筆完全沒有作用，而且它會每天這樣說一次——一則每天出現、每天都幫不上忙的
訊息，正是讓人停止閱讀的東西。

卡住的項目需要的不是一個數字，是一個決定：重跑，還是放棄。
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from xkb_pending_work import pending_breakdown  # noqa: E402


class Breakdown(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        self.bookmarks = root / "bookmarks"
        self.cards = root / "cards"
        self.bookmarks.mkdir()
        self.cards.mkdir()
        self.queue = root / "tiege-queue.json"
        self.addCleanup(self._tmp.cleanup)

    def bookmark(self, stem: str) -> None:
        (self.bookmarks / f"{stem}.md").write_text("# b\n", encoding="utf-8")

    def card(self, stem: str) -> None:
        (self.cards / f"{stem}.md").write_text("---\ntype: knowledge-card\n---\n",
                                               encoding="utf-8")

    def write_queue(self, statuses: dict[str, str]) -> None:
        self.queue.write_text(json.dumps(
            {"items": [{"id": k, "status": v} for k, v in statuses.items()]},
            ensure_ascii=False), encoding="utf-8")

    def counts(self) -> dict[str, int]:
        return pending_breakdown(self.bookmarks, self.cards, self.queue)

    def test_the_september_state_reads_as_stuck_not_as_a_backlog(self):
        for i in range(9):
            self.bookmark(f"failed{i}")
        for i in range(6):
            self.bookmark(f"skipped{i}")
        self.write_queue({**{f"failed{i}": "failed" for i in range(9)},
                          **{f"skipped{i}": "skipped" for i in range(6)}})

        counts = self.counts()

        self.assertEqual(counts["total"], 15)
        self.assertEqual(counts["stuck"], 9)
        self.assertEqual(counts["skipped"], 6)
        # 沒有一筆在排隊——所以「調高 limit」是錯的建議。
        self.assertEqual(counts["actionable"], 0)

    def test_a_real_backlog_is_actionable(self):
        for i in range(4):
            self.bookmark(f"todo{i}")
        self.write_queue({f"todo{i}": "todo" for i in range(4)})

        self.assertEqual(self.counts()["actionable"], 4)

    def test_a_bookmark_not_yet_in_the_queue_still_counts_as_work(self):
        """每次批次開頭都會同步佇列，剛抓下來的書籤下一輪就會排進去。

        把它算成「不會被處理」，等於讓新書籤在報表上消失。
        """
        self.bookmark("brand-new")
        self.write_queue({})

        counts = self.counts()
        self.assertEqual(counts["unqueued"], 1)
        self.assertEqual(counts["actionable"], 1)
        self.assertEqual(counts["stuck"], 0)

    def test_processing_counts_as_queued(self):
        self.bookmark("inflight")
        self.write_queue({"inflight": "processing"})
        self.assertEqual(self.counts()["queued"], 1)

    def test_carded_bookmarks_are_not_pending_at_all(self):
        self.bookmark("done1")
        self.card("done1")
        self.write_queue({"done1": "done"})
        self.assertEqual(self.counts()["total"], 0)

    def test_an_unreadable_queue_does_not_invent_a_backlog(self):
        """佇列讀不到時，不要假裝那些項目在排隊——那會蓋掉「卡住」的事實。"""
        self.bookmark("x")
        self.queue.write_text("{ not json", encoding="utf-8")

        counts = self.counts()
        self.assertEqual(counts["stuck"], 0)
        self.assertEqual(counts["unqueued"], 1)


class TheMessageUsesIt(unittest.TestCase):
    def test_the_batch_script_separates_the_two(self):
        text = (ROOT / "scripts" / "run_bookmark_batch.sh").read_text(encoding="utf-8")
        self.assertIn("pending_breakdown", text)
        self.assertIn("不會自動重試", text)
        # 「調高 limit」只能對真的在排隊的項目說。
        self.assertIn('"$queued" -gt 0', text)

    def test_the_daily_message_separates_the_two(self):
        sys.path.insert(0, str(ROOT / "scripts"))
        source = (ROOT / "scripts" / "health_check_notify.py").read_text(encoding="utf-8")
        self.assertIn("pending_breakdown", source)
        self.assertIn("卡住了", source)


if __name__ == "__main__":
    unittest.main()
