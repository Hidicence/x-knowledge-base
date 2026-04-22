#!/bin/bash
# fetch_bookmarks.sh - 從 Twitter/X 抓書籤（輸出 tweet ID）
# 依賴：bird CLI

set -euo pipefail

WORKSPACE_DIR="${WORKSPACE_DIR:-${OPENCLAW_WORKSPACE:-$HOME/.openclaw/workspace}}"
BOOKMARKS_DIR="${BOOKMARKS_DIR:-$WORKSPACE_DIR/memory/bookmarks}"
BOOKMARKS_TMP_FILE="${BOOKMARKS_TMP_FILE:-/tmp/new_bookmarks.txt}"
PROCESSED_FILE="$BOOKMARKS_DIR/urls.txt"
FETCH_MODE="${FETCH_MODE:-incremental}"   # incremental | full
FETCH_DAYS="${FETCH_DAYS:-28}"
MAX_PAGES="${MAX_PAGES:-5}"
SKIP_DEDUP="${SKIP_DEDUP:-0}"

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
echo "   mode=$FETCH_MODE  days=$FETCH_DAYS  max_pages=$MAX_PAGES"

# 抓取 JSON 格式
BOOKMARKS_JSON=$(bird --auth-token "$BIRD_AUTH_TOKEN" --ct0 "$BIRD_CT0" bookmarks --all --max-pages "$MAX_PAGES" --json 2>/dev/null || echo '{"tweets":[]}')

# 解析 tweet IDs（incremental 才套 days cutoff；full 不套）
printf "%s" "$BOOKMARKS_JSON" | python3 -c "
import json, os, sys
from datetime import datetime, timedelta, timezone

data = json.load(sys.stdin)
mode = os.environ.get('FETCH_MODE', 'incremental')
days = int(os.environ.get('FETCH_DAYS', '28'))
cutoff = datetime.now(timezone.utc) - timedelta(days=days)

for tweet in data.get('items', data.get('tweets', [])):
    tid = str(tweet.get('id', '') or '').strip()
    if not tid:
        continue
    if mode == 'full':
        print(tid)
        continue
    created = tweet.get('createdAt', '')
    try:
        dt = datetime.strptime(created, '%a %b %d %H:%M:%S %z %Y').astimezone(timezone.utc)
        if dt >= cutoff:
            print(tid)
    except Exception:
        pass
" | sort -u > "$BOOKMARKS_TMP_FILE"

# 去除已處理的（full import 可透過 SKIP_DEDUP=1 關閉）
if [[ "$SKIP_DEDUP" != "1" ]]; then
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
fi

NEW_COUNT=$(wc -l < "$BOOKMARKS_TMP_FILE")
echo "✅ 新書籤: $NEW_COUNT 個"
echo "📁 列表: $BOOKMARKS_TMP_FILE"
