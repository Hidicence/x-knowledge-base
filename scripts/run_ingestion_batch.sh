#!/usr/bin/env bash
# XKB ingestion batch — sync enriched cards into the search index, then embed.
#
# This exists because the two steps below used to be prose in a scheduler
# prompt marked "must run". On 2026-08-28 the run reported success and the
# vector index was never written, so new cards sat in the search index for
# a day and a half with no semantic vectors: findable by keyword, invisible
# to recall. An instruction an agent can report as done is not a pipeline.
#
# The builder's own exit code is not sufficient evidence either, so the last
# step checks that the index file was actually rewritten by this run.
#
# Usage:
#   bash scripts/run_ingestion_batch.sh [--env-file FILE]
#
# Credentials come from the process environment or XKB_ENV_FILE / --env-file,
# following the same contract as every other XKB entry point.

set -euo pipefail

SKILL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOG_FILE="${XKB_INGESTION_LOG:-/tmp/xkb-ingestion-batch.log}"
LOCK_FILE="/tmp/xkb-ingestion-batch.lock"

ENV_FILE="${XKB_ENV_FILE:-}"
while [[ $# -gt 0 ]]; do
  case $1 in
    --env-file) ENV_FILE="$2"; shift 2 ;;
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
  echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] ingestion batch skipped: another run is active." | tee -a "$LOG_FILE"
  exit 0
fi

cd "$SKILL_DIR"

VECTOR_FILE=$(python3 -c "
import sys
sys.path.insert(0, 'scripts')
import xkb_paths
print(xkb_paths.VECTOR_FILE.resolve())
")

started_at=$(date +%s)
echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] ingestion batch start" | tee -a "$LOG_FILE"

echo "▶ Syncing enriched cards into the search index..." | tee -a "$LOG_FILE"
python3 scripts/sync_enriched_index.py 2>&1 | tee -a "$LOG_FILE"

echo "▶ Updating the semantic vector index..." | tee -a "$LOG_FILE"
python3 scripts/build_vector_index.py "${EMBED_ARGS[@]}" 2>&1 | tee -a "$LOG_FILE"
embed_status=${PIPESTATUS[0]}
if [[ "$embed_status" -ne 0 ]]; then
  echo "[ERROR] build_vector_index exited $embed_status" | tee -a "$LOG_FILE" >&2
  exit "$embed_status"
fi

# The failure this script was written for: a clean exit with nothing written.
if [[ ! -f "$VECTOR_FILE" ]]; then
  echo "[ERROR] vector index missing after a successful build: $VECTOR_FILE" | tee -a "$LOG_FILE" >&2
  exit 1
fi
written_at=$(stat -c %Y "$VECTOR_FILE")
if [[ "$written_at" -lt "$started_at" ]]; then
  echo "[ERROR] vector index was not rewritten by this run (last write $(date -u -d "@$written_at" +%Y-%m-%dT%H:%M:%SZ))." | tee -a "$LOG_FILE" >&2
  echo "        The builder reported success without producing an index." | tee -a "$LOG_FILE" >&2
  exit 1
fi

echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] ingestion batch done — index written $(date -u -d "@$written_at" +%Y-%m-%dT%H:%M:%SZ)" | tee -a "$LOG_FILE"
