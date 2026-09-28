#!/bin/bash
# Search bookmarks using the same configured paths and index builder as ingestion.
set -euo pipefail
SKILL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
exec python3 - "$SKILL_DIR" "$@" <<'PY'
import contextlib
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(sys.argv[1]) / "scripts"))
import xkb_index
import xkb_paths

query = " ".join(sys.argv[2:]).strip()
if not query:
    print("Usage: search_bookmarks.sh <keywords>", file=sys.stderr)
    sys.exit(1)
print(f"Search: {query}\n================================\n")
index_file = xkb_paths.INDEX_FILE
if os.environ.get("AUTO_INDEX_UPDATE", "1") == "1" or not index_file.exists():
    with contextlib.redirect_stdout(sys.stderr):
        rebuilt = xkb_index.rebuild()
    if not rebuilt:
        print("[WARN] Index rebuild failed; results may be incomplete.", file=sys.stderr)

if not index_file.exists():
    # Preserve the full-text fallback when no derived index is available.
    matches = []
    for path in sorted(xkb_paths.BOOKMARKS_DIR.rglob("*.md")):
        try:
            content = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if query.lower() in content.lower():
            matches.append(path)
            print(path)
    print(f"{len(matches)} matching bookmark files (full-text fallback)")
    sys.exit(0)

terms = [t.lower() for t in query.split() if t.strip()]
data = json.loads(index_file.read_text(encoding="utf-8"))

items = data.get('items', [])
matches = []

for item in items:
    title = (item.get('title') or '').lower()
    category = (item.get('category') or '').lower()
    tags = ' '.join(item.get('tags') or []).lower()
    summary = (item.get('summary') or '').lower()
    blob = (item.get('searchable') or '').lower()

    if not all(term in blob for term in terms):
        continue

    score = 0
    for term in terms:
        if term in title:
            score += 6
        if term in tags:
            score += 5
        if term in category:
            score += 3
        if term in summary:
            score += 2
        if term in blob:
            score += 1

    item['_score'] = score
    matches.append(item)

if not matches:
    print("❌ 沒有找到相關書籤")
    sys.exit(0)

matches.sort(key=lambda x: x.get('_score', 0), reverse=True)

for i, item in enumerate(matches[:30], start=1):
    title = item.get('title') or '(untitled)'
    category = item.get('category') or 'general'
    path = item.get('relative_path') or item.get('path')
    summary = (item.get('summary') or '').replace('\n', ' ')

    score = item.get('_score', 0)
    print(f"📄 [{i}] {title}")
    print(f"   分類: {category}  ｜  分數: {score}")
    print(f"   檔案: {path}")
    if summary:
        print(f"   摘要: {summary[:120]}...")
    print()

print("================================")
print(f"✅ 共找到 {len(matches)} 個相關書籤（索引模式）")
PY
