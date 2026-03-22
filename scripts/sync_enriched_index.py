#!/usr/bin/env python3
"""
Sync enriched cards from memory/cards/ back into search_index.json.

For each enriched card found in memory/cards/:
- Update path to point to memory/cards/[id].md
- Extract and update summary from "## 1. 核心摘要" section
- Extract and update tags from frontmatter
- Extract and update title from "# Title" line
- Set enriched: true

Usage:
    python3 scripts/sync_enriched_index.py
    python3 scripts/sync_enriched_index.py --dry-run
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
import os

WORKSPACE = Path(os.getenv("OPENCLAW_WORKSPACE", os.getenv("WORKSPACE_DIR", str(Path.home() / ".openclaw" / "workspace"))))
BOOKMARKS_DIR = WORKSPACE / "memory" / "bookmarks"
CARDS_DIR = WORKSPACE / "memory" / "cards"
INDEX_FILE = BOOKMARKS_DIR / "search_index.json"


def _extract_summary(text: str) -> str:
    m = re.search(r"##\s+1\.\s*核心摘要\s*\n(.+?)(?=\n##|\Z)", text, re.DOTALL)
    if m:
        lines = [l.strip().lstrip("-").strip() for l in m.group(1).strip().splitlines() if l.strip()]
        return " ".join(lines)[:400]
    return ""


def _extract_tags(text: str) -> list[str]:
    m = re.search(r"^tags:\s*\[(.+?)\]", text, re.MULTILINE)
    if m:
        return [t.strip().strip('"').strip("'") for t in m.group(1).split(",") if t.strip()]
    return []


def _extract_title(text: str) -> str:
    m = re.search(r"^# (.+)$", text, re.MULTILINE)
    return m.group(1).strip() if m else ""


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if not CARDS_DIR.exists():
        print(f"❌ Cards dir not found: {CARDS_DIR}")
        sys.exit(1)
    if not INDEX_FILE.exists():
        print(f"❌ Index not found: {INDEX_FILE}")
        sys.exit(1)

    raw = json.loads(INDEX_FILE.read_text(encoding="utf-8"))
    items = raw.get("items", raw) if isinstance(raw, dict) else raw
    is_dict = isinstance(raw, dict)

    # Build tweet_id → item index, extracting ID from source_url
    id_to_idx: dict[str, int] = {}
    for idx, item in enumerate(items):
        item_id = ""
        m = re.search(r"/status/(\d+)", item.get("source_url", ""))
        if m:
            item_id = m.group(1)
        if not item_id:
            for key in ("path", "relative_path"):
                stem = Path(item.get(key, "")).stem
                if stem.isdigit():
                    item_id = stem
                    break
        if item_id:
            id_to_idx[item_id] = idx

    card_files = sorted(CARDS_DIR.glob("*.md"))
    print(f"📁 {len(card_files)} enriched cards  |  {len(id_to_idx)} indexed items")

    updated = 0
    not_found_ids = []

    for card_path in card_files:
        card_id = card_path.stem
        if card_id not in id_to_idx:
            not_found_ids.append(card_id)
            continue

        idx = id_to_idx[card_id]
        item = items[idx]
        text = card_path.read_text(encoding="utf-8", errors="ignore")

        summary = _extract_summary(text)
        tags = _extract_tags(text)
        title = _extract_title(text)
        new_path = str(card_path)

        changes: dict = {}
        if item.get("path") != new_path:
            changes["path"] = new_path
        if summary and item.get("summary") != summary:
            changes["summary"] = summary
        if tags and item.get("tags") != tags:
            changes["tags"] = tags
        if title and item.get("title") != title:
            changes["title"] = title
        if not item.get("enriched"):
            changes["enriched"] = True

        if changes:
            if not args.dry_run:
                item.update(changes)
            updated += 1
            if args.dry_run:
                print(f"  [dry-run] {card_id}: {list(changes.keys())}")

    print(f"✅ Updated: {updated}  |  Not in index: {len(not_found_ids)}")
    if not_found_ids:
        print(f"   (first 5 not found: {not_found_ids[:5]})")

    if not args.dry_run and updated > 0:
        if is_dict:
            raw["items"] = items
        else:
            raw = items
        INDEX_FILE.write_text(json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"💾 Saved → {INDEX_FILE}")
        print()
        print("Next: rebuild vector index to pick up enriched summaries")
        print("  GEMINI_API_KEY=... python3 scripts/build_vector_index.py")


if __name__ == "__main__":
    main()
