#!/usr/bin/env python3
"""
Audit x-knowledge-base search index quality without mutating data.

Checks for:
- low-signal titles
- placeholder/weak summaries
- invalid or suspicious source URLs
- legacy card paths
- missing source URLs
- duplicate source URLs

Usage:
    python3 scripts/audit_index_quality.py
    python3 scripts/audit_index_quality.py --json
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
BOOKMARKS_DIR = WORKSPACE / "memory" / "bookmarks"
INDEX_FILE = BOOKMARKS_DIR / "search_index.json"

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


def load_items() -> list[dict[str, Any]]:
    raw = json.loads(INDEX_FILE.read_text(encoding="utf-8"))
    return raw.get("items", raw) if isinstance(raw, dict) else raw


def classify_item(item: dict[str, Any]) -> dict[str, Any]:
    title = (item.get("title") or "").strip()
    summary = clean_summary(item.get("summary") or "")
    source_url = (item.get("source_url") or "").strip()
    rel_path = (item.get("relative_path") or item.get("path") or "").strip()
    category = (item.get("category") or "").strip()

    issues: list[str] = []

    if not title:
        issues.append("missing_title")
    elif re.fullmatch(r"\d{15,20}", title):
        issues.append("numeric_title")
    elif re.fullmatch(r"tweet\s+\d{15,20}", title.lower()):
        issues.append("tweet_numeric_title")
    elif re.match(r"^\d{4}-\d{2}-\d{2}-", title) and len(title) < 42:
        issues.append("date_slug_title")
    elif len(title) < 8:
        issues.append("very_short_title")

    if summary in LOW_SIGNAL_SUMMARIES:
        issues.append("low_signal_summary")
    elif len(summary) < 18:
        issues.append("very_short_summary")

    if not source_url:
        issues.append("missing_source_url")
    elif not is_valid_source_url(source_url):
        issues.append("invalid_source_url")

    if "/memory/cards/legacy-" in rel_path or rel_path.startswith("memory/cards/legacy-"):
        issues.append("legacy_card")
    elif "/legacy-" in rel_path or Path(rel_path).stem.startswith("legacy-"):
        issues.append("legacy_card")

    if rel_path.startswith("/"):
        issues.append("absolute_relative_path")

    if item.get("excluded"):
        issues.append("excluded")
    if item.get("duplicate_of"):
        issues.append("duplicate_entry")

    return {
        "title": title,
        "summary": summary,
        "source_url": source_url,
        "relative_path": rel_path,
        "category": category,
        "issues": issues,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit x-knowledge-base index quality")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    items = load_items()
    issue_buckets: dict[str, list[dict[str, Any]]] = defaultdict(list)
    source_buckets: dict[str, list[dict[str, Any]]] = defaultdict(list)

    for item in items:
        audited = classify_item(item)
        for issue in audited["issues"]:
            issue_buckets[issue].append(audited)
        source_url = audited["source_url"]
        if source_url:
            source_buckets[source_url].append(audited)

    duplicates = {url: rows for url, rows in source_buckets.items() if len(rows) > 1}

    report = {
        "total_items": len(items),
        "issue_counts": {k: len(v) for k, v in sorted(issue_buckets.items())},
        "duplicate_source_url_count": len(duplicates),
        "duplicate_source_url_examples": {
            k: [r["relative_path"] for r in v[:5]] for k, v in list(duplicates.items())[:10]
        },
        "examples": {
            k: v[:8] for k, v in sorted(issue_buckets.items())
        },
    }

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0

    print(f"總項目數：{report['total_items']}")
    print("\n問題統計：")
    for key, count in report["issue_counts"].items():
        print(f"- {key}: {count}")

    print(f"\n重複 source_url：{report['duplicate_source_url_count']}")
    for url, paths in report["duplicate_source_url_examples"].items():
        print(f"- {url}")
        for path in paths:
            print(f"  - {path}")

    print("\n各類問題範例：")
    for key, rows in report["examples"].items():
        print(f"\n## {key} ({len(issue_buckets[key])})")
        for row in rows[:5]:
            print(f"- {row['relative_path']}")
            print(f"  title: {row['title']}")
            print(f"  summary: {row['summary'][:120]}")
            print(f"  source: {row['source_url']}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
