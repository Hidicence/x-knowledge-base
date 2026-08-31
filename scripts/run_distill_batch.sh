#!/usr/bin/env bash
# XKB distillation batch — extract durable claims from the day into candidates.
#
# The last of the deterministic schedules that were agent prompts. This one had
# a second problem underneath: it ran fine every day and reported no insights,
# because the reader was pointed at the OpenClaw-era trace inbox that nothing
# has written to since 2026-08-24. Fixed on 08-31; the same three days then
# yielded 41 insights. A job that succeeds while doing nothing is the failure
# mode this whole repository keeps paying for.
#
# So this script says what it actually found, including the case that matters
# most: it ran, and there was nothing to read.
#
# Usage:
#   bash scripts/run_distill_batch.sh --label afternoon [--env-file FILE] [--days N]

set -euo pipefail

SKILL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOG_FILE="${XKB_DISTILL_LOG:-/tmp/xkb-distill-batch.log}"
LOCK_FILE="/tmp/xkb-distill-batch.lock"

ENV_FILE="${XKB_ENV_FILE:-}"
LABEL="batch"
DAYS=1
while [[ $# -gt 0 ]]; do
  case $1 in
    --env-file) ENV_FILE="$2"; shift 2 ;;
    --label) LABEL="$2"; shift 2 ;;
    --days) DAYS="$2"; shift 2 ;;
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
  echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] distill skipped: another run is active." >>"$LOG_FILE"
  exit 0
fi

cd "$SKILL_DIR"
OUT=$(mktemp)
trap 'rm -f "$OUT"' EXIT

set +e
python3 scripts/distill_memory_to_wiki.py --stage --auto-apply-high \
  --days "$DAYS" --label "$LABEL" >"$OUT" 2>&1
status=$?
set -e
cat "$OUT" >>"$LOG_FILE"

if [[ "$status" -ne 0 ]]; then
  echo "XKB 蒸餾（$LABEL）失敗：離開碼 $status（詳見 $LOG_FILE）" >&2
  exit "$status"
fi

inputs=$(sed -n 's/.*Loaded \([0-9][0-9]*\) input item(s).*/\1/p' "$OUT" | tail -1)
insights=$(sed -n 's/.*Insights found: \([0-9][0-9]*\).*/\1/p' "$OUT" | tail -1)
inputs=${inputs:-0}
insights=${insights:-0}

# 有素材卻抽不出任何洞見，偶爾正常——那天的記錄可能全是排程紀錄。
# 但「連素材都沒有」不正常：對話與每日筆記每天都該有東西，讀不到就是讀錯地方。
if [[ "$inputs" -eq 0 ]]; then
  echo "XKB 蒸餾（$LABEL）：最近 $DAYS 天讀不到任何對話或筆記。" >&2
  echo "捕捉那一端可能壞了，或讀取的路徑指錯地方（2026-08-24 就是這樣壞了七天）。" >&2
  exit 1
fi

if [[ "$insights" -gt 0 ]]; then
  echo "XKB 蒸餾（$LABEL）：從 $inputs 份素材抽出 $insights 條候選。"
fi
