#!/usr/bin/env python3
"""Report current demotion from namespace-scoped relevance verdicts. Legacy cosine statistics remain in knowledge_usage and do not drive this report."""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import xkb_console
import xkb_eviction as ev
import xkb_frontmatter
import xkb_paths

xkb_console.use_utf8()


def describe(record_id: str) -> tuple[str, str]:
    """record_id 的標題與**真正的**分類。

    record_id 的前綴是檔案放在哪個 bookmarks 資料夾（`02-seo-geo/2032...`），而那
    不是這張卡的分類——1,754 筆書籤裡有 650 筆的 frontmatter `category` 跟所在目錄
    不一致，而召回、wiki 路由、健檢讀的全都是 frontmatter。目錄只是位址。

    2026-09-12 我自己就被這個前綴騙過：看到 `02-seo-geo/` 就推論那張卡「被歸錯類
    所以被 SEO 問題撈出來」。它其實是日文的 AI 生圖 prompt 合集，frontmatter 寫
    04-ai-tools-agents，而它被撈出來是內容嵌入弱相關，跟資料夾毫無關係。報告印出
    前綴卻不印真正的分類，就是在邀請這個推論。
    """
    stem = record_id.rsplit("/", 1)[-1]
    card = xkb_paths.CARDS_DIR / f"{stem}.md"
    if not card.is_file():
        return "", ""
    try:
        text = card.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return "", ""
    title = ""
    for line in text.splitlines():
        if line.startswith("# "):
            title = line[2:].strip()
            break
    return title, (xkb_frontmatter.get(text, "category") or "")


def load_rows(namespace: str = "private") -> list[dict]:
    """Read the versioned merged-recall counters, never infer from cosine."""
    if not xkb_paths.SERVICE_DB.exists():
        return []
    from contextlib import closing
    with closing(sqlite3.connect(f"file:{xkb_paths.SERVICE_DB}?mode=ro", uri=True)) as db:
        db.row_factory = sqlite3.Row
        if not db.execute("SELECT 1 FROM sqlite_master WHERE name='recall_usage'").fetchone():
            return []
        return [dict(r) for r in db.execute(
            "SELECT * FROM recall_usage WHERE namespace=? ORDER BY judged_count DESC", (namespace,))]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--namespace", default="private")
    parser.add_argument("--after", type=int, default=ev.DEMOTE_AFTER_CONSIDERED)
    parser.add_argument("--limit", type=int, default=20)
    args = parser.parse_args()
    rows = load_rows(args.namespace)
    demoted = [r for r in rows if ev.is_demoted(r["judged_count"], r["relevant_count"], after=args.after)]
    if args.json:
        print(json.dumps({"measurement_basis": "explicit_relevance_v2", "namespace": args.namespace,
                          "after": args.after, "tracked": len(rows), "demoted": len(demoted),
                          "records": demoted}, ensure_ascii=False, indent=2))
        return 0
    print(f"Tracked {len(rows)} records; {len(demoted)} repeatedly judged irrelevant.")
    for r in demoted[:args.limit]:
        title, category = describe(r["record_id"])
        print(f"{r['judged_count']} judgements / {r['returned_count']} returned: [{category}] {title or r['record_id']}")
    print("A relevant verdict revives the record. Returned does not imply used in an answer.")
    return 0


import xkb_usage  # noqa: E402  — 量測誰在跑，見 scripts/xkb_usage.py

if __name__ == "__main__":
    xkb_usage.record(__file__)
    raise SystemExit(main())
