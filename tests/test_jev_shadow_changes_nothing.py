"""jev 影子模式：只觀測，不改變任何召回結果。

接一個新的判斷模型有兩種搞砸的方式，這份測試各擋一種：

  **它偷偷改變了行為。** 影子模式的全部價值在於「先量再決定」。只要它動到
  kept/dropped，之後拿來校準門檻的那份資料就是它自己造成的，而不是餘弦真正的
  決定。這個專案在「驗證了有動作，不是動得對」上有紀錄在案。

  **「沒跑成」被寫成「判斷為不相關」。** jev 回 None 代表沒跑（憑證沒設、端點
  打不通、回應形狀不對），不是 0。寫成同一件事的話，jev 一掛掉，分析資料會長得
  像「它覺得每一筆都不相關」，而如果之後讓它決定，知識庫會靜默關閉——那正是這個
  專案吃過最大虧的那種失敗（recall 壞了 12 週沒人發現）。

還有一條是效能：jev 實測 1.85 秒，而 questions 是平行評估的，所以 N 個候選必須
一次呼叫。逐一呼叫的話 10 個候選要 18 秒，比召回本身還慢。
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import xkb_jev  # noqa: E402
from xkb_memory_service import Store  # noqa: E402


class _Inline:
    """把背景執行緒改成同步執行，好在測試裡斷言它做了什麼。"""

    def __init__(self, target=None, daemon=None, name=None):
        self._target = target

    def start(self):
        self._target()


class TheClientTellsSilenceFromRejection(unittest.TestCase):
    def setUp(self):
        self.env = {"LLM_API_URL": "https://api.example/v1", "LLM_API_KEY": "k"}

    def test_no_credentials_means_did_not_run_not_irrelevant(self):
        with mock.patch.object(xkb_jev, "runtime_env", return_value={}):
            self.assertFalse(xkb_jev.available())
            self.assertIsNone(xkb_jev.judge("s", {"q": {"type": "noul"}}))
            self.assertIsNone(xkb_jev.relevance("問題", [("a", "內容")]))

    def test_a_transport_failure_returns_none(self):
        with mock.patch.object(xkb_jev, "runtime_env", return_value=self.env), \
             mock.patch.object(xkb_jev.urllib.request, "urlopen",
                               side_effect=OSError("連不上")):
            self.assertIsNone(xkb_jev.relevance("問題", [("a", "內容")]))

    def test_a_response_of_the_wrong_shape_returns_none(self):
        """回了 200 但形狀不對，一樣是「沒跑成」。

        對不上任何候選卻回一個空 dict 的話，呼叫端會把它當成「全部不相關」。
        """
        with mock.patch.object(xkb_jev, "runtime_env", return_value=self.env), \
             mock.patch.object(xkb_jev, "judge", return_value={"別的": {"noul": 1}}):
            self.assertIsNone(xkb_jev.relevance("問題", [("a", "內容")]))

    def test_a_real_zero_is_a_real_answer(self):
        with mock.patch.object(xkb_jev, "runtime_env", return_value=self.env), \
             mock.patch.object(xkb_jev, "judge",
                               return_value={"q0": {"type": "noul", "noul": 0.0}}):
            self.assertEqual(xkb_jev.relevance("問題", [("a", "內容")]), {"a": 0.0})


class OneCallForEveryCandidate(unittest.TestCase):
    """questions 是平行評估的。逐一呼叫會比召回本身還慢。"""

    def test_ten_candidates_are_one_request(self):
        seen = []

        def spy(state, questions, **kwargs):
            seen.append(questions)
            return {name: {"type": "noul", "noul": 0.5} for name in questions}

        candidates = [(f"cards/{i}.md", f"內容 {i}") for i in range(10)]
        with mock.patch.object(xkb_jev, "runtime_env",
                               return_value={"LLM_API_URL": "u", "LLM_API_KEY": "k"}), \
             mock.patch.object(xkb_jev, "judge", spy):
            out = xkb_jev.relevance("問題", candidates)

        self.assertEqual(len(seen), 1, "十個候選要一次呼叫，不是十次")
        self.assertEqual(len(seen[0]), 10)
        self.assertEqual(len(out), 10)

    def test_question_names_do_not_leak_file_paths(self):
        """問題名稱會變成回應 JSON 的欄位名，而候選的 key 是含斜線的路徑。"""
        seen = {}

        def spy(state, questions, **kwargs):
            seen.update(questions)
            return {name: {"type": "noul", "noul": 0.5} for name in questions}

        with mock.patch.object(xkb_jev, "runtime_env",
                               return_value={"LLM_API_URL": "u", "LLM_API_KEY": "k"}), \
             mock.patch.object(xkb_jev, "judge", spy):
            out = xkb_jev.relevance("問題", [("01-topic/2040096164452958475.md", "內容")])

        for name in seen:
            self.assertNotIn("/", name)
        self.assertIn("01-topic/2040096164452958475.md", out,
                      "序號要對得回原本的 key")

    def test_a_long_card_is_trimmed(self):
        seen = {}

        def spy(state, questions, **kwargs):
            seen.update(questions)
            return {name: {"type": "noul", "noul": 0.5} for name in questions}

        with mock.patch.object(xkb_jev, "runtime_env",
                               return_value={"LLM_API_URL": "u", "LLM_API_KEY": "k"}), \
             mock.patch.object(xkb_jev, "judge", spy):
            xkb_jev.relevance("問題", [("a", "很長" * 5000)])

        body = next(iter(seen.values()))["instructions"]
        self.assertLess(len(body), 1200, "instructions 是判斷準則，不是整篇文件")


class ShadowModeDecidesNothing(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = Store(Path(self.tmp.name) / "memory.sqlite")
        self.records = [
            {"id": "cards/good.md", "title": "好的", "summary": "正面回答"},
            {"id": "cards/weak.md", "title": "弱的", "summary": "沾到邊"},
        ]

    def _rows(self) -> list[dict]:
        with self.store.connect() as db:
            db.row_factory = __import__("sqlite3").Row
            return [dict(r) for r in db.execute(
                "SELECT * FROM relevance_shadow ORDER BY id")]

    def _drop(self, *, jev_result):
        """跑一次過濾，jev 回 jev_result。回傳 (kept, dropped, 寫進去的列)。"""
        catalog = self.store.catalog
        # 影子模式只在 jev 還沒接管決定時存在。接管之後它就是第二次呼叫同一個
        # 模型，所以預設關掉——要測影子模式就得明確說「現在不是 jev 在決定」。
        with mock.patch.dict("os.environ", {"XKB_JEV_DECIDE": "0"}), \
             mock.patch.object(xkb_jev, "available", return_value=True), \
             mock.patch.object(xkb_jev, "relevance", return_value=jev_result), \
             mock.patch("xkb_memory_service.threading.Thread", _Inline), \
             mock.patch("xkb_memory_service.xkb_relevance.filter_irrelevant",
                        return_value=([self.records[0]], 1,
                                      {"cards/good.md": 0.72, "cards/weak.md": 0.41})), \
             mock.patch("xkb_memory_service.xkb_relevance.vector_key",
                        side_effect=lambda rid: rid), \
             mock.patch("xkb_memory_service.xkb_relevance.min_similarity",
                        return_value=0.55):
            kept, dropped = catalog._drop_irrelevant("問題", list(self.records))
        return kept, dropped, self._rows()

    def test_the_kept_set_is_the_cosine_decision(self):
        """jev 說全部都相關，也不准改變誰被留下來。"""
        kept, dropped, rows = self._drop(
            jev_result={"cards/good.md": 0.9, "cards/weak.md": 0.9})

        self.assertEqual([r["id"] for r in kept], ["cards/good.md"])
        self.assertEqual(dropped, 1)
        self.assertEqual({r["record_id"]: r["kept"] for r in rows},
                         {"cards/good.md": 1, "cards/weak.md": 0})

    def test_both_scores_are_recorded_side_by_side(self):
        """要回答的問題是「兩邊在哪裡不一致」，所以兩個分數都要留。"""
        _kept, _dropped, rows = self._drop(
            jev_result={"cards/good.md": 0.30, "cards/weak.md": 0.02})

        by_id = {r["record_id"]: r for r in rows}
        self.assertAlmostEqual(by_id["cards/good.md"]["cosine"], 0.72)
        self.assertAlmostEqual(by_id["cards/good.md"]["jev"], 0.30)
        self.assertAlmostEqual(by_id["cards/weak.md"]["cosine"], 0.41)
        self.assertAlmostEqual(by_id["cards/weak.md"]["jev"], 0.02)
        self.assertEqual({r["floor"] for r in rows}, {0.55})
        self.assertEqual({r["query"] for r in rows}, {"問題"})

    def test_jev_not_running_writes_nothing_rather_than_a_row_of_nulls(self):
        """一整批 jev=NULL 的列，在分析時長得像「它覺得每一筆都不相關」。"""
        kept, dropped, rows = self._drop(jev_result=None)

        self.assertEqual([r["id"] for r in kept], ["cards/good.md"])
        self.assertEqual(dropped, 1)
        self.assertEqual(rows, [], "沒跑成就不要留下會被誤讀的紀錄")

    def test_a_crash_inside_the_shadow_never_breaks_recall(self):
        with mock.patch.dict("os.environ", {"XKB_JEV_DECIDE": "0"}), \
             mock.patch.object(xkb_jev, "available", return_value=True), \
             mock.patch.object(xkb_jev, "relevance",
                               side_effect=RuntimeError("炸了")), \
             mock.patch("xkb_memory_service.threading.Thread", _Inline), \
             mock.patch("xkb_memory_service.xkb_relevance.filter_irrelevant",
                        return_value=([self.records[0]], 1, {})), \
             mock.patch("xkb_memory_service.xkb_relevance.vector_key",
                        side_effect=lambda rid: rid), \
             mock.patch("xkb_memory_service.xkb_relevance.min_similarity",
                        return_value=0.55):
            kept, dropped = self.store.catalog._drop_irrelevant(
                "問題", list(self.records))

        self.assertEqual([r["id"] for r in kept], ["cards/good.md"])
        self.assertEqual(dropped, 1)

    def test_it_can_be_turned_off(self):
        with mock.patch.dict("os.environ", {"XKB_JEV_SHADOW": "0",
                                            "XKB_JEV_DECIDE": "0"}), \
             mock.patch.object(xkb_jev, "available", return_value=True), \
             mock.patch.object(xkb_jev, "relevance") as spy, \
             mock.patch("xkb_memory_service.threading.Thread", _Inline), \
             mock.patch("xkb_memory_service.xkb_relevance.filter_irrelevant",
                        return_value=([self.records[0]], 1, {})), \
             mock.patch("xkb_memory_service.xkb_relevance.vector_key",
                        side_effect=lambda rid: rid), \
             mock.patch("xkb_memory_service.xkb_relevance.min_similarity",
                        return_value=0.55):
            self.store.catalog._drop_irrelevant("問題", list(self.records))

        spy.assert_not_called()
        self.assertEqual(self._rows(), [])


class ItDoesNotBlockRecall(unittest.TestCase):
    def test_the_comparison_runs_off_the_recall_path(self):
        """jev 是 1.85 秒。影子模式下沒有東西依賴它的答案，不該讓任何人多等。"""
        import inspect
        from xkb_memory_service import KnowledgeCatalog
        source = inspect.getsource(KnowledgeCatalog._shadow_compare)
        self.assertIn("threading.Thread", source)
        self.assertIn("daemon=True", source)


class TheJudgeModelIsNotTheChatModel(unittest.TestCase):
    """把 llm.json 的 model 改成 jev 會讓所有生成腳本一起壞掉。"""

    def test_the_two_models_are_separate_settings(self):
        config = json.loads((ROOT / "config" / "llm.json").read_text(encoding="utf-8"))
        self.assertIn("judge_model", config)
        self.assertNotEqual(config["model"], config["judge_model"])

    def test_the_endpoint_is_not_chat_completions(self):
        self.assertEqual(xkb_jev.JUDGE_PATH, "/systemone")
        self.assertNotIn("chat", xkb_jev.JUDGE_PATH)


if __name__ == "__main__":
    unittest.main()
