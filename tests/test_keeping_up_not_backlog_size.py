"""堆積要問「追不追得上」，不是「積了幾筆」。

2026-09-12 實測：待審候選 180 筆、最舊 42 天，健檢紅燈說「用 xkb_review.py
審核」，而且被歸在「等你決定」底下。查下去：

  - 那 180 筆裡 106 筆是 safe_promotion，治理會自動吸收，一筆都不需要人。
  - 積起來的原因是治理每晚上限 20 筆，而候選進來得比 20 快——連續三天都打在
    上限上。

那是吞吐量設定，不是判斷。而「積了幾筆」這個問法在任何補跑期間都必定紅燈，
於是它每天叫人去審核一件系統自己會做的事。

同一個形狀書籤批次早就吃過：一次五筆、一天一次，而新書籤每天進來——那支腳本
自己的註解寫著「追不上的 worker 不是自動化，是一條慢慢漏的縫」。

這份測試先證明舊問法會誤報，再證明新問法不會。
"""
from __future__ import annotations

import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import health_check_pipeline as hc  # noqa: E402


def _candidate(date: str):
    """只需要 check_staging_backlog 用到的那幾個屬性。"""
    return mock.Mock(source_file="x.md", status="pending",
                     candidate_id=f"c-{date}", source_date=date)


class _Staging(unittest.TestCase):
    """把候選與帳本都換成假的，不要碰真的知識庫。"""

    def setUp(self):
        self._dir = mock.patch.object(
            hc, "WIKI_DIR", ROOT)          # 只要 _staging 存在判斷不會早退
        self._dir.start()
        self.addCleanup(self._dir.stop)
        (ROOT / "_staging").mkdir(exist_ok=True)
        self.addCleanup(lambda: (ROOT / "_staging").rmdir()
                        if (ROOT / "_staging").exists() else None)

    def run_check(self, *, pending: int, oldest_days: int,
                  absorbed, arrived):
        now = datetime.now(timezone.utc).date()
        dates = [str(now - timedelta(days=oldest_days))] + \
                [str(now)] * max(pending - 1, 0)
        fake_review = mock.Mock()
        fake_review._promoted_ids.return_value = set()
        fake_review.load_candidates.return_value = [_candidate(d) for d in dates]
        fake_review.GOVERNANCE_DIR = ROOT
        with mock.patch.dict(sys.modules, {"xkb_review": fake_review}), \
             mock.patch.object(hc, "_governance_rates",
                               lambda: (absorbed, arrived)), \
             mock.patch.object(hc, "_governance_runs", lambda: 3):
            return hc.check_staging_backlog()

    @staticmethod
    def msgs(section):
        return "\n".join(c["msg"] for c in section["checks"])


class TheOldQuestionMisfires(_Staging):
    def test_a_draining_queue_would_have_been_red_under_the_old_rule(self):
        """一個正在被清掉的佇列，用「積了幾筆」問會紅燈。

        這是那天真實的數字：180 筆、42 天，而治理一晚吸收 80 筆、每天進來 6 筆
        ——兩三天就清完。舊規則（pending > 60）在這裡報故障。
        """
        section = self.run_check(pending=180, oldest_days=42,
                                 absorbed=80.0, arrived=6.0)

        # 先確認舊規則真的會誤報，否則下面那個斷言沒有意義。
        self.assertGreater(180, 60)
        # 新規則：追得上就不是故障，不管積了幾筆。
        self.assertTrue(all(c["ok"] for c in section["checks"]), self.msgs(section))
        self.assertIn("追得上", self.msgs(section))


class TheNewQuestionFiresOnlyWhenItShould(_Staging):
    def test_falling_behind_is_a_fault_and_says_what_to_change(self):
        section = self.run_check(pending=180, oldest_days=42,
                                 absorbed=20.0, arrived=35.0)

        self.assertFalse(all(c["ok"] for c in section["checks"]))
        msgs = self.msgs(section)
        self.assertIn("追不上", msgs)
        # 要說出能改的東西，不是叫人去審核。
        self.assertIn("LIMIT", msgs)
        self.assertNotIn("審核", msgs)

    def test_age_alone_is_not_a_fault_while_keeping_up(self):
        """追得上的話，舊的那批排在前面、會自己被吸收掉。

        單看年齡會讓一個正在消化的佇列每天紅燈。
        """
        section = self.run_check(pending=180, oldest_days=42,
                                 absorbed=80.0, arrived=6.0)

        age_checks = [c for c in section["checks"] if "最舊" in c["msg"]]
        self.assertTrue(age_checks, "年齡那一項不見了")
        self.assertTrue(all(c["ok"] for c in age_checks))

    def test_age_is_a_fault_when_falling_behind(self):
        section = self.run_check(pending=180, oldest_days=42,
                                 absorbed=20.0, arrived=35.0)

        age_checks = [c for c in section["checks"] if "最舊" in c["msg"]]
        self.assertFalse(all(c["ok"] for c in age_checks))
        self.assertIn("不會自己消失", self.msgs(section))


class WithoutEnoughLedgerItStaysQuiet(_Staging):
    def test_it_does_not_fall_back_to_the_retired_question(self):
        """帳本不足時不要紅燈。

        原本的 fallback 是退回看「積了幾筆」——而那正是要淘汰的問法，且現在
        恰好就是補跑期間。拿一個已知會誤報的判準去填空窗，等於把噪音留在門口。
        """
        now = datetime.now(timezone.utc).date()
        fake_review = mock.Mock()
        fake_review._promoted_ids.return_value = set()
        fake_review.load_candidates.return_value = [
            _candidate(str(now)) for _ in range(180)]
        fake_review.GOVERNANCE_DIR = ROOT
        with mock.patch.dict(sys.modules, {"xkb_review": fake_review}), \
             mock.patch.object(hc, "_governance_rates", lambda: (None, None)), \
             mock.patch.object(hc, "_governance_runs", lambda: 1):
            section = hc.check_staging_backlog()

        self.assertTrue(all(c["ok"] for c in section["checks"]), self.msgs(section))
        self.assertIn("才算得出吸收速度", self.msgs(section))


class ItIsNoLongerPanDecision(unittest.TestCase):
    def test_staging_is_not_in_the_decision_lane(self):
        """吞吐量問題放進「等你決定」，等於每天叫人決定一件系統會做的事。"""
        import health_check_notify as notify
        self.assertNotIn("staging_backlog", notify.DECISION_SECTIONS)
        self.assertNotIn("governance_actionable", notify.DECISION_SECTIONS)

    def test_governance_records_its_throughput(self):
        """速度要從帳本算——實際發生過的量，不是設定值。"""
        text = (ROOT / "scripts" / "run_candidate_governance.sh").read_text(encoding="utf-8")
        self.assertIn("--stage candidate-governance", text)


if __name__ == "__main__":
    unittest.main()
