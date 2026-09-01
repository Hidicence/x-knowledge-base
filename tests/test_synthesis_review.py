"""消化：審閱過的那一份，必須就是併回去的那一份。

原本 --apply 會重跑一次模型。兩次消化同一批條列不會得到一樣的結論，所以
你看過的稿子跟寫進 wiki 的稿子是不同的兩份東西——審閱因此擋不住任何事，
而且同一頁要付兩次錢。

這裡不呼叫模型：把 synthesise() 換成會計次的假貨，就能直接驗證
「--apply 有沒有再產一次」這件事本身。
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import xkb_synthesize_topic as syn

PAGE = """---
title: 測試主題
---

# 測試主題

前言，這一段是人寫的。

- 這是一條夠長的敘述型條列。split_page 只把超過六十個字元的條列當成可以消化的素材，比這短的會被歸進前言，所以這裡刻意寫得夠長。
- 這是第二條夠長的敘述型條列。它同樣要超過六十個字元的門檻，否則會被當成前言的一部分，這個測試也就驗不到它想驗的那件事了。
- 這是第三條。樣本要夠大：扣掉消化不出結論的那一條之後，壓縮比仍然要高於 MIN_COMPRESSION，否則會撞上「這一頁內容彼此不相關」那道閘門。
- 這是第四條。它存在的理由跟第三條一樣，都是為了讓這個樣本頁大到足以走完合併那條路，而不是在壓縮比不足的地方就停住。
- [某個來源](https://example.com/a)
"""

# 條數從樣本頁自己算出來，不寫死。split_page 的門檻是字元數，
# 手動維護一個數字只會在下次改樣本時無聲對不上。
BULLETS = len(syn.split_page(PAGE)[1])

UNDIGESTED = ("- 這一批模型給不出結論。它必須原封不動留在頁面上，"
              "而不是被結論取代掉——下一次消化會再試一次。")

LATER = ("\n- 審閱之後才長出來的第三條。它一樣要超過六十個字元才會被算成素材，"
         "這樣這一頁的指紋才會真的改變；指紋沒變的話，舊審閱稿仍然對得上，"
         "這個測試也就驗不到「頁面變了要停下來」那件事了。\n")


class SynthesisReviewTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

        topics = root / "topics"
        topics.mkdir()
        self.page = topics / "t.md"
        self.page.write_text(PAGE, encoding="utf-8")

        for patch in (
            mock.patch.object(syn.xkb_paths, "WIKI_TOPICS_DIR", topics),
            mock.patch.object(syn, "REVIEW_DIR", root / "_synthesis"),
        ):
            patch.start()
            self.addCleanup(patch.stop)

        self.calls: list[int] = []
        self.lose: list[str] = []

        def fake(topic, prose, bullets, per_chunk):
            self.calls.append(len(bullets))
            return f"- 第 {len(self.calls)} 次消化的結論。", list(self.lose)

        patch = mock.patch.object(syn, "synthesise", fake)
        patch.start()
        self.addCleanup(patch.stop)

    def text(self) -> str:
        return self.page.read_text(encoding="utf-8")

    def test_apply_merges_the_draft_that_was_reviewed(self) -> None:
        self.assertEqual(syn.cmd_topic("t", apply=False), 0)
        self.assertEqual(syn.cmd_topic("t", apply=True), 0)

        self.assertEqual(self.calls, [BULLETS], "--apply 不該再跑一次模型")
        self.assertIn("第 1 次消化的結論", self.text())

    def test_apply_refuses_a_draft_made_from_an_older_page(self) -> None:
        self.assertEqual(syn.cmd_topic("t", apply=False), 0)
        self.page.write_text(self.text() + LATER, encoding="utf-8")

        # 稿子已經不是這一頁的消化結果了：停下來，不要默默重產、也不要併進去。
        self.assertEqual(syn.cmd_topic("t", apply=True), 3)
        self.assertEqual(self.calls, [BULLETS])
        self.assertNotIn("消化的結論", self.text())

    def test_regenerate_is_the_way_to_ask_for_a_new_draft(self) -> None:
        self.assertEqual(syn.cmd_topic("t", apply=False), 0)
        self.assertEqual(syn.cmd_topic("t", apply=True, regenerate=True), 0)

        self.assertEqual(self.calls, [BULLETS, BULLETS])
        self.assertIn("第 2 次消化的結論", self.text())


    def test_a_batch_that_digested_to_nothing_survives_the_merge(self) -> None:
        # 併回去是取代不是附加，所以消化不出來的那一批若沒有被帶過去，
        # 就會從 wiki 永久消失，而且壓縮比會因為分母少了而好看得不像話。
        self.lose = [UNDIGESTED]

        self.assertEqual(syn.cmd_topic("t", apply=False), 0)
        self.assertEqual(syn.cmd_topic("t", apply=True), 0)

        merged = self.text()
        self.assertIn("第 1 次消化的結論", merged)
        self.assertIn(UNDIGESTED, merged, "消化不出結論的條列必須留在頁面上")

    def test_the_undigested_bullets_are_remembered_by_the_draft(self) -> None:
        self.lose = [UNDIGESTED]
        self.assertEqual(syn.cmd_topic("t", apply=False), 0)

        # 隔一次執行才 --apply：那幾條記在審閱稿裡，不在記憶體裡。
        self.lose = []
        self.assertEqual(syn.cmd_topic("t", apply=True), 0)
        self.assertEqual(self.calls, [BULLETS], "應該沿用審閱稿，不是重跑一次")
        self.assertIn(UNDIGESTED, self.text())

    def test_digesting_twice_does_not_digest_the_conclusions(self) -> None:
        """再跑一次，素材是剩下的條列，不是上一輪的結論。

        2026-08-31 的 09:00 通知要求消化四頁，其中三頁前一晚才消化完——因為
        結論本身也是條列。照著做的話，會把結論再壓一次，那些讓結論值得留下
        的具體細節就沒了。
        """
        # 先產審閱稿再併回——沒有稿就 --apply 現在會被擋（回 5）。
        self.assertEqual(syn.cmd_topic("t", apply=False), 0)
        self.assertEqual(syn.cmd_topic("t", apply=True), 0)
        first = self.text()
        self.assertIn("第 1 次消化的結論", first)

        self.calls.clear()
        self.assertEqual(syn.cmd_topic("t", apply=True, regenerate=True), 0)

        # 素材空了就直接結束，連模型都不用叫——這一頁已經沒有東西好消化了。
        self.assertEqual(self.calls, [], "結論不該被當成素材再消化一次")
        self.assertIn("第 1 次消化的結論", self.text(), "既有結論必須原封不動保留")

    def test_the_undigested_bullets_are_offered_again(self) -> None:
        """「尚未消化」的相反：它們本來就在等下一次。"""
        self.lose = [UNDIGESTED]
        self.assertEqual(syn.cmd_topic("t", apply=False), 0)
        self.assertEqual(syn.cmd_topic("t", apply=True), 0)

        self.calls.clear()
        self.lose = []
        syn.cmd_topic("t", apply=False, regenerate=True)
        self.assertEqual(self.calls, [1], "上次消化不出來的那條要再試一次")

    def test_the_daily_summary_counts_the_same_thing(self) -> None:
        """通知不要自己數一套。數錯的那一套，就是每天叫你做已經做完的事。"""
        self.assertEqual(syn.cmd_topic("t", apply=False), 0)
        self.assertEqual(syn.cmd_topic("t", apply=True), 0)
        _, waiting, _, conclusions = syn.undigested(self.text())
        self.assertEqual(waiting, [], "消化完之後就沒有待消化的素材了")
        self.assertIn("第 1 次消化的結論", conclusions)


    def test_apply_without_a_draft_is_refused(self) -> None:
        """沒有審閱稿就併回，等於產完直接寫進 wiki——兩段式流程就沒有意義了。

        原本這道關卡只在「稿子存在但對不上」時才擋，所以一個全新的主題頁
        用一次 --apply 就會被產生並覆寫，中間沒有人看過。
        """
        self.assertEqual(syn.cmd_topic("t", apply=True), 5)
        self.assertEqual(self.calls, [], "被擋下時不該花錢跑模型")
        self.assertNotIn("消化的結論", self.text(), "也不該寫進頁面")

    def test_regenerate_is_the_explicit_way_to_skip_review(self) -> None:
        """明確表示不需要審閱時，仍然放行——擋的是預設行為，不是能力。"""
        self.assertEqual(syn.cmd_topic("t", apply=True, regenerate=True), 0)
        self.assertIn("第 1 次消化的結論", self.text())

if __name__ == "__main__":
    unittest.main()
