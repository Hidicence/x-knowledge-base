#!/usr/bin/env python3
"""
Normalize / exclude low-quality search index entries without touching source markdown.

Phase 1 rules:
- exclude entries with invalid source URLs
- exclude entries with date-slug titles + low-signal summaries
- exclude entries with tweet/numeric titles + low-signal summaries
- preserve all data by marking entries instead of deleting source files

Usage:
    python3 scripts/normalize_index_quality.py
    python3 scripts/normalize_index_quality.py --dry-run
"""

from __future__ import annotations

import argparse
import json
import os
import re
from pathlib import Path
from typing import Any

WORKSPACE = Path(os.getenv("OPENCLAW_WORKSPACE", os.getenv("WORKSPACE_DIR", str(Path.home() / ".openclaw" / "workspace"))))
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


def is_valid_source_url(url: str) -> bool:
    url = (url or "").strip()
    if not url.startswith(("http://", "https://")):
        return False
    x_status = re.search(r"https?://(?:x|twitter)\.com/[^\s/]+/status/(\d{15,20})(?:\b|/|\?)", url)
    x_i_status = re.search(r"https?://x\.com/i/status/(\d{15,20})(?:\b|/|\?)", url)
    if ("x.com" in url or "twitter.com" in url) and not (x_status or x_i_status):
        return False
    return True


def exclusion_reasons(item: dict[str, Any]) -> list[str]:
    title = (item.get("title") or "").strip()
    summary = clean_summary(item.get("summary") or "")
    source_url = (item.get("source_url") or "").strip()

    reasons: list[str] = []

    if source_url and not is_valid_source_url(source_url):
        reasons.append("invalid_source_url")

    if re.match(r"^\d{4}-\d{2}-\d{2}-", title) and summary in LOW_SIGNAL_SUMMARIES:
        reasons.append("date_slug_low_signal")

    if re.fullmatch(r"\d{15,20}", title) and summary in LOW_SIGNAL_SUMMARIES:
        reasons.append("numeric_title_low_signal")

    if re.fullmatch(r"tweet\s+\d{15,20}", title.lower()) and summary in LOW_SIGNAL_SUMMARIES:
        reasons.append("tweet_numeric_low_signal")

    return reasons


def main() -> int:
    parser = argparse.ArgumentParser(description="Normalize index quality")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    raw = json.loads(INDEX_FILE.read_text(encoding="utf-8"))
    items = raw.get("items", raw) if isinstance(raw, dict) else raw
    is_dict = isinstance(raw, dict)

    changed = 0
    excluded = 0
    examples: list[tuple[str, str, list[str]]] = []

    for item in items:
        reasons = exclusion_reasons(item)
        rel_path = item.get("relative_path") or item.get("path") or ""
        current_excluded = bool(item.get("excluded"))
        current_reasons = item.get("exclude_reasons") or []

        if reasons:
            merged = sorted(set(current_reasons) | set(reasons))
            if (not current_excluded) or merged != current_reasons:
                item["excluded"] = True
                item["exclude_reasons"] = merged
                changed += 1
            excluded += 1
            if len(examples) < 20:
                examples.append((rel_path, item.get("title", ""), merged))

    print(f"總項目數：{len(items)}")
    print(f"標記 excluded：{excluded}")
    print(f"實際變更：{changed}")
    print("\n範例：")
    for rel_path, title, reasons in examples:
        print(f"- {rel_path}")
        print(f"  title: {title}")
        print(f"  reasons: {', '.join(reasons)}")

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
