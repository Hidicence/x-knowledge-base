"""一件事只能有一個定義。

這裡的每一條規則都對應一次真的發生過的故障，不是風格意見：

  路徑    2026-07-28 資料目錄搬家，兩半管線各自推導位置，卡片全被吃掉。
          xkb_paths 是為了這件事寫的，但十五支腳本 import 了它之後，
          又用 WORKSPACE / "memory" / ... 自己拼一次，共二十八處。

  數量    「有幾張卡片」有兩個答案，1,538 和 1,539：一支用 rglob，
          把治理判定重複而移走的卡片也算了進去。

  出處    self-derived 的判斷曾經有三個寫法，於是 913 條自產知識繞過了
          回音室降權。

  斷詞    「這段文字的詞是哪些」有過八份實作。那個正則對中文是壞的：
          整串中文被吃成一個 token，於是中文問句幾乎命中不了任何東西，
          看起來像知識庫沒內容。xkb_text 修好了它，但四支腳本仍各留一份。

  門檻    餘弦門檻曾經有三個數字、兩個預設值共用同一個環境變數
          （XKB_CARD_MIN_SIMILARITY 同時被 0.55 和 0.58 兩處讀走）。
          舊的防護存在，但白名單只列了兩支腳本，第三支自動繞過。

  尺度    score_scale 是散在各處的字串字面量。打錯一個字不會報錯，
          會靜默掉到 0.5 的 fallback，整層排序錯位。

一個新的定義出現時，這裡會失敗，而不是等它在某個早上說出錯的數字。
"""
from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"

sys.path.insert(0, str(SCRIPTS))
import xkb_score  # noqa: E402

SHARED_PATHS = (
    "CARDS_DIR", "BOOKMARKS_DIR", "WIKI_DIR", "WIKI_TOPICS_DIR",
    "INDEX_FILE", "VECTOR_FILE", "TOPIC_PROFILE_FILE", "DATA_DIR",
)
ASSIGNMENT = re.compile(r"^(%s)\s*=\s*(.+)$" % "|".join(SHARED_PATHS), re.M)


def _sources() -> list[Path]:
    return [p for p in sorted(SCRIPTS.glob("*.py")) if p.name != "xkb_paths.py"]


