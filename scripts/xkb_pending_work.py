#!/usr/bin/env python3
"""Report sources that have arrived but not yet become knowledge.

Runs at the end of the daily ingestion batch, so a source that stops
producing shows up in the delivered output instead of being noticed weeks
later. It replaced an agent instruction that asked for the same judgement
and could report success without making it.

The first version counted any bookmark with no file in ``cards/``, which
was wrong in a way worth remembering: the YouTube path writes its
nine-section card in place, in the bookmarks tree, so 23 finished cards
were reported as unprocessed work every single day. They were in the search
index and had vectors the whole time. A number that is red when nothing is
wrong stops being read, which is the same reason the empty-queue case in
this batch was fixed yesterday.

A file that declares ``type: knowledge-card`` has already been through the
step this is looking for, wherever it happens to live.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import xkb_paths

SELF_DECLARED_CARD = re.compile(r"^type:\s*knowledge-card", re.M)
FRONTMATTER_BYTES = 400


def is_knowledge_card(path: Path) -> bool:
    try:
        return bool(SELF_DECLARED_CARD.search(path.read_text(encoding="utf-8", errors="ignore")[:FRONTMATTER_BYTES]))
    except OSError:
        return False


def uncarded_bookmarks(bookmarks_dir: Path, cards_dir: Path) -> list[Path]:
    carded = {path.stem for path in cards_dir.glob("*.md")}
    return [
        path
        for path in sorted(bookmarks_dir.rglob("*.md"))
        if path.stem not in carded and not is_knowledge_card(path)
    ]


def unprocessed_transcripts(raw_dir: Path) -> list[str]:
    if not raw_dir.exists():
        return []
    pending = []
    for path in sorted(raw_dir.glob("*.json")):
        try:
            status = json.loads(path.read_text(encoding="utf-8")).get("status")
        except (OSError, ValueError):
            status = "unreadable"
        if status != "completed":
            pending.append(f"{path.name} ({status})")
    return pending


def main() -> int:
    pending = uncarded_bookmarks(xkb_paths.BOOKMARKS_DIR, xkb_paths.CARDS_DIR)
    print(f"  bookmarks not yet turned into knowledge: {len(pending)}")
    by_folder: dict[str, int] = {}
    for path in pending:
        by_folder[path.parent.name] = by_folder.get(path.parent.name, 0) + 1
    for folder, count in sorted(by_folder.items(), key=lambda kv: -kv[1])[:5]:
        print(f"    {folder}: {count}")

    transcripts = unprocessed_transcripts(xkb_paths.XKB_DATA_DIR / "youtube-raw")
    print(f"  youtube transcripts awaiting a card: {len(transcripts)}")
    for item in transcripts[:5]:
        print(f"    {item}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
