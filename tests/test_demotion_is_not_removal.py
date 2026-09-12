"""降權要會動，而且不能殺掉「存起來很久以後才用」的東西。

Pan 選了最保守的退場規則，理由是「我自己常常存一些我真的很久以後才會用到的
東西」。那個顧慮是這份測試的第一個 class：規則必須在**定義上**碰不到那種卡片，
不是靠門檻調得夠高。

退場在這個專案有前例可循，而前例都是壞的：excluded 旗標做成單行道，害一張被
解析 bug 誤排除的好卡片只能手動改檔案救回來（2026-09-11）；消化的 --apply 用
結論取代筆記，丟掉四成內容還讓壓縮比看起來變好（記憶 xkb-synthesis-silent-loss）。
兩次都是「做了不可逆的事，而且看不出來」。所以這裡每一條測的都是不可逆性：

    降權的東西還在結果裡嗎
    它還會被量測嗎（不然永遠回不來）
    它這次真的相關的時候，會不會還是被壓掉
    統計讀不到的時候，召回會不會跟著壞
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import xkb_eviction as ev  # noqa: E402
import xkb_score  # noqa: E402
from xkb_memory_service import KnowledgeCatalog, Store  # noqa: E402


class StoredForLaterSurvives(unittest.TestCase):
    """Pan 的顧慮。規則要在定義上碰不到這種卡片。"""

    def test_a_card_nobody_has_searched_for_is_never_demoted(self):
        """存起來以後才用的卡片，特徵是**沒被撈出來過**。

        considered_count 低，所以不管 injected 是 0 還是什麼，都不該降權。
        這是這條規則跟「很久沒用到就退場」最大的差別——後者正好會殺掉它。
        """
        for considered in range(0, ev.DEMOTE_AFTER_CONSIDERED):
            with self.subTest(considered=considered):
                self.assertFalse(ev.is_demoted(considered, 0))

    def test_demotion_needs_repeated_uselessness_not_age(self):
        """規則裡沒有任何時間欄位。久沒被碰過不是降權理由。"""
        import inspect
        src = inspect.getsource(ev.is_demoted)
        for word in ("days", "last_", "age", "timestamp", "now"):
            self.assertNotIn(word, src,
                             "降權不該看時間——看時間就會殺掉存起來以後才用的東西")

    def test_one_hit_ever_is_enough_to_stay(self):
        """通過地板一次就夠了，不需要維持命中率。

        保守在這裡：這不是 gain 那套「比平均差就退」，是「從來沒有用過」。
        """
        self.assertFalse(ev.is_demoted(999, 1))


class DemotionIsNotRemoval(unittest.TestCase):
    def test_a_demoted_record_is_still_in_the_results(self):
        """壓到最後，不是不見。不然索引壞掉會長得像知識庫是空的。"""
        results = xkb_score.rank([
            {"title": "好卡片", "score": 0.88, "score_scale": "card_semantic"},
            {"title": "被降權的", "score": 0.10, "score_scale": "card_semantic",
             "demoted": True},
        ])
        self.assertEqual(len(results), 2)
        self.assertEqual(results[-1]["title"], "被降權的")

    def test_demoted_sinks_below_merely_weak(self):
        """被證實沒用過的，要排在只是這次弱的之後——limit 先切掉它。"""
        results = xkb_score.rank([
            {"title": "這次弱", "score": 0.10, "score_scale": "card_semantic"},
            {"title": "從來沒用過", "score": 0.12, "score_scale": "card_semantic",
             "demoted": True},
        ])
        self.assertEqual([r["title"] for r in results], ["這次弱", "從來沒用過"])

    def test_demotion_cannot_sink_a_hit_that_cleared_the_floor(self):
        """這一次真的相關的話，降權不准動它。

        那一次正是它的復活訊號。把它壓到最後，等於在它唯一有用的那一次把它
        藏起來——而且下一輪統計才會更新，誤差會固定下來。
        """
        results = xkb_score.rank([
            {"title": "被降權但這次命中", "score": 0.88,
             "score_scale": "card_semantic", "demoted": True},
            {"title": "沒被降權但這次很弱", "score": 0.05,
             "score_scale": "card_semantic"},
        ])
        self.assertEqual(results[0]["title"], "被降權但這次命中")
        self.assertGreater(results[0]["unified_score"], 0)


class RevivalNeedsNoIntervention(unittest.TestCase):
    """這是跟 excluded 旗標那個單行道的差別。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = Store(Path(self.tmp.name) / "memory.sqlite")

    def _consider(self, record_id: str, similarity: float, injected: bool, times: int):
        for _ in range(times):
            self.store.record_usage([(record_id, similarity, injected)])

    def test_it_becomes_demoted_after_enough_useless_considerations(self):
        n = ev.DEMOTE_AFTER_CONSIDERED
        self._consider("cards/noise.md", 0.51, False, n - 1)
        self.assertEqual(self.store.demoted_ids(), set(),
                         "還沒到門檻就降權的話，保守的意義就沒了")
        self._consider("cards/noise.md", 0.51, False, 1)
        self.assertEqual(self.store.demoted_ids(), {"cards/noise.md"})

    def test_one_real_hit_lifts_the_demotion_with_nobody_deciding(self):
        """被降權之後還是會被量測，所以它自己能回來。

        excluded 旗標做不到這件事：它把卡片從索引拿掉，於是再也不會被
        considered，injected_count 永遠是 0，永遠回不來。
        """
        self._consider("cards/borderline.md", 0.53, False,
                       ev.DEMOTE_AFTER_CONSIDERED)
        self.assertIn("cards/borderline.md", self.store.demoted_ids())

        # 有一天某個問法讓它通過地板。
        self._consider("cards/borderline.md", 0.71, True, 1)

        self.assertEqual(self.store.demoted_ids(), set(),
                         "降權必須自動解除，不能要人手動改檔案")

    def test_the_threshold_has_one_definition(self):
        """門檻不准有第二份拷貝——尺度與門檻在這個專案各自被複製過一次。"""
        self.assertEqual(
            self.store.demoted_ids.__defaults__, (ev.DEMOTE_AFTER_CONSIDERED,))


