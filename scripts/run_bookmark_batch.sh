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
    exit "$embed_status"
  fi
fi

after=$(pending)
log "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] bookmark batch done (done=$done_count failed=$failed_count pending=$after)"

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
