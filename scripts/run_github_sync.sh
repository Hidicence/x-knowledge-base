#!/bin/bash
# run_github_sync.sh
# Daily GitHub forks/stars sync into XKB knowledge cards.
# Mirrors the pattern of run_youtube_sync.sh.
#
# Usage:
#   bash scripts/run_github_sync.sh
#
# Environment:
#   OPENCLAW_WORKSPACE  — path to workspace (default: ~/.openclaw/workspace)
#   LLM_API_KEY         — LLM API key (read from openclaw.json if not set)
#   GITHUB_SYNC_FORKS   — "true" to sync forks (default: true)
#   GITHUB_SYNC_STARS   — "true" to sync stars (default: true)

set -euo pipefail

WORKSPACE="${OPENCLAW_WORKSPACE:-$HOME/.openclaw/workspace}"
SKILL_DIR="$WORKSPACE/skills/x-knowledge-base"
SCRIPT="$SKILL_DIR/scripts/fetch_github_repos.py"
LOG_FILE="/tmp/xkb-github-sync.log"

SYNC_FORKS="${GITHUB_SYNC_FORKS:-true}"
SYNC_STARS="${GITHUB_SYNC_STARS:-true}"

echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] Starting GitHub sync..." | tee -a "$LOG_FILE"

# Build flags
FLAGS=""
[ "$SYNC_FORKS" = "true" ] && FLAGS="$FLAGS --forks"
[ "$SYNC_STARS" = "true" ] && FLAGS="$FLAGS --stars"

if [ -z "$FLAGS" ]; then
    echo "[WARN] Neither forks nor stars enabled. Set GITHUB_SYNC_FORKS or GITHUB_SYNC_STARS." | tee -a "$LOG_FILE"
    exit 0
fi

cd "$WORKSPACE"
python3 "$SCRIPT" $FLAGS 2>&1 | tee -a "$LOG_FILE"

# Update vector index incrementally if new cards were added
if grep -q "共新增 [1-9]" "$LOG_FILE" 2>/dev/null; then
    echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] Updating vector index..." | tee -a "$LOG_FILE"
    python3 "$SKILL_DIR/scripts/build_vector_index.py" --incremental 2>&1 | tee -a "$LOG_FILE"
fi

echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] GitHub sync complete." | tee -a "$LOG_FILE"
