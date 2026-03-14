#!/bin/bash
# search_bookmarks.sh - 搜尋書籤功能（優先使用 search_index.json）

set -euo pipefail

WORKSPACE_DIR="${WORKSPACE_DIR:-${OPENCLAW_WORKSPACE:-$HOME/.openclaw/workspace}}"
SKILL_DIR="${SKILL_DIR:-$WORKSPACE_DIR/skills/x-knowledge-base}"
BOOKMARKS_DIR="${BOOKMARKS_DIR:-$WORKSPACE_DIR/memory/bookmarks}"
INDEX_FILE="${INDEX_FILE:-$BOOKMARKS_DIR/search_index.json}"
SEARCH_TERM="${1:-}"

if [[ -z "$SEARCH_TERM" ]]; then
    echo "用法: $0 <關鍵字>"
    echo "範例: $0 seedance"
    echo "範例: $0 openclaw seo"
    exit 1
fi

echo "🔍 搜尋: $SEARCH_TERM"
echo "================================"
echo ""

# 先嘗試靜默增量更新索引（可關閉：AUTO_INDEX_UPDATE=0）
AUTO_INDEX_UPDATE="${AUTO_INDEX_UPDATE:-1}"
BUILD_INDEX_SCRIPT="$SKILL_DIR/scripts/build_search_index.sh"
if [[ "$AUTO_INDEX_UPDATE" == "1" ]] && [[ -x "$BUILD_INDEX_SCRIPT" ]]; then
    "$BUILD_INDEX_SCRIPT" --incremental >/dev/null 2>&1 || true
fi

# 若索引仍不存在，再做一次全量建立
if [[ ! -f "$INDEX_FILE" ]] && [[ -x "$BUILD_INDEX_SCRIPT" ]]; then
    "$BUILD_INDEX_SCRIPT" >/dev/null 2>&1 || true
fi

if [[ -f "$INDEX_FILE" ]]; then
    python3 - "$INDEX_FILE" "$SEARCH_TERM" <<'PY'
import json
import sys

index_file = sys.argv[1]
query = sys.argv[2]
terms = [t.lower() for t in query.split() if t.strip()]

with open(index_file, 'r', encoding='utf-8') as f:
    data = json.load(f)

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
    exit 0
fi

# fallback: 沒索引就直接 grep
RESULTS=$(grep -r -i "$SEARCH_TERM" "$BOOKMARKS_DIR" --include="*.md" -l 2>/dev/null || echo "")

if [[ -z "$RESULTS" ]]; then
    echo "❌ 沒有找到相關書籤"
    exit 0
fi

COUNT=0
while read -r file; do
    [[ -z "$file" ]] && continue
    ((COUNT++)) || true

    title=$(grep "^title:" "$file" 2>/dev/null | head -1 | sed 's/title: *"\(.*\)"/\1/' || basename "$file")
    category=$(grep "^category:" "$file" 2>/dev/null | head -1 | sed 's/category: *//' || echo "general")
    snippet=$(grep -i -A1 "$SEARCH_TERM" "$file" 2>/dev/null | head -3 | tr '\n' ' ')

    echo "📄 [$COUNT] $title"
    echo "   分類: $category"
    echo "   檔案: ${file#$BOOKMARKS_DIR/}"
    echo "   內容: ${snippet:0:100}..."
    echo ""

done <<< "$RESULTS"

echo "================================"
echo "✅ 共找到 $COUNT 個相關書籤（全文模式）"
