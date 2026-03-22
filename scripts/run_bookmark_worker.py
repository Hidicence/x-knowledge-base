#!/usr/bin/env python3
"""
Bookmark enrichment worker — processes todo items from tiege-queue.json one at a time.

Usage:
    python3 scripts/run_bookmark_worker.py --limit 5
    python3 scripts/run_bookmark_worker.py --limit 1 --dry-run
    python3 scripts/run_bookmark_worker.py --worker myagent --limit 10
    python3 scripts/run_bookmark_worker.py --category 01-openclaw-workflows --limit 5
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

WORKSPACE = Path(os.getenv("OPENCLAW_WORKSPACE", os.getenv("WORKSPACE_DIR", str(Path.home() / ".openclaw" / "workspace"))))
BOOKMARKS_DIR = Path(os.getenv("BOOKMARKS_DIR", str(WORKSPACE / "memory" / "bookmarks")))
QUEUE_PATH = Path(os.getenv("XKB_QUEUE_PATH", str(WORKSPACE / "memory" / "x-knowledge-base" / "tiege-queue.json")))
CARDS_DIR = WORKSPACE / "memory" / "notebooklm_exports" / "cards"

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
    config_path = Path("/root/.openclaw/openclaw.json")
    if config_path.exists():
        config = json.loads(config_path.read_text(encoding="utf-8"))
        key = config.get("env", {}).get("MINIMAX_API_KEY", "")
    return key


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _load_queue() -> dict:
    return json.loads(QUEUE_PATH.read_text(encoding="utf-8"))


def _save_queue(data: dict) -> None:
    QUEUE_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _read_bookmark(source_path: str) -> str:
    full_path = WORKSPACE / source_path
    if full_path.exists():
        return full_path.read_text(encoding="utf-8", errors="ignore")
    return ""


def _call_minimax(api_key: str, bookmark_content: str, item: dict) -> str:
    system = SYSTEM_PROMPT.format(
        id=item["id"],
        source_url=item.get("source_url", ""),
        category=item.get("category", ""),
    )
    user_msg = f"""Please process this bookmark:

ID: {item['id']}
Source: {item.get('source_url', '')}
Category: {item.get('category', '')}

--- Raw content ---
{bookmark_content}
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
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=90) as resp:
        data = json.loads(resp.read().decode("utf-8"))

    # MiniMax may return thinking block first
    content_blocks = data.get("choices", [{}])[0].get("message", {}).get("content", "")
    if isinstance(content_blocks, list):
        text = next((b["text"] for b in content_blocks if b.get("type") == "text"), "")
    else:
        text = content_blocks
    return text.strip()


def _process_item(item: dict, api_key: str, dry_run: bool) -> tuple[str, str]:
    """Returns (status, error). status: done | skipped | failed"""
    bookmark_content = _read_bookmark(item["source_path"])
    if not bookmark_content:
        return "failed", "bookmark file not found"

    if dry_run:
        preview = bookmark_content[:80].replace("\n", " ")
        print(f"    preview: {preview}...")
        return "done", ""

    text = _call_minimax(api_key, bookmark_content, item)

    if not text:
        return "failed", "empty response from API"

    if re.match(r"^SKIPPED", text.strip(), re.IGNORECASE):
        return "skipped", ""

    CARDS_DIR.mkdir(parents=True, exist_ok=True)
    card_path = CARDS_DIR / f"{item['id']}.md"
    card_path.write_text(text, encoding="utf-8")
    return "done", ""


def main() -> None:
    parser = argparse.ArgumentParser(description="Bookmark enrichment worker")
    parser.add_argument("--limit", type=int, default=5, help="Max items to process (default: 5)")
    parser.add_argument("--worker", default="worker", help="Worker name recorded in queue")
    parser.add_argument("--dry-run", action="store_true", help="Simulate without calling API")
    parser.add_argument("--category", help="Filter by category slug (e.g. 01-openclaw-workflows)")
    args = parser.parse_args()

    api_key = "" if args.dry_run else _get_api_key()
    if not api_key and not args.dry_run:
        print("❌ MINIMAX_API_KEY not found. Set env var or add to openclaw.json.")
        sys.exit(1)

    data = _load_queue()
    items = data["items"]

    todo = [i for i in items if i["status"] == "todo"]
    if args.category:
        todo = [i for i in todo if args.category in i.get("category", "")]
    todo = todo[: args.limit]

    if not todo:
        print("✅ No todo items found")
        return

    total_todo = len([i for i in items if i["status"] == "todo"])
    print(f"📋 Processing {len(todo)}/{total_todo} todo items  [worker: {args.worker}]")
    if args.dry_run:
        print("   (dry-run mode — no API calls)")

    # Build id → [all indices] to handle duplicate IDs across categories
    from collections import defaultdict
    id_to_indices: dict[str, list[int]] = defaultdict(list)
    for idx, it in enumerate(items):
        id_to_indices[it["id"]].append(idx)

    results = {"done": 0, "skipped": 0, "failed": 0}

    for item in todo:
        print(f"  → {item['id']}  [{item.get('category', '')}]", end="  ", flush=True)

        indices = id_to_indices[item["id"]]
        for idx in indices:
            data["items"][idx].update({"status": "processing", "worker": args.worker, "started_at": _now_iso()})
        _save_queue(data)

        try:
            status, error = _process_item(item, api_key, args.dry_run)

            title_update = {}
            if status == "done" and not args.dry_run:
                card_path = CARDS_DIR / f"{item['id']}.md"
                if card_path.exists():
                    m = re.search(r"^# (.+)$", card_path.read_text(encoding="utf-8"), re.MULTILINE)
                    if m:
                        title_update = {"title": m.group(1).strip()}

            for idx in indices:
                data["items"][idx].update({"status": status, "finished_at": _now_iso(), "error": error, **title_update})

            results[status] += 1
            print(f"✓ {status}")
        except Exception as exc:
            for idx in indices:
                data["items"][idx].update({"status": "failed", "error": str(exc)[:200], "finished_at": _now_iso()})
            results["failed"] += 1
            print(f"✗ failed: {exc}")

        _save_queue(data)

    remaining = len([i for i in data["items"] if i["status"] == "todo"])
    print(f"\n📊 done={results['done']}  skipped={results['skipped']}  failed={results['failed']}  remaining todo={remaining}")


if __name__ == "__main__":
    main()
