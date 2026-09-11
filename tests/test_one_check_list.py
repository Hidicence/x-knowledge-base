"""健檢只能有一份清單，而且要問每天真的會跑的那條路。

2026-09-11 加了 card_production 與 pipeline_ledger，也寫了測試確認「有註冊」：

    source = (SCRIPTS / "health_check_pipeline.py").read_text(...)
    assertIn("check_card_production(),", source)

那個測試通過了，而那兩項在每日訊息裡一次都沒有出現過。因為清單有兩份：
health_check_pipeline 一份（我改的），health_check_notify 一份（排程每天跑的
那支，我沒碰）。反過來 external_dependencies 只在 notify 那份裡，所以手動跑
pipeline 時它不被檢查。

測試守錯了檔案。我當時想的是「有沒有接進 main()」，而系統有兩個 main()，每天
跑的是另一個。又一次：測試覆蓋我想像的機制，不是實際的執行路徑。

所以這份測試不讀原始碼字串，它問執行路徑：notify 會不會跑到每一項。
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

import health_check_pipeline as hc  # noqa: E402


class OneList(unittest.TestCase):
    def test_the_notifier_does_not_keep_its_own_list(self):
        """自己列一份就是第二個會漂走的答案。"""
        source = (SCRIPTS / "health_check_notify.py").read_text(encoding="utf-8")
        self.assertNotIn("hc.check_", source,
                         "notify 又自己列清單了——清單在 health_check_pipeline.CHECKS")
        self.assertIn("hc.run_all()", source)

    def test_every_check_function_is_in_the_list(self):
        """寫了檢查卻沒進清單，等於沒寫。

        比對的是模組裡所有 check_* 函式與 CHECKS 的內容，所以新增一個忘了註冊
        時這裡會紅——不必記得回來改測試。
        """
        defined = {name for name in dir(hc)
                   if name.startswith("check_") and callable(getattr(hc, name))}
        registered = {check.__name__ for check in hc.CHECKS}
        self.assertEqual(defined - registered, set(),
                         "這些檢查沒有被註冊進 CHECKS")

    def test_run_all_actually_calls_each_one(self):
        """問執行路徑，不問原始碼字串。

        原本的守衛是 assertIn("check_card_production(),", source)，它守的是
        pipeline 的檔案內容——而每天跑的是 notify。這裡改成把每個檢查換成間諜，
        看 run_all() 有沒有真的叫到。
        """
        called: list[str] = []

        def spy(name):
            def _check():
                called.append(name)
                return {"name": name, "checks": []}
            _check.__name__ = name
            return _check

        spies = tuple(spy(check.__name__) for check in hc.CHECKS)
        with mock.patch.object(hc, "CHECKS", spies):
            hc.run_all()

        self.assertEqual(called, [check.__name__ for check in hc.CHECKS])

    def test_the_notifier_runs_the_same_checks(self):
        """notify 的 main() 會不會跑到每一項——這是每天真的會走的那條路。"""
        import health_check_notify as notify

        called: list[str] = []

        def fake_run_all():
            called.append("run_all")
            return [{"name": check.__name__, "checks": []} for check in hc.CHECKS]

        with mock.patch.object(notify.hc, "run_all", fake_run_all), \
             mock.patch.object(notify, "_inventory_lines", lambda: []), \
             mock.patch.object(notify, "_save_alert_state", lambda _s: None), \
             mock.patch.object(notify, "_load_alert_state", lambda: {}), \
             mock.patch.object(sys, "argv", ["health_check_notify.py", "--dry-run"]):
            notify.main()

        self.assertEqual(called, ["run_all"])


class TheNewChecksReachTheDailyMessage(unittest.TestCase):
    """這一輪加的兩項，具體確認它們在每日那條路上。"""

    def test_card_production_is_registered(self):
        self.assertIn("check_card_production",
                      {c.__name__ for c in hc.CHECKS})

    def test_pipeline_ledger_is_registered(self):
        self.assertIn("check_pipeline_ledger",
                      {c.__name__ for c in hc.CHECKS})

    def test_external_dependencies_is_registered(self):
        """它原本只在 notify 那份清單裡，所以手動跑 pipeline 時不被檢查。"""
        self.assertIn("check_external_dependencies",
                      {c.__name__ for c in hc.CHECKS})

    def test_every_registered_check_has_a_label(self):
        """沒有標籤的話，通知裡會出現內部識別字。決策類的不需要標籤。"""
        import health_check_notify as notify
        names = {c.__name__.replace("check_", "") for c in hc.CHECKS}
        missing = names - set(notify.FAULT_LABELS) - notify.DECISION_SECTIONS
        self.assertEqual(missing, set(), f"這些 section 沒有可讀的標籤：{missing}")


class NoSelfInflictedImportCycle(unittest.TestCase):
    """xkb_paths 是基礎模組，不可以依賴純觀測工具。

    2026-09-11 批次加使用量測時，xkb_paths 也被接上了（它有 __main__，會印路徑）。
    而 xkb_usage 需要 xkb_paths 決定紀錄寫在哪——於是
    xkb_usage → xkb_paths → xkb_usage。Python 剛好不會炸，所以 401 項測試沒有
    一項發現。
    """

    def test_xkb_paths_does_not_import_the_usage_recorder(self):
        source = (SCRIPTS / "xkb_paths.py").read_text(encoding="utf-8")
        self.assertNotIn("import xkb_usage", source)

    def test_the_usage_recorder_is_a_leaf_apart_from_paths(self):
        """觀測可以讀路徑，但不可以被路徑讀。"""
        source = (SCRIPTS / "xkb_usage.py").read_text(encoding="utf-8")
        self.assertIn("import xkb_paths", source)


if __name__ == "__main__":
    unittest.main()
