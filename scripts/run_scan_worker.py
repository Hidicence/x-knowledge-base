#!/usr/bin/env python3
"""
Scan-mode bookmark enrichment worker.

Instead of reading from tiege-queue.json, directly scans memory/bookmarks/
for files that don't yet have a corresponding enriched card in memory/cards/.

Now uses XKB request/result inference queue instead of synchronous in-process
LLM calls, so bookmark enrichment follows the same non-blocking adapter path
as memory distill.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import time
from pathlib import Path

from _card_prompt import gbrain_put as _gbrain_put

WORKSPACE = Path(os.getenv("OPENCLAW_WORKSPACE", os.getenv("WORKSPACE_DIR", str(Path.home() / ".openclaw" / "workspace"))))
BOOKMARKS_DIR = Path(os.getenv("BOOKMARKS_DIR", str(WORKSPACE / "memory" / "bookmarks")))
CARDS_DIR = Path(os.getenv("CARDS_DIR", str(WORKSPACE / "memory" / "cards")))
RUNTIME_DIR = Path(os.getenv("RUNTIME_DIR", str(WORKSPACE / "memory" / "x-knowledge-base")))
ENQUEUE_SCRIPT = Path(__file__).resolve().parent / "bookmark_infer_enqueue.py"
RESULTS_DIR = RUNTIME_DIR / "bookmark-infer" / "results"


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
QUARANTINE_DIR = BOOKMARKS_DIR / "junk-review"


def _content_signals_value(body: str) -> bool:
    lower = body.lower()
    return any([
        'http://' in lower,
        'https://' in lower,
        't.co/' in lower,
        '## 🧵 thread 全文' in lower,
        '## 🔗 外部連結摘錄' in body,
        len(body.strip()) >= 120,
    ])


def _junk_reason(content: str) -> str | None:
    body = content.split("---", 2)[-1].strip() if content.startswith("---") else content.strip()
    for pat in _JUNK_PATTERNS:
        if pat in body:
            return "login_wall"
    if not _content_signals_value(body):
        return "thin_content"
    return None


def _quarantine_file(md_file: Path, reason: str) -> None:
    QUARANTINE_DIR.mkdir(parents=True, exist_ok=True)
    target = QUARANTINE_DIR / md_file.name
    if md_file.resolve() == target.resolve():
        return
    if target.exists():
        target.unlink()
    md_file.rename(target)
    print(f"  📦 Quarantine ({reason}): {md_file.name} -> {target}")


def scan_missing(limit: int, category_filter: str = "") -> list[tuple[Path, str, str, str, str]]:
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
        junk_reason = _junk_reason(content)
        if junk_reason == "login_wall":
            _quarantine_file(md_file, junk_reason)
            continue

        results.append((md_file, content, card_id, source_url, category))
        if len(results) >= limit:
            break

    return results


def _enqueue_bookmark(filepath: Path, card_id: str, source_url: str, category: str) -> str:
    cmd = [
        "python3",
        str(ENQUEUE_SCRIPT),
        "--bookmark-file",
        str(filepath),
        "--card-id",
        card_id,
        "--source-url",
        source_url,
        "--category",
        category,
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    if proc.returncode != 0:
        raise RuntimeError(f"enqueue failed: {(proc.stderr or proc.stdout).strip()[:300]}")
    return (proc.stdout or "").strip()


def _result_path(card_id: str) -> Path:
    return RESULTS_DIR / f"bookmark-{card_id}.json"


def _wait_for_result(card_id: str, timeout_s: int = 240) -> dict:
    path = _result_path(card_id)
    start = time.time()
    while time.time() - start < timeout_s:
        if path.exists():
            try:
                return json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                pass
        time.sleep(2)
    raise TimeoutError(f"bookmark infer timed out after {timeout_s}s for {card_id}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Scan-mode bookmark enrichment worker")
    parser.add_argument("--limit", type=int, default=20, help="Max items to process (default: 20)")
    parser.add_argument("--worker", default="scan-worker", help="Worker name")
    parser.add_argument("--dry-run", action="store_true", help="Simulate without API calls")
    parser.add_argument("--local-only", action="store_true", help="Skip LLM enrichment — scan and list unenriched bookmarks without sending content to any API")
    parser.add_argument("--category", default="", help="Filter by category slug")
    args = parser.parse_args()

    if args.local_only:
        args.dry_run = True

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
        print("   ⚠️  Bookmark content will be sent to LLM inference queue for enrichment.")

    results = {"done": 0, "skipped": 0, "failed": 0}

    for filepath, content, card_id, source_url, category in missing:
        label = str(filepath.relative_to(BOOKMARKS_DIR))
        print(f"  → {label}", end="  ", flush=True)

        if args.dry_run:
            print(f"[dry-run: {card_id}]")
            results["done"] += 1
            continue

        try:
            enqueue_status = _enqueue_bookmark(filepath, card_id, source_url, category)
            result = _wait_for_result(card_id)
            if not result.get("ok"):
                raise RuntimeError(result.get("error", {}).get("message") or "inference failed")
            text = (result.get("output", {}) or {}).get("card_markdown", "")
            if not text:
                results["failed"] += 1
                print("✗ empty response")
                continue
            if re.match(r"^SKIPPED", text.strip(), re.IGNORECASE):
                results["skipped"] += 1
                print(f"⏭ skipped [{enqueue_status}]")
                continue

            card_path = CARDS_DIR / f"{card_id}.md"
            card_path.write_text(text, encoding="utf-8")
            _gbrain_put(card_path, card_id)
            results["done"] += 1
            print(f"✓ done [{enqueue_status}]")
        except Exception as exc:
            results["failed"] += 1
            print(f"✗ {exc}")

    remaining = len(scan_missing(9999, args.category))
    print(f"\n📊 done={results['done']}  skipped={results['skipped']}  failed={results['failed']}  remaining={remaining}")


if __name__ == "__main__":
    main()
