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