class ItMustNotDependOnWhichLegFoundIt(unittest.TestCase):
    """第一版掛在語意腿尾端，真實資料一驗就破。

    每條召回腿都自己新建 dict（語意、關鍵字、wiki 各有一份欄位清單），所以掛在
    其中一條上的標記會被其他腿繞過。那張被撈出 8 次、從來沒用上的卡片，在日文
    查詢下是**關鍵字腿**撈到的——標記完全沒掛上，而單元測試全綠。

    這是記憶 xkb-many-writers-one-reader 的同一個形狀：保護要放在讀的那一端。
    這裡用關鍵字腿的欄位形狀（line 666 那一長串，沒有經過 _drop_irrelevant）
    來證明標記與腿無關。
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = Store(Path(self.tmp.name) / "memory.sqlite")
        for _ in range(ev.DEMOTE_AFTER_CONSIDERED):
            self.store.record_usage([("02-seo-geo/999", 0.54, False)])

    def _keyword_leg_record(self, record_id: str, score: float) -> dict:
        """關鍵字腿產出的形狀。它不經過 _drop_irrelevant。"""
        return {"record_type": "knowledge_card", "id": record_id,
                "title": record_id, "summary": "", "source_file": f"cards/{record_id}.md",
                "source_type": "card", "score_scale": "card_keyword", "score": score,
                "retrieval": "keyword"}

    def test_a_keyword_leg_hit_is_still_demoted(self):
        demoted_id = "02-seo-geo/999"
        records = [self._keyword_leg_record(demoted_id, 3.0),
                   self._keyword_leg_record("01-good/111", 1.0)]
        self.store.catalog.search = lambda *a, **k: {
            "records": records, "filtered_counts": {"by_layer": {}}}
        self.store.recall = lambda *a, **k: {"memories": []}

        out = self.store.knowledge_recall("ヘアメイク プロンプト", limit=10)

        got = {r["id"]: r for r in out["records"]}
        self.assertTrue(got[demoted_id].get("demoted"),
                        "關鍵字腿撈到的也要被降權——不然換一條腿就繞過了")
        self.assertFalse(got["01-good/111"].get("demoted"))

    def test_no_leg_tags_on_its_own(self):
        """標記只能在合併點發生。多一處就多一條可以不一致的路。"""
        import inspect
        from xkb_memory_service import KnowledgeCatalog as KC, Store as St
        per_leg = (KC._semantic_search, KC._wiki_search, KC._drop_irrelevant)
        for fn in per_leg:
            with self.subTest(fn=fn.__name__):
                self.assertNotIn("_tag_demoted", inspect.getsource(fn),
                                 "降權不要掛在單一條腿上")
        self.assertIn("_tag_demoted", inspect.getsource(St.knowledge_recall),
                      "合併點沒有標記，降權就完全不會發生")


class LosingTheStatsMustNotBreakRecall(unittest.TestCase):
    """降權是最佳化，不是正確性。"""

    def test_no_sink_tags_nothing(self):
        catalog = KnowledgeCatalog()
        records = [{"id": "cards/a.md"}]
        catalog._tag_demoted(records)
        self.assertNotIn("demoted", records[0])

    def test_a_failing_sink_is_swallowed(self):
        catalog = KnowledgeCatalog()

        def boom():
            raise RuntimeError("統計表不見了")

        catalog.demoted_sink = boom
        records = [{"id": "cards/a.md"}]
        catalog._tag_demoted(records)  # 不可以炸
        self.assertNotIn("demoted", records[0])

    def test_it_tags_only_the_matching_record(self):
        catalog = KnowledgeCatalog()
        catalog.demoted_sink = lambda: {"cards/noise.md"}
        records = [{"id": "cards/noise.md"}, {"id": "cards/good.md"}]
        catalog._tag_demoted(records)
        self.assertTrue(records[0].get("demoted"))
        self.assertNotIn("demoted", records[1])


if __name__ == "__main__":
    unittest.main()
