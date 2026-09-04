"""BM25 leg wired through route() / filter_irrelevant / rank() — the integration
paths the deleted test_identifier_recall.py used to guard.
"""
from __future__ import annotations
import sys, unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import recall_router as rr
import xkb_relevance as xrel
import xkb_score as xs


class LooksSpecific(unittest.TestCase):
    def test_identifier_shaped_queries(self):
        for q in ["Open-Magiviz", "2045420631295242340", "gpt-5.6-instruct",
                  "ERR_CONNECTION_RESET", "infiniflow-ragflow", "v2.3.1", "nash_su"]:
            self.assertTrue(rr._looks_specific(q), q)

    def test_prose_is_not_specific(self):
        for q in ["今天天氣真好", "AI agent memory", "memory systems",
                  "how to rank recall", "怎麼做碳盤查", "隨便聊聊"]:
            self.assertFalse(rr._looks_specific(q), q)

    def test_bare_numbers_are_not_specific(self):
        # 「3 個重點」「2024 年回顧」「第 5 頁」以前會因為單一數字被當成識別碼查詢。
        for q in ["3 個重點", "2024 年回顧", "第 5 頁", "整理成 10 條"]:
            self.assertFalse(rr._looks_specific(q), q)

    def test_known_false_positive_hyphenated_english_is_tolerated(self):
        # trade-off / end-to-end 跟 infiniflow-ragflow 在字面上無法區分；既有
        # 測試把後者釘成 specific，所以這個偽陽是刻意接受的（只多跑一次 BM25
        # 查詢、可能升 side_hint，低傷害）。這條測試把它記錄下來。
        self.assertTrue(rr._looks_specific("trade-off"))


class BM25SurvivesTheRelevanceFilter(unittest.TestCase):
    def test_bm25_hit_kept_below_cosine_floor_and_scale_not_rewritten(self):
        # 非-light 路徑：_drop_irrelevant_cards -> filter_irrelevant 的 0.55 餘弦
        # 門檻不能砍掉 BM25 命中，也不能把 score_scale 改寫掉（下游要靠它判斷
        # side_hint）。
        items = [
            {"id": "a", "score": 8.0, "score_scale": "card_bm25"},
            {"id": "b", "score": 0.9, "source_type": "card"},
        ]
        orig_sim = xrel.similarities
        self.addCleanup(setattr, xrel, "similarities", orig_sim)
        xrel.similarities = lambda q, keys: {k: 0.10 for k in keys if k}
        orig_key = xrel.vector_key
        self.addCleanup(setattr, xrel, "vector_key", orig_key)
        xrel.vector_key = lambda s: s
        kept, _, _ = xrel.filter_irrelevant("q", items, key_of=lambda i: i["id"],
                                            threshold=0.55)
        by_id = {i["id"]: i for i in kept}
        self.assertIn("a", by_id, "BM25 命中被餘弦門檻砍了")
        self.assertEqual(by_id["a"]["score_scale"], "card_bm25", "score_scale 被改寫")
        self.assertNotIn("b", by_id, "非字面、低於門檻的照樣要砍")


class RankDedupsAndAccumulatesLegs(unittest.TestCase):
    def test_two_leg_hit_beats_single_leg_and_appears_once(self):
        out = xs.rank([
            {"source_file": "cards/a.md", "score": 0.6, "score_scale": "card_semantic"},
            {"source_file": "cards/a.md", "score": 12.0, "score_scale": "card_bm25"},
            {"source_file": "cards/b.md", "score": 0.7, "score_scale": "card_semantic"},
        ])
        paths = [r["source_file"] for r in out]
        self.assertEqual(paths.count("cards/a.md"), 1)
        self.assertEqual(out[0]["source_file"], "cards/a.md")
        self.assertEqual(set(out[0]["matched_by"]), {"card_semantic", "card_bm25"})

    def test_same_wiki_page_from_two_legs_is_one_row(self):
        out = xs.rank([
            {"source_file": "wiki/topics/x.md", "score": 0.8, "score_scale": "wiki_semantic"},
            {"source_file": "wiki/topics/x.md", "score": 9.0, "score_scale": "wiki_bm25"},
        ])
        self.assertEqual(len(out), 1)


if __name__ == "__main__":
    unittest.main()
