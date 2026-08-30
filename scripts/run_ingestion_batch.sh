#!/usr/bin/env bash
# XKB ingestion batch — sync enriched cards into the search index, then embed.
#
# This exists because the two steps below used to be prose in a scheduler
# prompt marked "must run". On 2026-08-28 the run reported success and the
# vector index was never written, so new cards sat in the search index for
# a day and a half with no semantic vectors: findable by keyword, invisible
# to recall. An instruction an agent can report as done is not a pipeline.
#
# The builder's own exit code is not sufficient evidence either, so when there
# was work to do the last step checks that the index file was actually
# rewritten by this run.
#
# Output: everything goes to the log; stdout carries a summary only when the
# run did something or something went wrong. The scheduler delivers stdout,
# and a job that reports "nothing happened" every single day trains you to
# stop reading it — which is the state the daily health summary is for.
#
# Usage:
#   bash scripts/run_ingestion_batch.sh [--env-file FILE] [--verbose]
#
# Credentials come from the process environment or XKB_ENV_FILE / --env-file,
# following the same contract as every other XKB entry point.

set -euo pipefail

SKILL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOG_FILE="${XKB_INGESTION_LOG:-/tmp/xkb-ingestion-batch.log}"
LOCK_FILE="/tmp/xkb-ingestion-batch.lock"

ENV_FILE="${XKB_ENV_FILE:-}"
VERBOSE=0
while [[ $# -gt 0 ]]; do
  case $1 in
    --env-file) ENV_FILE="$2"; shift 2 ;;
    --verbose) VERBOSE=1; shift ;;
    *) echo "Unknown arg: $1" >&2; exit 1 ;;
  esac
done

if [[ -n "$ENV_FILE" && ! -f "$ENV_FILE" ]]; then
  echo "[ERROR] XKB env file not found: $ENV_FILE" >&2
  exit 1
fi

EMBED_ARGS=(--incremental)
[[ -n "$ENV_FILE" ]] && EMBED_ARGS+=(--env-file "$ENV_FILE")

# A slow embedding run must not overlap the next scheduled one.
exec 9>"$LOCK_FILE"
if ! flock -n 9; then
  echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] ingestion batch skipped: another run is active." >>"$LOG_FILE"
  exit 0
fi

cd "$SKILL_DIR"

log() { echo "$*" >>"$LOG_FILE"; }
run() { "$@" >>"$LOG_FILE" 2>&1; }

VECTOR_FILE=$(python3 -c "
import sys
sys.path.insert(0, 'scripts')
import xkb_paths
print(xkb_paths.VECTOR_FILE.resolve())
")

started_at=$(date +%s)
log "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] ingestion batch start"

log "> Syncing enriched cards into the search index..."
run python3 scripts/sync_enriched_index.py

log "> Updating the semantic vector index..."
EMBED_OUT=$(mktemp)
trap 'rm -f "$EMBED_OUT"' EXIT
set +e
python3 scripts/build_vector_index.py "${EMBED_ARGS[@]}" >"$EMBED_OUT" 2>&1
embed_status=$?
set -e
cat "$EMBED_OUT" >>"$LOG_FILE"
if [[ "$embed_status" -ne 0 ]]; then
  echo "XKB 攝取批次失敗：向量索引建置離開碼 $embed_status（詳見 $LOG_FILE）" >&2
  exit "$embed_status"
fi

# The failure this script was written for: a clean exit with nothing written.
# An empty queue is a legitimate no-op, though, and demanding a rewrite there
# would fail every run once the index has caught up.
pending=$(sed -n 's/.*To embed: \([0-9][0-9]*\).*/\1/p' "$EMBED_OUT" | tail -1)
if [[ -z "$pending" ]]; then
  echo "XKB 攝取批次失敗：讀不出待轉數量，無法確認索引有沒有更新（詳見 $LOG_FILE）" >&2
  exit 1
fi

if [[ "$pending" -gt 0 ]]; then
  if [[ ! -f "$VECTOR_FILE" ]]; then
    echo "XKB 攝取批次失敗：索引檔不存在（$VECTOR_FILE）" >&2
    exit 1
  fi
  written_at=$(stat -c %Y "$VECTOR_FILE")
  if [[ "$written_at" -lt "$started_at" ]]; then
    echo "XKB 攝取批次失敗：有 $pending 筆待轉，但索引沒有被這次執行寫入（詳見 $LOG_FILE）" >&2
    exit 1
  fi
fi

log "> Pending work:"
python3 scripts/xkb_pending_work.py >>"$LOG_FILE" 2>&1 \
  || log "  (pending-work report unavailable)"

log "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] ingestion batch done"

# Speak only when there was something to say. Counts for the day live in the
# 09:00 summary; repeating "nothing changed" here every afternoon is how a
# channel stops being read.
if [[ "$pending" -gt 0 ]]; then
  echo "XKB 攝取批次：新增 $pending 筆語意向量。"
elif [[ "$VERBOSE" -eq 1 ]]; then
  echo "XKB 攝取批次：索引已是最新，沒有新增。"
fi
