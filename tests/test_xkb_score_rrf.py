"""xkb_score.rank() — reciprocal rank fusion.

rank() replaced a hand-tuned anchor table (one anchor per scale, measured on 8
queries) with RRF: unified_score = Σ weight_leg/(K + rank_in_leg); below-floor
hits then get -1.0 so they sort last regardless of leg size.
The properties that matter, and the ones review found broken in the first cut:
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import xkb_score as xs


class RankFusesRanksNotScales(unittest.TestCase):
    def test_keyword_score_cannot_dominate_a_cosine_score(self) -> None:
        # 舊系統的核心 bug：關鍵字分數 6~20、餘弦 0~1，直接比大小。RRF 之下
        # 兩者都只是「腿內第 1 名」，貢獻幾乎相同。
        out = xs.rank([
            {"t": "kw", "score": 15.0, "score_scale": "card_keyword"},
            {"t": "cos", "score": 0.62, "score_scale": "card_semantic"},
        ])
        self.assertLess(abs(out[0]["unified_score"] - out[1]["unified_score"]), 0.001)

    def test_weak_sole_member_of_a_thin_leg_does_not_steal_rank_one(self) -> None:
        # review finding 1：wiki 腿只有十幾個 topic，常常只回一個弱命中。純 RRF
        # 會讓那個弱命中拿腿內第 1、壓過強卡片。相關度地板要擋住這個。
        out = xs.rank([
            {"t": "strong_card", "score": 0.9, "score_scale": "card_semantic"},
            {"t": "weak_wiki", "score": 0.31, "score_scale": "wiki_semantic"},
        ])
        self.assertEqual(out[0]["t"], "strong_card")

    def test_a_legitimate_wiki_hit_still_outranks_a_card_by_authority(self) -> None:
        # 但正常強度的 wiki 命中（過了自己的門檻）還是靠權威度排在卡片之前。
        out = xs.rank([
            {"t": "wiki", "score": 0.45, "score_scale": "wiki_semantic"},
            {"t": "card", "score": 0.75, "score_scale": "card_semantic"},
        ])
        self.assertEqual(out[0]["t"], "wiki")

    def test_below_floor_hit_sorts_behind_every_above_floor_hit_any_leg_size(self) -> None:
        # review：eff = i + 20 只把弱命中推到某條腿的前 ~17 名之後。一條腿有
        # 25 筆時，第 18~25 的真命中還是會被弱命中壓過。分層排序不管腿多長都對。
        cards = [{"t": f"c{n}", "score": 0.72 - n * 0.006, "score_scale": "card_semantic"}
                 for n in range(25)]
        weak_wiki = {"t": "weak", "score": 0.29, "score_scale": "wiki_semantic"}
        out = xs.rank(cards + [weak_wiki])
        self.assertEqual(out[-1]["t"], "weak",
                         "下地板命中沒有排在所有上地板命中之後")

    def test_leg_rank_and_unified_score_do_not_contradict(self) -> None:
        # review finding 2：leg_rank 存 penalty 前的 i，unified 反映 i+20。
        # 分層排序沒有動名次，兩者不該打架：同一條腿裡 leg_rank 小 => unified 大。
        out = xs.rank([
            {"t": "a", "score": 0.9, "score_scale": "card_semantic"},
            {"t": "b", "score": 0.7, "score_scale": "card_semantic"},
            {"t": "c", "score": 0.6, "score_scale": "card_semantic"},
        ])
        by_leg_rank = sorted(out, key=lambda r: r["leg_rank"])
        by_unified = sorted(out, key=lambda r: r["unified_score"], reverse=True)
        self.assertEqual([r["t"] for r in by_leg_rank], [r["t"] for r in by_unified])

    def test_authority_is_a_few_ranks_not_a_domination(self) -> None:
        # wiki 腿內第 3 名壓過卡片第 1 名可以；第 8 名不行。
        legs_wiki = [{"t": f"w{i}", "score": 0.8 - i * 0.02, "score_scale": "wiki_semantic"}
                     for i in range(10)]
        card1 = {"t": "c1", "score": 0.85, "score_scale": "card_semantic"}
        out = xs.rank(legs_wiki + [card1])
        pos = [r["t"] for r in out].index("c1")
        self.assertGreaterEqual(pos, 2, "卡片第 1 名被 wiki 壓太多")
        self.assertLessEqual(pos, 6, "卡片第 1 名壓過太多 wiki")

    def test_zero_or_missing_score_gets_no_rank_and_sorts_last(self) -> None:
        out = xs.rank([
            {"t": "real", "score": 0.7, "score_scale": "card_semantic"},
            {"t": "zero", "score": 0.0, "source_type": "action"},
            {"t": "missing", "source_type": "action"},
        ])
        self.assertEqual(out[0]["t"], "real")
        self.assertLess(out[-1]["unified_score"], out[0]["unified_score"])
        self.assertEqual({r["t"] for r in out[1:]}, {"zero", "missing"})

    def test_rank_is_idempotent(self) -> None:
        d = [{"t": "x", "score": 0.7, "score_scale": "card_semantic"},
             {"t": "y", "score": 0.5, "score_scale": "card_semantic"}]
        xs.rank(d); xs.rank(d)
        third = xs.rank(d)
        for item in third:
            self.assertEqual(item["matched_by"], ["card_semantic"])
        self.assertEqual([r["t"] for r in third], [r["t"] for r in xs.rank(d)])

    def test_unified_score_and_relevance_agree_on_direction_within_a_leg(self) -> None:
        # review finding 3：召回服務叫模型「拿 relevance 和 unified_score 當提示」，
        # 兩者在同一條腿裡不能反向。
        out = xs.rank([
            {"t": "hi", "score": 0.9, "score_scale": "card_semantic"},
            {"t": "lo", "score": 0.6, "score_scale": "card_semantic"},
        ])
        by_u = sorted(out, key=lambda r: r["unified_score"], reverse=True)
        by_r = sorted(out, key=lambda r: r["relevance"], reverse=True)
        self.assertEqual([r["t"] for r in by_u], [r["t"] for r in by_r])


    def test_unified_score_order_matches_returned_order_across_tiers(self) -> None:
        # review：sort key 是 (tier, -rrf) 但 unified_score 只存 rrf，於是照
        # unified_score 重排會把被降級的弱命中又抬回去。兩者必須一致。
        cards = [{"t": f"c{n}", "score": 0.72 - n * 0.01, "score_scale": "card_semantic"}
                 for n in range(6)]
        weak = {"t": "weak", "score": 0.29, "score_scale": "wiki_semantic"}
        out = xs.rank(cards + [weak])
        by_unified = sorted(out, key=lambda r: r["unified_score"], reverse=True)
        self.assertEqual([r["t"] for r in out], [r["t"] for r in by_unified])


class RankKeyAndLegDedup(unittest.TestCase):
    def test_same_key_same_leg_adds_the_rrf_term_once(self) -> None:
        # 合併後 _legs 對同一條腿有兩筆（同頁 wiki 兩段被舊 _key 併掉、或
        # search() 不再去重後的重複列）。w/(K+i) 不能加兩次、matched_by 不能重複。
        row = lambda sc: {"source_type": "wiki", "source_file": "wiki/topics/x.md",
                          "section": "a", "score": sc, "score_scale": "wiki_semantic"}
        one = xs.rank([row(0.8)])
        dup = xs.rank([row(0.8), row(0.5)])
        self.assertEqual(len(dup), 1)
        self.assertEqual(dup[0]["matched_by"], ["wiki_semantic"])
        self.assertAlmostEqual(dup[0]["unified_score"], one[0]["unified_score"], places=6)

    def test_two_sections_of_one_wiki_file_stay_separate(self) -> None:
        out = xs.rank([
            {"source_type": "wiki", "source_file": "wiki/topics/x.md",
             "section": "四層架構", "score": 0.8, "score_scale": "wiki_semantic"},
            {"source_type": "wiki", "source_file": "wiki/topics/x.md",
             "section": "退場機制", "score": 0.7, "score_scale": "wiki_semantic"},
        ])
        self.assertEqual(len(out), 2)

    def test_card_merges_across_legs_even_if_section_differs(self) -> None:
        # 卡片身分是路徑；語意腿和 BM25 腿給的 section（標題）可能字面不同，
        # 不能因此就併不起來——否則雙腿 RRF 加分又沒了。
        out = xs.rank([
            {"source_type": "card", "source_file": "cards/a.md",
             "section": "標題 A", "score": 0.6, "score_scale": "card_semantic"},
            {"source_type": "card", "source_file": "cards/a.md",
             "section": "標題 A（索引版）", "score": 12.0, "score_scale": "card_bm25"},
        ])
        self.assertEqual(len(out), 1)
        self.assertEqual(set(out[0]["matched_by"]), {"card_semantic", "card_bm25"})

    def test_titleless_rows_do_not_merge_on_a_shared_section(self) -> None:
        out = xs.rank([
            {"section": "常見問題", "score": 0.6, "score_scale": "card_semantic"},
            {"section": "常見問題", "score": 0.7, "score_scale": "card_semantic"},
        ])
        self.assertEqual(len(out), 2)


if __name__ == "__main__":
    unittest.main()
