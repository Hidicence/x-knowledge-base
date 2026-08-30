from __future__ import annotations

import importlib
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

pending_work = importlib.import_module("xkb_pending_work")

CARD = """---
id: youtube-abc
type: knowledge-card
source_type: youtube
---
# 標題

## 1. 核心問題與結論
"""

RAW = """---
source_url: https://x.com/i/status/1
---
一段還沒消化的原文。
"""


class UncardedBookmarkTests(unittest.TestCase):
    def _dirs(self, tmp: str):
        bookmarks = Path(tmp) / "bookmarks"
        cards = Path(tmp) / "cards"
        (bookmarks / "youtube").mkdir(parents=True)
        (bookmarks / "inbox").mkdir(parents=True)
        cards.mkdir()
        return bookmarks, cards

    def test_a_bookmark_that_is_itself_a_card_is_not_pending(self) -> None:
        """The YouTube path writes its card in place, in the bookmarks tree.

        Counting those as unprocessed reported 23 finished cards as work
        outstanding every day, while they sat in the search index with
        vectors. A number that is red when nothing is wrong stops being read.
        """
        with tempfile.TemporaryDirectory() as tmp:
            bookmarks, cards = self._dirs(tmp)
            (bookmarks / "youtube" / "abc.md").write_text(CARD, encoding="utf-8")
            self.assertEqual(pending_work.uncarded_bookmarks(bookmarks, cards), [])

    def test_a_raw_bookmark_is_pending(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bookmarks, cards = self._dirs(tmp)
            (bookmarks / "inbox" / "raw.md").write_text(RAW, encoding="utf-8")
            self.assertEqual(
                [p.name for p in pending_work.uncarded_bookmarks(bookmarks, cards)],
                ["raw.md"],
            )

    def test_a_bookmark_with_a_card_elsewhere_is_not_pending(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bookmarks, cards = self._dirs(tmp)
            (bookmarks / "inbox" / "raw.md").write_text(RAW, encoding="utf-8")
            (cards / "raw.md").write_text("# 已消化", encoding="utf-8")
            self.assertEqual(pending_work.uncarded_bookmarks(bookmarks, cards), [])

    def test_the_marker_is_read_from_frontmatter_not_body(self) -> None:
        """A body that merely mentions the phrase must not count as done."""
        with tempfile.TemporaryDirectory() as tmp:
            bookmarks, cards = self._dirs(tmp)
            body = RAW + "\n" + "x" * 500 + "\ntype: knowledge-card\n"
            (bookmarks / "inbox" / "raw.md").write_text(body, encoding="utf-8")
            self.assertEqual(
                [p.name for p in pending_work.uncarded_bookmarks(bookmarks, cards)],
                ["raw.md"],
            )


class TranscriptTests(unittest.TestCase):
    def test_only_incomplete_transcripts_are_reported(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            raw = Path(tmp) / "youtube-raw"
            raw.mkdir()
            (raw / "done.json").write_text(json.dumps({"status": "completed"}), encoding="utf-8")
            (raw / "waiting.json").write_text(json.dumps({"status": "pending_card_generation"}), encoding="utf-8")
            self.assertEqual(
                pending_work.unprocessed_transcripts(raw),
                ["waiting.json (pending_card_generation)"],
            )

    def test_unreadable_transcripts_are_surfaced_not_skipped(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            raw = Path(tmp) / "youtube-raw"
            raw.mkdir()
            (raw / "broken.json").write_text("{not json", encoding="utf-8")
            self.assertEqual(pending_work.unprocessed_transcripts(raw), ["broken.json (unreadable)"])

    def test_absent_directory_is_empty(self) -> None:
        self.assertEqual(pending_work.unprocessed_transcripts(Path("/nonexistent")), [])


if __name__ == "__main__":
    unittest.main()
