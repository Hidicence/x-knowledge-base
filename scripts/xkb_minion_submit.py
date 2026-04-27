#!/usr/bin/env python3
"""
XKB Minion Job Submitter

Scans for unenriched bookmarks and submits them as Minion jobs to the
gbrain Postgres queue. Jobs are idempotent — safe to run repeatedly.

Usage:
    python3 scripts/xkb_minion_submit.py [--dry-run] [--limit N]
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

import psycopg2
import psycopg2.extras

WORKSPACE = Path(os.getenv("OPENCLAW_WORKSPACE", os.getenv("WORKSPACE_DIR", str(Path.home() / ".openclaw" / "workspace"))))
BOOKMARKS_DIR = Path(os.getenv("BOOKMARKS_DIR", str(WORKSPACE / "memory" / "bookmarks")))
CARDS_DIR = Path(os.getenv("CARDS_DIR", str(WORKSPACE / "memory" / "cards")))

GBRAIN_DB_URL = os.environ["GBRAIN_DATABASE_URL"]  # required: set in env, no fallback

XKB_QUEUE = "xkb"
JOB_NAME = "xkb-enrich"
TIMEOUT_MS = 300_000   # 5 min per bookmark
MAX_ATTEMPTS = 2


# ── Reuse helpers from run_scan_worker ────────────────────────────────────────

def _extract_frontmatter_value(text: str, key: str) -> str:
    m = re.search(rf'^{re.escape(key)}:\s*"?([^"\n]+)"?\s*$', text, re.MULTILINE)
    return m.group(1).strip() if m else ""

def _extract_status_id(value: str) -> str:
    m = re.search(r"/status/(\d{15,20})", value)
    return m.group(1) if m else ""

def _build_legacy_card_id(filepath: Path) -> str:
    slug = re.sub(r"[^a-zA-Z0-9._-]+", "-", filepath.stem).strip("-").lower() or "untitled"
    return f"legacy-{slug}"

def _get_card_id(filepath: Path, content: str) -> str:
    tweet_id = _extract_frontmatter_value(content, "tweet_id")
    if tweet_id and re.fullmatch(r"\d{15,20}", tweet_id):
        return tweet_id
    for field in ("source_url", "source"):
        url = _extract_frontmatter_value(content, field)
        status_id = _extract_status_id(url)
        if status_id:
            return status_id
    if re.fullmatch(r"\d{15,20}", filepath.stem):
        return filepath.stem
    m = re.match(r"^(\d{15,20})", filepath.stem)
    if m:
        return m.group(1)
    return _build_legacy_card_id(filepath)

def _get_source_url(content: str, card_id: str) -> str:
    for field in ("source_url", "source"):
        url = _extract_frontmatter_value(content, field)
        if url and url.startswith("http"):
            return url
    if re.fullmatch(r"\d{15,20}", card_id):
        return f"https://x.com/i/status/{card_id}"
    return ""

def _get_category(filepath: Path) -> str:
    try:
        parts = filepath.relative_to(BOOKMARKS_DIR).parts
        if len(parts) >= 2:
            return parts[0]
    except Exception:
        pass
    return ""

_JUNK_PATTERNS = [
    "Don't miss what's happening",
    "People on X are the first to know",
    "Log in to X",
    "Sign in to X",
    "JavaScript is not available",
]

def _is_junk_content(content: str) -> bool:
    body = content.split("---", 2)[-1].strip() if content.startswith("---") else content.strip()
    return any(pat in body for pat in _JUNK_PATTERNS)

def scan_unenriched(limit: int = 9999) -> list[dict]:
    """Return list of bookmark info dicts that have no card yet."""
    CARDS_DIR.mkdir(parents=True, exist_ok=True)
    existing_ids = {f.stem for f in CARDS_DIR.glob("*.md")}
    skip_dirs = {"notebooklm_exports", "__pycache__", "youtube"}
    results = []

    for md_file in sorted(BOOKMARKS_DIR.rglob("*.md")):
        if any(d in md_file.parts for d in skip_dirs):
            continue
        if md_file.name.startswith("."):
            continue

        content = md_file.read_text(encoding="utf-8", errors="ignore")
        card_id = _get_card_id(md_file, content)
        if card_id in existing_ids:
            continue
        if _is_junk_content(content):
            print(f"  🗑️  Junk, deleting: {md_file.name}")
            md_file.unlink()
            continue

        results.append({
            "card_id": card_id,
            "filepath": str(md_file),
            "source_url": _get_source_url(content, card_id),
            "category": _get_category(md_file),
        })
        if len(results) >= limit:
            break

    return results


def submit_jobs(bookmarks: list[dict], dry_run: bool = False) -> tuple[int, int]:
    """Submit bookmark enrichment jobs. Returns (submitted, skipped_existing)."""
    if not bookmarks:
        return 0, 0

    conn = psycopg2.connect(GBRAIN_DB_URL)
    submitted = skipped = 0
    try:
        with conn:
            with conn.cursor() as cur:
                for bm in bookmarks:
                    ikey = f"xkb-enrich-{bm['card_id']}"
                    if dry_run:
                        print(f"  [dry-run] would submit: {bm['card_id']}")
                        submitted += 1
                        continue
                    try:
                        cur.execute(
                            """
                            INSERT INTO minion_jobs
                                (name, queue, data, max_attempts, timeout_ms, idempotency_key)
                            VALUES (%s, %s, %s, %s, %s, %s)
                            ON CONFLICT (idempotency_key) DO NOTHING
                            """,
                            (
                                JOB_NAME,
                                XKB_QUEUE,
                                json.dumps(bm),
                                MAX_ATTEMPTS,
                                TIMEOUT_MS,
                                ikey,
                            ),
                        )
                        if cur.rowcount > 0:
                            submitted += 1
                        else:
                            skipped += 1
                    except Exception as e:
                        print(f"  ⚠️  Failed to submit {bm['card_id']}: {e}")
    finally:
        conn.close()

    return submitted, skipped


def main() -> None:
    parser = argparse.ArgumentParser(description="Submit XKB bookmark enrichment jobs to Minion queue")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be submitted without inserting")
    parser.add_argument("--limit", type=int, default=9999, help="Max jobs to submit per run")
    args = parser.parse_args()

    print("🔍 Scanning for unenriched bookmarks...")
    bookmarks = scan_unenriched(args.limit)
    print(f"   Found {len(bookmarks)} unenriched bookmarks")

    if not bookmarks:
        print("✅ Nothing to submit")
        return

    submitted, skipped = submit_jobs(bookmarks, dry_run=args.dry_run)
    print(f"📤 Submitted: {submitted}  |  Already queued: {skipped}")


if __name__ == "__main__":
    main()
