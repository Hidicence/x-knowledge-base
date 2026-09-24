"""沒有 frontmatter 就沒有地方寫分類——那要說「做不到」，不是「待辦」。

索引 1,706 列裡有 9 列的檔案沒有 YAML frontmatter（github stars、youtube、抓取
失敗的空殼）。這些檔的分類落在 build_search_index.sh 的預設值，所以它們永遠在
「清單外」那一份名單裡。

第一版把它們照樣排進計畫，於是報告每次都說「要重新分類 3 筆」，--apply 之後那 3
筆還在。這是這個專案反覆出現的同一個形狀：**把「做不到」寫成「待辦」**，看起來像
還沒輪到，實際上永遠不會發生。（記憶 xkb-gate-asked-the-impossible：放行率 0 不是
門檻太嚴，是資料根本無法滿足。）
"""
from __future__ import annotations

import io
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import xkb_recategorize as rc  # noqa: E402


class FilesWithoutFrontmatterAreReportedNotPlanned(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def _run(self, files: list[Path]) -> str:
        item = {"category": "general", "title": "每次都說要改的那一筆",
                "relative_path": str(files[0])}
        out = io.StringIO()
        with mock.patch.object(rc, "load_items", return_value=[item]), \
             mock.patch.object(rc, "off_taxonomy", return_value=[item]), \
             mock.patch.object(rc.xkb_index, "files_for_item", return_value=files), \
             mock.patch.object(rc.category_classifier, "taxonomy",
                               return_value=["99-general"]), \
             mock.patch.object(rc.category_classifier, "proposals", return_value={}), \
             mock.patch.object(rc, "gather_promoted", return_value=[]), \
             mock.patch.object(sys, "argv", ["xkb_recategorize.py"]), \
             redirect_stdout(out):
            rc.main()
        return out.getvalue()

    def test_a_file_without_frontmatter_is_not_listed_as_work_to_do(self):
        path = self.root / "every-app__open-seo.md"
        path.write_text("# every-app/open-seo\n\n- **Category:** seo-geo\n",
                        encoding="utf-8")

        output = self._run([path])

        self.assertIn("沒有 YAML frontmatter", output)
        self.assertNotIn("要重新分類", output,
                         "寫不進去的不該列成待辦——報告會每次重複一個不會發生的承諾")

    def test_it_does_not_call_the_classifier_for_a_file_it_cannot_write(self):
        """連 LLM 都不該花。判斷完也沒有地方寫。"""
        path = self.root / "no-frontmatter.md"
        path.write_text("# 只有正文\n", encoding="utf-8")

        with mock.patch.object(rc.category_classifier, "classify_content") as spy:
            self._run([path])

        spy.assert_not_called()

    def test_a_group_with_one_writable_file_is_still_planned(self):
        """一組裡只要有一個檔寫得進去就算可做——別把整組一起放棄。"""
        plain = self.root / "bookmark.md"
        plain.write_text("# 沒有 frontmatter\n", encoding="utf-8")
        card = self.root / "card.md"
        card.write_text("---\ncategory: general\ntitle: t\n---\n# t\n",
                        encoding="utf-8")

        with mock.patch.object(
                rc.category_classifier, "classify_content",
                return_value={"category": "99-general", "confidence": "high",
                              "llm": True, "proposed_category": "",
                              "proposal_count": 0}):
            output = self._run([card, plain])

        self.assertIn("要重新分類", output)
        self.assertNotIn("沒有 YAML frontmatter", output)


if __name__ == "__main__":
    unittest.main()