class OneDefinitionTest(unittest.TestCase):
    def test_shared_paths_come_from_xkb_paths(self) -> None:
        """別名可以，重新推導不行。

        `CARDS_DIR = xkb_paths.CARDS_DIR` 只是本地名字；
        `CARDS_DIR = WORKSPACE / "memory" / "cards"` 是第二個定義，
        而且它不會跟著 .xkb.json 或 XKB_DATA_DIR 走。
        """
        offenders = []
        for path in _sources():
            lines = path.read_text(encoding="utf-8").splitlines()
            for i, line in enumerate(lines):
                match = ASSIGNMENT.match(line)
                if not match:
                    continue
                rhs, j = match.group(2), i + 1
                while rhs.count("(") > rhs.count(")") and j < len(lines):
                    rhs += " " + lines[j].strip()
                    j += 1
                if "xkb_paths" not in rhs:
                    offenders.append(f"{path.name}:{i + 1} {match.group(1)} = {rhs[:60]}")
        self.assertEqual(offenders, [], "這些路徑沒有取自 xkb_paths，資料目錄一搬就會指到舊位置")

    def test_cards_are_counted_in_one_place(self) -> None:
        """卡片是哪些檔案，只有 xkb_paths.card_files 說了算。

        cards/_deduped_redundant/ 裡是治理判定重複而移走的卡片。用 rglob
        會把它們算回來，用 glob 不會，於是同一個問題有兩個答案。
        """
        offenders = [
            f"{path.name}:{i + 1}"
            for path in _sources()
            for i, line in enumerate(path.read_text(encoding="utf-8").splitlines())
            if re.search(r"\bCARDS_DIR\b.*\.r?glob\(", line)
        ]
        self.assertEqual(offenders, [], "改用 xkb_paths.card_files() / card_ids()")

    def test_provenance_has_one_reader(self) -> None:
        """判斷「這是不是自己產的」只有 xkb_provenance 一份規則。

        健檢曾經自己寫一套，於是把 322 條正確標記的知識報成壞的。
        """
        offenders = []
        for path in _sources():
            text = path.read_text(encoding="utf-8")
            if path.name == "xkb_provenance.py":
                continue
            if re.search(r'["\']self-derived["\']|self-derived\|', text) \
                    and "xkb_provenance" not in text and "is_self_derived" not in text:
                offenders.append(path.name)
        self.assertEqual(offenders, [], "self-derived 的判斷要走 xkb_provenance")


    def test_tokenising_goes_through_xkb_text(self) -> None:
        """切詞的規則只有 xkb_text 一份。

        判準是「有沒有自己拿一條含 CJK 範圍的正則去掃文字」——re.sub 做
        slug、re.search 判斷有沒有中文都不算，那些不是在決定「詞是哪些」。
        """
        tokenising = re.compile(r"\b(findall|finditer|re\.compile|re\.split)\b")
        # 原始碼兩種寫法都算：跳脫的 \u4e00-\u9fff，和直接寫的字元範圍。
        cjk_range = re.compile(r"(?:\\u4e00|一)\s*-")
        offenders = []
        for path in _sources():
            if path.name == "xkb_text.py":
                continue
            for i, line in enumerate(path.read_text(encoding="utf-8").splitlines()):
                if line.lstrip().startswith("#"):
                    continue
                if tokenising.search(line) and cjk_range.search(line):
                    offenders.append(f"{path.name}:{i + 1}")
        self.assertEqual(offenders, [], "自己寫斷詞正則；改用 xkb_text.tokenize()")

    def test_similarity_thresholds_come_from_xkb_relevance(self) -> None:
        """餘弦門檻只有 xkb_relevance 說了算。

        別名可以（`CARD_MIN_SIMILARITY = xkb_relevance.threshold("card_recall")`），
        自己算一個不行。這條規則刻意掃全部腳本：上一版的防護寫死了兩支
        呼叫端的白名單，於是第三支寫自己的門檻時沒有任何東西發現。

        recall_router 的 MIN_SCORE_HARD / SOFT 不在這裡，因為它們不是餘弦：
        那是 xkb_score.rank() 之後的合併分數，另一套尺度、另一個量。
        """
        assignment = re.compile(r"^([A-Z_]*(?:MIN_SIMILARITY|MIN_RELEVANCE)[A-Z_]*)\s*=\s*(.+)$", re.M)
        offenders = []
        for path in _sources():
            if path.name == "xkb_relevance.py":
                continue
            for i, line in enumerate(path.read_text(encoding="utf-8").splitlines()):
                match = assignment.match(line)
                if match and "xkb_relevance" not in match.group(2):
                    offenders.append(f"{path.name}:{i + 1} {match.group(1)}")
        self.assertEqual(
            offenders, [],
            "這些門檻沒有取自 xkb_relevance；改用 threshold() 並把值寫進 DEFAULT_THRESHOLDS",
        )

    def test_every_score_scale_is_in_the_anchor_table(self) -> None:
        """宣告出來的尺度，跨層排序時必須認得。

        xkb_score.rank() 對不認得的鍵會靜默退回 0.5，那一層的排序整段錯位，
        而且不會有任何錯誤訊息——只會是一個看起來排好、實際上翻轉了的順序。
        """
        literal = re.compile(r"""["\']score_scale["\']\]?\s*[:=]\s*["\']([a-z_]+)["\']""")
        known = set(xkb_score.DEFAULT_ANCHORS)
        offenders = []
        for path in _sources():
            for i, line in enumerate(path.read_text(encoding="utf-8").splitlines()):
                for name in literal.findall(line):
                    if name not in known:
                        offenders.append(f"{path.name}:{i + 1} score_scale={name!r}")
        self.assertEqual(offenders, [], "不在 xkb_score.DEFAULT_ANCHORS 裡的尺度 = 靜默錯位")


if __name__ == "__main__":
    unittest.main()
