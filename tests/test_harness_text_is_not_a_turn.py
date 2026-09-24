"""harness 產生的文字不是一輪對話，不該抵達召回。

2026-09-24 在正式資料的 1,020 個 turn 裡量到 137 個（13.5%）的 query 其實是
harness 產生的文字，最大宗是背景任務完成通知。它們同時污染三個地方：turns 表、
對話軌跡，以及召回。

召回那邊的後果看得見：這種文字剛好跟 openclaw 類的卡片用字重疊，所以餘弦給
0.57~0.62，高過 0.55 的地板，整批被注入進 context。同一批候選 jev 給 0.03~0.10
——也就是說，這不是門檻調錯，是這些文字根本不該被拿去查。

**這份測試用的是資料庫裡真實出現過的字串。** 今天已經犯過一次「測試覆蓋的是我
想像的情況，不是實際發生的那個」，所以下面每一個樣本都是從 turns 表撈出來的。

最重要的一條是誤殺：`<pasted_content id="...">` 是 Pan 貼東西進來時的包裝，資料
裡有 5 筆。用「以 < 開頭」這種寬鬆規則會把它一起擋掉，等於他貼的東西全部變成
看不見——那比多注入幾筆雜訊糟得多。
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import xkb_agent_hook  # noqa: E402
from conversation_state_parser import is_harness_text, noise_kind  # noqa: E402

# 以下四段都是 turns 表裡真實出現過的開頭。
REAL_TASK_NOTIFICATION = (
    "<task-notification>\n<task-id>a063252154283d8bf</task-id>\n"
    "<output-file>/tmp/x.output</output-file>\n<status>completed</status>\n"
    "</task-notification>")
REAL_OPENCLAW_CONTEXT = (
    "<<<BEGIN_OPENCLAW_INTERNAL_CONTEXT>>>\n"
    "OpenClaw runtime context (internal):\n"
    "This context is runtime-generated, not user-authored.\n"
    "<<<END_OPENCLAW_INTERNAL_CONTEXT>>>")
REAL_PASTED_CONTENT = (
    '<pasted_content id="46f0"> 畫面由 GPS 研究流程拉回到 REALI 呈現品牌資料、'
    "不同 AI Agent、團隊分工")
REAL_QUESTION = "食品展的客戶通常怎麼找"


class ItRecognisesWhatTheHarnessWrote(unittest.TestCase):
    def test_the_two_shapes_that_actually_occurred(self):
        self.assertTrue(is_harness_text(REAL_TASK_NOTIFICATION))
        self.assertTrue(is_harness_text(REAL_OPENCLAW_CONTEXT))

    def test_the_marker_must_be_at_the_start(self):
        """137 筆全部是開頭就帶標記，沒有夾在中間的。

        不錨定的話，一句提到這些標記的真話會被當成 harness 文字——而這份
        對話本身就是這樣的一句。
        """
        self.assertFalse(is_harness_text(
            "為什麼 <task-notification> 會被當成查詢送進召回？"))

    def test_leading_whitespace_does_not_hide_it(self):
        self.assertTrue(is_harness_text("\n  " + REAL_TASK_NOTIFICATION))

    def test_it_is_reported_as_its_own_kind(self):
        """跟問候／應答分開，因為要修的東西不一樣。

        問候是判斷問題（之後交給 jev），這個是接線問題——這種文字根本不該
        抵達召回。混成同一種的話，報告上看不出哪一種在發生。
        """
        self.assertEqual(noise_kind(REAL_TASK_NOTIFICATION), "harness_text")
        self.assertEqual(noise_kind("早安"), "greeting")
        self.assertEqual(noise_kind("好的"), "acknowledgement")


class ItMustNotSwallowWhatPanPasted(unittest.TestCase):
    """最貴的誤殺。資料裡有 5 筆 `<pasted_content>`，那是他貼進來的東西。"""

    def test_pasted_content_is_not_harness_text(self):
        self.assertFalse(is_harness_text(REAL_PASTED_CONTENT))
        self.assertEqual(noise_kind(REAL_PASTED_CONTENT), "")

    def test_a_plain_question_is_untouched(self):
        self.assertFalse(is_harness_text(REAL_QUESTION))
        self.assertEqual(noise_kind(REAL_QUESTION), "")

    def test_an_angle_bracket_alone_means_nothing(self):
        for text in ("<div> 這段 HTML 要怎麼改", "<3", "<= 這個符號怎麼念"):
            with self.subTest(text=text):
                self.assertFalse(is_harness_text(text))


class TheHookDoesNotOpenATurnForIt(unittest.TestCase):
    """不是「開了 turn 但不召回」——它根本不是一輪對話。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def _run(self, prompt: str) -> list[str]:
        calls: list[str] = []

        def spy(path, body, cfg):
            calls.append(path)
            return {"session_id": "s", "turn_id": "t", "retrieval": {"records": []}}

        state = Path(self.tmp.name) / "state.json"
        with mock.patch.object(xkb_agent_hook, "call", spy), \
             mock.patch.object(xkb_agent_hook, "session_key", return_value="k"), \
             mock.patch.object(xkb_agent_hook, "next_ordinal", return_value=1), \
             mock.patch.object(xkb_agent_hook, "STATE_DIR", Path(self.tmp.name)), \
             mock.patch.object(xkb_agent_hook, "state_path", return_value=state), \
             mock.patch.object(xkb_agent_hook, "emit"):
            xkb_agent_hook.on_prompt({"prompt": prompt}, {"source": "test"})
        return calls

    def test_harness_text_leaves_no_state_behind(self):
        """不開 turn，也不該留下狀態——留下的話會讓下一輪對不上。"""
        self._run(REAL_TASK_NOTIFICATION)
        self.assertEqual(list(Path(self.tmp.name).glob("*.json")), [])

    def test_a_task_notification_opens_nothing(self):
        self.assertEqual(self._run(REAL_TASK_NOTIFICATION), [],
                         "連 session 都不該開——它不是一輪對話")

    def test_the_openclaw_internal_context_opens_nothing(self):
        self.assertEqual(self._run(REAL_OPENCLAW_CONTEXT), [])

    def test_a_real_prompt_still_opens_a_turn(self):
        calls = self._run(REAL_QUESTION)
        self.assertIn("/v1/sessions/open", calls)
        self.assertIn("/v1/turns/start", calls)

    def test_pasted_content_still_opens_a_turn(self):
        calls = self._run(REAL_PASTED_CONTENT)
        self.assertIn("/v1/turns/start", calls)


class OneDefinitionForBothEnds(unittest.TestCase):
    """hook 擋一次、召回再擋一次，但判斷只能有一份。

    兩邊各寫一份就會各自漂移——這個專案的「多寫入者一讀取者」記錄在案，而
    _skip_reason 的註解裡也寫著它從 parser 拿清單的理由：它自己那份拷貝曾經
    漏掉複合應答，於是「ok 收到」撈了十筆進一個什麼都沒問的對話。
    """

    def test_the_hook_imports_the_definition(self):
        import inspect
        source = inspect.getsource(xkb_agent_hook)
        self.assertIn("from conversation_state_parser import is_harness_text",
                      source)
        self.assertNotIn("task-notification", source,
                         "標記清單不要在 hook 裡再寫一份")

    def test_recall_skips_it_through_the_same_function(self):
        import inspect
        from xkb_memory_service import Store
        source = inspect.getsource(Store._skip_reason)
        self.assertIn("_noise_kind", source)
        self.assertEqual(noise_kind(REAL_TASK_NOTIFICATION), "harness_text",
                         "召回端的 skip_reason 會直接變成這個字串")


if __name__ == "__main__":
    unittest.main()
