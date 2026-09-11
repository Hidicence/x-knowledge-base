"""健檢要看得見「東西進來了但沒變成知識」。

2026-09-01 到 09-09，書籤轉卡每晚都失敗、八天 0 產出，而每日健檢報的是
「索引沒有跟上新內容 / search_index last updated: 91.5h ago」。

那句話有兩個問題。一是它指錯地方：索引沒壞，是上游沒東西給它寫。二是它問錯
問題：sync_enriched_index 只在有東西要寫時才寫檔，所以「26 小時內有沒有被寫
過」實際上問的是「今天有沒有新知識進來」——安靜的一天照樣報壞，而真正的故障
它只報得出一個症狀的症狀。

這裡測的是修好之後的兩件事：
  - 索引新鮮度問的是「索引有沒有落後於卡片」，不是「檔案多久沒被動過」。
  - 有一項專門盯擷取管線：佇列不是空的、卻久到不合理沒生出任何新卡片。
"""
from __future__ import annotations

import os
import sys
import time
import tempfile
import unittest
from pathlib import Path
from unittest import mock

SKILL_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL_DIR / "scripts"))

import xkb_paths  # noqa: E402
import health_check_pipeline as hc  # noqa: E402

HOUR = 3600.0


def _touch(path: Path, *, hours_ago: float, body: str = "# card\n") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    when = time.time() - hours_ago * HOUR
    os.utime(path, (when, when))
    return path


