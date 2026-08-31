#!/usr/bin/env bash
# XKB candidate governance — absorb what clears the gates, then make it findable.
#
# Governance itself was never the fragile part. The step after it is: anything
# written into the wiki has to be embedded, or it is reachable by keyword and
# invisible to semantic recall, which is the whole point of the system.
#
# On 2026-08-31 that step failed with "GEMINI_API_KEY is required" and the run
# still reported success. The key was on the machine the entire time; the job
# was an agent prompt with an empty environment, so nothing passed it. Same
# shape as the 2026-08-28 ingestion failure and the two wiki-import jobs: a
# deterministic step, handed to a model, with no way to tell a real run from a
# reported one.
#
# So the embedding failure is fatal here. A governance batch that wrote to the
# wiki and could not embed it has not finished, and must say so.
#
# Usage:
#   bash scripts/run_candidate_governance.sh [--env-file FILE] [--limit N]

set -euo pipefail

SKILL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOG_FILE="${XKB_GOVERNANCE_LOG:-/tmp/xkb-governance.log}"
LOCK_FILE="/tmp/xkb-governance.lock"

ENV_FILE="${XKB_ENV_FILE:-}"
LIMIT=20
while [[ $# -gt 0 ]]; do
  case $1 in
    --env-file) ENV_FILE="$2"; shift 2 ;;
    --limit) LIMIT="$2"; shift 2 ;;
    *) echo "Unknown arg: $1" >&2; exit 1 ;;
  esac
done

if [[ -n "$ENV_FILE" && ! -f "$ENV_FILE" ]]; then
  echo "[ERROR] XKB env file not found: $ENV_FILE" >&2
  exit 1
fi
export XKB_ENV_FILE="$ENV_FILE"

exec 9>"$LOCK_FILE"
if ! flock -n 9; then
  echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] governance skipped: another run is active." >>"$LOG_FILE"
  exit 0
fi

cd "$SKILL_DIR"
log() { echo "$*" >>"$LOG_FILE"; }

log "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] governance start (limit=$LIMIT)"

GOV_OUT=$(mktemp)
GOV_ERR=$(mktemp)
trap 'rm -f "$GOV_OUT" "$GOV_ERR"' EXIT

# stdout 是 JSON，stderr 不是。原本合在一起，於是任何一行警告——包括這一天
# 才加上的 xkb_failures 提示——都會讓解析失敗，腳本在嵌入之前就結束，
# 留下的正是它存在要防止的那個狀態：進了 wiki、沒進索引。
set +e
python3 scripts/xkb_review.py --governance --limit "$LIMIT" --write-governance \
  >"$GOV_OUT" 2>"$GOV_ERR"
gov_status=$?
set -e
cat "$GOV_OUT" "$GOV_ERR" >>"$LOG_FILE"

if [[ "$gov_status" -ne 0 ]]; then
  echo "XKB 候選治理失敗：離開碼 $gov_status（詳見 $LOG_FILE）" >&2
  exit "$gov_status"
fi

# The CLI emits JSON; read it as JSON. A regex over the text would also match
# "near_duplicates" or a promoted id inside a list and report a number that
# looks plausible and is wrong.
promoted=$(python3 -c '
import json, sys
try:
    print(json.load(open(sys.argv[1]))["stats"]["promoted"])
except Exception:
    print("?")
' "$GOV_OUT")

if [[ "$promoted" == "?" ]]; then
  echo "XKB 候選治理：讀不出治理結果，無法確認吸收了幾筆（詳見 $LOG_FILE）" >&2
  exit 1
fi

# Anything promoted is now in the wiki. Until it is embedded it is findable by
# keyword and invisible to recall, so this is not an optional tail step.
log "> Embedding what governance wrote..."
EMBED_ARGS=(--incremental)
[[ -n "$ENV_FILE" ]] && EMBED_ARGS+=(--env-file "$ENV_FILE")

set +e
python3 scripts/build_vector_index.py "${EMBED_ARGS[@]}" >>"$LOG_FILE" 2>&1
embed_status=$?
set -e

if [[ "$embed_status" -ne 0 ]]; then
  echo "XKB 候選治理：已吸收 $promoted 筆，但語意索引更新失敗（離開碼 $embed_status）。" >&2
  echo "這批內容現在關鍵字查得到、語意查不到。詳見 $LOG_FILE" >&2
  exit "$embed_status"
fi

log "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] governance done (promoted=$promoted)"

# Silent on a quiet night. The 09:00 summary carries the standing counts; a job
# that says "nothing happened" every morning is a job that stops being read.
if [[ "$promoted" -gt 0 ]]; then
  echo "XKB 候選治理：吸收 $promoted 筆進 wiki，語意索引已更新。"
fi
