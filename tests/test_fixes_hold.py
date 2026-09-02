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

import inspect
import os
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

        script = Path(inspect.getsourcefile(gate))
        with tempfile.TemporaryDirectory() as tmp:
            # 要把腳本真正會寫的那個檔導到這裡來。原本這個測試在 tmp 底下放
            # 一個哨兵、卻沒告訴子行程，於是那個斷言永遠不可能失敗——真正撐著
            # 它的只有 stdout 檢查。而 guard 壞掉時，它會寫進**正式**的
            # review-decisions.json（Pan 累積的 285 筆判斷）。
            # 一個失敗時會破壞正式資料的測試，比沒有測試更糟。
            decisions = Path(tmp) / "review-decisions.json"
            decisions.write_text(
                '{"_comment": "sentinel", "topics": {}, "decisions": {}}',
                encoding="utf-8")
            before = decisions.read_text(encoding="utf-8")

            env = dict(os.environ, XKB_REVIEW_DECISIONS=str(decisions))
            proc = subprocess.run(
                [sys.executable, str(script), "--review", "--topic", "__no_such_topic__"],
                capture_output=True, text=True, encoding="utf-8",
                errors="replace", timeout=300, cwd=str(ROOT), env=env,
            )
            # 乾淨結束才算數：提早 return 1 的話，下面的斷言會是空的。
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
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
                window = "\n".join(src.split("\n")[lineno - 1:lineno + 16])
                self.assertTrue(
                    any(tok in window for tok in ("for vec in", "for v in rows", "max(", "for rows")),
                    f"{name}:{lineno} 沒有把每張卡的多條向量拆開：\n{window}",
                )


class PruningKeysOnEnumerationAndRunsAtAll(unittest.TestCase):
    """清理寫錯過三次，每次都是同一個混淆的不同面向。這裡釘的是那三件事。"""

    SRC = (ROOT / "scripts" / "build_vector_index.py").read_text(encoding="utf-8")

    def test_it_keys_on_what_was_enumerated_not_what_was_queued(self) -> None:
        # 一：用 queued_keys 推來源，會把沒變動的段落當成消失了而刪掉。
        self.assertIn("enumerated_keys", self.SRC)
        self.assertNotIn('examined = {key.rsplit("#", 1)[0] for key in queued_keys}', self.SRC)
        start = self.SRC.index("    examined_docs = ")
        block = self.SRC[start:self.SRC.index("and key not in enumerated_keys]", start)]
        self.assertIn("enumerated_keys", block)
        self.assertNotIn("queued_keys", block)

    def test_it_is_not_gated_on_a_mode_nothing_uses(self) -> None:
        # 二：只在完整重建時清理，等於永遠不清——每一條排程都帶 --incremental。
        start = self.SRC.index("    examined_docs = ")
        prune = self.SRC[start:self.SRC.index("and key not in enumerated_keys]", start)]
        self.assertNotIn("args.incremental", prune)

    def test_it_is_decided_before_the_early_return(self) -> None:
        # 三：規則對了卻放在「沒東西要嵌入就提早結束」後面，平常永遠跑不到。
        decided = self.SRC.index("    stale_keys = [")
        early_return = self.SRC.index('print("✅ Nothing to embed.")')
        self.assertLess(decided, early_return,
                        "死鍵是在提早結束之後才算的，平常那條路根本走不到")
        guard = self.SRC[self.SRC.index("    if not to_embed and _partitions_ok"):]
        self.assertIn("not stale_keys", guard[:guard.index("\n")],
                      "有死鍵時仍會提早結束")

    def test_the_rule_does_not_depend_on_the_mode_at_all(self) -> None:
        """清理規則不看 --incremental，那正是它對的跡象。

        這裡原本有一個測試，宣稱要盯住「每一條排程都帶 --incremental」。它比對的
        是整份檔案裡有沒有出現這個字串，所以 fetch_and_summarize.sh 靠一句無關的
        build_search_index.sh 就過了——而那支腳本本身還有一條刻意的完整重建分支，
        也就是說它宣稱要守的前提本來就不成立。

        但那個前提只對「規則被模式擋住」的那一版有意義。現在規則問的是這一輪
        列舉到了哪些鍵，兩種模式的答案一樣好，所以沒有前提需要守。
        守著一個過期前提、而且用一個根本沒在檢查它的斷言，比沒有測試更糟：
        它對一件自己從沒看過的事情回報信心。
        """
        prune = self.SRC[self.SRC.index("    examined_docs = "):]
        prune = prune[:prune.index("and key not in enumerated_keys]")]
        self.assertNotIn("incremental", prune)


if __name__ == "__main__":
    unittest.main()
