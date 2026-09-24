"""jev 接管相關性判斷：它決定哪些記錄真的回答了問題。

換掉的是固定的餘弦地板。2026-09-24 用 12 個 Pan 真的問過的問題、走完整召回路徑
量到的分布說明為什麼要換：

    確實回答問題    jev 0.17~0.92     餘弦 0.52~0.72
    純雜訊          jev 0.01~0.07     餘弦 0.57~0.72

餘弦把兩者壓在同一段——「agent 記憶應該當成內部服務嗎」這一題，餘弦給
「20x in July. 22x in August, etc. etc. :)」0.724，而給那句幾乎逐字回答問題的
「Agent memory should be treated as an internal service」排在它下面。jev 分別給
0.01 與 0.92。**錯的不只是門檻，是排序本身。**

這份測試釘住三件會讓它變成災難的事：

  **對話軌跡要被判斷，不能因為欄位不同就拿不到文字。** 卡片與 wiki 的內容在
  title/summary，對話軌跡在 query/answer。只讀前者的話，「你之前說過什麼」那一
  整層會拿不到判斷——而拿不到判斷在下游等於被丟掉。那一層混著金礦和垃圾：問
  「食品展的客戶通常怎麼找」時，注入的三筆軌跡有一筆是完整正確答案。

  **判斷不出來就全部保留。** jev 沒跑成時退回今天的行為，不是清空召回。這個專案
  為「靜默關閉知識庫」付過 12 週。

  **判斷在合併點。** 三條腿加對話軌跡只在那裡同時存在；塞在語意腿裡的話，軌跡與
  關鍵字命中會整批繞過判斷——那正是切換前的實際狀況。
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import xkb_jev  # noqa: E402
import xkb_memory_service as svc  # noqa: E402

CARD = {"id": "cards/good.md", "title": "角色一致性三步法",
        "summary": "先定錨、再複用、最後校正"}
WIKI = {"id": "wiki/topics/x.md", "title": "XKB 召回架構", "summary": "四層召回"}
NOISE = {"id": "cards/noise.md", "title": "Tweet 123",
         "summary": "20x in July. 22x in August, etc. etc. :)"}
TRACE = {"record_type": "conversation_trace",
         "query": "食品展的客戶通常怎麼找",
         "answer": "展前拿名單、展中面對面、展後跟催"}


class EveryRecordTypeCanBeJudged(unittest.TestCase):
    def test_a_card_uses_title_and_summary(self):
        text = svc.judgeable_text(CARD)
        self.assertIn("角色一致性三步法", text)
        self.assertIn("先定錨", text)

    def test_a_conversation_trace_uses_query_and_answer(self):
        """只讀 title/summary 的話，這一層拿不到文字——等於被無條件丟掉。"""
        text = svc.judgeable_text(TRACE)
        self.assertIn("食品展", text)
        self.assertIn("展前拿名單", text)

    def test_an_empty_record_yields_nothing(self):
        self.assertEqual(svc.judgeable_text({"id": "x"}), "")


class ItDropsWhatDoesNotAnswerTheQuestion(unittest.TestCase):
    def _judge(self, records, verdicts):
        def fake(query, candidates, **kwargs):
            # 呼叫端用 id() 當 key，所以照順序對回去。
            return {key: verdicts[index]
                    for index, (key, _text) in enumerate(candidates)}

        with mock.patch.object(xkb_jev, "relevance", fake):
            return svc.judge_relevance("問題", records)

    def test_noise_goes_and_the_answer_stays(self):
        records = [dict(CARD), dict(NOISE)]
        kept, note = self._judge(records, [0.88, 0.02])

        self.assertEqual([r["id"] for r in kept], ["cards/good.md"])
        self.assertEqual(note["status"], "judged")
        self.assertEqual((note["considered"], note["kept"], note["dropped"]),
                         (2, 1, 1))

    def test_a_conversation_trace_survives_on_its_own_merit(self):
        records = [dict(NOISE), dict(TRACE)]
        kept, _note = self._judge(records, [0.03, 0.91])

        self.assertEqual(len(kept), 1)
        self.assertEqual(kept[0]["record_type"], "conversation_trace")

    def test_the_floor_comes_from_the_measurement(self):
        """0.07~0.11 是量到的空隙。邊緣的 0.11 要留，雜訊的 0.07 要砍。"""
        self.assertEqual(svc.JUDGE_FLOOR, 0.10)
        records = [dict(CARD), dict(WIKI), dict(NOISE)]
        kept, _note = self._judge(records, [0.11, 0.10, 0.07])
        self.assertEqual([r["id"] for r in kept],
                         ["cards/good.md", "wiki/topics/x.md"])

    def test_the_score_is_recorded_on_the_record(self):
        records = [dict(CARD)]
        kept, _note = self._judge(records, [0.88])
        self.assertEqual(kept[0]["judge"], 0.88)


class WhenItCannotJudgeNothingIsLost(unittest.TestCase):
    def test_jev_unavailable_keeps_everything(self):
        records = [dict(CARD), dict(NOISE)]
        with mock.patch.object(xkb_jev, "relevance", return_value=None):
            kept, note = svc.judge_relevance("問題", records)

        self.assertEqual(len(kept), 2, "沒跑成要退回今天的行為，不是清空召回")
        self.assertEqual(note["status"], "unavailable")

    def test_a_record_with_no_verdict_is_kept(self):
        """缺答案不是否定的答案。"""
        records = [dict(CARD), dict(NOISE)]

        def partial(query, candidates, **kwargs):
            return {candidates[0][0]: 0.88}  # 第二筆沒有答案

        with mock.patch.object(xkb_jev, "relevance", partial):
            kept, _note = svc.judge_relevance("問題", records)

        self.assertEqual(len(kept), 2)
        self.assertIsNone(kept[1]["judge"])

    def test_records_without_text_are_not_judged_away(self):
        records = [{"id": "x"}, {"id": "y"}]
        with mock.patch.object(xkb_jev, "relevance") as spy:
            kept, note = svc.judge_relevance("問題", records)
        spy.assert_not_called()
        self.assertEqual(len(kept), 2)
        self.assertEqual(note["status"], "no_text")


class TheDecisionIsVisibleAndReversible(unittest.TestCase):
    def test_it_happens_at_the_merge_point_not_inside_a_leg(self):
        import inspect
        merge = inspect.getsource(svc.Store.knowledge_recall)
        self.assertIn("judge_relevance", merge)
        for leg in (svc.KnowledgeCatalog._semantic_search,
                    svc.KnowledgeCatalog._wiki_search,
                    svc.KnowledgeCatalog._drop_irrelevant):
            with self.subTest(leg=leg.__name__):
                self.assertNotIn("judge_relevance", inspect.getsource(leg),
                                 "塞在某一條腿裡的話，對話軌跡會整批繞過判斷")

    def test_there_is_an_off_switch(self):
        import inspect
        merge = inspect.getsource(svc.Store.knowledge_recall)
        self.assertIn("XKB_JEV_DECIDE", merge)

    def test_the_result_is_reported_not_silent(self):
        """砍掉幾筆、門檻多少、有沒有跑成，都要看得見。

        這個專案吃過最大的虧就是「什麼都沒有」跟「壞了」長得一樣。
        """
        import inspect
        merge = inspect.getsource(svc.Store.knowledge_recall)
        self.assertIn('"judge": judge_note', merge)

    def test_a_crash_in_the_judge_does_not_break_recall(self):
        import inspect
        merge = inspect.getsource(svc.Store.knowledge_recall)
        self.assertIn("except Exception", merge)
        self.assertIn("judge_note = {\"status\": \"error\"}", merge)


if __name__ == "__main__":
    unittest.main()
