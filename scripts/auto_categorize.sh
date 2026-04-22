#!/bin/bash
# 自動分類書籤腳本
# 根據 config/category-rules.json 的規則自動分類到對應資料夾

set -euo pipefail

WORKSPACE_DIR="${WORKSPACE_DIR:-${OPENCLAW_WORKSPACE:-$HOME/.openclaw/workspace}}"
SKILL_DIR="${SKILL_DIR:-$WORKSPACE_DIR/skills/x-knowledge-base}"
BOOKMARKS_DIR="${BOOKMARKS_DIR:-$WORKSPACE_DIR/memory/bookmarks}"
INBOX_DIR="$BOOKMARKS_DIR/inbox"
RULES_FILE="${RULES_FILE:-$SKILL_DIR/config/category-rules.json}"

if [[ ! -f "$RULES_FILE" ]]; then
    echo "❌ 缺少分類規則檔: $RULES_FILE"
    exit 1
fi

default_category=$(python3 - <<PY
import json
from pathlib import Path
p = Path(r'''$RULES_FILE''')
data = json.loads(p.read_text(encoding='utf-8'))
print(data.get('default_category', '99-general'))
PY
)

echo "📂 開始自動分類..."
echo "🧭 規則檔: $RULES_FILE"

moved=0
for file in "$INBOX_DIR"/*.md; do
    [[ -e "$file" ]] || continue

    filename=$(basename "$file" .md)
    target_category=$(python3 - <<PY
import json
from pathlib import Path

rules_file = Path(r'''$RULES_FILE''')
source_file = Path(r'''$file''')

data = json.loads(rules_file.read_text(encoding='utf-8'))
text = source_file.read_text(encoding='utf-8', errors='ignore').lower()

for rule in data.get('rules', []):
    for keyword in rule.get('keywords', []):
        if keyword.lower() in text:
            print(rule['category'])
            raise SystemExit(0)

print(data.get('default_category', '99-general'))
PY
)

    target_dir="$BOOKMARKS_DIR/$target_category"
    mkdir -p "$target_dir"
    mv "$file" "$target_dir/"
    echo "  ✅ $filename → $target_category"
    ((moved++)) || true
done

echo "✅ 分類完成：移動了 $moved 個檔案"
echo "📦 預設分類：$default_category"