class _Sandbox(unittest.TestCase):
    """把健檢指到一個臨時的知識庫，不要碰主機上真的那一份。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        self.cards = root / "cards"
        self.bookmarks = root / "bookmarks"
        self.cards.mkdir()
        self.bookmarks.mkdir()
        self.index = self.bookmarks / "search_index.json"

        patches = [
            mock.patch.object(hc, "CARDS_DIR", self.cards),
            mock.patch.object(hc, "BOOKMARKS_DIR", self.bookmarks),
            mock.patch.object(hc, "INDEX_FILE", self.index),
            # card_files() 是「什麼算一張卡」的唯一定義，健檢也走它。
            mock.patch.object(xkb_paths, "CARDS_DIR", self.cards),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(self._tmp.cleanup)

    @staticmethod
    def _msgs(section: dict) -> str:
        return "\n".join(c["msg"] for c in section["checks"])


class CardProduction(_Sandbox):
    def test_a_full_queue_with_no_new_cards_is_a_fault(self):
        _touch(self.cards / "old.md", hours_ago=96)
        _touch(self.bookmarks / "2096614609457488312.md", hours_ago=20)

        section = hc.check_card_production()

        self.assertFalse(all(c["ok"] for c in section["checks"]))
        self.assertIn("沒有任何新卡片", self._msgs(section))

    def test_an_empty_queue_is_not_a_fault_however_quiet_it_has_been(self):
        """沒有新書籤的時候，很久沒產出新卡片是正常的，不是故障。

        舊的檢查會在這裡報壞——它其實在問「今天有沒有新東西進來」。
        """
        _touch(self.cards / "old.md", hours_ago=240)

        section = hc.check_card_production()

        self.assertTrue(all(c["ok"] for c in section["checks"]), self._msgs(section))

    def test_a_draining_queue_is_not_a_fault(self):
        _touch(self.cards / "fresh.md", hours_ago=3)
        _touch(self.bookmarks / "2096614609457488312.md", hours_ago=2)

        section = hc.check_card_production()

        self.assertTrue(all(c["ok"] for c in section["checks"]), self._msgs(section))


class IndexFreshness(_Sandbox):
    def _write_index(self, *stems: str, hours_ago: float = 1, urls: dict[str, str] | None = None):
        urls = urls or {}
        rows = ",".join(
            '{"relative_path": "memory/cards/%s.md", "source_url": "%s",'
            ' "summary": "s", "enriched": true}' % (s, urls.get(s, ""))
            for s in stems
        )
        _touch(self.index, hours_ago=hours_ago, body='{"items": [%s]}' % rows)

    def test_a_duplicate_card_file_is_not_a_missing_one(self):
        """同一個來源有兩個卡片檔名，索引去重後只留一列。

        內容查得到，這不是故障。比檔名的版本會在這裡誤報——而誤報正是這一項
        原本要修掉的毛病。
        """
        url = "https://github.com/Hidicence/x-knowledge-base"
        _touch(self.cards / "github_fork-x.md", hours_ago=200,
               body=f'---\nsource_url: "{url}"\n---\n')
        _touch(self.cards / "github_star-x.md", hours_ago=200,
               body=f'---\nsource_url: "{url}"\n---\n')
        # 陪襯用的卡片：讓「卡片數 vs 索引數」那一項維持在門檻內，這個測試
        # 要問的是漏索引判斷，不是覆蓋率。
        padding = [f"pad{i}" for i in range(20)]
        for stem in padding:
            _touch(self.cards / f"{stem}.md", hours_ago=200)
        self._write_index("github_fork-x", *padding, hours_ago=100,
                          urls={"github_fork-x": url})

        section = hc.check_index_freshness()

        self.assertTrue(all(c["ok"] for c in section["checks"]), self._msgs(section))

    def test_a_card_the_index_never_picked_up_is_reported(self):
        _touch(self.cards / "indexed.md", hours_ago=200)
        _touch(self.cards / "orphan.md", hours_ago=200)
        self._write_index("indexed", hours_ago=100)

        section = hc.check_index_freshness()

        self.assertFalse(all(c["ok"] for c in section["checks"]))
        self.assertIn("orphan.md", self._msgs(section))

    def test_a_quiet_week_is_not_a_stale_index(self):
        """索引三天沒被寫過，但每張卡都在裡面——這是安靜，不是故障。

        這正是 2026-09-09 那封「索引沒有跟上新內容」誤報的情況。
        """
        _touch(self.cards / "old.md", hours_ago=200)
        self._write_index("old", hours_ago=90)

        section = hc.check_index_freshness()

        self.assertTrue(all(c["ok"] for c in section["checks"]), self._msgs(section))

    def test_a_card_still_in_flight_is_not_a_miss(self):
        """剛產出、還沒輪到同步的卡片不算落後，否則跑批次的當下必定紅燈。"""
        settled = [f"old{i}" for i in range(10)]
        for stem in settled:
            _touch(self.cards / f"{stem}.md", hours_ago=200)
        _touch(self.cards / "just_written.md", hours_ago=0.5)
        self._write_index(*settled, hours_ago=1)

        section = hc.check_index_freshness()

        self.assertTrue(all(c["ok"] for c in section["checks"]), self._msgs(section))

    def test_a_maintenance_rewrite_does_not_look_like_a_stale_index(self):
        """去重／鏡像／回填會動到卡片 mtime。比 mtime 的版本會在這裡誤報。"""
        _touch(self.cards / "old.md", hours_ago=0.1)  # 剛被維護動過，但早就在索引裡
        self._write_index("old", hours_ago=40)

        section = hc.check_index_freshness()

        self.assertTrue(all(c["ok"] for c in section["checks"]), self._msgs(section))


class Wiring(unittest.TestCase):
    def test_the_new_check_is_actually_registered(self):
        """寫了檢查卻沒接進 main()，等於沒寫。"""
        # 問清單，不問某個檔案的字串。原本這裡讀 pipeline 的原始碼，而每天
        # 跑的是 notify——那份清單少了這一項，而這個測試照樣綠。
        self.assertIn("check_card_production", {c.__name__ for c in hc.CHECKS})

    def test_the_daily_message_can_name_it(self):
        """section 名稱是內部識別字；沒有標籤，通知裡就會出現生字。"""
        sys.path.insert(0, str(SKILL_DIR / "scripts"))
        import health_check_notify as notify

        self.assertIn("card_production", notify.FAULT_LABELS)


if __name__ == "__main__":
    unittest.main()
