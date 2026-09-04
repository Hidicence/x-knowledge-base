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
        for q in ["2045420631295242340", "gpt-5.6-instruct", "ERR_CONNECTION_RESET",
                  "v2.3.1", "nash_su", "zvec-ai/zvec-grep", "sha256"]:
            self.assertTrue(rr._looks_specific(q), q)

    def test_prose_is_not_specific(self):
        for q in ["今天天氣真好", "AI agent memory", "memory systems",
                  "how to rank recall", "怎麼做碳盤查", "隨便聊聊"]:
            self.assertFalse(rr._looks_specific(q), q)

    def test_bare_numbers_are_not_specific(self):
        # 「3 個重點」「2024 年回顧」「第 5 頁」以前會因為單一數字被當成識別碼查詢。
        for q in ["3 個重點", "2024 年回顧", "第 5 頁", "整理成 10 條"]:
            self.assertFalse(rr._looks_specific(q), q)

    def test_hyphenated_english_is_not_specific(self):
        # 純連字號英文詞不含數字／底線／斜線，一律不算 specific——precision
        # 優先。代價：沒帶 URL 的 repo slug（Open-Magiviz、infiniflow-ragflow）
        # 也不會觸發 BM25 腿，但它們跟 open-source 在字面上無法區分。
        for q in ["trade-off", "end-to-end", "open-source", "real-time",
                  "machine-learning", "Open-Magiviz", "infiniflow-ragflow"]:
            self.assertFalse(rr._looks_specific(q), q)


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
        # 真實的列一定帶 source_type（_assoc_dict 一定會給）。卡片跨腿合併靠
        # 路徑，帶不帶 source_type 都要成立。
        out = xs.rank([
            {"source_type": "card", "source_file": "cards/a.md", "section": "A",
             "score": 0.6, "score_scale": "card_semantic"},
            {"source_type": "card", "source_file": "cards/a.md", "section": "A（索引版）",
             "score": 12.0, "score_scale": "card_bm25"},
            {"source_type": "card", "source_file": "cards/b.md", "section": "B",
             "score": 0.7, "score_scale": "card_semantic"},
        ])
        paths = [r["source_file"] for r in out]
        self.assertEqual(paths.count("cards/a.md"), 1)
        self.assertEqual(out[0]["source_file"], "cards/a.md")
        self.assertEqual(set(out[0]["matched_by"]), {"card_semantic", "card_bm25"})

    def test_same_wiki_section_from_one_leg_dedups(self):
        # 同一條腿、同一頁同一段回兩次（合併後 _legs 可能有重複列）-> 一筆。
        out = xs.rank([
            {"source_type": "wiki", "source_file": "wiki/topics/x.md", "section": "四層架構",
             "score": 0.8, "score_scale": "wiki_semantic"},
            {"source_type": "wiki", "source_file": "wiki/topics/x.md", "section": "四層架構",
             "score": 0.6, "score_scale": "wiki_semantic"},
        ])
        self.assertEqual(len(out), 1)

    def test_wiki_same_section_two_legs_merges(self):
        # F6：語意腿（wiki_semantic）和 BM25 腿（wiki）撈到同一頁同一段
        # -> 一筆，兩條腿的 RRF 都算。source_type 字串不同不該擋住合併。
        out = xs.rank([
            {"source_type": "wiki_semantic", "source_file": "wiki/topics/x.md",
             "section": "四層架構", "score": 0.8, "score_scale": "wiki_semantic"},
            {"source_type": "wiki", "source_file": "wiki/topics/x.md",
             "section": "四層架構", "score": 9.0, "score_scale": "wiki_bm25"},
        ])
        self.assertEqual(len(out), 1)
        self.assertEqual(set(out[0]["matched_by"]), {"wiki_semantic", "wiki_bm25"})

    def test_wiki_different_sections_stay_separate(self):
        out = xs.rank([
            {"source_type": "wiki_semantic", "source_file": "wiki/topics/x.md",
             "section": "四層架構", "score": 0.8, "score_scale": "wiki_semantic"},
            {"source_type": "wiki", "source_file": "wiki/topics/x.md",
             "section": "退場機制", "score": 9.0, "score_scale": "wiki_bm25"},
        ])
        self.assertEqual(len(out), 2)


if __name__ == "__main__":
    unittest.main()
