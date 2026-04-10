#!/bin/bash
# YouTube 播放清單自動同步腳本
# 每日執行：抓新影片 → 生成知識卡 → 更新語意索引

# Use HOME-relative default instead of hardcoded /root/
OPENCLAW_HOME="${OPENCLAW_HOME:-$HOME/.openclaw}"
WORKSPACE="${OPENCLAW_WORKSPACE:-$OPENCLAW_HOME/workspace}"
SKILL_DIR="${SKILL_DIR:-$WORKSPACE/skills/x-knowledge-base}"
LOG_FILE="${XKB_YOUTUBE_LOG:-/tmp/xkb-youtube-sync.log}"

# Add user-local bin to PATH (yt-dlp is often installed there)
export PATH="$PATH:$HOME/.local/bin"

# 讀取 secrets
SECRETS_FILE="$SKILL_DIR/../../../.secrets/x-knowledge-base.env"
if [[ -f "$SECRETS_FILE" ]]; then
    source "$SECRETS_FILE"
fi

# 讀取 openclaw.json 的 API keys（如果 env var 未設定）
OPENCLAW_JSON="${OPENCLAW_JSON:-$OPENCLAW_HOME/openclaw.json}"
if [[ -z "$MINIMAX_API_KEY" && -f "$OPENCLAW_JSON" ]]; then
    export MINIMAX_API_KEY=$(python3 -c "import json; print(json.load(open('$OPENCLAW_JSON'))['env'].get('MINIMAX_API_KEY',''))" 2>/dev/null)
fi
if [[ -z "$GEMINI_API_KEY" && -f "$OPENCLAW_JSON" ]]; then
    export GEMINI_API_KEY=$(python3 -c "import json; print(json.load(open('$OPENCLAW_JSON'))['env'].get('GEMINI_API_KEY',''))" 2>/dev/null)
fi

echo "[$(date '+%Y-%m-%d %H:%M')] YouTube sync start" >> "$LOG_FILE"

cd "$SKILL_DIR"

# 1. 抓新影片並生成知識卡
python3 scripts/fetch_youtube_playlist.py 2>&1 | tee -a "$LOG_FILE"

# 2. 更新語意索引（增量）
VECTOR_INDEX="$WORKSPACE/memory/bookmarks/vector_index.json"
if [[ -f "$VECTOR_INDEX" ]]; then
    EMBEDDING_PROVIDER=gemini EMBEDDING_MODEL=gemini-embedding-2-preview     python3 scripts/build_vector_index.py --incremental 2>&1 | tee -a "$LOG_FILE"
fi

echo "[$(date '+%Y-%m-%d %H:%M')] YouTube sync done" >> "$LOG_FILE"
