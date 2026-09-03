"""The identifier-exact-match leg.

Measured before: 12 identifier queries, hit@3 = 5/12, every miss a card that
exists and is indexed. Vector search has no representation for a repo slug or a
tweet ID, so the right card never enters the vector-ranked candidate set and the
keyword leg — which only re-weights that set — cannot recover it. This leg scans
the index for a literal match when the query contains identifier-shaped tokens.

Two ways it can go wrong, both covered here: it must not fire on prose or years
(over-injection), and it must actually surface the card when it does fire.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import recall_for_conversation as rfc


class IdentifierTokenDetection(unittest.TestCase):
    def test_real_identifiers_are_recognised(self) -> None:
        for q in ["2045420631295242340", "gpt-5.6-instruct", "ERR_CONNECTION_RESET_1042",
                  "nash_su", "infiniflow-ragflow", 'search "Open-Magiviz" repo']:
            self.assertTrue(rfc._identifier_tokens(q),
                            f"{q!r} 應該被認成含識別碼")

    def test_prose_and_years_are_not_identifiers(self) -> None:
        for q in ["碳盤查要怎麼做", "AI agent memory systems", "2026 update",
                  "python tool", "how to rank recall", "video editing rhythm"]:
            self.assertEqual(rfc._identifier_tokens(q), [],
                             f"{q!r} 不該被當成識別碼查詢")


class IdentifierRecall(unittest.TestCase):
    ITEMS = [
        {"id": "github_star-ItusiAI-Open-Magiviz", "title": "Open Magiviz — AI 影片工具",
         "source_url": "https://github.com/ItusiAI/Open-Magiviz",
         "relative_path": "cards/github_star-ItusiAI-Open-Magiviz.md", "summary": "一個影片工作流工具"},
        {"id": "2045420631295242340", "title": "GBrain v0.11 推出 Minions 模組",
         "source_url": "https://x.com/i/status/2045420631295242340",
         "relative_path": "cards/99-general/2045420631295242340.md", "summary": "BullMQ 任務佇列"},
        {"id": "note-2026-planning", "title": "2026 年度規劃", "source_url": "",
         "relative_path": "cards/note-2026-planning.md", "summary": "2026 目標與里程碑"},
        {"id": "note-2026-review", "title": "2026 檢討", "source_url": "",
         "relative_path": "cards/note-2026-review.md", "summary": "2026 年回顧"},
    ] + [
        {"id": f"filler-{i}", "title": f"2026 筆記 {i}", "source_url": "",
         "relative_path": f"cards/filler-{i}.md", "summary": "2026"} for i in range(20)
    ]

    def test_exact_id_surfaces_the_card_first(self) -> None:
        hits = rfc.identifier_recall("2045420631295242340", self.ITEMS, limit=5)
        self.assertTrue(hits, "精確 tweet ID 應該召回到卡片")
        self.assertIn("2045420631295242340", hits[0]["relative_path"])
        self.assertIn("字面命中", hits[0]["relevance_reason"])

    def test_repo_slug_surfaces_the_card(self) -> None:
        hits = rfc.identifier_recall("Open-Magiviz", self.ITEMS, limit=5)
        self.assertTrue(hits)
        self.assertIn("Open-Magiviz", hits[0]["relative_path"])

    def test_a_token_matching_many_cards_is_dropped_as_noise(self) -> None:
        # "2026" 命中 20+ 張填充卡 —— 不是識別碼，這一腿應該完全不回東西
        hits = rfc.identifier_recall("2026 planning", self.ITEMS, limit=5)
        self.assertEqual(hits, [],
                         "命中太多卡片的 token 應被判為雜訊，不灌進召回")

    def test_hard_field_match_scores_above_soft(self) -> None:
        in_id = rfc.identifier_recall("2045420631295242340", self.ITEMS, limit=5)[0]["score"]
        soft_only = rfc.identifier_recall(
            "widget_xyz",
            [{"id": "c1", "title": "about widget_xyz", "relative_path": "cards/c1.md",
              "source_url": "", "summary": ""}], limit=5)[0]["score"]
        self.assertGreater(in_id, soft_only)

    def test_hits_are_tagged_literal_for_the_downstream_filter(self) -> None:
        # 這是審查抓到的缺口：透過 router 的路徑上，_drop_irrelevant_cards 會
        # 用 0.55 的餘弦門檻砍結果，而識別碼的餘弦一定低。命中必須帶
        # match_kind="literal"，下游才知道放它過。
        hits = rfc.identifier_recall("2045420631295242340", self.ITEMS, limit=3)
        self.assertTrue(all(h.get("match_kind") == "literal" for h in hits))

    def test_quoted_multiword_phrase_is_an_identifier(self) -> None:
        self.assertIn("machine learning survey",
                      rfc._identifier_tokens('"machine learning survey" 相關卡片'))

    def test_trailing_ascii_period_is_stripped(self) -> None:
        self.assertIn("gpt-5.6", rfc._identifier_tokens("which model is gpt-5.6."))


class LiteralMatchesSurviveTheRelevanceFilter(unittest.TestCase):
    def test_filter_irrelevant_keeps_literal_matches_below_cosine_floor(self) -> None:
        import xkb_relevance as xr
        items = [
            {"id": "a", "score": 0.9, "match_kind": "literal"},
            {"id": "b", "score": 0.9},
        ]
        # 索引解不出 id 'a'/'b' 的向量鍵 => similarity 判不出來；但 literal 那筆
        # 一定要留下。用一個保證低於門檻的假 similarities。
        orig = xr.similarities
        xr.similarities = lambda q, keys: {k: 0.10 for k in keys if k}
        xr.vector_key = lambda s: s  # 讓鍵可解析
        try:
            kept, dropped, _ = xr.filter_irrelevant(
                "q", items, key_of=lambda i: i["id"], threshold=0.55)
        finally:
            xr.similarities = orig
        kept_ids = {i["id"] for i in kept}
        self.assertIn("a", kept_ids, "字面命中的項目被餘弦門檻砍掉了")
        self.assertNotIn("b", kept_ids, "非字面命中、低於門檻的項目應該被砍")


class SearchMergeBehaviour(unittest.TestCase):
    def test_prose_query_does_not_touch_search_mode(self) -> None:
        src = (ROOT / "scripts" / "recall_for_conversation.py").read_text(encoding="utf-8")
        # the merge must be gated on _identifier_tokens(query) being non-empty
        self.assertIn("if _identifier_tokens(query):", src)
        # and the index is only loaded inside that gate, not on every search
        gate = src.index("if _identifier_tokens(query):")
        load_after = src.index("load_index(index_path)", gate)
        self.assertLess(gate, load_after)
        self.assertNotIn("load_index(index_path)", src[:gate].rsplit("def search(", 1)[-1])


if __name__ == "__main__":
    unittest.main()
