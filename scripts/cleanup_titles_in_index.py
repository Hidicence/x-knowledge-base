#!/usr/bin/env python3
"""
Lightweight title cleanup directly in search_index.json.

Rules:
- if title is numeric or Tweet <id>, try replacing it with the first sentence of summary
- keep changes conservative; do not fabricate
- do not touch entries already having a human-readable title

Usage:
    python3 scripts/cleanup_titles_in_index.py
    python3 scripts/cleanup_titles_in_index.py --dry-run
"""

from __future__ import annotations

import argparse
import json
import os
import re
from pathlib import Path

WORKSPACE = Path(os.getenv("OPENCLAW_WORKSPACE", os.getenv("WORKSPACE_DIR", str(Path.home() / ".openclaw" / "workspace"))))
BOOKMARKS_DIR = Path(os.getenv("BOOKMARKS_DIR", str(WORKSPACE / "memory" / "bookmarks")))
INDEX_FILE = Path(os.getenv("INDEX_FILE", str(BOOKMARKS_DIR / "search_index.json")))
LOW_SIGNAL_SUMMARIES = {"", "（待整理）", "待整理", "todo", "tbd", "n/a", "一句话摘要", "一句話摘要"}


def clean_summary(text: str) -> str:
    text = (text or "").strip()
    text = re.sub(r"^#+\s*", "", text)
    text = re.sub(r"^一句話摘要\s*", "", text)
    text = re.sub(r"^一句话摘要\s*", "", text)
    text = re.sub(r"^[-•]\s*", "", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def suggest_title(summary: str) -> str:
    summary = clean_summary(summary)
    if summary in LOW_SIGNAL_SUMMARIES or len(summary) < 10:
        return ""
    first = summary.split("。", 1)[0].split(".", 1)[0].strip()
    first = first[:48].strip(" -—–_。.")
    if len(first) < 8:
        return ""
    return first


def needs_cleanup(title: str) -> bool:
    title = (title or "").strip()
    if re.fullmatch(r"\d{15,20}", title):
        return True
    if re.fullmatch(r"tweet\s+\d{15,20}", title.lower()):
        return True
    return False


def main() -> int:
    parser = argparse.ArgumentParser(description="Cleanup low-quality titles in index")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    raw = json.loads(INDEX_FILE.read_text(encoding="utf-8"))
    items = raw.get("items", raw) if isinstance(raw, dict) else raw
    is_dict = isinstance(raw, dict)

    changed = 0
    examples = []
    for item in items:
        title = (item.get("title") or "").strip()
        if not needs_cleanup(title):
            continue
        new_title = suggest_title(item.get("summary") or "")
        if not new_title or new_title == title:
            continue
        item["title"] = new_title
        changed += 1
        if len(examples) < 20:
            examples.append((title, new_title, item.get("relative_path") or item.get("path") or ""))

    print(f"title cleaned: {changed}")
    for old, new, path in examples:
        print(f"- {path}")
        print(f"  old: {old}")
        print(f"  new: {new}")

    if not args.dry_run and changed > 0:
        if is_dict:
            raw["items"] = items
        else:
            raw = items
        INDEX_FILE.write_text(json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n💾 Saved → {INDEX_FILE}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
