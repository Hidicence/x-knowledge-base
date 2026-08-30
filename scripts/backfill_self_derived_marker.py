#!/usr/bin/env python3
"""Add the self-derived marker to wiki lines governance wrote without it.

Promotion wrote ``*(source: <staging file>#<n>)*`` while the recall penalty
looks for ``self-derived``. 913 claims distilled from Pan's own notes were
therefore competing at full weight against external evidence. The writer is
fixed; this repairs what it already wrote.

Only lines carrying an ``xkb-candidate:`` marker are touched, so nothing
written by another path is rewritten, and a line that already says
self-derived is left alone — running this twice changes nothing.

    python3 scripts/backfill_self_derived_marker.py            # report only
    python3 scripts/backfill_self_derived_marker.py --apply    # rewrite
"""
from __future__ import annotations

import argparse
import re
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import xkb_paths
import xkb_provenance
from xkb_provenance import MARKER

GOVERNANCE_LINE = xkb_provenance.CANDIDATE_MARKER_RE
PLAIN_SOURCE = "*(source: "
MARKED_SOURCE = f"*({MARKER} · source: "


def repair(text: str) -> tuple[str, int]:
    out = []
    changed = 0
    for line in text.splitlines(keepends=True):
        if (GOVERNANCE_LINE.search(line)
                and MARKER not in line
                and PLAIN_SOURCE in line):
            line = line.replace(PLAIN_SOURCE, MARKED_SOURCE, 1)
            changed += 1
        out.append(line)
    return "".join(out), changed


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="write the repaired files")
    args = parser.parse_args()

    topics = sorted(xkb_paths.WIKI_TOPICS_DIR.glob("*.md"))
    if not topics:
        print(f"no topic pages under {xkb_paths.WIKI_TOPICS_DIR}", file=sys.stderr)
        return 1

    backup_dir = None
    if args.apply:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        backup_dir = xkb_paths.WIKI_TOPICS_DIR.parent / f"topics-backup-{stamp}"
        backup_dir.mkdir(parents=True, exist_ok=False)

    total = 0
    for path in topics:
        original = path.read_text(encoding="utf-8")
        repaired, changed = repair(original)
        if not changed:
            continue
        total += changed
        print(f"  {changed:>4}  {path.name}")
        if args.apply:
            shutil.copy2(path, backup_dir / path.name)
            path.write_text(repaired, encoding="utf-8")

    if not total:
        print("nothing to repair — every governance line already carries the marker")
        return 0

    if args.apply:
        print(f"\nrepaired {total} lines; originals in {backup_dir}")
        print("re-run the ingestion batch so the changed sections are re-embedded")
    else:
        print(f"\n{total} lines would be repaired (--apply to write)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
