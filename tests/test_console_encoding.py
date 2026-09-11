"""腳本做完事情之後，不可以死在回報那一行。

XKB 的輸出到處是 emoji。在 Windows 的 cp950 主控台，`print("📚 …")` 會拋
UnicodeEncodeError——而那個 print 發生在工作已經完成之後。結果是事情做完了，
卻以一個看起來像程式壞掉的 traceback 結束。

這個現象被當成「環境問題」處理了很久，做法是要求每個呼叫端帶 PYTHONUTF8=1。
2026-09-11 的完整測試裡，那是唯一一個從頭到尾都紅的 error，而我每次都帶著那個
環境變數跑，所以每次都看不到——我把環境設定當成了測試通過。

它不是環境問題：任何人在 Windows 主控台直接跑 build_vector_index.py 都會炸。
"""
from __future__ import annotations

import io
import os
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

import xkb_console  # noqa: E402

EMOJI = "📚 ✅ ❌ 🔢"


class UseUtf8(unittest.TestCase):
    def test_it_is_a_no_op_when_the_stream_has_no_reconfigure(self):
        """被包起來的 stream（測試、pipe）不一定有 reconfigure，不能因此炸。"""
        real_out, real_err = sys.stdout, sys.stderr
        sys.stdout = io.StringIO()
        sys.stderr = io.StringIO()
        try:
            xkb_console.use_utf8()          # 不該拋
        finally:
            sys.stdout, sys.stderr = real_out, real_err

    def test_it_does_not_raise_twice(self):
        xkb_console.use_utf8()
        xkb_console.use_utf8()


class EmojiSurvivesALegacyConsole(unittest.TestCase):
    """在一個刻意設成非 UTF-8 的子行程裡印 emoji。"""

    def _run(self, body: str, *, legacy: bool) -> subprocess.CompletedProcess:
        env = {**os.environ}
        if legacy:
            # 模擬 Windows 主控台的舊字碼頁；POSIX 上用 ascii 效果等價。
            env["PYTHONIOENCODING"] = "cp950" if os.name == "nt" else "ascii"
            env.pop("PYTHONUTF8", None)
        return subprocess.run(
            [sys.executable, "-c", body], capture_output=True, text=True,
            encoding="utf-8", errors="replace", env=env, cwd=str(ROOT))

    def test_printing_emoji_without_the_guard_fails_on_a_legacy_console(self):
        """先證明這個環境真的會炸——否則下面那個測試什麼都沒驗到。"""
        proc = self._run(f'print("{EMOJI}")', legacy=True)
        if proc.returncode == 0:
            raise unittest.SkipTest("這個環境的 stdout 不會因為字碼頁失敗")
        self.assertIn("UnicodeEncodeError", proc.stderr)

    def test_printing_emoji_with_the_guard_succeeds(self):
        body = (
            "import sys; sys.path.insert(0, 'scripts')\n"
            "import xkb_console; xkb_console.use_utf8()\n"
            f'print("{EMOJI}")\n'
        )
        proc = self._run(body, legacy=True)
        self.assertEqual(proc.returncode, 0, proc.stderr)


class TheScriptThatCrashedIsGuarded(unittest.TestCase):
    def test_build_vector_index_calls_the_guard(self):
        """它是實際炸過的那一支：print(f"📚 Loaded …") 在 cp950 下拋例外。"""
        text = (SCRIPTS / "build_vector_index.py").read_text(encoding="utf-8")
        self.assertIn("xkb_console.use_utf8()", text)
        # 要在任何 print 之前就轉好，所以 guard 必須在 main() 之外。
        before_main = text.split("def main(")[0]
        self.assertIn("xkb_console.use_utf8()", before_main)


if __name__ == "__main__":
    unittest.main()
