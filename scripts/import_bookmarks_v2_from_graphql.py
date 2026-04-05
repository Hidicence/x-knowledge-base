#!/usr/bin/env python3
"""
Import raw bookmark markdown files into bookmarks_v2/inbox from GraphQL crawl state.

Uses the existing per-tweet fetch flow (bird + jina fallback + agent-reach enrichment)
but sources tweet ids from the direct GraphQL crawl baseline instead of bird bookmark paging.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
from pathlib import Path

WORKSPACE = Path(os.getenv("OPENCLAW_WORKSPACE", os.getenv("WORKSPACE_DIR", str(Path.home() / ".openclaw" / "workspace"))))
SKILL_DIR = WORKSPACE / "skills" / "x-knowledge-base"
STATE_FILE = WORKSPACE / "memory" / "x-knowledge-base" / "graphql-bookmarks-crawl.json"
BOOKMARKS_V2 = WORKSPACE / "memory" / "bookmarks"
INBOX = BOOKMARKS_V2 / "inbox"
TMP_IDS = "/tmp/graphql_bookmarks_v2_ids.txt"


def main() -> int:
    parser = argparse.ArgumentParser(description="Import bookmarks_v2 from GraphQL crawl state")
    parser.add_argument("--limit", type=int, default=0, help="Optional limit for first import run")
    parser.add_argument("--prepare-only", action="store_true", help="Stop after raw fetch + enrich")
    args = parser.parse_args()

    if not STATE_FILE.exists():
        print(f"❌ Missing GraphQL crawl state: {STATE_FILE}")
        return 1

    state = json.loads(STATE_FILE.read_text(encoding="utf-8"))
    ids = state.get("unique_ids", [])
    if not ids:
        print("❌ No tweet ids found in GraphQL crawl state")
        return 1
    if args.limit > 0:
        ids = ids[: args.limit]

    INBOX.mkdir(parents=True, exist_ok=True)
    BOOKMARKS_V2.mkdir(parents=True, exist_ok=True)

    with open(TMP_IDS, "w", encoding="utf-8") as f:
        for tid in ids:
            f.write(f"{tid}\n")

    env = os.environ.copy()
    env.update(
        {
            "WORKSPACE_DIR": str(WORKSPACE),
            "OPENCLAW_WORKSPACE": str(WORKSPACE),
            "SKILL_DIR": str(SKILL_DIR),
            "BOOKMARKS_DIR": str(BOOKMARKS_V2),
            "CARDS_DIR": str(WORKSPACE / "memory" / "cards"),
            "RUNTIME_DIR": str(WORKSPACE / "memory" / "x-knowledge-base"),
            "BOOKMARKS_TMP_FILE": TMP_IDS,
            "PREPARE_ONLY": "1" if args.prepare_only else env.get("PREPARE_ONLY", "0"),
            "FETCH_SKIP_BOOKMARKS": "1",
        }
    )

    subprocess.run(["bash", str(SKILL_DIR / "scripts" / "fetch_and_summarize.sh")], check=True, env=env)
    print(f"✅ Imported {len(ids)} GraphQL bookmark ids into v2 pipeline")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
