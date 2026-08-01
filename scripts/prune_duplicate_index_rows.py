#!/usr/bin/env python3
"""
Prune duplicate entries in memory/bookmarks/search_index.json.

Canonical policy:
- identity is source_url when present, otherwise resolved stable id/path stem
- keep the highest-quality row (usually enriched memory/cards/*)
- remove weaker duplicate rows from the index entirely

This is intentionally stronger than canonicalize_duplicates.py, which only marks
weak rows as excluded. Search/vector/recall should not keep duplicate rows around
because downstream scripts count and embed physical rows.
"""

from __future__ import annotations

import argparse
import json
import os
import re
from collections import defaultdict
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import xkb_paths

WORKSPACE = xkb_paths.WORKSPACE
BOOKMARKS_DIR = Path(os.getenv("BOOKMARKS_DIR", str(WORKSPACE / "memory" / "bookmarks")))
INDEX_FILE = Path(os.getenv("INDEX_FILE", str(BOOKMARKS_DIR / "search_index.json")))
LOW_SIGNAL_SUMMARIES = {"", "（待整理）", "待整理", "todo", "tbd", "n/a"}


def clean_summary(text: str) -> str:
    text = (text or "").strip()
    text = re.sub(r"^#+\s*", "", text)
    text = re.sub(r"^一句話摘要\s*", "", text)
    text = re.sub(r"^[-•]\s*", "", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def extract_status_id(value: str) -> str:
    if not value:
        return ""
    m = re.search(r"/(?:status|i/status)/(\d{15,20})(?:\b|/|\?)?", value)
    return m.group(1) if m else ""


def infer_identity(item: dict[str, Any]) -> str:
    rel = item.get("relative_path") or item.get("path") or ""
    stem = Path(rel).stem
    if re.fullmatch(r"\d{15,20}", stem):
        return f"x:{stem}"

    source_url = (item.get("source_url") or "").strip()
    if source_url:
        # Prefer tweet id for X URLs so x.com/user/status/id and x.com/i/status/id collapse.
        status_id = extract_status_id(source_url)
        return f"x:{status_id}" if status_id else f"url:{source_url}"

    for key in ("id", "tweet_id", "source_id"):
        value = str(item.get(key) or "").strip()
        if value:
            return f"id:{value}"

    return f"stem:{stem}" if stem else f"row:{id(item)}"


def entry_score(item: dict[str, Any]) -> tuple:
    title = (item.get("title") or "").strip()
    summary = clean_summary(item.get("summary") or "")
    rel_path = (item.get("relative_path") or item.get("path") or "").strip()
    path = (item.get("path") or "").strip()

    enriched = 1 if item.get("enriched") else 0
    in_cards = 1 if rel_path.startswith("memory/cards/") or "/memory/cards/" in path else 0
    not_excluded = 1 if not item.get("excluded") else 0
    has_good_summary = 1 if summary not in LOW_SIGNAL_SUMMARIES and len(summary) >= 18 else 0
    has_human_title = 0 if (not title or re.fullmatch(r"\d{15,20}", title) or re.fullmatch(r"tweet\s+\d{15,20}", title.lower())) else 1
    source_url = 1 if item.get("source_url") else 0
    searchable_len = min(len(item.get("searchable") or ""), 3000)
    summary_len = min(len(summary), 300)
    title_len = min(len(title), 120)
    mtime = int(item.get("mtime") or 0)

    return (
        not_excluded,
        enriched,
        in_cards,
        has_good_summary,
        has_human_title,
        source_url,
        summary_len,
        title_len,
        searchable_len,
        mtime,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Remove duplicate rows from search_index.json")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    raw = json.loads(INDEX_FILE.read_text(encoding="utf-8"))
    items = raw.get("items", raw) if isinstance(raw, dict) else raw
    is_dict = isinstance(raw, dict)

    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in items:
        groups[infer_identity(item)].append(item)

    kept: list[dict[str, Any]] = []
    removed: list[tuple[str, str, str]] = []
    duplicate_groups = 0

    for ident, group in groups.items():
        if len(group) == 1:
            kept.append(group[0])
            continue
        duplicate_groups += 1
        ranked = sorted(group, key=entry_score, reverse=True)
        keep = ranked[0]
        kept.append(keep)
        keep_rel = keep.get("relative_path") or keep.get("path") or ""
        for drop in ranked[1:]:
            drop_rel = drop.get("relative_path") or drop.get("path") or ""
            removed.append((ident, keep_rel, drop_rel))

    kept.sort(key=lambda x: x.get("relative_path", ""))

    print(f"total before: {len(items)}")
    print(f"total after : {len(kept)}")
    print(f"duplicate groups: {duplicate_groups}")
    print(f"removed duplicate rows: {len(removed)}")
    print("\nexamples:")
    for ident, keep, drop in removed[:20]:
        print(f"- {ident}")
        print(f"  keep: {keep}")
        print(f"  drop: {drop}")

    if not args.dry_run and len(kept) != len(items):
        if is_dict:
            raw["items"] = kept
            raw["count"] = len(kept)
            raw["dedupe"] = {
                "method": "prune_duplicate_index_rows",
                "duplicate_groups": duplicate_groups,
                "removed": len(removed),
            }
        else:
            raw = kept
        INDEX_FILE.write_text(json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n💾 Saved → {INDEX_FILE}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
