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


# worker 只會處理 todo/processing。failed 與 skipped 是終點：
# sync_tiege_queue 明文「保留 failed/skipped 狀態，由操作者自行重設」，
# 也就是沒有任何排程會再碰它們。
RETRYABLE_STATUSES = {"todo", "processing"}
TERMINAL_STATUSES = {"failed", "skipped"}


def pending_breakdown(bookmarks_dir: Path, cards_dir: Path,
                      queue_path: Path | None = None) -> dict[str, int]:
    """待轉的書籤分成「還會被處理的」和「卡住的」。

    這兩種混在同一個數字裡會產生錯的建議。2026-09-10 佇列裡剩 17 筆，全部
    是 failed(9) + skipped(6) + 不在佇列(2)——沒有任何一筆在排隊，而批次的
    摘要照樣說「進來的比消化的快，limit 需要調高」。調高 limit 對這 17 筆
    完全沒有作用，而且它會每天這樣說一次。

    卡住的那些也不會自己好：那 9 筆是模型設定壞掉那八天留下的，根因修好之
    後它們仍然停在 failed，要人手動重設才會重跑。所以它們需要的不是一個數
    字，是一個決定。
    """
    queue_path = queue_path or xkb_paths.QUEUE_PATH
    uncarded = uncarded_bookmarks(bookmarks_dir, cards_dir)

    status_by_id: dict[str, str] = {}
    try:
        data = json.loads(queue_path.read_text(encoding="utf-8"))
        for item in (data.get("items", []) if isinstance(data, dict) else data):
            key = str(item.get("id") or item.get("tweet_id") or "").strip()
            if key:
                status_by_id[key] = str(item.get("status") or "")
    except (OSError, ValueError):
        # 佇列讀不到時，全部算「不在佇列」而不是假裝它們在排隊。
        status_by_id = {}

    counts = {"total": len(uncarded), "queued": 0, "stuck": 0,
              "skipped": 0, "unqueued": 0}
    for path in uncarded:
        status = status_by_id.get(path.stem)
        if status in RETRYABLE_STATUSES:
            counts["queued"] += 1
        elif status == "failed":
            counts["stuck"] += 1
        elif status == "skipped":
            counts["skipped"] += 1
        else:
            counts["unqueued"] += 1

    # 還沒進佇列的也算會被處理：每次批次開頭都會跑一次佇列同步，把新的書籤
    # 檔加進去。把它們算成「不會被處理」，等於讓剛抓下來的書籤在報表上消失。
    counts["actionable"] = counts["queued"] + counts["unqueued"]
    return counts


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
