#!/usr/bin/env python3
"""
Initialize parallel v2 paths for x-knowledge-base rebuild.

Creates directories and placeholder runtime files without touching current production data.

Usage:
    python3 scripts/init_rebuild_v2.py
    python3 scripts/init_rebuild_v2.py --dry-run
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

WORKSPACE = Path(os.getenv("OPENCLAW_WORKSPACE", os.getenv("WORKSPACE_DIR", str(Path.home() / ".openclaw" / "workspace"))))

DIRS = [
    WORKSPACE / "memory" / "bookmarks_v2",
    WORKSPACE / "memory" / "cards_v2",
    WORKSPACE / "memory" / "x-knowledge-base-v2",
    WORKSPACE / "memory" / "x-knowledge-base-v2" / "quarantine",
]

FILES = {
    WORKSPACE / "memory" / "bookmarks_v2" / "search_index_v2.json": {"items": []},
    WORKSPACE / "memory" / "bookmarks_v2" / "vector_index_v2.json": {"meta": {}, "vectors": {}, "text_hashes": {}},
    WORKSPACE / "memory" / "x-knowledge-base-v2" / "topic_profile_v2.json": {"top_categories": [], "top_tags": []},
    WORKSPACE / "memory" / "x-knowledge-base-v2" / "tiege-queue-v2.json": {"items": []},
    WORKSPACE / "memory" / "x-knowledge-base-v2" / "README-V2.txt": "Parallel rebuild workspace for x-knowledge-base v2. Do not overwrite production paths until validation passes.\n",
}


def main() -> int:
    parser = argparse.ArgumentParser(description="Initialize x-knowledge-base rebuild v2 scaffold")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    print("Creating directories:")
    for d in DIRS:
        print(f"- {d}")
        if not args.dry_run:
            d.mkdir(parents=True, exist_ok=True)

    print("\nCreating files:")
    for path, content in FILES.items():
        print(f"- {path}")
        if args.dry_run:
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, str):
            if not path.exists():
                path.write_text(content, encoding="utf-8")
        else:
            if not path.exists():
                path.write_text(json.dumps(content, ensure_ascii=False, indent=2), encoding="utf-8")

    print("\nDone.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
