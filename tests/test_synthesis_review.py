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
- [某個來源](https://example.com/a)
"""

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

        self.assertEqual(self.calls, [2], "--apply 不該再跑一次模型")
        self.assertIn("第 1 次消化的結論", self.text())

    def test_apply_refuses_a_draft_made_from_an_older_page(self) -> None:
        self.assertEqual(syn.cmd_topic("t", apply=False), 0)
        self.page.write_text(self.text() + LATER, encoding="utf-8")

        # 稿子已經不是這一頁的消化結果了：停下來，不要默默重產、也不要併進去。
        self.assertEqual(syn.cmd_topic("t", apply=True), 3)
        self.assertEqual(self.calls, [2])
        self.assertNotIn("消化的結論", self.text())

    def test_regenerate_is_the_way_to_ask_for_a_new_draft(self) -> None:
        self.assertEqual(syn.cmd_topic("t", apply=False), 0)
        self.assertEqual(syn.cmd_topic("t", apply=True, regenerate=True), 0)

        self.assertEqual(self.calls, [2, 2])
        self.assertIn("第 2 次消化的結論", self.text())


    def test_a_batch_that_digested_to_nothing_blocks_the_merge(self) -> None:
        # 併回去是取代不是附加，所以消化不出來的那一批會就此從 wiki 消失。
        # 壓縮比也會因為分母少了而好看得不像話。
        self.lose = ["- 這一批消化不出任何結論，它必須擋下合併，而不是被靜默丟掉。"]

        self.assertEqual(syn.cmd_topic("t", apply=False), 0)
        self.assertEqual(syn.cmd_topic("t", apply=True), 4)
        self.assertNotIn("消化的結論", self.text())

    def test_the_loss_is_remembered_by_the_draft(self) -> None:
        self.lose = ["- 這一批消化不出任何結論，它必須擋下合併，而不是被靜默丟掉。"]
        self.assertEqual(syn.cmd_topic("t", apply=False), 0)

        # 隔一次執行才 --apply，也要擋——記錄在審閱稿裡，不在記憶體裡。
        self.lose = []
        self.assertEqual(syn.cmd_topic("t", apply=True), 4)
        self.assertEqual(self.calls, [2], "應該沿用審閱稿，不是重跑一次")


if __name__ == "__main__":
    unittest.main()
