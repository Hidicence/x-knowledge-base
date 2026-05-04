#!/usr/bin/env python3
"""
Build per-user topic profile for x-knowledge-base.

Reads search_index.json, aggregates category/tag frequencies,
and writes topic_profile.json for trigger policy and future ranking.

Usage:
    python3 scripts/build_topic_profile.py
    python3 scripts/build_topic_profile.py --dry-run
    python3 scripts/build_topic_profile.py --top-categories 10 --top-tags 20
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

WORKSPACE_DIR = Path(
    os.getenv("OPENCLAW_WORKSPACE", os.getenv("WORKSPACE_DIR", str(Path.home() / ".openclaw" / "workspace")))
)
BOOKMARKS_DIR = Path(os.getenv("BOOKMARKS_DIR", str(WORKSPACE_DIR / "memory" / "bookmarks")))
XKB_DATA_DIR = WORKSPACE_DIR / "memory" / "x-knowledge-base"
INDEX_FILE = BOOKMARKS_DIR / "search_index.json"
TOPIC_PROFILE_FILE = Path(os.getenv("XKB_TOPIC_PROFILE_PATH", str(XKB_DATA_DIR / "topic_profile.json")))

GENERIC_TAGS = {
    "ai", "tool", "tools", "other", "general", "misc", "note", "notes",
    "thread", "video", "content", "idea", "ideas", "news", "update",
}

GENERIC_CATEGORIES = {
    "general", "99-general", "other", "misc", "uncategorized"
}

def _build_category_aliases(bookmarks_dir: Path) -> dict[str, str]:
    """Dynamically build aliases by stripping NN- prefix from bookmark subdirs.
    E.g. '01-openclaw-workflows' → 'openclaw-workflows'
    Falls back to empty dict if directory doesn't exist yet.
    """
    aliases: dict[str, str] = {}
    if not bookmarks_dir.is_dir():
        return aliases
    for d in bookmarks_dir.iterdir():
        if d.is_dir() and re.match(r"^\d{2,3}-", d.name):
            clean = re.sub(r"^\d{2,3}-", "", d.name)
            if clean:
                aliases[d.name] = clean
    return aliases


CATEGORY_ALIASES = _build_category_aliases(BOOKMARKS_DIR)


def load_items(index_path: Path) -> list[dict]:
    if not index_path.exists():
        raise FileNotFoundError(f"search_index.json not found: {index_path}")
    raw = json.loads(index_path.read_text(encoding="utf-8"))
    return raw.get("items", raw) if isinstance(raw, dict) else raw


def slugify_tag(tag: str) -> str:
    tag = (tag or "").strip().lower()
    tag = re.sub(r"\s+", "-", tag)
    tag = re.sub(r"[^a-z0-9_\-\u4e00-\u9fff]", "", tag)
    return tag.strip("-_")


def is_good_tag(tag: str) -> bool:
    if not tag:
        return False
    if len(tag) < 2:
        return False
    if tag in GENERIC_TAGS:
        return False
    if tag.isdigit():
        return False
    return True


def normalize_counter(counter: Counter, top_n: int) -> list[dict]:
    if not counter:
        return []
    most_common = counter.most_common(top_n)
    max_count = most_common[0][1]
    if max_count <= 0:
        return []
    return [
        {
            "name": name,
            "count": count,
            "weight": round(count / max_count, 4),
        }
        for name, count in most_common
    ]


def canonicalize_category(category: str) -> str:
    category = (category or "").strip().lower()
    if not category:
        return ""
    category = CATEGORY_ALIASES.get(category, category)
    return category


def iter_tags(items: Iterable[dict]) -> Iterable[str]:
    for item in items:
        for raw_tag in item.get("tags") or []:
            tag = slugify_tag(raw_tag)
            if is_good_tag(tag):
                yield tag


def build_profile(items: list[dict], top_categories: int, top_tags: int) -> dict:
    category_counter: Counter[str] = Counter()
    generic_category_counter: Counter[str] = Counter()
    source_counter: Counter[str] = Counter()

    for item in items:
        category = canonicalize_category(item.get("category") or "")
        if category:
            if category in GENERIC_CATEGORIES:
                generic_category_counter[category] += 1
            else:
                category_counter[category] += 1

        source = (item.get("source") or "").strip().lower()
        if source:
            source_counter[source] += 1

    tag_counter = Counter(iter_tags(items))

    return {
        "version": 1,
        "built_at": datetime.now(timezone.utc).isoformat(),
        "card_count": len(items),
        "top_categories": normalize_counter(category_counter, top_categories),
        "generic_categories": normalize_counter(generic_category_counter, 10),
        "top_tags": normalize_counter(tag_counter, top_tags),
        "top_sources": normalize_counter(source_counter, 10),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Build topic profile for x-knowledge-base")
    parser.add_argument("--index-file", default=str(INDEX_FILE))
    parser.add_argument("--output-file", default=str(TOPIC_PROFILE_FILE))
    parser.add_argument("--top-categories", type=int, default=10)
    parser.add_argument("--top-tags", type=int, default=20)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    index_path = Path(args.index_file)
    output_path = Path(args.output_file)

    try:
        items = load_items(index_path)
    except FileNotFoundError as e:
        print(f"❌ {e}", file=sys.stderr)
        return 1

    profile = build_profile(items, args.top_categories, args.top_tags)

    print(f"📚 Loaded {profile['card_count']} cards from {index_path}")
    print(f"🏷️  Top categories: {len(profile['top_categories'])}")
    print(f"🔖 Top tags: {len(profile['top_tags'])}")

    if args.dry_run:
        print("\n[dry-run] topic profile preview:\n")
        print(json.dumps(profile, ensure_ascii=False, indent=2))
        return 0

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(profile, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"✅ Saved topic profile → {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
