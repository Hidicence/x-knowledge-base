#!/bin/bash
# YouTube 播放清單自動同步腳本
# 每日執行：抓新影片 → 生成知識卡 → 更新語意索引

export PATH=$PATH:/root/.local/bin

SKILL_DIR="/root/.openclaw/workspace/skills/x-knowledge-base"
LOG_FILE="/tmp/xkb-youtube-sync.log"

# 讀取 secrets
SECRETS_FILE="$SKILL_DIR/../../../.secrets/x-knowledge-base.env"
if [[ -f "$SECRETS_FILE" ]]; then
    source "$SECRETS_FILE"
fi

# 讀取 openclaw.json 的 MINIMAX_API_KEY
export MINIMAX_API_KEY=$(python3 -c "import json; print(json.load(open('/root/.openclaw/openclaw.json'))['env'].get('MINIMAX_API_KEY',''))" 2>/dev/null)
export GEMINI_API_KEY=$(python3 -c "import json; print(json.load(open('/root/.openclaw/openclaw.json'))['env'].get('GEMINI_API_KEY',''))" 2>/dev/null)

echo "[$(date '+%Y-%m-%d %H:%M')] YouTube sync start" >> "$LOG_FILE"

cd "$SKILL_DIR"

# 1. 抓新影片並生成知識卡
python3 scripts/fetch_youtube_playlist.py 2>&1 | tee -a "$LOG_FILE"

# 2. 更新語意索引（增量）
if [[ -f "$SKILL_DIR/../../../memory/bookmarks/vector_index.json" ]]; then
    EMBEDDING_PROVIDER=gemini EMBEDDING_MODEL=gemini-embedding-2-preview     python3 scripts/build_vector_index.py --incremental 2>&1 | tee -a "$LOG_FILE"
fi

echo "[$(date '+%Y-%m-%d %H:%M')] YouTube sync done" >> "$LOG_FILE"
