#!/usr/bin/env python3
"""
XKB + Wiki pipeline status summary.

Shows the current health and activity of the entire knowledge pipeline:
  XKB bookmarks → cards → wiki sync → staging → topics

Usage:
  python3 status_knowledge_pipeline.py
  python3 status_knowledge_pipeline.py --json
  python3 status_knowledge_pipeline.py --days 7
"""

from __future__ import annotations

import argparse
import json
import os
import re
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

WORKSPACE = Path(os.getenv("OPENCLAW_WORKSPACE", "/root/.openclaw/workspace"))
WIKI_DIR = WORKSPACE / "wiki"
TOPICS_DIR = WIKI_DIR / "topics"
STAGING_DIR = WIKI_DIR / "_staging"
MEMORY_DIR = WORKSPACE / "memory"
BOOKMARKS_DIR = MEMORY_DIR / "bookmarks"
CARDS_DIR = MEMORY_DIR / "cards"
SEARCH_INDEX_PATH = BOOKMARKS_DIR / "search_index.json"
REVIEW_DECISIONS_PATH = WIKI_DIR / "review-decisions.json"
LOG_PATH = WIKI_DIR / "log.md"
INDEX_PATH = WIKI_DIR / "index.md"
TOPIC_MAP_PATH = WIKI_DIR / "topic-map.json"


def load_search_index() -> list[dict]:
    if not SEARCH_INDEX_PATH.exists():
        return []
    data = json.loads(SEARCH_INDEX_PATH.read_text())
    return data.get("items", data) if isinstance(data, dict) else data


def load_review_decisions() -> dict:
    if not REVIEW_DECISIONS_PATH.exists():
        return {}
    try:
        return json.loads(REVIEW_DECISIONS_PATH.read_text()).get("decisions", {})
    except Exception:
        return {}


def load_topic_map() -> dict:
    if not TOPIC_MAP_PATH.exists():
        return {}
    return json.loads(TOPIC_MAP_PATH.read_text())


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


def get_recent_log_entries(days: int = 7) -> list[str]:
    if not LOG_PATH.exists():
        return []
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    entries = []
    for line in LOG_PATH.read_text().splitlines():
        m = re.match(r"## \[(\d{4}-\d{2}-\d{2})", line)
        if m:
            try:
                d = datetime.strptime(m.group(1), "%Y-%m-%d").replace(tzinfo=timezone.utc)
                if d >= cutoff:
                    entries.append(line)
            except ValueError:
                pass
    return entries


def status_bookmarks(items: list[dict], days: int) -> dict:
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d")
    active = [i for i in items if not i.get("excluded")]
    recent = [i for i in active if (i.get("date") or "") >= cutoff]
    cats = Counter(i.get("category", "?") for i in active)
    return {
        "total": len(items),
        "active": len(active),
        "excluded": len(items) - len(active),
        "recent_added": len(recent),
        "by_category": dict(cats.most_common()),
    }


def status_cards() -> dict:
    if not CARDS_DIR.exists():
        return {"total": 0}
    cards = list(CARDS_DIR.glob("*.md"))
    return {"total": len(cards)}


def status_wiki_topics() -> list[dict]:
    if not TOPICS_DIR.exists():
        return []
    today = datetime.now(timezone.utc).date()
    topics = []
    for f in sorted(TOPICS_DIR.glob("*.md")):
        fm = parse_frontmatter(f)
        lu = fm.get("last_updated", "")
        try:
            days_ago = (today - datetime.strptime(lu, "%Y-%m-%d").date()).days
        except Exception:
            days_ago = None
        # Count sources mentions in file
        content = f.read_text()
        source_count = len(re.findall(r"^\s*- \[", content, re.MULTILINE))
        topics.append({
            "slug": f.stem,
            "status": fm.get("status", "?"),
            "sources": fm.get("sources", "?"),
            "last_updated": lu,
            "days_since_update": days_ago,
            "source_lines": source_count,
        })
    return topics


def status_absorb(decisions: dict) -> dict:
    counts = Counter(v.get("decision", "?") for v in decisions.values())
    return {
        "total_evaluated": len(decisions),
        "approved": counts.get("approve", 0),
        "skipped": counts.get("skip", 0),
        "by_reason": Counter(
            v.get("reason", "no-reason")
            for v in decisions.values()
            if v.get("decision") == "skip"
        ),
    }


def status_staging() -> dict:
    if not STAGING_DIR.exists():
        return {"files": 0, "total_candidates": 0, "pending_review": 0}
    files = list(STAGING_DIR.glob("*-candidates.md"))
    total_candidates = 0
    pending = 0
    for f in files:
        content = f.read_text()
        candidates = len(re.findall(r"^## Candidate \d+", content, re.MULTILINE))
        approved = len(re.findall(r"\[x\] approve", content, re.IGNORECASE))
        total_candidates += candidates
        pending += candidates - approved
    return {
        "files": len(files),
        "total_candidates": total_candidates,
        "pending_review": pending,
        "latest": files[-1].name if files else None,
    }


