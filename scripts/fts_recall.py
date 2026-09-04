#!/usr/bin/env python3
"""BM25 召回腿（SQLite FTS5）。

XKB 的關鍵字腿一直是 token overlap（沒有 IDF）。罕見的精確 token——錯誤碼、
repo slug、tweet ID——拿不到「因為稀有所以重要」的分，於是得靠一條審了十輪的
識別碼特例腿去補。SQLite FTS5 內建 BM25，久經考驗、零新依賴，把那條特例腿
換掉：BM25 的 IDF 天生就會把罕見 token 往上抬。

跟向量腿對等——不是取代。語意查詢向量腿扛，字面查詢 BM25 扛，
xkb_score.rank() 用 RRF 融合。

索引由 build_fts_index.py 建（跟 build_vector_index.py 並排跑）。
"""
from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any, Dict, List

import xkb_paths
import xkb_text

FTS_DB = xkb_paths.BOOKMARKS_DIR / "fts_index.db"

_conn: sqlite3.Connection | None = None


def _db() -> sqlite3.Connection | None:
    global _conn
    if _conn is not None:
        return _conn
    if not FTS_DB.exists():
        return None
    _conn = sqlite3.connect(f"file:{FTS_DB}?mode=ro", uri=True, check_same_thread=False)
    return _conn


def available() -> bool:
    return FTS_DB.exists()


def fts_search(query: str, limit: int = 5) -> List[Dict[str, Any]]:
    """BM25 命中，recall() 的 dict 形狀。score 越大越相關（= -bm25()）。

    kind=card 的標 score_scale=card_bm25、kind=wiki 的標 wiki_bm25，rank() 就把
    它們當各自的一條腿融合。
    """
    db = _db()
    if db is None:
        return []
    toks = xkb_text.tokenize(query or "")
    if not toks:
        return []
    # 每個 token 當一個帶引號的片語（避免 FTS5 把 - 之類當運算子），OR 起來。
    match = " OR ".join(f'"{t}"' for t in toks)
    try:
        rows = db.execute(
            "SELECT path, title, kind, bm25(docs) AS s FROM docs "
            "WHERE docs MATCH ? ORDER BY s LIMIT ?",
            (match, limit),
        ).fetchall()
    except sqlite3.OperationalError:
        return []

    out: List[Dict[str, Any]] = []
    for path, title, kind, s in rows:
        # wiki 的 path 是 wiki/topics/foo.md#段落；卡片是 relative_path
        rel = path.split("#", 1)[0] if kind == "wiki" else path
        out.append({
            "title": title or Path(rel).stem,
            "summary": "",
            "category": "general",
            "tags": [],
            "relative_path": rel,
            "source_url": "",
            "score": round(-float(s), 6),
            "score_scale": "wiki_bm25" if kind == "wiki" else "card_bm25",
            "section": title,
            "relevance_reason": f"BM25 字面命中（{kind}）",
        })
    return out

