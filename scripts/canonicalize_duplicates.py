#!/usr/bin/env python3
"""
Canonicalize duplicate source_url entries in search_index.json.

Strategy:
- group by source_url
- skip invalid / empty source_url
- score candidates by quality
- keep best entry as canonical
- mark the rest as excluded duplicates

Usage:
    python3 scripts/canonicalize_duplicates.py
    python3 scripts/canonicalize_duplicates.py --dry-run
"""

from __future__ import annotations

import argparse
import json
import os
import re
from collections import defaultdict
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


def entry_score(item: dict[str, Any]) -> tuple:
    title = (item.get("title") or "").strip()
    summary = clean_summary(item.get("summary") or "")
    rel_path = (item.get("relative_path") or item.get("path") or "").strip()

    enriched = 1 if item.get("enriched") else 0
    has_good_summary = 1 if summary not in LOW_SIGNAL_SUMMARIES and len(summary) >= 18 else 0
    has_human_title = 1
    if not title or re.fullmatch(r"\d{15,20}", title) or re.fullmatch(r"tweet\s+\d{15,20}", title.lower()):
        has_human_title = 0
    in_cards = 1 if rel_path.startswith("memory/cards/") else 0
    not_excluded = 1 if not item.get("excluded") else 0
    non_legacy = 1 if "legacy-" not in rel_path else 0
    title_len = min(len(title), 120)
    summary_len = min(len(summary), 300)

    return (
        not_excluded,
        enriched,
        in_cards,
        has_good_summary,
        has_human_title,
        non_legacy,
        summary_len,
        title_len,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Canonicalize duplicate source_url entries")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    raw = json.loads(INDEX_FILE.read_text(encoding="utf-8"))
    items = raw.get("items", raw) if isinstance(raw, dict) else raw
    is_dict = isinstance(raw, dict)

    groups: dict[str, list[int]] = defaultdict(list)
    for idx, item in enumerate(items):
        source_url = (item.get("source_url") or "").strip()
        if source_url and is_valid_source_url(source_url):
            groups[source_url].append(idx)

    duplicate_groups = {url: idxs for url, idxs in groups.items() if len(idxs) > 1}
    changed = 0
    excluded_dupes = 0
    examples = []

    for source_url, idxs in duplicate_groups.items():
        ranked = sorted(idxs, key=lambda i: entry_score(items[i]), reverse=True)
        keep_idx = ranked[0]
        keep_item = items[keep_idx]
        keep_rel = keep_item.get("relative_path") or keep_item.get("path") or ""

        for idx in ranked[1:]:
            item = items[idx]
            rel_path = item.get("relative_path") or item.get("path") or ""
            changed_here = False

            if not item.get("excluded"):
                item["excluded"] = True
                changed_here = True
            reasons = set(item.get("exclude_reasons") or [])
            if "duplicate_source_url" not in reasons:
                reasons.add("duplicate_source_url")
                item["exclude_reasons"] = sorted(reasons)
                changed_here = True
            if item.get("duplicate_of") != keep_rel:
                item["duplicate_of"] = keep_rel
                changed_here = True

            if changed_here:
                changed += 1
            excluded_dupes += 1
            if len(examples) < 20:
                examples.append((source_url, keep_rel, rel_path))

    print(f"duplicate groups: {len(duplicate_groups)}")
    print(f"duplicate entries excluded: {excluded_dupes}")
    print(f"changed: {changed}")
    print("\nexamples:")
    for source_url, keep_rel, rel_path in examples:
        print(f"- source: {source_url}")
        print(f"  keep: {keep_rel}")
        print(f"  drop: {rel_path}")

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
