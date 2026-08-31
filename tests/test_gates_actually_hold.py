"""閘門要真的擋得住,而且乾跑要跟實跑走同一條路。

兩件事都是外部審查在 2026-09-01 抓到的,而且兩件都是「看起來有做,實際沒做」:

TTL 隔離   放行清單少了過期過濾。上面的迴圈對過期候選 continue,所以乾跑
           報「隔離 1、放行 0」;實跑走的是另一條路,同一批統計會說
           「隔離 1、放行 1」——指的是同一條。乾跑因此不可能發現這件事。

逾時       `with ThreadPoolExecutor(...) as pool: fut.result(timeout=N)`
           的 __exit__ 會 shutdown(wait=True),所以逾時之後照樣等它跑完。
           實測宣稱 0.5 秒、實際 3.00 秒。它存在的理由就是不要被卡住的
           端點拖住六秒的 hook 預算,而它一秒都沒擋到。

一個攔不下東西的閘門比沒有閘門更糟,因為它讓人以為擋住了。
"""
from __future__ import annotations

import sys
import tempfile
import time
import unittest
import unittest.mock
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import xkb_review

EXPIRED = """## Candidate 1
- **Topic:** topic-a
- **Confidence:** high
- **Source date:** 2020-01-01
- **Status:** [ ] approve  [ ] skip

這是一條來源日期在 2020 年、早就過期的候選，內容有 https://example.test/evidence 當證據。
"""


class GatesActuallyHoldTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        base = Path(self._tmp.name)

        self.staging = base / "staging"
        self.topics = base / "topics"
        for d in (self.staging, self.topics, base / "gov"):
            d.mkdir()
        (self.topics / "topic-a.md").write_text("---\ntitle: A\n---\n# A\n", encoding="utf-8")
        (self.staging / "x.md").write_text(EXPIRED, encoding="utf-8")

        for name, value in (("STAGING_DIR", self.staging),
                            ("TOPICS_DIR", self.topics),
                            ("GOVERNANCE_DIR", base / "gov")):
            original = getattr(xkb_review, name)
            setattr(xkb_review, name, value)
            self.addCleanup(setattr, xkb_review, name, original)

    def test_an_expired_candidate_does_not_reach_the_wiki(self) -> None:
        stats = xkb_review.governance_batch(limit=10, dry_run=False, ttl_days=30)["stats"]
        self.assertEqual(stats["quarantine"], 1)
        self.assertEqual(stats["promoted"], 0, "隔離了又放行，那不是閘門")
        self.assertNotIn("早就過期",
                         (self.topics / "topic-a.md").read_text(encoding="utf-8"))

    def test_the_preview_agrees_with_the_real_run(self) -> None:
        """乾跑跟實跑必須走同一條路，否則預覽會騙你。

        原本它們不一樣：乾跑報放行 0、實跑放行 1，而使用者看得到的只有乾跑。
        """
        preview = xkb_review.governance_batch(limit=10, dry_run=True, ttl_days=30)["stats"]
        real = xkb_review.governance_batch(limit=10, dry_run=False, ttl_days=30)["stats"]
        for key in ("ttl", "quarantine", "promoted"):
            self.assertEqual(preview[key], real[key], f"乾跑與實跑的 {key} 不一致")


class TimeoutActuallyBoundsTest(unittest.TestCase):
    def test_the_card_layer_gives_up_instead_of_waiting(self) -> None:
        """逾時要真的放棄，不是拋了例外還在等。"""
        import recall_router

        started = time.monotonic()
        with unittest.mock.patch.object(
            recall_router, "ASSOCIATIVE_TIMEOUT_S", 0.3
        ), unittest.mock.patch(
            "recall_for_conversation.search",
            side_effect=lambda *a, **k: (time.sleep(3), {"results": []})[1],
        ):
            recall_router.run_associative_recall("測試", limit=1)
        elapsed = time.monotonic() - started

        self.assertLess(elapsed, 1.5,
                        f"宣稱 0.3 秒逾時，實際等了 {elapsed:.2f} 秒——界線沒有生效")


if __name__ == "__main__":
    unittest.main()
