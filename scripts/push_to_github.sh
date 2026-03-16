#!/bin/bash
# push_to_github.sh
# 將 x-knowledge-base skill 推送到獨立的 GitHub repo
# 用法：bash scripts/push_to_github.sh "commit message"
#
# 需要設定：GITHUB_TOKEN 環境變數（或 .secrets/x-knowledge-base.env 中）

set -euo pipefail

SKILL_DIR="${SKILL_DIR:-$(cd "$(dirname "$0")/.." && pwd)}"
GITHUB_REPO="https://x-access-token:${GITHUB_TOKEN:-}@github.com/Hidicence/x-knowledge-base.git"
COMMIT_MSG="${1:-chore: sync x-knowledge-base skill}"

echo "📦 x-knowledge-base → GitHub"
echo "   Commit: $COMMIT_MSG"
echo ""

# 暫存 .git 不存在時才 init
if [[ ! -d "$SKILL_DIR/.git" ]]; then
    git -C "$SKILL_DIR" init -b main
    git -C "$SKILL_DIR" remote add origin "$GITHUB_REPO"
    # 取得遠端歷史（保留 README 等遠端已有檔案）
    git -C "$SKILL_DIR" fetch origin main --depth=1 2>/dev/null || true
    git -C "$SKILL_DIR" reset origin/main 2>/dev/null || true
fi

# Stage 所有 skill 檔案（排除 __pycache__ 和 .env）
git -C "$SKILL_DIR" add \
    SKILL.md \
    .env.example \
    config/ \
    evals/ \
    references/ \
    scripts/ \
    tools/ \
    assets/ \
    2>/dev/null || true

# 檢查有沒有東西要 commit
if git -C "$SKILL_DIR" diff --cached --quiet; then
    echo "✅ 沒有需要推送的變更"
else
    git -C "$SKILL_DIR" commit -m "$COMMIT_MSG"
    git -C "$SKILL_DIR" push origin main 2>&1 || \
    git -C "$SKILL_DIR" push origin master:main 2>&1
    echo "✅ 推送完成"
fi

# 清掉暫時建立的 .git（保持 skill 目錄乾淨）
rm -rf "$SKILL_DIR/.git"
echo "🧹 已清理暫存 .git"
