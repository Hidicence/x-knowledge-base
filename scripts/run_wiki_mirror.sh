#!/usr/bin/env bash
# Mirror the XKB wiki into the OpenClaw memory-wiki vault.
#
# XKB owns the knowledge; the vault is a copy that lets OpenClaw agents reach
# it through `openclaw wiki search`. One direction only — nothing here ever
# writes back — so if the vault is lost it is rebuilt by running this again.
#
# This used to be two scheduler jobs, each asking a model to run one fixed
# command and report whether it worked. That is the arrangement that let the
# 2026-08-28 ingestion "succeed" without writing anything: a report of success
# is not evidence of success, and a model call is a strange price to pay for a
# command with no decisions in it. So it is a script now, and it checks.
#
# The dependency on the `openclaw` binary is the only one XKB still has. It is
# stated loudly here rather than left to be discovered when the binary goes
# away and the mirror quietly stops being written.
#
# Usage:
#   bash scripts/run_wiki_mirror.sh [--verbose]

set -euo pipefail

VAULT="${XKB_WIKI_VAULT:-/root/.openclaw/wiki/memory-wiki}"
LOG_FILE="${XKB_WIKI_MIRROR_LOG:-/tmp/xkb-wiki-mirror.log}"
LOCK_FILE="/tmp/xkb-wiki-mirror.lock"
SKILL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

VERBOSE=0
[[ "${1:-}" == "--verbose" ]] && VERBOSE=1

exec 9>"$LOCK_FILE"
if ! flock -n 9; then
  echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] wiki mirror skipped: another run is active." >>"$LOG_FILE"
  exit 0
fi

log() { echo "$*" >>"$LOG_FILE"; }

if ! command -v openclaw >/dev/null 2>&1; then
  echo "XKB wiki 鏡像失敗：找不到 openclaw 指令。這是 XKB 唯一還依賴的外部程式，" \
       "它負責把 wiki 複製到 OpenClaw 的記憶庫，讓 agent 搜得到。" \
       "XKB 自己的召回不受影響。" >&2
  exit 1
fi

cd "$SKILL_DIR"

newest() {  # newest mtime under the given roots, 0 when there is nothing
  # awk rather than `sort | head`: head closes the pipe as soon as it has
  # its line, sort takes SIGPIPE for it, and under `pipefail` that kills
  # the run with 141 before anything has happened.
  find "$@" -type f -name '*.md' -printf '%T@\n' 2>/dev/null \
    | awk 'BEGIN{m=0} {t=int($1); if (t>m) m=t} END{print m}'
}

WIKI_DIR=$(python3 -c "
import sys
sys.path.insert(0, 'scripts')
import xkb_paths
print(xkb_paths.WIKI_DIR.resolve())
")

source_at=$(newest "$WIKI_DIR"); source_at=${source_at:-0}
vault_before=$(newest "$VAULT"); vault_before=${vault_before:-0}
started_at=$(date +%s)

log "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] wiki mirror start (source=$source_at vault=$vault_before)"

set +e
OUT=$(openclaw wiki unsafe-local import 2>&1)
status=$?
set -e
echo "$OUT" >>"$LOG_FILE"

if [[ "$status" -ne 0 ]]; then
  echo "XKB wiki 鏡像失敗：openclaw wiki unsafe-local import 離開碼 $status（詳見 $LOG_FILE）" >&2
  exit "$status"
fi

# The whole point of the script. A clean exit means nothing on its own; if the
# wiki had changes the vault had not seen, the vault must have been written.
if [[ "$source_at" -gt "$vault_before" ]]; then
  vault_after=$(newest "$VAULT"); vault_after=${vault_after:-0}
  if [[ "$vault_after" -lt "$started_at" ]]; then
    echo "XKB wiki 鏡像失敗：wiki 有更新，但這次執行沒有寫進 vault（$VAULT，詳見 $LOG_FILE）" >&2
    exit 1
  fi
  pages=$(find "$VAULT/sources" -type f -name '*.md' 2>/dev/null | wc -l)
  log "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] wiki mirror done ($pages pages)"
  echo "XKB wiki 鏡像：已更新，vault 現有 $pages 頁。"
  exit 0
fi

log "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] wiki mirror done (no source changes)"
if [[ "$VERBOSE" -eq 1 ]]; then
  echo "XKB wiki 鏡像：wiki 沒有變動，vault 已是最新。"
fi
