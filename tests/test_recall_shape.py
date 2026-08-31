"""召回的形狀:一份搜尋邏輯、兩層平行、後端壞掉要出聲。

這三件事都是量出來的,不是偏好:

  一份邏輯   搜尋原本長在 recall_for_conversation 的 main() 裡,所以 router
             只能另開一個 Python 行程去用它。同一份程式兩個入口,而其中一個
             要付行程啟動的錢,還把例外變成無法解析的 stdout。

  兩層平行   continuity 花 2,682ms 在本機向量運算,卡片層花 1,187ms 等外部
             行程,而且互不相依。改成平行後 hard 召回從 3,373ms 降到 2,180ms。
             hook 只給六秒,而索引還在長。

  要出聲     那條路上原本有兩層 except,外層會回報、內層先把例外吃成空陣列,
             所以回報從來沒有執行過。
"""
from __future__ import annotations

import ast
import io
import sys
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import recall_router  # noqa: E402


class RecallShapeTest(unittest.TestCase):
    def source(self) -> str:
        return Path(recall_router.__file__).read_text(encoding="utf-8")

    def test_the_search_is_not_a_child_process(self) -> None:
        """再開一次行程就是又多一份索引載入,和一個無法解析的 stdout。"""
        for node in ast.walk(ast.parse(self.source())):
            if not isinstance(node, ast.Call):
                continue
            target = ast.unparse(node.func)
            if target.endswith("subprocess.run") or target == "run":
                args = ast.unparse(node).replace(" ", "")
                self.assertNotIn(
                    "recall_for_conversation", args,
                    "召回不該再另開行程跑 recall_for_conversation.py",
                )

    def test_the_two_expensive_layers_overlap(self) -> None:
        """排隊跑的話,hard 召回會多花 1.2 秒,而 hook 只給六秒。"""
        self.assertIn(
            "ThreadPoolExecutor", self.source(),
            "continuity 與卡片層互不相依,應該平行取回",
        )

    def test_a_failed_search_is_reported_not_emptied(self) -> None:
        """後端壞掉回空陣列,跟「這個主題沒有知識」長得一模一樣。"""
        import recall_for_conversation

        def boom(*_args, **_kwargs):
            raise RuntimeError("後端壞掉")

        buf = io.StringIO()
        with mock.patch.object(recall_for_conversation, "search", boom):
            with redirect_stderr(buf):
                text, results = recall_router.run_associative_recall("測試", limit=2)

        self.assertEqual(results, [], "行為不變:呼叫端不會因此中斷")
        self.assertIn("associative recall", buf.getvalue(), "但必須留下痕跡")
        self.assertIn("error", text, "回傳的文字也要說明是錯誤,不是查無資料")


if __name__ == "__main__":
    unittest.main()
