"""消化不可以丟掉筆記——結構上不可以，不是靠一個指標去追。

2026-09-11 量到的狀況：

  - 提示詞本身就在要求丟東西：「最多 8 條，超過的會被直接截掉，不要把每條筆記
    都改寫一遍」。30 條進去、8 條出來，另外 22 條無聲消失。
  - `--apply` 是用結論「取代」原始條列，所以那 22 條就此不見。
  - 壓縮比會因此好看（實測有一次 16.2x，其實是整批不見了）。**一個會因為弄壞
    事情而變好看的指標，不該留在會被自動執行的路徑上。**

我第一版的修法是「用出處對帳」：要求結論抄上來源標記，沒被引用到的筆記留著。
那個修法在真實資料上是假的——21 條筆記只有 4 個相異出處（一天的筆記都寫進同一
個 memory 檔），所以一條結論引用一次就「覆蓋」了 10 條。它會回報「沒丟東西」，
而它量不出丟了沒有。

Pan 選的做法是不對帳：**筆記全部留著**，結論是附加上去的。代價是頁面變長，而
語意召回撈的是段落不是整頁，所以那個代價很小。

這份測試守的是那個結構保證。
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import xkb_synthesize_topic as syn  # noqa: E402

NOTE = ("- 這是一條夠長的筆記，長度必須超過六十個字元才會被當成敘述型條列而不是"
        "雜項，所以這裡要把它寫得長一些。 *(self-derived · memory/{day}.md)*")


class ProvenanceIsTooCoarseToReconcileWith(unittest.TestCase):
    """先證明第一版的修法是假的——否則沒人知道為什麼要換成「全部保留」。"""

    def test_many_notes_share_one_source_so_coverage_overcounts(self):
        # 真實資料：ai-coding-quality-gates 有 21 條筆記、只有 4 個相異出處，
        # 其中 10 條都是 memory/2026-09-10.md。
        # 編號要放在出處標記之前——標記是錨在行尾的，放後面就讀不到出處，
        # 那會讓這個測試通過的理由變成「讀不到出處」而不是「出處太粗」。
        same_day = [f"- 第{i}條筆記，內容各不相同但來自同一天的 memory 檔，長度要超過六十個字元才算敘述型條列。 *(self-derived · memory/2026-09-10.md)*"
                    for i in range(10)]
        # 一條結論只引用那個出處一次。
        one_conclusion = "- 合併後的結論。 *(self-derived · memory/2026-09-10.md)*"

        covered = [syn._is_covered(n, one_conclusion) for n in same_day]

        # 十條全部被判定「已消化」，而實際上只消化了其中一部分。
        self.assertTrue(all(covered))
        self.assertEqual(len(covered), 10)

    def test_a_note_with_no_provenance_is_treated_as_undigested(self):
        """無法對帳的就當沒消化——留著比丟掉安全。"""
        self.assertFalse(syn._is_covered("- 沒有出處的一條筆記。", "- 某個結論"))


class NothingIsEverReplaced(unittest.TestCase):
    def test_the_compression_gate_is_gone(self):
        """那個門檻會因為丟掉內容而變好看，所以它獎勵的正是要防的行為。"""
        source = (ROOT / "scripts" / "xkb_synthesize_topic.py").read_text(encoding="utf-8")
        # 只剩說明它為什麼被拿掉的註解，不能還有在用的常數或判斷。
        self.assertNotIn("MIN_COMPRESSION = ", source)
        self.assertNotIn("ratio < ", source)

    def test_the_digested_section_is_a_recognised_block(self):
        """已消化區要被認得，否則下一次 --apply 會把它當成別人的東西或素材。"""
        self.assertIn(syn.DIGESTED_HEADING, syn.GENERATED_HEADINGS)

    def test_digested_notes_are_not_offered_for_digestion_again(self):
        """留在頁面上是為了可追溯，不是為了再消化一次。

        再交出去的話，每次 --apply 都會把同一批筆記重新消化，結論會一層層疊加。
        """
        page = (
            "# 主題\n\n一段人寫的前言。\n\n"
            + syn.CONCLUSIONS_HEADING + "\n\n- 既有的結論。\n\n"
            + syn.DIGESTED_HEADING + "\n\n"
            + NOTE.format(day="2026-08-01") + "\n\n"
            + syn.SOURCES_HEADING + "\n\n- [來源](https://example.test/a)\n"
        )

        _, waiting, _, conclusions = syn.undigested(page)

        self.assertEqual(waiting, [])
        self.assertIn("既有的結論", conclusions)

    def test_prior_digested_notes_are_read_back_so_they_survive(self):
        """併回去時要把上一輪保留的筆記再寫出去，否則它們會在這一輪消失——
        那就又變成「消化會丟東西」，只是晚一輪發生。"""
        note = NOTE.format(day="2026-08-01")
        page = (
            "# 主題\n\n前言。\n\n"
            + syn.DIGESTED_HEADING + "\n\n" + note + "\n\n"
            + syn.SOURCES_HEADING + "\n\n- [來源](https://example.test/a)\n"
        )

        self.assertEqual(syn.prior_digested(page), [note])

    def test_prior_digested_is_empty_when_the_section_is_absent(self):
        self.assertEqual(syn.prior_digested("# 主題\n\n沒有那個區塊。\n"), [])


class TheApplyPathKeepsEverything(unittest.TestCase):
    """讀 --apply 那段的組裝，確認三種筆記都有出口。"""

    def test_every_bullet_has_a_destination(self):
        source = (ROOT / "scripts" / "xkb_synthesize_topic.py").read_text(encoding="utf-8")
        merge = source.split("# 這次併回去的是")[1].split("path.write_text(merged")[0]
        # 消化出結論的 → 已消化區；沒消化出來的 → 尚未消化區；上一輪的 → 讀回來。
        self.assertIn("DIGESTED_HEADING", merge)
        self.assertIn("UNDIGESTED_HEADING", merge)
        self.assertIn("prior_digested(text)", merge)


if __name__ == "__main__":
    unittest.main()
