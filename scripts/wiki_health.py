#!/usr/bin/env python3
"""
Wiki Health Report — knowledge source contribution and coverage analysis.

Shows:
- Entry count per topic, broken down by source (bookmarks vs conversation memory)
- Last updated date per topic
- Topics with no conversation-sourced entries (external-only coverage)
- Most recalled topics vs wiki coverage (cross-reference with recall telemetry)

Usage:
  python3 wiki_health.py
  python3 wiki_health.py --json
  python3 wiki_health.py --topic <slug>
"""
from __future__ import annotations

import argparse
import json
import os
import re
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

WORKSPACE = Path(os.getenv("OPENCLAW_WORKSPACE", str(Path.home() / ".openclaw" / "workspace")))
_SKILL_DIR = Path(__file__).resolve().parent.parent
WIKI_DIR = Path(os.getenv("XKB_WIKI_DIR", str(_SKILL_DIR / "wiki")))
TOPICS_DIR = WIKI_DIR / "topics"
TELEMETRY_PATH = _SKILL_DIR / "recall-telemetry.jsonl"


def parse_topic(path: Path) -> dict:
    """Extract health stats from a single wiki topic file."""
    content = path.read_text(encoding="utf-8")

    # last_updated from frontmatter
    lu_m = re.search(r"last_updated:\s*(\S+)", content)
    last_updated = lu_m.group(1) if lu_m else "unknown"

    # Count bullet entries — lines starting with "- " that aren't headers
    entries = [l for l in content.splitlines() if re.match(r"^- .+", l) and "last_updated" not in l]

    # Source classification: entries citing memory/YYYY-MM-DD.md = conversation
    # entries citing search_index / cards / bookmarks = external
    conv_entries = [e for e in entries if re.search(r"\*\(memory/\d{4}-\d{2}-\d{2}", e)]
    ext_entries = [e for e in entries if e not in conv_entries]

    # Section coverage
    sections = re.findall(r"^## (.+)", content, re.MULTILINE)

    return {
        "slug": path.stem,
        "last_updated": last_updated,
        "total_entries": len(entries),
        "conversation_entries": len(conv_entries),
        "external_entries": len(ext_entries),
        "sections": sections,
    }


def load_recall_telemetry(top_n: int = 20) -> dict[str, int]:
    """Count how many times each slug was recalled from telemetry."""
    counts: dict[str, int] = defaultdict(int)
    if not TELEMETRY_PATH.exists():
        return counts
    with open(TELEMETRY_PATH, encoding="utf-8") as f:
        for line in f:
            try:
                entry = json.loads(line)
                slug = entry.get("slug") or entry.get("topic") or entry.get("page_slug")
                if slug:
                    counts[slug] += 1
            except Exception:
                pass
    return dict(sorted(counts.items(), key=lambda x: -x[1])[:top_n])


def run_report(slug_filter: str | None = None, as_json: bool = False) -> None:
    if not TOPICS_DIR.exists():
        print(f"[ERROR] topics dir not found: {TOPICS_DIR}")
        return

    topics = sorted(TOPICS_DIR.glob("*.md"))
    if slug_filter:
        topics = [t for t in topics if t.stem == slug_filter]
        if not topics:
            print(f"[ERROR] Topic '{slug_filter}' not found in {TOPICS_DIR}")
            return

    stats = [parse_topic(t) for t in topics]
    recall_counts = load_recall_telemetry()

    if as_json:
        for s in stats:
            s["recall_count"] = recall_counts.get(s["slug"], 0)
        print(json.dumps(stats, indent=2, ensure_ascii=False))
        return

    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    print(f"# Wiki Health Report — {now}")
    print(f"Topics: {len(stats)}\n")

    # Summary table
    print(f"{'Slug':<35} {'Updated':<12} {'Total':>6} {'Conv':>5} {'Ext':>5} {'Recall':>7}")
    print("-" * 75)
    for s in sorted(stats, key=lambda x: -x["total_entries"]):
        rc = recall_counts.get(s["slug"], 0)
        conv = s["conversation_entries"]
        ext = s["external_entries"]
        flag = " [!]" if conv == 0 and s["total_entries"] > 0 else ""
        print(f"{s['slug']:<35} {s['last_updated']:<12} {s['total_entries']:>6} {conv:>5} {ext:>5} {rc:>7}{flag}")

    print("\n[!]  = no conversation-sourced entries (external-only coverage)")

    # Topics with zero conversation entries
    no_conv = [s for s in stats if s["conversation_entries"] == 0 and s["total_entries"] > 0]
    if no_conv:
        print(f"\n── Topics with no conversation distillation ({len(no_conv)}) ──")
        for s in no_conv:
            print(f"  {s['slug']} ({s['external_entries']} external entries)")

    # Empty topics
    empty = [s for s in stats if s["total_entries"] == 0]
    if empty:
        print(f"\n── Empty topics ({len(empty)}) ──")
        for s in empty:
            print(f"  {s['slug']} (last_updated: {s['last_updated']})")

    # High-recall but thin wiki coverage
    if recall_counts:
        print(f"\n── High recall, thin coverage ──")
        for slug, rc in list(recall_counts.items())[:10]:
            topic = next((s for s in stats if s["slug"] == slug), None)
            entries = topic["total_entries"] if topic else 0
            if rc >= 3 and entries < 5:
                print(f"  {slug}: recalled {rc}x but only {entries} wiki entries")

    # Stale topics (not updated in 30+ days)
    today = datetime.now(timezone.utc).date()
    stale = []
    for s in stats:
        try:
            lu = datetime.strptime(s["last_updated"], "%Y-%m-%d").date()
            days_ago = (today - lu).days
            if days_ago >= 30:
                stale.append((s["slug"], days_ago))
        except ValueError:
            pass
    if stale:
        print(f"\n── Stale topics (≥30 days since update) ──")
        for slug, days in sorted(stale, key=lambda x: -x[1]):
            print(f"  {slug}: last updated {days} days ago")


def main() -> None:
    parser = argparse.ArgumentParser(description="Wiki health and coverage report")
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    parser.add_argument("--topic", metavar="SLUG", help="Show report for one topic only")
    args = parser.parse_args()
    run_report(slug_filter=args.topic, as_json=args.json)


if __name__ == "__main__":
    main()
