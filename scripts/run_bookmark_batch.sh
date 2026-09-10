#!/usr/bin/env bash
# XKB bookmark batch — turn fetched bookmarks into knowledge cards.
#
# This is the answer to "shouldn't XKB do this by itself?". It did, at five
# items a night, while more arrived every day — so the queue grew instead of
# draining and the daily summary reported a backlog that was structurally
# guaranteed to keep growing. A worker that cannot keep up is not automation,
# it is a slow leak.
#
# The limit is now sized to clear a normal day and make progress on the
# backlog. Each item costs one model call, so the number is a spending decision
# as much as a throughput one; it is here, in one place, rather than implied by
# a default buried in an argument parser.
#
# Speaks only when it did something or when the queue is not shrinking. A job
# that reports the same backlog every morning teaches you to stop reading it.
#
# Usage:
#   bash scripts/run_bookmark_batch.sh [--env-file FILE] [--limit N]

set -euo pipefail

SKILL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=scripts/_xkb_env.sh
source "$SKILL_DIR/scripts/_xkb_env.sh"
LOG_FILE="${XKB_BOOKMARK_LOG:-/tmp/xkb-bookmark-batch.log}"
LOCK_FILE="/tmp/xkb-bookmark-batch.lock"

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

# An explicit --env-file outranks whatever the scheduler put in the environment.
xkb_env_file_wins "$ENV_FILE"
export XKB_ENV_FILE="$ENV_FILE"

exec 9>"$LOCK_FILE"
if ! flock -n 9; then
  echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] bookmark batch skipped: another run is active." >>"$LOG_FILE"
  exit 0
fi

cd "$SKILL_DIR"
log() { echo "$*" >>"$LOG_FILE"; }

pending() {
  python3 - <<'PY' 2>/dev/null || echo "?"
import sys
sys.path.insert(0, "scripts")
import xkb_paths
from xkb_pending_work import uncarded_bookmarks
print(len(uncarded_bookmarks(xkb_paths.BOOKMARKS_DIR, xkb_paths.CARDS_DIR)))
PY
}

before=$(pending)
log "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] bookmark batch start (limit=$LIMIT, pending=$before)"

OUT=$(mktemp)
trap 'rm -f "$OUT"' EXIT

# 帳本：這一次收了多少、產出多少、壞了多少、還剩多少。log 是給人追細節的,
# 這一行是給機器問「昨晚這一階段做了什麼」的——那八天 0 產出之所以沒人發現,
# 就是因為沒有任何地方能回答那個問題。
ledger() {
  local produced="$1" failed="$2" pend="$3" reason="$4" okflag="$5"
  local args=(record --stage bookmark-batch)
  [[ "$before" =~ ^[0-9]+$ ]] && args+=(--intake "$before")
  args+=(--produced "$produced" --failed "$failed")
  [[ "$pend" =~ ^[0-9]+$ ]] && args+=(--pending "$pend")
  [[ -n "$reason" ]] && args+=(--reason "$reason")
  [[ "$okflag" == "not-ok" ]] && args+=(--not-ok)
  python3 scripts/xkb_ledger.py "${args[@]}" >>"$LOG_FILE" 2>&1 || true
}

# 失敗長什麼樣,一行就夠——分辨「模型設定壞了」和「這幾筆內容抓不到」靠的
# 就是這一行,而它原本只存在於 /tmp 的 log 裡,沒進任何摘要。
first_failure_reason() {
  sed -n 's/.*✗ failed: //p' "$OUT" | head -1 | cut -c1-200
}

set +e
python3 scripts/run_bookmark_worker.py --limit "$LIMIT" >"$OUT" 2>&1
status=$?
set -e
cat "$OUT" >>"$LOG_FILE"

# worker 對單筆失敗也回非零，而這裡原本直接結束——於是十九張成功的卡片
# 寫進了磁碟卻沒進語意索引，正是這支腳本註解裡說「最不該有的狀態」。
# 單筆失敗交給下面的 failed_count 報告；只有連 done 都讀不出來才算整批壞掉。
if [[ "$status" -ne 0 ]]; then
  log "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] worker exited $status — 仍會嘗試嵌入已產出的卡片"
fi

done_count=$(sed -n 's/.*done=\([0-9][0-9]*\).*/\1/p' "$OUT" | tail -1)
failed_count=$(sed -n 's/.*failed=\([0-9][0-9]*\).*/\1/p' "$OUT" | tail -1)

# 上面說「只有連 done 都讀不出來才算整批壞掉」，但那個判斷從來沒寫出來：
# worker 整支崩掉（沒印出任何 done=）時，這支腳本照樣以 0 結束，排程看到的
# 是一次成功的執行。真正的整批失敗要讓它以非零收場。
if [[ "$status" -ne 0 && -z "$done_count" ]]; then
  log "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] worker 整批失敗（exit $status，沒有任何 done= 輸出）"
  ledger 0 0 "$(pending)" "worker 整批失敗 exit $status: $(first_failure_reason)" not-ok
  exit "$status"
fi

done_count=${done_count:-0}
failed_count=${failed_count:-0}

# 新卡片要被嵌入才召回得到。這一步失敗就是整批失敗——寫進 wiki 卻沒進索引，
# 等於關鍵字查得到、語意查不到，那是這個系統最不該有的狀態。
if [[ "$done_count" -gt 0 ]]; then
  EMBED_ARGS=(--incremental)
  [[ -n "$ENV_FILE" ]] && EMBED_ARGS+=(--env-file "$ENV_FILE")
  set +e
  python3 scripts/build_vector_index.py "${EMBED_ARGS[@]}" >>"$LOG_FILE" 2>&1
  embed_status=$?
  set -e
  if [[ "$embed_status" -ne 0 ]]; then
    echo "XKB 書籤批次：產了 $done_count 張卡，但語意索引更新失敗（離開碼 $embed_status）。" >&2
    echo "這批卡片現在關鍵字查得到、語意查不到。詳見 $LOG_FILE" >&2
    ledger "$done_count" "$failed_count" "$(pending)" "語意索引更新失敗 exit $embed_status" not-ok
    exit "$embed_status"
  fi
fi

after=$(pending)
log "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] bookmark batch done (done=$done_count failed=$failed_count pending=$after)"
ledger "$done_count" "$failed_count" "$after" "$( [[ "$failed_count" -gt 0 ]] && first_failure_reason )" ok

if [[ "$failed_count" -gt 0 ]]; then
  echo "XKB 書籤批次：產了 $done_count 張卡，$failed_count 筆失敗，還有 $after 筆待處理。"
  exit 0
fi

# 佇列沒有變小才值得說。追不上是這支腳本存在的理由，所以它要看得見。
if [[ "$before" != "?" && "$after" != "?" && "$after" -ge "$before" && "$before" -gt 0 ]]; then
  echo "XKB 書籤批次：產了 $done_count 張卡，但待處理從 $before 變成 $after —— 進來的比消化的快，limit 需要調高。"
elif [[ "$done_count" -gt 0 ]]; then
  echo "XKB 書籤批次：產了 $done_count 張卡，還有 $after 筆待處理。"
fi
