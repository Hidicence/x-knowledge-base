#!/usr/bin/env python3
"""
Scan-mode bookmark enrichment worker.

Instead of reading from tiege-queue.json, directly scans memory/bookmarks/
for files that don't yet have a corresponding enriched card in memory/cards/.

Handles:
  - Named files (01-clawpal.md) that have tweet_id inside frontmatter
  - Files with truncated/non-standard tweet IDs
  - Any bookmark without a matching memory/cards/ entry

Usage:
    python3 scripts/run_scan_worker.py --limit 20
    python3 scripts/run_scan_worker.py --dry-run
    python3 scripts/run_scan_worker.py --limit 50 --worker pipeline
    python3 scripts/run_scan_worker.py --category 01-openclaw-workflows --limit 5
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

# ── Shared card prompt module ─────────────────────────────────────────────────
sys.path.insert(0, str(Path(__file__).parent))
from _card_prompt import build_prompt, find_related_context, llm_call as _llm_call

WORKSPACE = Path(os.getenv("OPENCLAW_WORKSPACE", os.getenv("WORKSPACE_DIR", str(Path.home() / ".openclaw" / "workspace"))))
BOOKMARKS_DIR = Path(os.getenv("BOOKMARKS_DIR", str(WORKSPACE / "memory" / "bookmarks")))
CARDS_DIR = Path(os.getenv("CARDS_DIR", str(WORKSPACE / "memory" / "cards")))


def _get_api_key() -> str:
    for env_key in ("LLM_API_KEY", "MINIMAX_API_KEY"):
        key = os.environ.get(env_key, "")
        if key:
            return key
    config_path = Path(os.environ.get("OPENCLAW_JSON", str(Path.home() / ".openclaw" / "openclaw.json")))
    if config_path.exists():
        try:
            config = json.loads(config_path.read_text(encoding="utf-8"))
            env = config.get("env", {})
            return env.get("LLM_API_KEY") or env.get("MINIMAX_API_KEY") or ""
        except Exception:
            pass
    return ""


def _get_index_items() -> list[dict]:
    index_path = WORKSPACE / "memory" / "bookmarks" / "search_index.json"
    if not index_path.exists():
        return []
    try:
        data = json.loads(index_path.read_text(encoding="utf-8"))
        return data.get("items", []) if isinstance(data, dict) else []
    except Exception:
        return []


def _call_llm(api_key: str, content: str, card_id: str, source_url: str, category: str,
              related_context: str = "") -> str:
    prompt = build_prompt(
        content=f"Please process this bookmark. If content is low-value (login page/404/noise), output only: SKIPPED\n\n"
                f"--- Raw content ---\n{content[:4000]}\n---",
        card_id=card_id,
        source_type="x-bookmark",
        source_url=source_url,
        category=category,
        related_context=related_context or "（知識庫中尚無明顯相關的已存 card）",
    )
    return _llm_call(prompt, api_key, max_tokens=2500)


# ── Scan-specific helpers ──────────────────────────────────────────────────────

def _extract_frontmatter_value(text: str, key: str) -> str:
    m = re.search(rf'^{re.escape(key)}:\s*"?([^"\n]+)"?\s*$', text, re.MULTILINE)
    return m.group(1).strip() if m else ""


def _extract_status_id(value: str) -> str:
    if not value:
        return ""
    m = re.search(r"/status/(\d{15,20})", value)
    return m.group(1) if m else ""


def _build_legacy_card_id(filepath: Path) -> str:
    slug = re.sub(r"[^a-zA-Z0-9._-]+", "-", filepath.stem).strip("-").lower() or "untitled"
    return f"legacy-{slug}"


def _get_card_id(filepath: Path, content: str) -> str:
    """Determine a stable card ID for this bookmark file."""
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


def scan_missing(limit: int, category_filter: str = "") -> list[tuple[Path, str, str, str, str]]:
    """Return list of (filepath, content, card_id, source_url, category) for unenriched files."""
    CARDS_DIR.mkdir(parents=True, exist_ok=True)
    existing_card_ids = {f.stem for f in CARDS_DIR.glob("*.md")}

    results = []
    skip_dirs = {"notebooklm_exports", "__pycache__", "youtube"}

    for md_file in sorted(BOOKMARKS_DIR.rglob("*.md")):
        if any(d in md_file.parts for d in skip_dirs):
            continue
        if md_file.name.startswith("."):
            continue

        content = md_file.read_text(encoding="utf-8", errors="ignore")
        card_id = _get_card_id(md_file, content)
        source_url = _get_source_url(content, card_id)
        category = _get_category(md_file)

        if category_filter and category_filter not in category:
            continue

        if card_id in existing_card_ids:
            continue

        results.append((md_file, content, card_id, source_url, category))
        if len(results) >= limit:
            break

    return results


def main() -> None:
    parser = argparse.ArgumentParser(description="Scan-mode bookmark enrichment worker")
    parser.add_argument("--limit",      type=int, default=20,    help="Max items to process (default: 20)")
    parser.add_argument("--worker",     default="scan-worker",   help="Worker name")
    parser.add_argument("--dry-run",    action="store_true",     help="Simulate without API calls")
    parser.add_argument("--local-only", action="store_true",     help="Skip LLM enrichment — scan and list unenriched bookmarks without sending content to any API")
    parser.add_argument("--category",   default="",              help="Filter by category slug")
    args = parser.parse_args()

    if args.local_only:
        args.dry_run = True  # local-only implies dry-run (no API calls)

    api_key = "" if args.dry_run else _get_api_key()
    if not api_key and not args.dry_run:
        print("❌ LLM_API_KEY not found. Set env var or add to openclaw.json.")
        sys.exit(1)

    missing = scan_missing(args.limit, args.category)
    total_missing = len(scan_missing(9999, args.category))

    if not missing:
        print("✅ All bookmarks already enriched")
        return

    print(f"📋 Found {total_missing} unenriched bookmarks  |  Processing {len(missing)} [worker: {args.worker}]")
    if args.local_only:
        print("   (local-only mode — no content sent to external APIs)")
    elif args.dry_run:
        print("   (dry-run — no API calls)")
    else:
        print(f"   ⚠️  Bookmark content will be sent to LLM API ({LLM_API_URL}) for enrichment.")

    results = {"done": 0, "skipped": 0, "failed": 0}

    for filepath, content, card_id, source_url, category in missing:
        label = str(filepath.relative_to(BOOKMARKS_DIR))
        print(f"  → {label}", end="  ", flush=True)

        if args.dry_run:
            print(f"[dry-run: {card_id}]")
            results["done"] += 1
            continue

        try:
            related_ctx = _find_related_context(content)
            text = _call_llm(api_key, content, card_id, source_url, category, related_ctx)
            if not text:
                results["failed"] += 1
                print("✗ empty response")
                continue
            if re.match(r"^SKIPPED", text.strip(), re.IGNORECASE):
                results["skipped"] += 1
                print("⏭ skipped")
                continue

            card_path = CARDS_DIR / f"{card_id}.md"
            card_path.write_text(text, encoding="utf-8")
            results["done"] += 1
            print("✓ done")
        except Exception as exc:
            results["failed"] += 1
            print(f"✗ {exc}")

    remaining = len(scan_missing(9999, args.category))
    print(f"\n📊 done={results['done']}  skipped={results['skipped']}  failed={results['failed']}  remaining={remaining}")


if __name__ == "__main__":
    main()
