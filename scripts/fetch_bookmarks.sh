#!/bin/bash
# fetch_bookmarks.sh - 從 Twitter/X 抓書籤（輸出 tweet ID）
# 依賴：bird CLI

set -euo pipefail

WORKSPACE_DIR="${WORKSPACE_DIR:-${OPENCLAW_WORKSPACE:-$HOME/.openclaw/workspace}}"
BOOKMARKS_DIR="${BOOKMARKS_DIR:-$WORKSPACE_DIR/memory/bookmarks}"
BOOKMARKS_TMP_FILE="${BOOKMARKS_TMP_FILE:-/tmp/new_bookmarks.txt}"
PROCESSED_FILE="$BOOKMARKS_DIR/urls.txt"

# 從環境變數讀取；若工作區有 secrets 檔則一併載入
SKILL_DIR="${SKILL_DIR:-$WORKSPACE_DIR/skills/x-knowledge-base}"
SECRETS_FILE="${SECRETS_FILE:-$WORKSPACE_DIR/.secrets/x-knowledge-base.env}"
if [[ -f "$SECRETS_FILE" ]]; then
  source "$SECRETS_FILE"
fi
BIRD_AUTH_TOKEN="${BIRD_AUTH_TOKEN:-}"
BIRD_CT0="${BIRD_CT0:-}"

if [[ -z "$BIRD_AUTH_TOKEN" || -z "$BIRD_CT0" ]]; then
  echo "❌ 缺少 BIRD_AUTH_TOKEN / BIRD_CT0"
  exit 1
fi

if ! command -v bird >/dev/null 2>&1; then
  echo "❌ 缺少 bird CLI（請先安裝）"
  exit 1
fi

mkdir -p "$BOOKMARKS_DIR"
: > "$BOOKMARKS_TMP_FILE"

echo "📥 開始抓取書籤..."

# 抓取 JSON 格式
BOOKMARKS_JSON=$(bird --auth-token "$BIRD_AUTH_TOKEN" --ct0 "$BIRD_CT0" bookmarks --all --max-pages 5 --json 2>/dev/null || echo '{"tweets":[]}')

# 解析 tweet IDs（4週內）
echo "$BOOKMARKS_JSON" | python3 -c "
import json, sys
from datetime import datetime, timedelta, timezone

data = json.load(sys.stdin)
cutoff = datetime.now(timezone.utc) - timedelta(days=28)

for tweet in data.get('items', data.get('tweets', [])):
    created = tweet.get('createdAt', '')
    try:
        dt = datetime.strptime(created, '%a %b %d %H:%M:%S %z %Y').astimezone(timezone.utc)
        if dt >= cutoff:
            tid = tweet.get('id', '')
            if tid:
                print(tid)
    except Exception:
        pass
" | sort -u > "$BOOKMARKS_TMP_FILE"

# 去除已處理的
if [[ -f "$PROCESSED_FILE" ]]; then
    # 優先使用 urls.txt（舊流程）
    comm -13 <(cut -d',' -f1 "$PROCESSED_FILE" | sort) "$BOOKMARKS_TMP_FILE" > "${BOOKMARKS_TMP_FILE}.tmp" 2>/dev/null || true
    mv "${BOOKMARKS_TMP_FILE}.tmp" "$BOOKMARKS_TMP_FILE" 2>/dev/null || true
else
    # fallback：若 urls.txt 不存在，直接用現有 markdown 檔名（tweet_id.md）去重
    EXISTING_IDS_TMP="/tmp/existing_bookmark_ids.txt"
    find "$BOOKMARKS_DIR" -type f -name '*.md' -printf '%f\n' \
      | sed 's/\.md$//' \
      | grep -E '^[0-9]{10,}$' \
      | sort -u > "$EXISTING_IDS_TMP" || true

    if [[ -s "$EXISTING_IDS_TMP" ]]; then
      comm -23 "$BOOKMARKS_TMP_FILE" "$EXISTING_IDS_TMP" > "${BOOKMARKS_TMP_FILE}.tmp" 2>/dev/null || true
      mv "${BOOKMARKS_TMP_FILE}.tmp" "$BOOKMARKS_TMP_FILE" 2>/dev/null || true
    fi
fi

NEW_COUNT=$(wc -l < "$BOOKMARKS_TMP_FILE")
echo "✅ 新書籤: $NEW_COUNT 個"
echo "📁 列表: $BOOKMARKS_TMP_FILE"
