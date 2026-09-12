"""新分類不是看到一筆就開，是同一個名字累積到門檻才開。

這條規則 wiki topic 那一層早就有（xkb_review.PROMOTE_AFTER 的註解：「一個名字被
提議過幾次，才算『重複出現』而值得開一頁。五次是刻意保守的：開一頁很便宜，但一頁
只放一條就是把佇列的問題搬進 wiki 裡」），不到門檻的條目先導向 general，但身上留著
proposed_topic，門檻到了再撈回來。

分類這一層原本沒有候診室：classify_content 只要模型講 NEW_CATEGORY 且信心 high，
就立刻 register_category，一筆零星內容就能長出一個永久分類。

而 2026-09-12 我「修」它的方式是傳 allow_new=False，把提案整個丟掉——那是相反方向
的同一種錯：規則說的是不能馬上開，不是不能開。這份測試把兩個方向都擋住。

最後一段最容易漏：門檻到了、分類開了，但沒有人回頭把那些先被歸到既有分類、身上帶著
proposed_category 的卡片搬過去。漏掉它的話，門檻到了也只是多一個空分類。
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import category_classifier as cc  # noqa: E402
import xkb_frontmatter  # noqa: E402
import xkb_recategorize as rc  # noqa: E402
import xkb_review  # noqa: E402


class OneRuleTwoLayers(unittest.TestCase):
    def test_the_threshold_matches_the_wiki_topic_layer(self):
        """同一條規則不該在兩層用不同的數字。

        分類這邊刻意不 import xkb_review（那是一支帶 main 的大模組，為一個常數把它
        拉進攝取路徑不划算），所以用測試擋住兩邊悄悄分岔。
        """
        self.assertEqual(cc.PROMOTE_AFTER, xkb_review.PROMOTE_AFTER)

    def test_the_threshold_is_more_than_one(self):
        """門檻等於 1 就是「馬上開」，規則會在不改任何程式碼的情況下消失。"""
        self.assertGreater(cc.PROMOTE_AFTER, 1)


class ProposalsAccumulate(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        for name, attr in (("category-proposals.json", "PROPOSALS_PATH"),
                           ("category-taxonomy.json", "RUNTIME_TAXONOMY_PATH")):
            patcher = mock.patch.object(cc, attr, root / name)
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_one_proposal_does_not_open_a_category(self):
        count, opened = cc.propose_category("07-carbon", reason="碳盤查")
        self.assertEqual(count, 1)
        self.assertFalse(opened, "一票就開的話候診室等於不存在")
        self.assertNotIn("07-carbon", cc.taxonomy())

    def test_it_opens_exactly_at_the_threshold(self):
        for n in range(1, cc.PROMOTE_AFTER):
            count, opened = cc.propose_category("07-carbon")
            self.assertEqual(count, n)
            self.assertFalse(opened, "第 %d 票就開了" % n)
            self.assertNotIn("07-carbon", cc.taxonomy())

        count, opened = cc.propose_category("07-carbon")

        self.assertEqual(count, cc.PROMOTE_AFTER)
        self.assertTrue(opened)
        self.assertIn("07-carbon", cc.taxonomy())

    def test_the_proposal_records_why_and_which_cards(self):
        """只有名字是不夠的——回頭要看得出這個分類當初為什麼會出現。"""
        cc.propose_category("07-carbon", reason="碳盤查與 ISO 14064",
                            record_id="memory/cards/a.md")
        cc.propose_category("07-carbon", record_id="memory/cards/b.md")

        entry = cc.proposals()["07-carbon"]

        self.assertEqual(entry["count"], 2)
        self.assertIn("碳盤查", entry["reason"])
        self.assertEqual(entry["examples"], ["memory/cards/a.md", "memory/cards/b.md"])
        self.assertIn("first_at", entry)
        self.assertIn("last_at", entry)

    def test_the_same_card_is_not_counted_twice_in_the_examples(self):
        cc.propose_category("07-carbon", record_id="memory/cards/a.md")
        cc.propose_category("07-carbon", record_id="memory/cards/a.md")
        self.assertEqual(cc.proposals()["07-carbon"]["examples"],
                         ["memory/cards/a.md"])

    def test_proposing_an_existing_category_is_a_no_op(self):
        count, opened = cc.propose_category("99-general")
        self.assertEqual((count, opened), (0, False))
        self.assertEqual(cc.proposals(), {})

    def test_an_unwritable_proposals_file_opens_nothing(self):
        """記不下提案就不該放行。

        悄悄開一個沒人知道為什麼存在的分類，比「每次都差一票」糟得多。
        """
        with mock.patch("pathlib.Path.mkdir", side_effect=OSError("唯讀")):
            count, opened = cc.propose_category("07-carbon")
        self.assertEqual((count, opened), (0, False))
        self.assertNotIn("07-carbon", cc.taxonomy())


class ClassifyContentGoesThroughTheWaitingRoom(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        for name, attr in (("category-proposals.json", "PROPOSALS_PATH"),
                           ("category-taxonomy.json", "RUNTIME_TAXONOMY_PATH")):
            patcher = mock.patch.object(cc, attr, root / name)
            patcher.start()
            self.addCleanup(patcher.stop)

    def _llm_says_new(self) -> str:
        return json.dumps({"category": "NEW_CATEGORY", "new_category": "07-carbon",
                           "confidence": "high", "reason": "碳盤查", "tags": []},
                          ensure_ascii=False)

    def test_a_high_confidence_new_category_is_only_a_vote(self):
        with mock.patch.object(cc, "_llm_call", return_value=self._llm_says_new()):
            result = cc.classify_content("碳盤查 ISO 14064 排放係數")

        self.assertIn(result["category"], cc.taxonomy(),
                      "還在候診時要先歸到既有分類")
        self.assertEqual(result["proposed_category"], "07-carbon")
        self.assertEqual(result["proposal_count"], 1)
        self.assertNotIn("07-carbon", cc.taxonomy())

    def test_the_category_is_used_once_the_quorum_is_reached(self):
        with mock.patch.object(cc, "_llm_call", return_value=self._llm_says_new()):
            for _ in range(cc.PROMOTE_AFTER - 1):
                cc.classify_content("碳盤查 ISO 14064")
            result = cc.classify_content("碳盤查 ISO 14064")

        self.assertEqual(result["category"], "07-carbon")
        self.assertEqual(result["proposed_category"], "",
                         "已經開了就不該再回傳提案名")
        self.assertIn("07-carbon", cc.taxonomy())

    def test_a_name_that_is_already_open_is_used_not_re_proposed(self):
        """門檻到了之後，同類的下一張卡片要直接進那個分類。

        這個缺陷是既有測試意外抓到的，不是我想到的：propose_category 對已存在的
        分類回 (0, False)（既有分類不再累積票數），而我把那個回答當成「還在等」，
        於是第一批開完之後，同類的每一張卡片都被丟回 99-general——門檻機制反而
        讓它想收攏的那些知識散掉。
        """
        with mock.patch.object(cc, "_llm_call", return_value=self._llm_says_new()):
            for _ in range(cc.PROMOTE_AFTER):
                cc.classify_content("碳盤查 ISO 14064")
            self.assertIn("07-carbon", cc.taxonomy())

            after = cc.classify_content("另一張碳盤查的卡片")

        self.assertEqual(after["category"], "07-carbon",
                         "開完之後同類的卡片不該被丟回 general")
        self.assertEqual(after["proposed_category"], "")

    def test_allow_new_false_still_works_for_callers_that_mean_it(self):
        """關掉提案仍然可行，但那不是預設，也不是規則。"""
        with mock.patch.object(cc, "_llm_call", return_value=self._llm_says_new()):
            result = cc.classify_content("碳盤查", allow_new=False)
        self.assertEqual(result["proposed_category"], "")
        self.assertEqual(cc.proposals(), {})


class ThePromotedCardsGetGathered(unittest.TestCase):
    """門檻到了之後，還要有人把在候診的卡片搬過去。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def _card(self, name: str, category: str, proposed: str) -> Path:
        path = self.root / name
        path.write_text(
            "---\ncategory: %s\nproposed_category: %s\ntitle: t\n---\n# t\n"
            % (category, proposed), encoding="utf-8")
        return path

    def test_a_card_waiting_for_a_now_existing_category_is_listed(self):
        card = self._card("a.md", "99-general", "07-carbon")
        with mock.patch.object(cc, "taxonomy",
                               return_value=["99-general", "07-carbon"]), \
             mock.patch.object(rc.xkb_index, "files_for_item", return_value=[card]):
            out = rc.gather_promoted([{"category": "99-general"}])
        self.assertEqual(out, [("07-carbon", [card])])

    def test_a_card_whose_proposal_has_not_been_opened_is_left_waiting(self):
        card = self._card("a.md", "99-general", "07-carbon")
        with mock.patch.object(cc, "taxonomy", return_value=["99-general"]), \
             mock.patch.object(rc.xkb_index, "files_for_item", return_value=[card]):
            self.assertEqual(rc.gather_promoted([{"category": "99-general"}]), [])

    def test_a_card_already_in_its_proposed_category_is_not_moved_again(self):
        card = self._card("a.md", "07-carbon", "07-carbon")
        with mock.patch.object(cc, "taxonomy",
                               return_value=["99-general", "07-carbon"]), \
             mock.patch.object(rc.xkb_index, "files_for_item", return_value=[card]):
            self.assertEqual(rc.gather_promoted([{"category": "07-carbon"}]), [])

    def test_apply_to_keeps_the_proposal_on_the_card(self):
        """候診中的卡片要帶著提議的名字，不然門檻到了撈不回來。"""
        path = self.root / "a.md"
        path.write_text("---\ncategory: 99-general\ntitle: t\n---\n# t\n",
                        encoding="utf-8")

        rc.apply_to([path], "99-general", "07-carbon")

        text = path.read_text(encoding="utf-8")
        self.assertEqual(xkb_frontmatter.get(text, "proposed_category"), "07-carbon")


if __name__ == "__main__":
    unittest.main()
