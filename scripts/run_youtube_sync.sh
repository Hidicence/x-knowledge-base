#!/bin/bash
# YouTube 播放清單自動同步腳本
# 每日執行：抓新影片 → 生成知識卡 → 更新語意索引

set -o pipefail

# Use HOME-relative default instead of hardcoded /root/
OPENCLAW_HOME="${OPENCLAW_HOME:-$HOME/.openclaw}"
WORKSPACE="${OPENCLAW_WORKSPACE:-$OPENCLAW_HOME/workspace}"
# skill 目錄由腳本自身位置推導——不要拿資料路徑去推程式路徑（那是 VPS 的擺法）
SKILL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOG_FILE="${XKB_YOUTUBE_LOG:-/tmp/xkb-youtube-sync.log}"

# Add user-local bin to PATH (yt-dlp is often installed there)
export PATH="$PATH:$HOME/.local/bin"

# Credentials/configuration are injected by the caller (process environment or
# XKB_ENV_FILE); this wrapper never reads private host paths.

echo "[$(date '+%Y-%m-%d %H:%M')] YouTube sync start" >> "$LOG_FILE"

cd "$SKILL_DIR"

# 1. 抓新影片並生成知識卡
python3 scripts/fetch_youtube_playlist.py 2>&1 | tee -a "$LOG_FILE"

# 2. 更新語意索引（增量）
VECTOR_INDEX="$WORKSPACE/memory/bookmarks/vector_index.json"
if [[ -f "$VECTOR_INDEX" ]]; then
    EMBEDDING_ARGS=(--incremental)
    if [[ -n "${XKB_ENV_FILE:-}" ]]; then
        EMBEDDING_ARGS+=(--env-file "$XKB_ENV_FILE")
    fi
    EMBEDDING_PROVIDER=gemini EMBEDDING_MODEL=gemini-embedding-2-preview \
        python3 scripts/build_vector_index.py "${EMBEDDING_ARGS[@]}" 2>&1 | tee -a "$LOG_FILE"
fi

echo "[$(date '+%Y-%m-%d %H:%M')] YouTube sync done" >> "$LOG_FILE"
