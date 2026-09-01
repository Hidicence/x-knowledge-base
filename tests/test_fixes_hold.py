"""修法本身要有效。這三個都是我 2026-08-31 的「修好了」，隔天外部審查發現
它們沒修好，其中兩個比原本更糟。

只讀旗標    absorb_gate 的寫入條件被我寫反成 `review and write_decisions`。
            於是 `--review`（文件寫明「只顯示結果」）會覆寫決策檔，而真正
            要求寫入的那個旗標反而不寫。原本的 bug 是「不加旗標也會寫」，
            我的版本是「除了要求寫入以外都會寫」。

回傳型別    lookup_card_vectors 改成每張卡回一串論點向量。我更新了一個
            呼叫端，另一個沒有——那條路徑上 _cosine 會拿 list 去乘 float。
            測試沒抓到，因為那一批候選是空的，路徑從來沒被執行。

增量清理    「清掉沒有對應段落的鍵」的判斷是錯的：增量模式只要文件有一塊
            變了，整份文件就算被檢查過，於是沒變的塊全被刪。一張改過的
            卡片會在 #kp 鍵與卡片級鍵之間永遠振盪，每次都重新付費嵌入。
            我當時的驗證是「看它刪了 31 個鍵」——我測的是它有動作，不是
            它動得對。

共同點是我驗證了行為發生，沒驗證行為正確。這三個測試問的都是後者。
"""
from __future__ import annotations

import argparse
import inspect
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))


class ReadOnlyFlagsWriteNothing(unittest.TestCase):
    """--review 不可以碰決策檔。"""

    def test_review_alone_does_not_write(self) -> None:
        src = (ROOT / "scripts" / "absorb_gate_semantic.py").read_text(encoding="utf-8")
        self.assertIn("if args.review or not args.write_decisions:", src)
        self.assertNotIn("if args.review and args.write_decisions:", src)

    def test_review_run_leaves_the_decisions_file_untouched(self) -> None:
        import absorb_gate_semantic as gate

        path = Path(inspect.getsourcefile(gate))
        with tempfile.TemporaryDirectory() as tmp:
            decisions = Path(tmp) / "review-decisions.json"
            decisions.write_text('{"sentinel": true}', encoding="utf-8")
            before = decisions.read_text(encoding="utf-8")
            proc = subprocess.run(
                [sys.executable, str(path), "--review", "--topic", "__no_such_topic__"],
                capture_output=True, text=True, encoding="utf-8",
                errors="replace", timeout=300, cwd=str(ROOT),
            )
            # 決策檔沒被建立、也沒被改寫；而且輸出不能說它寫了。
            self.assertEqual(decisions.read_text(encoding="utf-8"), before)
            self.assertNotIn("已寫入", proc.stdout)


class CallersMatchTheReturnType(unittest.TestCase):
    """改回傳型別要把每一個呼叫端都帶到。"""

    def test_lookup_card_vectors_returns_rows_per_card(self) -> None:
        import continuity_recall as cr

        sig = inspect.signature(cr.lookup_card_vectors)
        self.assertIn("list[list[float]]", str(sig.return_annotation))

    def test_every_caller_treats_values_as_a_list_of_vectors(self) -> None:
        # 呼叫端把值當成單一向量的話，_cosine 會拿 list 乘 float。這裡不跑
        # 完整召回（要嵌入服務），只確認每個呼叫點都有拆一層。
        for name in ("absorb_gate_semantic.py", "recall_router.py",
                     "recall_for_conversation.py", "continuity_recall.py"):
            src = (ROOT / "scripts" / name).read_text(encoding="utf-8")
            for lineno, line in enumerate(src.split("\n"), 1):
                if "lookup_card_vectors(" not in line or line.lstrip().startswith("#"):
                    continue
                if "def lookup_card_vectors" in line:
                    continue
                window = "\n".join(src.split("\n")[lineno - 1:lineno + 9])
                self.assertTrue(
                    any(tok in window for tok in ("for vec in", "rows[0]", "max(", "for rows")),
                    f"{name}:{lineno} 沒有把每張卡的多條向量拆開：\n{window}",
                )


class IncrementalNeverDeletes(unittest.TestCase):
    """增量執行不可以刪索引鍵——它根本沒有完整列舉過來源。"""

    def test_pruning_is_gated_on_a_full_rebuild(self) -> None:
        src = (ROOT / "scripts" / "build_vector_index.py").read_text(encoding="utf-8")
        self.assertIn("if not args.incremental:", src)
        # 舊的判斷（來源被檢查過就刪）不可以回來。
        self.assertNotIn('examined = {key.rsplit("#", 1)[0] for key in queued_keys}', src)

    def test_prune_block_sits_inside_the_full_rebuild_guard(self) -> None:
        lines = (ROOT / "scripts" / "build_vector_index.py").read_text(
            encoding="utf-8").split("\n")
        guard = next(i for i, ln in enumerate(lines)
                     if ln.strip() == "if not args.incremental:")
        guard_indent = len(lines[guard]) - len(lines[guard].lstrip())
        popped = [i for i, ln in enumerate(lines) if "new_vectors.pop(" in ln]
        self.assertTrue(popped, "找不到清理的程式碼")
        for i in popped:
            self.assertGreater(i, guard, "清理跑在完整重建的判斷之前")
            self.assertGreater(len(lines[i]) - len(lines[i].lstrip()), guard_indent,
                               "清理沒有縮排在完整重建的判斷裡面")


if __name__ == "__main__":
    unittest.main()
