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

echo "> Syncing enriched cards into the search index..." | tee -a "$LOG_FILE"
python3 scripts/sync_enriched_index.py 2>&1 | tee -a "$LOG_FILE"

echo "> Updating the semantic vector index..." | tee -a "$LOG_FILE"
EMBED_OUT=$(mktemp)
trap 'rm -f "$EMBED_OUT"' EXIT
set +e
python3 scripts/build_vector_index.py "${EMBED_ARGS[@]}" >"$EMBED_OUT" 2>&1
embed_status=$?
set -e
tee -a "$LOG_FILE" <"$EMBED_OUT"
if [[ "$embed_status" -ne 0 ]]; then
  echo "[ERROR] build_vector_index exited $embed_status" | tee -a "$LOG_FILE" >&2
  exit "$embed_status"
fi

# The failure this script was written for: a clean exit with nothing written.
# An empty queue is a legitimate no-op, though, and demanding a rewrite there
# would fail every run once the index has caught up.
pending=$(sed -n 's/.*To embed: \([0-9][0-9]*\).*/\1/p' "$EMBED_OUT" | tail -1)
if [[ -z "$pending" ]]; then
  echo "[ERROR] could not read the embedding queue size from the builder output." | tee -a "$LOG_FILE" >&2
  exit 1
fi

if [[ "$pending" -eq 0 ]]; then
  echo "  index already current — nothing to embed this run" | tee -a "$LOG_FILE"
else
  if [[ ! -f "$VECTOR_FILE" ]]; then
    echo "[ERROR] vector index missing after a successful build: $VECTOR_FILE" | tee -a "$LOG_FILE" >&2
    exit 1
  fi
  written_at=$(stat -c %Y "$VECTOR_FILE")
  if [[ "$written_at" -lt "$started_at" ]]; then
    echo "[ERROR] $pending items were queued but the index was not rewritten by this run" | tee -a "$LOG_FILE" >&2
    echo "        (last write $(date -u -d "@$written_at" +%Y-%m-%dT%H:%M:%SZ))." | tee -a "$LOG_FILE" >&2
    exit 1
  fi
fi

# The scheduler entry this replaced also had an agent check whether anything
# from the overnight syncs had failed to land. That was worth keeping, but it
# is a count rather than a judgement, so it belongs here: a source that stops
# producing cards shows up in the delivered output instead of being noticed
# weeks later.
echo "> Pending work:" | tee -a "$LOG_FILE"
python3 scripts/xkb_pending_work.py 2>&1 | tee -a "$LOG_FILE" \
  || echo "  (pending-work report unavailable)" | tee -a "$LOG_FILE"

echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] ingestion batch done" | tee -a "$LOG_FILE"
