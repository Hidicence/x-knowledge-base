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

    def test_quoted_prose_is_not_an_identifier(self) -> None:
        # 引號不是免死金牌：隨口一句 "good idea" 不該拿到字面命中的特權
        self.assertEqual(rfc._identifier_tokens('他說那是個 "good idea" 啦'), [])

    def test_quoted_identifier_still_works(self) -> None:
        self.assertIn("gpt-5.6", rfc._identifier_tokens('查一下 "gpt-5.6" 的卡'))

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
        orig_sim, orig_key = xr.similarities, xr.vector_key
        self.addCleanup(setattr, xr, "similarities", orig_sim)
        self.addCleanup(setattr, xr, "vector_key", orig_key)
        xr.similarities = lambda q, keys: {k: 0.10 for k in keys if k}
        xr.vector_key = lambda s: s  # 讓鍵可解析
        kept, dropped, _ = xr.filter_irrelevant(
            "q", items, key_of=lambda i: i["id"], threshold=0.55)
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


class RouterMappingIsSharedAndSafe(unittest.TestCase):
    SRC = (ROOT / "scripts" / "recall_router.py").read_text(encoding="utf-8")

    def test_the_assoc_dict_mapping_has_one_definition(self) -> None:
        # 以前這個映射有三份手抄，其中兩份 source_type 判斷還不一樣。
        self.assertIn("def _assoc_dict(", self.SRC)
        # run_associative_recall 與 light 路徑都要呼叫它，不能各自重寫 dict literal
        assoc = self.SRC[self.SRC.index("def run_associative_recall"):]
        assoc = assoc[:assoc.index("\n\ndef ")]
        self.assertIn("_assoc_dict(", assoc)
        self.assertNotIn('"source_type": "card" if', assoc,
                         "run_associative_recall 還在自己組 dict，沒走共用函式")

    def test_source_type_is_card_for_memory_cards_paths(self) -> None:
        import recall_router as rr
        d = rr._assoc_dict({"relative_path": "memory/cards/github_star-x.md",
                            "title": "x", "summary": "y", "score": 0.8})
        self.assertEqual(d["source_type"], "card")

    def test_light_identifier_recall_reports_index_failure_not_silent(self) -> None:
        import recall_router as rr
        import recall_for_conversation as rfc
        import xkb_failures

        noted = []
        orig_load, orig_note = rfc.load_index, xkb_failures.note
        self.addCleanup(setattr, rfc, "load_index", orig_load)
        self.addCleanup(setattr, xkb_failures, "note", orig_note)

        xkb_failures.note = lambda where, err: noted.append((where, type(err).__name__))

        # 兩種執行期壞法都要：出聲、route() 不中止、其他 soft 召回照回。
        for breaker, exc in (
            (FileNotFoundError("search index gone"), "FileNotFoundError"),
            (["not", "a", "dict"], "AttributeError"),  # JSON 壞成 list -> .get() 炸
        ):
            noted.clear()
            if isinstance(breaker, Exception):
                rfc.load_index = lambda *a, _e=breaker, **k: (_ for _ in ()).throw(_e)
            else:
                rfc.load_index = lambda *a, _v=breaker, **k: _v
            r = rr.route("2045420631295242340")
            self.assertIsInstance(r, dict, f"{exc}: route() 中止了整個召回")
            self.assertIn("formatted_text", r, f"{exc}: route() 回了殘缺的 dict")
            self.assertTrue(noted, f"{exc} 被靜默吞掉了")
            self.assertEqual(noted[0][1], exc)

    def test_import_failure_is_not_swallowed(self) -> None:
        # symbol 改名 / 模組搬走是硬性安裝錯誤——import 在 try 外，要當場炸。
        src = (ROOT / "scripts" / "recall_router.py").read_text(encoding="utf-8")
        block = src[src.index("light scan 不跑，太貴"):]
        block = block[:block.index("\n        else:")]
        import_pos = block.index("from recall_for_conversation import (")
        try_pos = block.index("try:")
        self.assertLess(import_pos, try_pos,
                        "identifier import 在 try 裡面——會被 except 吞掉")


if __name__ == "__main__":
    unittest.main()
