#!/bin/bash
# sync_to_drive.sh - 將本地 bookmarks 同步到 Google Drive（rclone remote）

set -euo pipefail

WORKSPACE_DIR="${WORKSPACE_DIR:-${OPENCLAW_WORKSPACE:-$HOME/.openclaw/workspace}}"
BOOKMARKS_DIR="${BOOKMARKS_DIR:-$WORKSPACE_DIR/memory/bookmarks}"
RCLONE_REMOTE="${RCLONE_REMOTE:-pan-drive:OpenClaw-Bookmarks}"
DRY_RUN="${DRY_RUN:-0}"

if ! command -v rclone >/dev/null 2>&1; then
  echo "❌ 缺少 rclone"
  exit 1
fi

if [[ ! -d "$BOOKMARKS_DIR" ]]; then
  echo "❌ 本地書籤目錄不存在: $BOOKMARKS_DIR"
  exit 1
fi

echo "☁️ 同步本地書籤到雲端"
echo "   local : $BOOKMARKS_DIR"
echo "   remote: $RCLONE_REMOTE"

RCLONE_ARGS=(copy "$BOOKMARKS_DIR" "$RCLONE_REMOTE" \
  --include "*.md" \
  --exclude "inbox/**" \
  --create-empty-src-dirs \
  --update \
  --verbose)

if [[ "$DRY_RUN" == "1" ]]; then
  echo "🧪 DRY_RUN=1：只預覽，不實際上傳"
  RCLONE_ARGS+=(--dry-run)
fi

rclone "${RCLONE_ARGS[@]}"

echo "✅ Drive 同步完成"
