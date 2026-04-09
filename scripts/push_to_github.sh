#!/bin/bash
# push_to_github.sh
# 將 x-knowledge-base skill 推送到獨立的 GitHub repo
# 使用 gh CLI 認證（需先執行 gh auth login）
#
# 用法：
#   bash scripts/push_to_github.sh "commit message"

set -euo pipefail

SKILL_DIR="${SKILL_DIR:-$(cd "$(dirname "$0")/.." && pwd)}"
COMMIT_MSG="${1:-chore: sync x-knowledge-base skill}"

GITHUB_REPO="https://github.com/Hidicence/x-knowledge-base.git"
TEMP_GIT=0

# ── 確認 gh CLI 已登入 ────────────────────────────────────────────────────────
if ! gh auth status &>/dev/null; then
    echo "❌ gh CLI 未登入" >&2
    echo "   請先執行：gh auth login" >&2
    exit 1
fi

# ── trap：只清除本腳本建立的暫存 .git，避免誤刪真正 repo ───────────────────
cleanup() {
    if [[ "$TEMP_GIT" == "1" && -d "$SKILL_DIR/.git" ]]; then
        rm -rf "$SKILL_DIR/.git"
        echo "🧹 已清理暫存 .git"
    fi
}
trap cleanup EXIT

echo "📦 x-knowledge-base → GitHub"
echo "   Commit : $COMMIT_MSG"
echo "   Skill  : $SKILL_DIR"
echo ""

if [[ -d "$SKILL_DIR/.git" ]]; then
    echo "❌ 偵測到 $SKILL_DIR 已經是一個 git repo；為避免誤覆蓋或誤刪 metadata，腳本停止。" >&2
    echo "   請改在乾淨的 skill 複本上執行，或先移除/搬走既有 .git 後再重試。" >&2
    exit 1
fi

# ── 暫時 init、對齊遠端 main ─────────────────────────────────────────────────
git -C "$SKILL_DIR" init -b main
TEMP_GIT=1
git -C "$SKILL_DIR" remote add origin "$GITHUB_REPO"

if ! git -C "$SKILL_DIR" fetch origin main --depth=1 2>&1; then
    echo "❌ fetch 失敗：請確認 gh 已登入且 repo 可存取" >&2
    exit 1
fi
git -C "$SKILL_DIR" reset origin/main

# ── 嚴格檢查：若 skill 目錄有未追蹤或未提交變更，先停止 ───────────────
if [[ -n "$(git -C "$SKILL_DIR" status --porcelain)" ]]; then
    echo "❌ 偵測到 skill 目錄有未提交變更；為避免把不相干修改一起推上 GitHub，腳本停止。" >&2
    echo "   請先在獨立複本整理好要推的內容，或明確建立 commit 後再推。" >&2
    exit 1
fi

# ── Stage 指定 skill 檔案（排除 .env、__pycache__、runtime state）──────────
git -C "$SKILL_DIR" add \
    SKILL.md \
    .env.example \
    README.md \
    config/ \
    evals/ \
    references/ \
    scripts/ \
    tools/ \
    assets/ \
    2>/dev/null || true

# ── Commit & push ────────────────────────────────────────────────────────────
if git -C "$SKILL_DIR" diff --cached --quiet; then
    echo "✅ 沒有需要推送的變更"
else
    git -C "$SKILL_DIR" commit -m "$COMMIT_MSG"

    if ! git -C "$SKILL_DIR" push origin HEAD:main 2>&1; then
        echo "❌ push 失敗" >&2
        echo "   可能原因：gh 認證過期 / 遠端有更新的 commit" >&2
        echo "   請先手動確認 GitHub 狀態後重試" >&2
        exit 1
    fi

    echo "✅ 推送完成 → https://github.com/Hidicence/x-knowledge-base"
fi

# cleanup 由 trap 負責，此處不需重複呼叫
