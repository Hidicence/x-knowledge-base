"""暫時性錯誤要重試，永久性錯誤要立刻失敗。

一次消化要連續呼叫模型上百次，所以整批的可靠度等於最脆弱的那一次呼叫。
2026-08-30 有一批 105 次呼叫的重跑，死在第 4 次的一個 503，後面九十幾次
本來會成功的呼叫全都沒有發生。

反過來也要成立：金鑰錯了、模型名稱不存在，重試五次只是把一秒的失敗拖成
兩分鐘，還讓真正的原因埋在等待裡。
"""
from __future__ import annotations

import io
import sys
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import _llm


def http_error(code: int) -> urllib.error.HTTPError:
    return urllib.error.HTTPError(
        "http://example.invalid", code, "boom", {}, io.BytesIO(b"{}")
    )


class _Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False


def ok() -> _Response:
    return _Response(b'{"choices": [{"message": {"content": "fine"}}]}')


class LlmRetryTest(unittest.TestCase):
    def setUp(self) -> None:
        # 只換掉 urlopen。整個 urllib 換成 Mock 的話，模組裡的
        # urllib.error.HTTPError 也會變成 Mock 屬性，except 子句就再也
        # 攔不到任何東西了。
        sleep = mock.patch("time.sleep")  # 不要真的等
        sleep.start()
        self.addCleanup(sleep.stop)

    def run_with(self, side_effect):
        with mock.patch.object(_llm.urllib.request, "urlopen",
                               side_effect=side_effect) as opener:
            try:
                result = _llm._post_with_retry(mock.Mock(), timeout=1)
            except Exception as err:  # noqa: BLE001 — 測試要檢查它拋什麼
                return opener.call_count, err
            return opener.call_count, result

    def test_a_transient_failure_is_waited_out(self) -> None:
        calls, result = self.run_with([http_error(503), ok()])
        self.assertEqual(calls, 2)
        self.assertEqual(result["choices"][0]["message"]["content"], "fine")

    def test_rate_limiting_is_waited_out_too(self) -> None:
        calls, result = self.run_with([http_error(429), http_error(429), ok()])
        self.assertEqual(calls, 3)
        self.assertIn("choices", result)

    def test_a_dropped_connection_is_retried(self) -> None:
        calls, result = self.run_with([urllib.error.URLError("reset"), ok()])
        self.assertEqual(calls, 2)
        self.assertIn("choices", result)

    def test_a_rejected_key_fails_at_once(self) -> None:
        # 重試五次只會把一秒的失敗拖成兩分鐘，而且把原因埋起來。
        calls, err = self.run_with([http_error(401)] * 5)
        self.assertEqual(calls, 1)
        self.assertIsInstance(err, RuntimeError)

    def test_an_unknown_model_fails_at_once(self) -> None:
        calls, err = self.run_with([http_error(400)] * 5)
        self.assertEqual(calls, 1)
        self.assertIsInstance(err, RuntimeError)

    def test_it_gives_up_eventually(self) -> None:
        calls, err = self.run_with([http_error(503)] * _llm.MAX_ATTEMPTS)
        self.assertEqual(calls, _llm.MAX_ATTEMPTS)
        self.assertIsInstance(err, RuntimeError)


if __name__ == "__main__":
    unittest.main()
