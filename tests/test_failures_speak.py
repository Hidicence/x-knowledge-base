"""退回 fallback 的時候，要留下痕跡。

XKB 有四十一處會吞掉例外，多數是對的：掃九百個檔案時單一檔案讀不到就跳過、
可有可無的加值失敗、寫遙測失敗——這些都不該中斷對話。

危險的不是「安靜」，是「一模一樣」。「語意後端掛了」跟「這個主題我們沒有
知識」都回空陣列，於是 2026-05-04 那次召回故障持續了十二週，系統每天禮貌地
回答它什麼都不知道。

這裡驗的是：那幾個會把故障說成「查無資料」的地方，現在會在 stderr 留一行。
行為不變（還是回空的，對話不會斷），但兩種情況分得出來了。
"""
from __future__ import annotations

import io
import sys
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import xkb_failures


class FailuresSpeakTest(unittest.TestCase):
    def setUp(self) -> None:
        xkb_failures.reset()
        self.addCleanup(xkb_failures.reset)

    def test_it_says_what_failed(self) -> None:
        err = io.BlockingIOError("index unreadable")
        buf = io.StringIO()
        with redirect_stderr(buf):
            xkb_failures.note("search index", err, detail="/tmp/search_index.json")
        out = buf.getvalue()
        self.assertIn("search index", out)
        self.assertIn("BlockingIOError", out)
        self.assertIn("/tmp/search_index.json", out)

    def test_the_same_failure_is_reported_once(self) -> None:
        """掃九百個檔案時，同一個原因印九百次會把有用的訊息淹掉。"""
        buf = io.StringIO()
        with redirect_stderr(buf):
            for _ in range(5):
                xkb_failures.note("search index", ValueError("bad json"))
        self.assertEqual(buf.getvalue().count("search index"), 1)

    def test_reporting_never_raises(self) -> None:
        """會報告失敗的東西，自己不能失敗。"""

        class Nasty(Exception):
            def __str__(self) -> str:
                raise RuntimeError("這個例外連自己都印不出來")

        with redirect_stderr(io.StringIO()):
            xkb_failures.note("somewhere", Nasty())  # 不該拋

    def test_a_broken_semantic_backend_is_not_silence(self) -> None:
        """語意召回的後端掛掉，要留下痕跡，但不能中斷呼叫端。"""
        import xbrain_recall

        buf = io.StringIO()
        # 只換 run。整個 subprocess 換成 Mock 的話，
        # subprocess.TimeoutExpired 也會變成 Mock，except 子句就再也攔不到東西。
        with mock.patch.object(xbrain_recall.subprocess, "run",
                               side_effect=OSError("gbrain not installed")):
            with redirect_stderr(buf):
                result = xbrain_recall.xbrain_query("測試查詢", limit=1)

        self.assertEqual(result, [], "行為不變：還是回空的，對話不會斷")
        self.assertIn("semantic recall", buf.getvalue(), "但要說出來")


if __name__ == "__main__":
    unittest.main()
