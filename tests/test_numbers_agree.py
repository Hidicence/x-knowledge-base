"""系統報給你的每一個數字，都要有第二種算法同意它。

2026-08-31 這一天,同一個缺陷出現六次,形狀完全一樣:**同一件事有兩個定義**。

    資料路徑        xkb_paths vs 自己拼,28 處
    有幾張卡片      glob vs rglob,1538 vs 1539
    還有多少沒消化   工具算一套、健檢算一套,結論被當成待辦
    self-derived   三個寫法,913 條繞過回音室降權
    有多少待處理     治理說 3、健檢說 77
    有幾條提案      導向前數 vs 導向後數,說 1、實際 0

前五個是使用者一個一個問出來的——那是最差的發現方式,也是為什麼修到後來
會讓人覺得沒完沒了。第六個是把清單列出來一次掃出來的。

所以這個測試存在的理由不是「再加一道防線」,是**換一種發現方式**:
XKB 會報的數字是有限的,每一個都在這裡被獨立算第二次。對不上就是
兩個定義又出現了,不必等到某天早上的通知說錯話。
"""
from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tools"))

import xkb_paths


class NumbersAgreeTest(unittest.TestCase):
    """每一項都拿系統的答案，對上一個不共用實作的答案。"""

    def test_card_count_has_one_answer(self) -> None:
        """cards/ 第一層的 .md。子目錄放的是治理判定重複而移走的，不算。"""
        independent = len([p for p in xkb_paths.CARDS_DIR.iterdir()
                           if p.is_file() and p.suffix == ".md"])
        self.assertEqual(len(xkb_paths.card_files()), independent)
        self.assertEqual(len(xkb_paths.card_ids()), independent)

    def test_pending_bookmarks_have_one_answer(self) -> None:
        """一個宣告自己是 knowledge-card 的檔案已經是成品，不是待辦。"""
        from xkb_pending_work import uncarded_bookmarks

        card_ids = xkb_paths.card_ids()
        independent = 0
        for path in xkb_paths.BOOKMARKS_DIR.rglob("*.md"):
            if not path.is_file() or path.stem in card_ids:
                continue
            if "type: knowledge-card" in path.read_text(encoding="utf-8", errors="ignore")[:400]:
                continue
            independent += 1
        reported = len(uncarded_bookmarks(xkb_paths.BOOKMARKS_DIR, xkb_paths.CARDS_DIR))
        self.assertEqual(reported, independent)

    def test_governance_counts_match_what_governance_would_do(self) -> None:
        """通知說的待處理，必須等於治理下一輪真的會處理的。

        健檢原本數的是「staging 裡還是 pending 的」，而治理刻意不動 staging，
        所以一個已經被看過、判定不放行的候選永遠算成待辦——報 77，實際 3。
        永遠亮著的紅燈跟永遠不亮的綠燈一樣，看久了就不看了。
        """
        import xkb_review

        counts = xkb_review.governance_health_counts(30)
        batch = xkb_review.governance_batch(limit=10_000, dry_run=True, ttl_days=30)
        self.assertEqual(counts["pending"], batch["stats"]["discovered"])
        self.assertEqual(
            counts["proposal"],
            len([t for t in batch["topic_suggestions"] if t["action"] == "proposal"]),
            "提案要在導向 general 之後才數，跟治理同一個順序",
        )

    def test_bloated_pages_exclude_what_was_already_digested(self) -> None:
        """結論本身也是條列。把它算成待消化，就會每天叫你做已經做完的事。"""
        from xkb_synthesize_topic import undigested

        for path in sorted(xkb_paths.WIKI_TOPICS_DIR.glob("*.md")):
            body = path.read_text(encoding="utf-8", errors="ignore")
            # 人寫的部分，加上「尚未消化」那一段。結論不算——它已經消化過了；
            # 但尚未消化那一段要算，它就是在等下一次。第一版把三段一起切掉，
            # 於是 openclaw-agent-workflows 的 480 條變成 0。
            head = re.split(
                r"^## (?:結論（消化自累積筆記）|尚未消化|出處)\s*$",
                body, maxsplit=1, flags=re.M,
            )[0]
            waiting = ""
            if "## 尚未消化\n" in body:
                waiting = body.split("## 尚未消化\n", 1)[1].split("\n## ", 1)[0]
            trimmed = head + "\n" + waiting
            # 純連結是出處，不是素材。算進去的話 ai-seo-and-geo 會從 11 條
            # 變成 55 條——多出來的 44 條長這樣：
            #     - [標題](https://x.com/...) — 2025-02，xkb
            # 尾巴是日期與來源標記，所以「開頭是連結」就足以判定，不需要
            # 整行都是連結。第一版寫成整行才算，於是漏掉全部 44 條。
            #
            # 這裡的「獨立」不是指寫出不一樣的規則，是指不呼叫同一段程式：
            # 規則在兩個地方各自寫一次，其中一邊改了而另一邊沒跟上時，
            # 這個測試會失敗——那正是今天六個 bug 的共同形狀。
            link_only = re.compile(r"^\s*-\s*\[[^\]]+\]\(https?://")
            independent = [line for line in trimmed.splitlines()
                           if line.lstrip().startswith("- ")
                           and len(line.strip()) > 60
                           and not link_only.match(line)]
            self.assertEqual(
                len(undigested(body)[1]), len(independent),
                f"{path.name}：待消化的條數有兩個答案",
            )


if __name__ == "__main__":
    unittest.main()
