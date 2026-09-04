#!/usr/bin/env python3
"""建 BM25 全文索引（SQLite FTS5）。

XKB 的關鍵字腿一直是 token overlap（沒有 IDF），罕見的精確 token——錯誤碼、
repo slug、tweet ID——拿不到「因為稀有所以重要」的分。這就是那條審了十輪的
識別碼腿要補的洞。SQLite FTS5 內建 BM25，是久經考驗的實作，零新依賴。

中文沒有空格，所以索引與查詢兩邊都先用 xkb_text.tokenize（專案共用的 2/3 字
n-gram 斷詞）切好，空白接起來，餵給 unicode61 tokenizer。識別碼欄位（檔名、
source_url 路徑段）另外當 token 存進去，這樣純數字 tweet ID 也搜得到——
那正是 zvec-grep 的 FTS 漏掉、而我的識別碼腿特例處理的 case。

用法：
  python3 scripts/build_fts_index.py [--index-file FILE]
輸出：<BOOKMARKS_DIR>/fts_index.db
"""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import xkb_paths
import xkb_text
from build_vector_index import extract_card_text, knowledge_section_docs

FTS_DB = xkb_paths.BOOKMARKS_DIR / "fts_index.db"


def _identifier_tokens_for(item: dict) -> str:
    """檔名 stem、source_url 的路徑段、id —— 當成額外 token，讓純識別碼搜得到。"""
    bits: list[str] = []
    rel = str(item.get("relative_path") or item.get("path") or "")
    if rel:
        bits.append(Path(rel).stem)
    url = str(item.get("source_url") or "")
    if url:
        bits += [seg for seg in re.split(r"[/:?#=&]+", url) if len(seg) >= 3]
    for k in ("id", "slug"):
        v = item.get(k)
        if v:
            bits.append(str(v))
    return " ".join(bits)


def _tok(text: str) -> str:
    """切成 n-gram，空白接起來（FTS5 的 unicode61 會照空白切）。"""
    return " ".join(xkb_text.tokenize(text or ""))


def build(index_file: Path) -> int:
    raw = json.loads(index_file.read_text(encoding="utf-8"))
    items = raw.get("items") if isinstance(raw, dict) else raw
    items = items or []

    tmp = FTS_DB.with_suffix(".db.tmp")
    tmp.unlink(missing_ok=True)
    db = sqlite3.connect(tmp)
    db.execute("""
        CREATE VIRTUAL TABLE docs USING fts5(
            path UNINDEXED, title UNINDEXED, kind UNINDEXED,
            body, tokenize='unicode61'
        )
    """)

    n_card = 0
    for item in items:
        rel = item.get("relative_path") or item.get("path") or ""
        if not rel:
            continue
        text = extract_card_text(item)
        blob = _tok(f"{item.get('title', '')} {text}") + " " + _tok(_identifier_tokens_for(item))
        db.execute("INSERT INTO docs(path, title, kind, body) VALUES (?,?,?,?)",
                   (rel, item.get("title", ""), "card", blob))
        n_card += 1

    n_wiki = 0
    for key, wiki_text, _hash in knowledge_section_docs():
        # key 長這樣 wiki/topics/foo.md#段落標題
        db.execute("INSERT INTO docs(path, title, kind, body) VALUES (?,?,?,?)",
                   (key, key.split("#", 1)[-1], "wiki", _tok(wiki_text)))
        n_wiki += 1

    db.commit()
    db.execute("INSERT INTO docs(docs) VALUES('optimize')")
    db.commit()
    db.close()
    tmp.replace(FTS_DB)
    print(f"FTS 索引：{n_card} 張卡 + {n_wiki} 個 wiki 段 -> {FTS_DB}")
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--index-file", default=str(xkb_paths.INDEX_FILE))
    a = ap.parse_args()
    raise SystemExit(build(Path(a.index_file)))
