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
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.request
from pathlib import Path

WORKSPACE = Path(os.getenv("OPENCLAW_WORKSPACE", os.getenv("WORKSPACE_DIR", str(Path.home() / ".openclaw" / "workspace"))))
BOOKMARKS_DIR = Path(os.getenv("BOOKMARKS_DIR", str(WORKSPACE / "memory" / "bookmarks")))
CARDS_DIR = Path(os.getenv("CARDS_DIR", str(WORKSPACE / "memory" / "cards")))

MINIMAX_API_URL = "https://api.minimaxi.chat/v1/chat/completions"
MINIMAX_MODEL = "MiniMax-M2.5"

SYSTEM_PROMPT = """You are a bookmark knowledge card generator. Given the raw content of a single X/Twitter bookmark, output one structured knowledge card in Traditional Chinese.

Strict rules:
- Process ONLY this one bookmark
- Leave fields empty if uncertain — never hallucinate
- If content is a login page, 404, or homepage noise, output exactly: SKIPPED

Output format (Markdown):
---
id: {id}
type: x-knowledge-card
source_type: x-bookmark
source_url: {source_url}
author: (infer from content, leave blank if unsure)
created_at: (infer from content, leave blank if unsure)
category: {category}
tags: [tag1, tag2, tag3]
confidence: medium
---

# <title>

## 1. 核心摘要
One sentence capturing the core value.

## 2. 重點整理
- Point 1
- Point 2
- Point 3

## 3. 作者補充 / Thread 重點
- Thread highlights or author follow-ups (2–4 points)
- If none: 無明顯補充

## 4. 外部連結重點
- Key info from linked articles/repos
- If none: 無外部連結內容

## 5. 對使用者的價值
- What to track
- How to apply it
- Which project/workflow it fits

## 6. 關聯主題
- Topic A

## 7. 原始來源
- Tweet: {source_url}
- Links: (list URLs found in content)

Quality principles: conservative > hallucination, quality > coverage, structured > verbose"""


def _get_api_key() -> str:
    key = os.environ.get("MINIMAX_API_KEY", "")
    if key:
        return key
    config_path = Path(os.environ.get("OPENCLAW_JSON", str(Path.home() / ".openclaw" / "openclaw.json")))
    if config_path.exists():
        config = json.loads(config_path.read_text(encoding="utf-8"))
        key = config.get("env", {}).get("MINIMAX_API_KEY", "")
    return key


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


def _call_minimax(api_key: str, content: str, card_id: str, source_url: str, category: str) -> str:
    system = SYSTEM_PROMPT.format(id=card_id, source_url=source_url, category=category)
    user_msg = f"""Please process this bookmark:

ID: {card_id}
Source: {source_url}
Category: {category}

--- Raw content ---
{content[:3000]}
---

Output the knowledge card. If content is low-value (login page/404/noise), output only: SKIPPED"""

    payload = {
        "model": MINIMAX_MODEL,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user_msg},
        ],
        "max_tokens": 2000,
        "temperature": 0.3,
    }
    req = urllib.request.Request(
        MINIMAX_API_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=90) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    content_blocks = data.get("choices", [{}])[0].get("message", {}).get("content", "")
    if isinstance(content_blocks, list):
        text = next((b["text"] for b in content_blocks if b.get("type") == "text"), "")
    else:
        text = content_blocks
    return text.strip()


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
    parser.add_argument("--limit", type=int, default=20, help="Max items to process (default: 20)")
    parser.add_argument("--worker", default="scan-worker", help="Worker name")
    parser.add_argument("--dry-run", action="store_true", help="Simulate without API calls")
    parser.add_argument("--category", default="", help="Filter by category slug")
    args = parser.parse_args()

    api_key = "" if args.dry_run else _get_api_key()
    if not api_key and not args.dry_run:
        print("❌ MINIMAX_API_KEY not found")
        sys.exit(1)

    missing = scan_missing(args.limit, args.category)
    total_missing = len(scan_missing(9999, args.category))

    if not missing:
        print("✅ All bookmarks already enriched")
        return

    print(f"📋 Found {total_missing} unenriched bookmarks  |  Processing {len(missing)} [worker: {args.worker}]")
    if args.dry_run:
        print("   (dry-run — no API calls)")

    results = {"done": 0, "skipped": 0, "failed": 0}

    for filepath, content, card_id, source_url, category in missing:
        label = f"{filepath.relative_to(BOOKMARKS_DIR)}"
        print(f"  → {label}", end="  ", flush=True)

        if args.dry_run:
            print(f"[dry-run: {card_id}]")
            results["done"] += 1
            continue

        try:
            text = _call_minimax(api_key, content, card_id, source_url, category)
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
