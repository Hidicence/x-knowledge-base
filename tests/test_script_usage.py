"""刪掉一支腳本之前，要有證據說沒人在用。

scripts/ 底下有五十幾支可以直接執行的腳本。有些是遷移時期的一次性工具，有些
是每天跑的管線，有些是 Pan 偶爾手動叫的。從程式碼裡分不出來：

  - 「被幾個檔案引用」分不出來：action_recall 只被引用一次，卻在每一次召回
    裡都會跑；migrate_schema 同樣只被引用一次，而它上一次執行可能是半年前。
  - 檔名分不出來：full_sync_v2 聽起來現役，init_rebuild_v2 聽起來像一次性的。
  - 我分不出來：哪支是手動工作流程的一環，只有用的人知道。

所以不分類，只量測。跑一段時間之後「從來沒被叫過」才是證據，而不是猜測。

這份測試守的是量測本身：新加的可執行腳本要一起被量到，否則這份紀錄會慢慢
變成一份看起來完整、其實有缺口的清單——那比沒有更危險。
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

import xkb_usage  # noqa: E402


class Recording(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.path = Path(self._tmp.name) / "script-usage.jsonl"
        self.addCleanup(self._tmp.cleanup)

    def test_a_run_is_recorded(self):
        xkb_usage.record("scripts/xkb_ask.py", ["--json", "some query"], path=self.path)
        rows = xkb_usage.read(self.path)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["script"], "xkb_ask")

    def test_argument_values_are_not_kept(self):
        """參數值可能是查詢內容或路徑。這份紀錄要回答的是「有沒有人用」，
        不是「他查了什麼」。"""
        xkb_usage.record("scripts/xkb_ask.py",
                         ["--env-file", "/etc/xkb/fixture.env", "我的報價策略"],
                         path=self.path)
        blob = self.path.read_text(encoding="utf-8")
        self.assertIn("--env-file", blob)
        self.assertNotIn("/etc/xkb", blob)
        self.assertNotIn("報價", blob)

    def test_an_unwritable_path_is_silent(self):
        """觀測不能弄掛被觀測的東西。"""
        xkb_usage.record("scripts/x.py", [], path=Path(self._tmp.name) / "no" / "x.jsonl")

    def test_scripts_never_run_still_appear_in_the_summary(self):
        """從沒被叫過的那些才是重點。只列跑過的，等於把答案藏起來。"""
        with tempfile.TemporaryDirectory() as tmp:
            fake = Path(tmp)
            (fake / "used.py").write_text('if __name__ == "__main__":\n    pass\n',
                                          encoding="utf-8")
            (fake / "never.py").write_text('if __name__ == "__main__":\n    pass\n',
                                           encoding="utf-8")
            (fake / "notrunnable.py").write_text("x = 1\n", encoding="utf-8")
            xkb_usage.record("used.py", [], path=self.path)

            data = xkb_usage.summary(path=self.path, scripts_dir=fake)

        self.assertEqual(data["used"]["runs"], 1)
        self.assertEqual(data["never"]["runs"], 0)
        self.assertNotIn("notrunnable", data)


# 刻意不量測的，附上理由。
#
# 這不是「忘了接」的白名單——每一項都要說得出為什麼，否則這個例外清單會變成
# 讓缺口合法化的地方。
NOT_MEASURED = {
    # xkb_usage 需要 xkb_paths 決定紀錄寫在哪，接上去就形成
    # xkb_usage → xkb_paths → xkb_usage 的循環（2026-09-12 圖譜抓到）。
    # 而且它的 __main__ 只是印出解析到的路徑，那是除錯輸出，不是一個
    # 「有沒有人在用」值得追蹤的工具。
    "xkb_paths.py",
}


class EveryEntryPointIsMeasured(unittest.TestCase):
    def test_every_runnable_script_records_itself(self):
        missing = []
        for path in sorted(SCRIPTS.glob("*.py")):
            if path.name in NOT_MEASURED:
                continue
            text = path.read_text(encoding="utf-8", errors="ignore")
            if '__name__ == "__main__"' not in text:
                continue
            if "xkb_usage.record(__file__)" not in text:
                missing.append(path.name)
        self.assertEqual(
            missing, [],
            "這些可執行腳本沒有被量到，之後判斷「沒人用」時會少算：\n  "
            + "\n  ".join(missing))

    def test_the_exception_list_stays_honest(self):
        """例外只能是「刻意不量」，不能是「已經刪掉的檔案」留在清單裡。

        一個指向不存在檔案的例外，會讓這個清單看起來有理由、實際上什麼都沒擋。
        """
        for name in NOT_MEASURED:
            with self.subTest(script=name):
                self.assertTrue((SCRIPTS / name).exists(),
                                f"{name} 不存在了，例外清單該清掉這一行")

    def test_importing_a_script_does_not_count_as_running_it(self):
        """被 import 的次數不代表被使用——record 只在當主程式跑時呼叫。"""
        for path in sorted(SCRIPTS.glob("*.py")):
            text = path.read_text(encoding="utf-8", errors="ignore")
            if "xkb_usage.record(__file__)" not in text:
                continue
            before = text.split("xkb_usage.record(__file__)")[0]
            with self.subTest(script=path.name):
                self.assertIn('__name__ == "__main__"', before)


class TheReportRuns(unittest.TestCase):
    def test_report_does_not_crash_on_an_empty_ledger(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = {"XKB_DATA_DIR": tmp, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"}
            import os
            proc = subprocess.run(
                [sys.executable, str(SCRIPTS / "xkb_usage.py"), "report", "--json"],
                capture_output=True, text=True, encoding="utf-8", errors="replace", env={**os.environ, **env})
        self.assertEqual(proc.returncode, 0, proc.stderr)
        json.loads(proc.stdout)


if __name__ == "__main__":
    unittest.main()
