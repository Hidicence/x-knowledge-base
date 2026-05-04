#!/usr/bin/env python3
"""
Crawl X bookmarks coverage page by page via bird, with resume support.

Purpose:
- find how many bookmarks are actually reachable
- avoid unstable one-shot `--all --max-pages N`
- persist state between runs

Usage:
    python3 scripts/crawl_bookmarks_coverage.py
    python3 scripts/crawl_bookmarks_coverage.py --max-pages 50
    python3 scripts/crawl_bookmarks_coverage.py --resume-file /tmp/bookmarks-crawl.json
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

WORKSPACE = Path(os.getenv("OPENCLAW_WORKSPACE", os.getenv("WORKSPACE_DIR", str(Path.home() / ".openclaw" / "workspace"))))
DEFAULT_RESUME = WORKSPACE / "memory" / "x-knowledge-base" / "bookmarks-crawl-state.json"
SECRETS_FILE = WORKSPACE / ".secrets" / "x-knowledge-base.env"


def load_env_file(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip())


def run_bird_page(auth: str, ct0: str, count: int, cursor: str | None, timeout: int) -> tuple[dict[str, Any] | None, str | None]:
    cmd = [
        "bird",
        "--auth-token", auth,
        "--ct0", ct0,
        "bookmarks",
        "--count", str(count),
        "--max-pages", "1",
        "--json",
    ]
    if cursor:
        cmd += ["--cursor", cursor]
    try:
        out = subprocess.check_output(cmd, stderr=subprocess.STDOUT, text=True, timeout=timeout)
    except subprocess.CalledProcessError as e:
        return None, f"bird failed: {e.output[:500]}"
    except subprocess.TimeoutExpired:
        return None, f"bird timeout after {timeout}s"
    m = re.search(r"\{", out)
    if not m:
        return None, f"no json object found in output: {out[:500]}"
    try:
        data = json.loads(out[m.start():])
    except Exception as e:
        return None, f"json parse failed: {e}"
    return data, None


def extract_next_cursor(data: dict[str, Any]) -> str | None:
    candidates = [
        data.get("next_cursor"),
        data.get("nextCursor"),
        data.get("cursor"),
        data.get("next"),
        data.get("next_cursor_str"),
    ]
    for c in candidates:
        if isinstance(c, str) and c.strip():
            return c.strip()
    return None


def load_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {
            "pages_crawled": 0,
            "total_items_seen": 0,
            "unique_ids": [],
            "last_cursor": None,
            "completed": False,
            "errors": [],
        }
    return json.loads(path.read_text(encoding="utf-8"))


def save_state(path: Path, state: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Crawl bookmarks coverage via bird")
    parser.add_argument("--count", type=int, default=20)
    parser.add_argument("--max-pages", type=int, default=50)
    parser.add_argument("--delay-ms", type=int, default=1200)
    parser.add_argument("--timeout", type=int, default=90)
    parser.add_argument("--resume-file", default=str(DEFAULT_RESUME))
    parser.add_argument("--restart", action="store_true")
    args = parser.parse_args()

    load_env_file(SECRETS_FILE)
    auth = os.getenv("BIRD_AUTH_TOKEN", "")
    ct0 = os.getenv("BIRD_CT0", "")
    if not auth or not ct0:
        print("❌ missing BIRD_AUTH_TOKEN / BIRD_CT0")
        return 1

    resume_path = Path(args.resume_file)
    state = load_state(resume_path)
    if args.restart:
        state = {
            "pages_crawled": 0,
            "total_items_seen": 0,
            "unique_ids": [],
            "last_cursor": None,
            "completed": False,
            "errors": [],
        }

    seen = set(state.get("unique_ids", []))
    cursor = state.get("last_cursor")
    pages_crawled = int(state.get("pages_crawled", 0))
    total_items_seen = int(state.get("total_items_seen", 0))

    for _ in range(args.max_pages):
        data, err = run_bird_page(auth, ct0, args.count, cursor, args.timeout)
        if err:
            state["pages_crawled"] = pages_crawled
            state["total_items_seen"] = total_items_seen
            state["unique_ids"] = sorted(seen)
            state["last_cursor"] = cursor
            state["completed"] = False
            state.setdefault("errors", []).append(err)
            save_state(resume_path, state)
            print(json.dumps({
                "ok": False,
                "pages_crawled": pages_crawled,
                "total_items_seen": total_items_seen,
                "unique_count": len(seen),
                "last_cursor": cursor,
                "error": err,
                "resume_file": str(resume_path),
            }, ensure_ascii=False, indent=2))
            return 2

        items = data.get("items", data.get("tweets", [])) or []
        page_ids = [str(x.get("id", "")).strip() for x in items if str(x.get("id", "")).strip()]
        total_items_seen += len(page_ids)
        before = len(seen)
        seen.update(page_ids)
        added = len(seen) - before
        pages_crawled += 1
        next_cursor = extract_next_cursor(data)

        state["pages_crawled"] = pages_crawled
        state["total_items_seen"] = total_items_seen
        state["unique_ids"] = sorted(seen)
        state["last_cursor"] = next_cursor
        state["completed"] = False
        save_state(resume_path, state)

        print(json.dumps({
            "page": pages_crawled,
            "page_count": len(page_ids),
            "added_unique": added,
            "unique_count": len(seen),
            "has_next_cursor": bool(next_cursor),
        }, ensure_ascii=False))

        if not next_cursor or len(page_ids) == 0:
            state["completed"] = True
            state["last_cursor"] = None
            save_state(resume_path, state)
            print(json.dumps({
                "ok": True,
                "completed": True,
                "pages_crawled": pages_crawled,
                "total_items_seen": total_items_seen,
                "unique_count": len(seen),
                "resume_file": str(resume_path),
            }, ensure_ascii=False, indent=2))
            return 0

        cursor = next_cursor
        time.sleep(args.delay_ms / 1000)

    state["pages_crawled"] = pages_crawled
    state["total_items_seen"] = total_items_seen
    state["unique_ids"] = sorted(seen)
    state["last_cursor"] = cursor
    state["completed"] = False
    save_state(resume_path, state)
    print(json.dumps({
        "ok": True,
        "completed": False,
        "pages_crawled": pages_crawled,
        "total_items_seen": total_items_seen,
        "unique_count": len(seen),
        "last_cursor": cursor,
        "resume_file": str(resume_path),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
