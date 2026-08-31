"""一件事只能有一個定義。

這裡的每一條規則都對應一次真的發生過的故障，不是風格意見：

  路徑    2026-07-28 資料目錄搬家，兩半管線各自推導位置，卡片全被吃掉。
          xkb_paths 是為了這件事寫的，但十五支腳本 import 了它之後，
          又用 WORKSPACE / "memory" / ... 自己拼一次，共二十八處。

  數量    「有幾張卡片」有兩個答案，1,538 和 1,539：一支用 rglob，
          把治理判定重複而移走的卡片也算了進去。

  出處    self-derived 的判斷曾經有三個寫法，於是 913 條自產知識繞過了
          回音室降權。

一個新的定義出現時，這裡會失敗，而不是等它在某個早上說出錯的數字。
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"

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


if __name__ == "__main__":
    unittest.main()
