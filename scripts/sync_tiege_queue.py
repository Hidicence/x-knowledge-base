#!/usr/bin/env python3
"""Sync XKB bookmark markdown files into tiege-queue.json.

This is the bridge between raw bookmark ingestion and run_bookmark_worker.py.
It scans memory/bookmarks/**/*.md, reconciles existing cards, and preserves
existing queue state where possible.
"""
from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import xkb_failures
import xkb_paths

WORKSPACE = xkb_paths.WORKSPACE
BOOKMARKS_DIR = xkb_paths.BOOKMARKS_DIR
CARDS_DIR = xkb_paths.CARDS_DIR
QUEUE_PATH = Path(os.getenv("XKB_QUEUE_PATH", str(WORKSPACE / "memory" / "x-knowledge-base" / "tiege-queue.json")))

VALID_STATUSES = {"todo", "processing", "done", "failed", "skipped"}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def rel(path: Path) -> str:
    try:
        return str(path.relative_to(WORKSPACE))
    except ValueError:
        return str(path)


def parse_frontmatter_value(text: str, key: str) -> str:
    m = re.search(rf"^{re.escape(key)}:\s*\"?([^\"\n]+)\"?\s*$", text, re.MULTILINE)
    return m.group(1).strip() if m else ""


def parse_tweet_id(path: Path, text: str) -> str:
    for key in ("tweet_id", "id"):
        val = parse_frontmatter_value(text, key)
        if re.fullmatch(r"\d{10,}", val):
            return val
    return path.stem if re.fullmatch(r"\d{10,}", path.stem) else ""


def parse_title(path: Path, text: str) -> str:
    title = parse_frontmatter_value(text, "title")
    if title:
        return title
    m = re.search(r"^#\s+(.+)$", text, re.MULTILINE)
    if m:
        return m.group(1).strip()
    return f"Tweet {path.stem}" if path.stem.isdigit() else path.stem


def parse_source_url(text: str, tweet_id: str) -> str:
    for key in ("source_url", "original_url"):
        val = parse_frontmatter_value(text, key)
        if val:
            return val
    return f"https://x.com/i/status/{tweet_id}" if tweet_id else ""


def category_for(path: Path) -> str:
    try:
        parts = path.relative_to(BOOKMARKS_DIR).parts
        # inbox files are still valid raw bookmarks, but their category is not final yet.
        if len(parts) >= 2 and parts[0] != "inbox":
            return parts[0]
    except Exception:
        pass
    return "99-general"


def load_existing_queue() -> dict[str, dict]:
    if not QUEUE_PATH.exists():
        return {}
    try:
        data = json.loads(QUEUE_PATH.read_text(encoding="utf-8"))
        return {str(item.get("id")): item for item in data.get("items", []) if item.get("id")}
    except Exception as err:
        # 佇列讀不動會被當成空佇列，於是已經處理過的東西全部重排一次。
        xkb_failures.note("tiege queue", err, detail=str(QUEUE_PATH))
        return {}


def should_skip_path(path: Path) -> bool:
    name = path.name
    if name.startswith(".") or name in {"INDEX.md", "README.md"}:
        return True
    # Generated indexes/backups are not raw bookmarks.
    if "search_index" in name or "vector_index" in name or name.endswith(".bak"):
        return True
    if "notebooklm_exports" in path.parts:
        return True
    return False


def main() -> int:
    existing = load_existing_queue()
    existing_card_ids = xkb_paths.card_ids()
    ts = now_iso()

    rows: list[dict] = []
    seen: set[str] = set()

    for path in sorted(BOOKMARKS_DIR.rglob("*.md")):
        if should_skip_path(path):
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        tweet_id = parse_tweet_id(path, text)
        if not tweet_id or tweet_id in seen:
            continue
        seen.add(tweet_id)

        old = existing.get(tweet_id, {})
        status = old.get("status") or "todo"
        if status not in VALID_STATUSES:
            status = "todo"

        worker = old.get("worker", "")
        started_at = old.get("started_at", "")
        finished_at = old.get("finished_at", "")
        error = old.get("error", "")

        if tweet_id in existing_card_ids and status != "processing":
            status = "done"
            worker = worker or "reconcile"
            finished_at = finished_at or ts
            error = ""
        elif status in {"failed", "skipped"}:
            # Preserve explicit failed/skipped states; operator can reset if needed.
            pass
        elif status == "processing":
            # Avoid clobbering in-flight work.
            pass
        else:
            status = "todo"

        rows.append({
            "id": tweet_id,
            "title": old.get("title") or parse_title(path, text),
            "source_path": rel(path),
            "source_url": old.get("source_url") or parse_source_url(text, tweet_id),
            "category": old.get("category") or category_for(path),
            "status": status,
            "priority": old.get("priority", "normal"),
            "worker": worker,
            "started_at": started_at,
            "finished_at": finished_at,
            "error": error,
        })

    payload = {
        "version": 4,
        "updated_at": ts,
        "mode": "single-item",
        "notes": "Canonical single-item queue synced from memory/bookmarks for tiege processing and reconciled against memory/cards.",
        "items": rows,
    }

    QUEUE_PATH.parent.mkdir(parents=True, exist_ok=True)
    QUEUE_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    todo = sum(1 for r in rows if r["status"] == "todo")
    done = sum(1 for r in rows if r["status"] == "done")
    print(f"✅ queue synced: {QUEUE_PATH} ({len(rows)} items, todo={todo}, done={done})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
