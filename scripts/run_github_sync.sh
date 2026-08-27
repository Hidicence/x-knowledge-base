#!/bin/bash
# run_github_sync.sh
# Daily GitHub forks/stars sync into XKB knowledge cards.
#
# Usage:
#   bash scripts/run_github_sync.sh
#
# Environment:
#   OPENCLAW_WORKSPACE  — path to workspace (default: ~/.openclaw/workspace)
#   GITHUB_SYNC_FORKS   — "true" to sync forks (default: true)
#   GITHUB_SYNC_STARS   — "true" to sync stars (default: true)
#   XKB_ENV_FILE        — optional dotenv file used by all XKB provider entry points

set -euo pipefail

WORKSPACE="${OPENCLAW_WORKSPACE:-$HOME/.openclaw/workspace}"
# skill 目錄由腳本自身位置推導——不要拿資料路徑去推程式路徑（那是 VPS 的擺法）
SKILL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SCRIPT="$SKILL_DIR/scripts/fetch_github_repos.py"
LOG_FILE="/tmp/xkb-github-sync.log"
LOCK_FILE="/tmp/xkb-github-sync.lock"

# Do not let a slow API/embedding run overlap the next scheduled run.
exec 9>"$LOCK_FILE"
if ! flock -n 9; then
    echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] GitHub sync skipped: another run is active." | tee -a "$LOG_FILE"
    exit 0
fi

SYNC_FORKS="${GITHUB_SYNC_FORKS:-true}"
SYNC_STARS="${GITHUB_SYNC_STARS:-true}"

echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] Starting GitHub sync..." | tee -a "$LOG_FILE"

FLAGS=""
[ "$SYNC_FORKS" = "true" ] && FLAGS="$FLAGS --forks"
[ "$SYNC_STARS" = "true" ] && FLAGS="$FLAGS --stars"

if [ -z "$FLAGS" ]; then
    echo "[WARN] Neither forks nor stars enabled." | tee -a "$LOG_FILE"
    exit 0
fi

cd "$WORKSPACE" || { echo "[ERROR] workspace not found: $WORKSPACE" >&2; exit 1; }
test -f "$SCRIPT" || { echo "[ERROR] fetch script not found: $SCRIPT" >&2; exit 1; }
python3 "$SCRIPT" $FLAGS 2>&1 | tee -a "$LOG_FILE"
EXIT_CODE=${PIPESTATUS[0]}

# Exit code 2 = new cards were added — update vector index
if [ "$EXIT_CODE" -eq 2 ]; then
    echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] New cards detected, updating vector index..." | tee -a "$LOG_FILE"
    EMBEDDING_ENV_ARGS=()
    if [[ -n "${XKB_ENV_FILE:-}" ]]; then
        EMBEDDING_ENV_ARGS=(--env-file "$XKB_ENV_FILE")
    fi
    python3 "$SKILL_DIR/scripts/build_vector_index.py" --incremental "${EMBEDDING_ENV_ARGS[@]}" 2>&1 | tee -a "$LOG_FILE"
elif [ "$EXIT_CODE" -eq 0 ]; then
    echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] No new repos, skipping vector index update." | tee -a "$LOG_FILE"
else
    echo "[ERROR] fetch_github_repos.py exited with code $EXIT_CODE" | tee -a "$LOG_FILE"
    exit "$EXIT_CODE"
fi

echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] GitHub sync complete." | tee -a "$LOG_FILE"