def status_gaps(items: list[dict], topic_files: list[Path]) -> list[dict]:
    topic_map = load_topic_map()
    existing_slugs = {f.stem for f in topic_files}
    cat_counts = Counter(i.get("category", "?") for i in items if not i.get("excluded"))
    gaps = []
    for cat, count in cat_counts.most_common():
        if count < 5:
            continue
        mapping = topic_map.get("mapping", {}).get(cat, {})
        mapped = mapping.get("topics") or []
        if mapped and any(t in existing_slugs for t in mapped):
            continue
        gaps.append({"category": cat, "cards": count,
                     "mapped_to": mapped or None,
                     "map_status": mapping.get("status", "unmapped")})
    return gaps


def fmt_section(title: str) -> str:
    return f"\n{'─' * 50}\n  {title}\n{'─' * 50}"


def main() -> None:
    parser = argparse.ArgumentParser(description="XKB + Wiki pipeline status")
    parser.add_argument("--days", type=int, default=7, help="Lookback window in days")
    parser.add_argument("--json", action="store_true", help="Output raw JSON")
    args = parser.parse_args()

    items = load_search_index()
    decisions = load_review_decisions()
    topic_files = list(TOPICS_DIR.glob("*.md")) if TOPICS_DIR.exists() else []
    log_entries = get_recent_log_entries(args.days)

    report = {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "lookback_days": args.days,
        "bookmarks": status_bookmarks(items, args.days),
        "cards": status_cards(),
        "wiki_topics": status_wiki_topics(),
        "absorb": status_absorb(decisions),
        "staging": status_staging(),
        "gap_topics": status_gaps(items, topic_files),
        "recent_log": log_entries,
    }

    if args.json:
        print(json.dumps(report, indent=2, ensure_ascii=False))
        return

    now = report["generated_at"]
    days = args.days
    bk = report["bookmarks"]
    cards = report["cards"]
    topics = report["wiki_topics"]
    absorb = report["absorb"]
    staging = report["staging"]
    gaps = report["gap_topics"]

    print(f"\n🔭  XKB + Wiki Pipeline Status  ({now})")
    print(f"    Lookback: last {days} days")

    # Bookmarks
    print(fmt_section("📚 Bookmarks (XKB)"))
    print(f"  Total: {bk['total']}  |  Active: {bk['active']}  |  Excluded: {bk['excluded']}")
    print(f"  Added in last {days}d: {bk['recent_added']}")
    print("  By category:")
    for cat, n in list(bk["by_category"].items())[:6]:
        print(f"    {n:4d}  {cat}")

    # Cards
    print(fmt_section("🃏 Knowledge Cards"))
    print(f"  Total cards in memory/cards/: {cards['total']}")

    # Wiki topics
    print(fmt_section("📖 Wiki Topics"))
    if topics:
        for t in topics:
            age = f"{t['days_since_update']}d ago" if t['days_since_update'] is not None else "unknown"
            print(f"  [{t['status']:8s}] {t['slug']}")
            print(f"             sources: {t['sources']} | updated: {t['last_updated']} ({age})")
    else:
        print("  No topic pages yet.")

    # Absorb gate
    print(fmt_section("🚦 Absorb Gate (sync_cards_to_wiki)"))
    print(f"  Total evaluated: {absorb['total_evaluated']}")
    print(f"  Approved: {absorb['approved']}  |  Skipped: {absorb['skipped']}")
    if absorb["by_reason"]:
        print("  Skip reasons:")
        for reason, count in absorb["by_reason"].most_common():
            print(f"    {count:3d}  {reason}")

    # Staging
    print(fmt_section("📋 Staging (_staging/)"))
    print(f"  Files: {staging['files']}  |  Total candidates: {staging['total_candidates']}")
    print(f"  Pending review: {staging['pending_review']}")
    if staging["latest"]:
        print(f"  Latest: {staging['latest']}")

    # Gap topics
    print(fmt_section("⚠️  Gap Topics (5+ cards, no wiki page)"))
    if gaps:
        for g in gaps:
            mapped = f"→ {g['mapped_to']}" if g["mapped_to"] else "(unmapped)"
            print(f"  {g['cards']:3d} cards  {g['category']}  {mapped}  [{g['map_status']}]")
    else:
        print("  No gaps detected.")

    # Recent log
    print(fmt_section(f"📝 Wiki Log (last {days}d)"))
    if log_entries:
        for entry in log_entries[-8:]:
            print(f"  {entry}")
    else:
        print("  No log entries in this period.")

    print()


if __name__ == "__main__":
    main()
