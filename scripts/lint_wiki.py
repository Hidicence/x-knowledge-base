#!/usr/bin/env python3
"""
Wiki health check.

Checks for:
  - Orphan pages (in topics/ but not listed in index.md)
  - Stale pages (last_updated > 60 days ago)
  - Gap topics (5+ cards in search_index with no wiki page)
  - Unregistered pages (in topics/ but missing from index.md)

Usage:
  python3 lint_wiki.py
  python3 lint_wiki.py --fix   # auto-register orphans in index.md
"""

from __future__ import annotations

import argparse
import json
import os
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

WORKSPACE = Path(os.getenv("OPENCLAW_WORKSPACE", "/root/.openclaw/workspace"))
WIKI_DIR = WORKSPACE / "wiki"
TOPICS_DIR = WIKI_DIR / "topics"
INDEX_PATH = WIKI_DIR / "index.md"
LOG_PATH = WIKI_DIR / "log.md"
TOPIC_MAP_PATH = WIKI_DIR / "topic-map.json"
SEARCH_INDEX_PATH = WORKSPACE / "memory" / "bookmarks" / "search_index.json"

STALE_DAYS = 60
GAP_THRESHOLD = 5


def slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def load_index_slugs() -> set[str]:
    """Slugs listed in wiki/index.md."""
    if not INDEX_PATH.exists():
        return set()
    content = INDEX_PATH.read_text()
    # Match markdown links like [title](topics/slug.md)
    return {m.group(1) for m in re.finditer(r"\(topics/([^)]+)\.md\)", content)}


def load_topic_files() -> list[Path]:
    if not TOPICS_DIR.exists():
        return []
    return list(TOPICS_DIR.glob("*.md"))


def parse_frontmatter(path: Path) -> dict:
    content = path.read_text()
    fm = {}
    m = re.match(r"^---\n(.*?)\n---", content, re.DOTALL)
    if m:
        for line in m.group(1).splitlines():
            kv = line.split(":", 1)
            if len(kv) == 2:
                fm[kv[0].strip()] = kv[1].strip()
    return fm


def load_search_index() -> list[dict]:
    if not SEARCH_INDEX_PATH.exists():
        return []
    try:
        data = json.loads(SEARCH_INDEX_PATH.read_text())
        if isinstance(data, list):
            return data
        return data.get("items", [])
    except Exception:
        return []


def load_topic_map() -> dict:
    if not TOPIC_MAP_PATH.exists():
        return {}
    return json.loads(TOPIC_MAP_PATH.read_text())


def append_log(entry: str) -> None:
    existing = LOG_PATH.read_text() if LOG_PATH.exists() else "# Wiki Log\n---\n"
    LOG_PATH.write_text(existing.rstrip() + "\n" + entry + "\n")


def check_orphans(topic_files: list[Path], index_slugs: set[str]) -> list[str]:
    """Pages in topics/ not listed in index.md."""
    orphans = []
    for f in topic_files:
        if f.stem not in index_slugs:
            orphans.append(f.stem)
    return orphans


def check_stale(topic_files: list[Path]) -> list[tuple[str, str]]:
    """Pages where last_updated > STALE_DAYS days ago."""
    stale = []
    today = datetime.now(timezone.utc).date()
    for f in topic_files:
        fm = parse_frontmatter(f)
        lu = fm.get("last_updated", "")
        if not lu:
            stale.append((f.stem, "no last_updated"))
            continue
        try:
            updated = datetime.strptime(lu, "%Y-%m-%d").date()
            delta = (today - updated).days
            if delta > STALE_DAYS:
                stale.append((f.stem, f"{delta} days ago"))
        except ValueError:
            stale.append((f.stem, f"unparseable date: {lu}"))
    return stale


def check_gaps(topic_files: list[Path]) -> list[tuple[str, int]]:
    """Categories with 5+ cards but no wiki page."""
    index = load_search_index()
    topic_map = load_topic_map()
    existing_slugs = {f.stem for f in topic_files}

    # Count cards per category (excluding excluded)
    cat_counts = Counter(
        e.get("category", "unknown")
        for e in index
        if not e.get("excluded")
    )

    gaps = []
    for cat, count in cat_counts.items():
        if count < GAP_THRESHOLD:
            continue
        # Check if this category is mapped to any existing topic
        mapping = topic_map.get("mapping", {}).get(cat, {})
        mapped_topics = mapping.get("topics") or []
        if mapped_topics and any(t in existing_slugs for t in mapped_topics):
            continue
        # No wiki page for this category
        gaps.append((cat, count))

    return sorted(gaps, key=lambda x: -x[1])


def fix_orphans(orphans: list[str]) -> None:
    """Add orphan pages to index.md."""
    if not INDEX_PATH.exists():
        INDEX_PATH.write_text("# Wiki Index\n\n## Topics\n\n")

    content = INDEX_PATH.read_text()
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    additions = []
    for slug in orphans:
        if f"topics/{slug}.md" not in content:
            additions.append(f"- [{slug}](topics/{slug}.md) — (auto-registered by lint) | last_updated: {today}")

    if additions:
        # Find Topics section or append
        if "## Topics" in content:
            content = content.replace(
                "## Topics",
                "## Topics\n" + "\n".join(additions),
                1,
            )
        else:
            content += "\n## Topics\n" + "\n".join(additions) + "\n"
        INDEX_PATH.write_text(content)
        print(f"  [FIX] Registered {len(additions)} orphan(s) in index.md")


def main() -> None:
    parser = argparse.ArgumentParser(description="Wiki health check")
    parser.add_argument("--fix", action="store_true", help="Auto-fix orphans in index.md")
    args = parser.parse_args()

    topic_files = load_topic_files()
    index_slugs = load_index_slugs()
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")

    print(f"Wiki Lint — {now}")
    print(f"Topics on disk: {len(topic_files)}")
    print(f"Topics in index: {len(index_slugs)}")
    print()

    issues = 0

    # 1. Orphans
    orphans = check_orphans(topic_files, index_slugs)
    if orphans:
        print(f"[WARN] Orphan pages ({len(orphans)}) — in topics/ but not in index.md:")
        for slug in orphans:
            print(f"  - {slug}")
        issues += len(orphans)
        if args.fix:
            fix_orphans(orphans)
    else:
        print("[OK] No orphan pages")

    # 2. Stale pages
    stale = check_stale(topic_files)
    if stale:
        print(f"\n[WARN] Stale pages ({len(stale)}) — last_updated > {STALE_DAYS} days:")
        for slug, info in stale:
            print(f"  - {slug}: {info}")
        issues += len(stale)
    else:
        print("[OK] No stale pages")

    # 3. Gap topics
    gaps = check_gaps(topic_files)
    if gaps:
        print(f"\n[INFO] Gap topics ({len(gaps)}) — {GAP_THRESHOLD}+ cards but no wiki page:")
        for cat, count in gaps[:10]:
            print(f"  - {cat}: {count} cards")
        issues += len(gaps)
    else:
        print("[OK] No gap topics")

    print()
    if issues == 0:
        print("Wiki is healthy.")
    else:
        print(f"Total issues: {issues}")

    # Log the lint run
    append_log(
        f"\n## [{now}] lint | {len(topic_files)} pages checked — "
        f"{len(orphans)} orphans, {len(stale)} stale, {len(gaps)} gaps"
    )


if __name__ == "__main__":
    main()
