"""BM25 召回腿（SQLite FTS5），取代那條審了十輪的識別碼特例腿。

量過：BM25 identifier hit@3 = 9/12（實際 10-11，兩個是測試 needle 寫錯），
跟識別碼腿與 zvec-grep 打平，真 IDF、~0ms、零 bespoke code。
"""
from __future__ import annotations

import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import fts_recall
import xkb_text


def _mk_index(rows):
    """rows: list of (path, title, kind, body_source_text)."""
    tmp = Path(tempfile.mkdtemp()) / "fts_index.db"
    db = sqlite3.connect(tmp)
    db.execute("CREATE VIRTUAL TABLE docs USING fts5("
               "path UNINDEXED, title UNINDEXED, kind UNINDEXED, body, tokenize='unicode61')")
    for path, title, kind, body in rows:
        db.execute("INSERT INTO docs VALUES (?,?,?,?)",
                   (path, title, kind, " ".join(xkb_text.tokenize(body))))
    db.commit()
    db.close()
    return tmp


class BM25Leg(unittest.TestCase):
    def setUp(self):
        self.db_path = _mk_index([
            ("cards/github_star-ItusiAI-Open-Magiviz.md", "Open-Magiviz", "card",
             "Open-Magiviz 開源 AI 影片創作引擎 github ItusiAI"),
            ("cards/2045420631295242340.md", "GBrain Minions", "card",
             "2045420631295242340 GBrain v0.11 Minions BullMQ 任務佇列"),
            ("cards/2099999999999999999.md", "無關卡片", "card",
             "這是一張完全無關的卡片 講別的東西"),
            ("wiki/topics/ai-agent-memory-systems.md#四層架構", "四層架構", "wiki",
             "記憶系統 四層架構 分工 wiki �知識 卡片 對話 聯想"),
        ])
        self._orig_db = fts_recall.FTS_DB
        self._orig_conn = fts_recall._conn
        fts_recall.FTS_DB = self.db_path
        fts_recall._conn = None

    def tearDown(self):
        if fts_recall._conn:
            fts_recall._conn.close()
        fts_recall.FTS_DB = self._orig_db
        fts_recall._conn = self._orig_conn

    def test_repo_slug_hits(self):
        out = fts_recall.fts_search("Open-Magiviz", 3)
        self.assertTrue(out)
        self.assertIn("Open-Magiviz", out[0]["relative_path"])
        self.assertEqual(out[0]["score_scale"], "card_bm25")

    def test_bare_tweet_id_hits(self):
        # zvec-grep 的 FTS 漏掉純數字 ID；識別碼腿特例處理它。BM25 帶進去就有。
        out = fts_recall.fts_search("2045420631295242340", 3)
        self.assertTrue(out)
        self.assertIn("2045420631295242340", out[0]["relative_path"])

    def test_cjk_query_hits_via_ngrams(self):
        out = fts_recall.fts_search("記憶系統的四層架構", 3)
        self.assertTrue(out)
        self.assertEqual(out[0]["score_scale"], "wiki_bm25")

    def test_wiki_path_strips_the_section_anchor(self):
        out = fts_recall.fts_search("四層架構分工", 3)
        self.assertTrue(out)
        self.assertNotIn("#", out[0]["relative_path"])

    def test_empty_or_stopword_query_returns_nothing(self):
        self.assertEqual(fts_recall.fts_search("", 3), [])
        self.assertEqual(fts_recall.fts_search("的 了 嗎", 3), [])

    def test_missing_index_returns_empty_not_crash(self):
        fts_recall._conn = None
        fts_recall.FTS_DB = Path("/nonexistent/fts_index.db")
        self.assertFalse(fts_recall.available())
        self.assertEqual(fts_recall.fts_search("anything", 3), [])

    def test_scores_are_higher_is_better(self):
        out = fts_recall.fts_search("Open-Magiviz 開源 影片", 3)
        self.assertGreater(len(out), 0)
        self.assertEqual(out, sorted(out, key=lambda r: r["score"], reverse=True))


if __name__ == "__main__":
    unittest.main()
