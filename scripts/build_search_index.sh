#!/bin/bash
# build_search_index.sh - 建立 / 增量更新 search_index.json（加速搜尋）

set -euo pipefail

WORKSPACE_DIR="${WORKSPACE_DIR:-${OPENCLAW_WORKSPACE:-$HOME/.openclaw/workspace}}"
BOOKMARKS_DIR="${BOOKMARKS_DIR:-$WORKSPACE_DIR/memory/bookmarks}"
INDEX_FILE="${INDEX_FILE:-$BOOKMARKS_DIR/search_index.json}"
MODE="${1:-full}"  # full | --incremental

mkdir -p "$(dirname "$INDEX_FILE")"

python3 - "$BOOKMARKS_DIR" "$INDEX_FILE" "$MODE" <<'PY'
import json
import re
import sys
from pathlib import Path

bookmarks_dir = Path(sys.argv[1])
index_file = Path(sys.argv[2])
mode = sys.argv[3]
incremental = mode == "--incremental"


def parse_record(f: Path):
    text = f.read_text(encoding="utf-8", errors="ignore")

    title = ""
    m = re.search(r'^title:\s*"?(.+?)"?\s*$', text, re.MULTILINE)
    if m:
        title = m.group(1).strip()
    if not title:
        m = re.search(r'^#\s+(.+)$', text, re.MULTILINE)
        if m:
            title = m.group(1).strip()
    if not title:
        title = f.stem

    category = "general"
    m = re.search(r'^category:\s*"?(.+?)"?\s*$', text, re.MULTILINE)
    if m:
        category = m.group(1).strip()

    tags = []
    m = re.search(r'^tags:\s*\[(.*?)\]\s*$', text, re.MULTILINE)
    if m:
        tags = [t.strip().strip('"\'') for t in m.group(1).split(',') if t.strip()]
    if not tags:
        tags = re.findall(r"#([\w\-\u4e00-\u9fff]+)", text)

    summary = ""
    m = re.search(r'##\s*📌\s*一句話摘要\s*\n+(.+)', text)
    if m:
        summary = m.group(1).strip()
    if not summary:
        m = re.search(r'##\s*📝\s*AI\s*濃縮\s*\n+(.+)', text)
        if m:
            summary = m.group(1).strip()[:200]

    # extract source_url at index time (avoids re-reading file at recall time)
    source_url = ""
    for _pat in [
        r'^source_url:\s*"?([^"\n]+)"?\s*$',
        r'^original_url:\s*"?([^"\n]+)"?\s*$',
        r'\*\*原始連結\*\*：\s*(\S+)',
        r'https://x\.com/\S+',
        r'https://twitter\.com/\S+',
    ]:
        _mu = re.search(_pat, text, re.MULTILINE)
        if _mu:
            source_url = (_mu.group(1) if _mu.groups() else _mu.group(0)).strip().strip('"')
            break

    searchable = "\n".join([
        title,
        category,
        " ".join(tags),
        summary,
        text[:2000],
    ])

    st = f.stat()
    return {
        "path": str(f),
        "relative_path": str(f.relative_to(bookmarks_dir)),
        "title": title,
        "category": category,
        "tags": tags,
        "summary": summary,
        "source_url": source_url,
        "searchable": searchable,
        "mtime": int(st.st_mtime),
        "size": int(st.st_size),
    }


files = []
for f in bookmarks_dir.rglob("*.md"):
    if f.name.startswith("."):
        continue
    if f.name in {"INDEX.md"}:
        continue
    if "inbox" in f.parts:
        continue
    files.append(f)

if not incremental or not index_file.exists():
    records = [parse_record(f) for f in files]
    status = "full"
else:
    old = json.loads(index_file.read_text(encoding="utf-8"))
    old_items = old.get("items", [])
    by_rel = {it.get("relative_path"): it for it in old_items if it.get("relative_path")}

    current_rel_set = set()
    changed = 0

    for f in files:
        rel = str(f.relative_to(bookmarks_dir))
        current_rel_set.add(rel)
        st = f.stat()
        old_item = by_rel.get(rel)

        if (
            old_item is None
            or int(old_item.get("mtime", -1)) != int(st.st_mtime)
            or int(old_item.get("size", -1)) != int(st.st_size)
        ):
            by_rel[rel] = parse_record(f)
            changed += 1

    # 移除已刪除檔案
    removed_keys = [k for k in by_rel.keys() if k not in current_rel_set]
    for k in removed_keys:
        by_rel.pop(k, None)

    records = list(by_rel.values())
    status = f"incremental (changed: {changed}, removed: {len(removed_keys)})"

records.sort(key=lambda x: x.get("relative_path", ""))
payload = {
    "version": "1.1",
    "bookmarks_dir": str(bookmarks_dir),
    "count": len(records),
    "mode": status,
    "items": records,
}

index_file.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
print(f"✅ index built: {index_file} ({len(records)} items, {status})")
PY
