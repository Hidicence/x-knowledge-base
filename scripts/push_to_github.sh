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

# ── 確認 gh CLI 已登入 ────────────────────────────────────────────────────────
if ! gh auth status &>/dev/null; then
    echo "❌ gh CLI 未登入" >&2
    echo "   請先執行：gh auth login" >&2
    exit 1
fi

# ── trap：無論成功或失敗都清掉暫存 .git ─────────────────────────────────────
cleanup() {
    if [[ -d "$SKILL_DIR/.git" ]]; then
        rm -rf "$SKILL_DIR/.git"
        echo "🧹 已清理暫存 .git"
    fi
}
trap cleanup EXIT

echo "📦 x-knowledge-base → GitHub"
echo "   Commit : $COMMIT_MSG"
echo "   Skill  : $SKILL_DIR"
echo ""

# ── 暫時 init、對齊遠端 main ─────────────────────────────────────────────────
git -C "$SKILL_DIR" init -b main
git -C "$SKILL_DIR" remote add origin "$GITHUB_REPO"

if ! git -C "$SKILL_DIR" fetch origin main --depth=1 2>&1; then
    echo "❌ fetch 失敗：請確認 gh 已登入且 repo 可存取" >&2
    exit 1
fi
git -C "$SKILL_DIR" reset origin/main

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
