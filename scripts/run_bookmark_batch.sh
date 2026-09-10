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

# 待轉的分成「還會被處理的」與「卡住的」。混成一個數字會給出錯的建議：佇列
# 裡只剩 failed/skipped 時，摘要照樣叫人「調高 limit」，而調高 limit 對它們
# 完全沒有作用——它們要的是一個決定，不是更多額度。
queued_now() {
  python3 - <<'PYQ' 2>/dev/null || echo ""
import sys
sys.path.insert(0, "scripts")
import xkb_paths
from xkb_pending_work import pending_breakdown
c = pending_breakdown(xkb_paths.BOOKMARKS_DIR, xkb_paths.CARDS_DIR)
print(f"{c['actionable']} {c['stuck']}")
PYQ
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

# 這一次做了什麼，只認 worker 自己那行摘要——見 scripts/xkb_batch_summary.py。
# 原本這裡是 sed 抓「最後一個 done=」，而佇列同步那行也有 done=，於是佇列沒
# 事做的晚上會把佇列的累計總數當成這次的產出。實測報過「產了 1575 張卡」，
# 而且因為 done_count > 0，還會多跑一次向量索引。
set +e
summary=$(python3 scripts/xkb_batch_summary.py <"$OUT" 2>>"$LOG_FILE")
summary_status=$?
set -e

# 讀不出結果 ≠ 產出 0。前者是壞了，後者是沒事做，而原本的 ${done_count:-0}
# 讓兩者長得一模一樣——正是這條管線八天沒被發現的那個毛病。
if [[ "$summary_status" -ne 0 ]]; then
  log "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] 讀不出 worker 的執行結果（worker exit $status）"
  ledger 0 0 "$(pending)" "讀不出 worker 的執行結果（worker exit $status）：$(first_failure_reason)" not-ok
  echo "XKB 書籤批次：讀不出 worker 的執行結果，無法確認這次做了什麼（詳見 $LOG_FILE）" >&2
  if [[ "$status" -ne 0 ]]; then exit "$status"; fi
  exit 1
fi

# summary 的格式是 `done=N failed=N outcome=X`，用 bash 自己的展開拆就好。
done_count=${summary#done=}
done_count=${done_count%% *}
failed_count=${summary#*failed=}
failed_count=${failed_count%% *}

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

read -r queued stuck <<<"$(queued_now)"
stuck_note=""
# 卡住的那些不會自己好，也沒有任何排程會再碰它們——sync_tiege_queue 明文寫著
# 「由操作者自行重設」。所以它們每次都要被說出來，而且要說清楚沒有人會處理。
if [[ "$stuck" =~ ^[0-9]+$ && "$stuck" -gt 0 ]]; then
  stuck_note="另有 $stuck 筆卡在失敗狀態，不會自動重試——要重跑得手動重設。"
fi

if [[ "$failed_count" -gt 0 ]]; then
  echo "XKB 書籤批次：產了 $done_count 張卡，$failed_count 筆失敗，還有 $queued 筆排隊中。$stuck_note"
  exit 0
fi

# 追不上是這支腳本存在的理由，所以它要看得見——但只有「還會被處理的」那些
# 算數。原本這裡比的是全部待轉，於是佇列裡只剩卡住的項目時，它每天都會叫人
# 調高 limit，而那對卡住的項目毫無作用。
if [[ "$queued" =~ ^[0-9]+$ && "$before" != "?" && "$after" != "?" \
      && "$after" -ge "$before" && "$queued" -gt 0 ]]; then
  echo "XKB 書籤批次：產了 $done_count 張卡，但排隊中仍有 $queued 筆 —— 進來的比消化的快，limit 需要調高。$stuck_note"
elif [[ "$done_count" -gt 0 ]]; then
  echo "XKB 書籤批次：產了 $done_count 張卡，還有 $queued 筆排隊中。$stuck_note"
elif [[ -n "$stuck_note" ]]; then
  echo "XKB 書籤批次：這次沒有東西要處理。$stuck_note"
fi
