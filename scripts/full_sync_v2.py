#!/usr/bin/env python3
"""
Run x-knowledge-base v2 bootstrap/full sync until convergence.

Stages:
1. GraphQL crawl baseline (resume-capable)
2. Import raw markdown into bookmarks_v2
3. Repeatedly enrich missing cards in batches
4. Sync v2 index / governance / vector rebuild
5. Emit completion report
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

WORKSPACE = Path(os.getenv("OPENCLAW_WORKSPACE", os.getenv("WORKSPACE_DIR", str(Path.home() / ".openclaw" / "workspace"))))
SKILL_DIR = WORKSPACE / "skills" / "x-knowledge-base"
BOOKMARKS_V2 = WORKSPACE / "memory" / "bookmarks"
CARDS_V2 = WORKSPACE / "memory" / "cards"
RUNTIME_V2 = WORKSPACE / "memory" / "x-knowledge-base"
INDEX_V2 = BOOKMARKS_V2 / "search_index.json"
VECTOR_V2 = BOOKMARKS_V2 / "vector_index.json"


def run(cmd: list[str], env: dict[str, str] | None = None) -> None:
    print("$", " ".join(cmd))
    subprocess.run(cmd, check=True, env=env)


def count_raw_markdown() -> int:
    return len([p for p in BOOKMARKS_V2.rglob("*.md") if p.name != "INDEX.md"])


def count_cards() -> int:
    return len(list(CARDS_V2.glob("*.md")))


def load_graphql_state() -> dict:
    p = RUNTIME_V2 / "graphql-bookmarks-crawl.json"
    if not p.exists():
        return {}
    return json.loads(p.read_text(encoding="utf-8"))


def completion_report() -> dict:
    report = {
        "raw_count": count_raw_markdown(),
        "cards_count": count_cards(),
        "graphql_unique": len(load_graphql_state().get("unique_ids", [])),
        "index_items": 0,
        "index_enriched": 0,
        "vector_count": 0,
    }
    if INDEX_V2.exists():
        raw = json.loads(INDEX_V2.read_text(encoding="utf-8"))
        items = raw.get("items", raw) if isinstance(raw, dict) else raw
        report["index_items"] = len(items)
        report["index_enriched"] = sum(1 for i in items if isinstance(i, dict) and i.get("enriched"))
    if VECTOR_V2.exists():
        raw = json.loads(VECTOR_V2.read_text(encoding="utf-8"))
        report["vector_count"] = len(raw.get("vectors", {}))
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="Run x-knowledge-base full sync v2 until convergence")
    parser.add_argument("--max-crawl-pages", type=int, default=40)
    parser.add_argument("--enrich-batch", type=int, default=40)
    parser.add_argument("--max-enrich-rounds", type=int, default=8)
    args = parser.parse_args()

    env = os.environ.copy()
    env.update(
        {
            "WORKSPACE_DIR": str(WORKSPACE),
            "OPENCLAW_WORKSPACE": str(WORKSPACE),
            "BOOKMARKS_DIR": str(BOOKMARKS_V2),
            "CARDS_DIR": str(CARDS_V2),
            "RUNTIME_DIR": str(RUNTIME_V2),
            "INDEX_FILE": str(INDEX_V2),
            "VECTOR_INDEX_PATH": str(VECTOR_V2),
            "XKB_QUEUE_PATH": str(RUNTIME_V2 / "tiege-queue.json"),
            "FETCH_SKIP_BOOKMARKS": "1",
        }
    )

    run([sys.executable, str(SKILL_DIR / "scripts" / "init_rebuild_v2.py")], env=env)
    run([sys.executable, str(SKILL_DIR / "scripts" / "crawl_bookmarks_graphql.py"), "--max-pages", str(args.max_crawl_pages)], env=env)
    run([sys.executable, str(SKILL_DIR / "scripts" / "import_bookmarks_v2_from_graphql.py"), "--prepare-only"], env=env)

    last_cards = count_cards()
    for round_idx in range(1, args.max_enrich_rounds + 1):
        print(f"\n=== enrich round {round_idx} ===")
        run([
            sys.executable,
            str(SKILL_DIR / "scripts" / "run_scan_worker.py"),
            "--limit",
            str(args.enrich_batch),
            "--worker",
            f"full-sync-v2-{round_idx}",
        ], env=env)
        run([
            sys.executable,
            str(SKILL_DIR / "scripts" / "sync_enriched_index.py"),
        ], env=env)
        run([
            sys.executable,
            str(SKILL_DIR / "scripts" / "normalize_index_quality.py"),
        ], env=env)
        run([
            sys.executable,
            str(SKILL_DIR / "scripts" / "canonicalize_duplicates.py"),
        ], env=env)
        run([
            sys.executable,
            str(SKILL_DIR / "scripts" / "cleanup_titles_in_index.py"),
        ], env=env)
        run([
            sys.executable,
            str(SKILL_DIR / "scripts" / "build_vector_index.py"),
            "--index-file",
            str(INDEX_V2),
            "--vector-file",
            str(VECTOR_V2),
            "--incremental",
        ], env=env)

        current_cards = count_cards()
        print(f"cards_v2: {current_cards}")
        if current_cards <= last_cards:
            print("No further card growth in this round; stopping.")
            break
        last_cards = current_cards

    print("\n=== completion report ===")
    print(json.dumps(completion_report(), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
