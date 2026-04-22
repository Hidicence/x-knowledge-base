#!/usr/bin/env bash
# xkb_demo.sh
#
# XKB Demo Mode：用 sample dataset 展示完整知識吸收流程
# 10 分鐘內看到：notes → cards → wiki topics → ask
#
# Usage:
#   bash scripts/xkb_demo.sh
#   bash scripts/xkb_demo.sh --reset    # 清除上次 demo 資料重跑
#
# Environment:
#   OPENCLAW_WORKSPACE — workspace path (default: ~/.openclaw/workspace)

set -euo pipefail

WORKSPACE="${OPENCLAW_WORKSPACE:-$HOME/.openclaw/workspace}"
SKILL_DIR="$WORKSPACE/skills/x-knowledge-base"
SCRIPTS="$SKILL_DIR/scripts"
SAMPLE_DIR="$SKILL_DIR/demo/sample-notes"
DEMO_TOPIC_MAP="$SKILL_DIR/demo/demo-topic-map.json"
WIKI_DIR="$WORKSPACE/wiki"

# Colors
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
NC='\033[0m'

say() { echo -e "${BLUE}▶ $1${NC}"; }
ok()  { echo -e "${GREEN}✅ $1${NC}"; }
warn(){ echo -e "${YELLOW}⚠️  $1${NC}"; }

# ── Argument parsing ───────────────────────────────────────────────────────────
RESET=false
for arg in "$@"; do
  [[ "$arg" == "--reset" ]] && RESET=true
done

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  📚 X Knowledge Base — Demo Mode"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""
echo "  這個 demo 會："
echo "  1. 把 10 份 sample 筆記轉成知識卡片"
echo "  2. 透過 Absorb Gate 過濾後生成 wiki topic 頁"
echo "  3. 讓你直接提問，看到帶引用的答案"
echo ""

# ── Check sample data ──────────────────────────────────────────────────────────
if [[ ! -d "$SAMPLE_DIR" ]]; then
  warn "找不到 sample data：$SAMPLE_DIR"
  exit 1
fi

NOTE_COUNT=$(ls "$SAMPLE_DIR"/*.md 2>/dev/null | wc -l)
if [[ "$NOTE_COUNT" -eq 0 ]]; then
  warn "sample-notes 目錄是空的"
  exit 1
fi

say "找到 $NOTE_COUNT 份 sample 筆記"
echo ""

# ── Reset if requested ────────────────────────────────────────────────────────
if $RESET; then
  say "清除上次 demo 資料..."
  cd "$WORKSPACE"
  python3 << 'PYEOF'
import json, os, glob
from pathlib import Path

workspace = Path(os.environ.get("OPENCLAW_WORKSPACE", os.path.expanduser("~/.openclaw/workspace")))
index_file = workspace / "memory" / "bookmarks" / "search_index.json"

if index_file.exists():
    data = json.loads(index_file.read_text())
    before = len(data.get("items", []))
    data["items"] = [i for i in data.get("items", [])
                     if not (i.get("source_type") == "local" and
                             "demo/sample-notes" in i.get("source_file", ""))]
    index_file.write_text(json.dumps(data, ensure_ascii=False, indent=2))
    print(f"  index: {before} → {len(data['items'])} items")

for card in glob.glob(str(workspace / "memory" / "cards" / "local-*-demo-*.md")):
    os.remove(card)
    print(f"  removed: {Path(card).name}")
PYEOF
  ok "清除完成"
  echo ""
fi

# ── Step 1: Ingest sample notes → cards ───────────────────────────────────────
echo "━━━ Step 1/3：筆記 → 知識卡片 ━━━━━━━━━━━━━━━━━━━━"
echo ""

cd "$WORKSPACE"
set +e
python3 "$SCRIPTS/local_ingest.py" "$SAMPLE_DIR" --tag demo
INGEST_EXIT=$?
set -e

echo ""

if [[ $INGEST_EXIT -eq 0 ]]; then
  warn "所有 sample 筆記已存在（上次 demo 留下的），跳過重新生成"
  warn "如需重跑，加上 --reset 參數"
elif [[ $INGEST_EXIT -eq 2 ]]; then
  ok "知識卡片生成完成"
else
  warn "local_ingest 發生錯誤（exit $INGEST_EXIT）"
  exit 1
fi

# Count demo cards
CARD_COUNT=$(ls "$WORKSPACE/memory/cards/local-"*.md 2>/dev/null | wc -l)
echo ""
say "目前共 $CARD_COUNT 張本地知識卡片"

# ── Step 2: Suggest + apply topic map ─────────────────────────────────────────
echo ""
echo "━━━ Step 2/3：建立 Wiki Topic 頁 ━━━━━━━━━━━━━━━━━━"
echo ""

# Create a minimal demo topic map pointing demo categories to existing topics
# or auto-suggest
say "使用現有 topic map 進行同步..."
python3 "$SCRIPTS/sync_cards_to_wiki.py" --apply --limit 15 --no-llm 2>/dev/null || \
python3 "$SCRIPTS/sync_cards_to_wiki.py" --apply --limit 15

WIKI_COUNT=$(ls "$WIKI_DIR/topics/"*.md 2>/dev/null | wc -l)
echo ""
ok "$WIKI_COUNT 個 wiki topic 頁已更新"

# ── Step 3: Show results ───────────────────────────────────────────────────────
echo ""
echo "━━━ 生成結果 ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""
echo "📋 知識卡片（memory/cards/）"
ls "$WORKSPACE/memory/cards/local-"*.md 2>/dev/null | while read f; do
  title=$(grep '^title:' "$f" | head -1 | sed 's/^title: //' | sed 's/^"//' | sed 's/"$//')
  echo "   • $title"
done

echo ""
echo "📖 Wiki Topics（wiki/topics/）"
ls "$WIKI_DIR/topics/"*.md 2>/dev/null | while read f; do
  title=$(grep '^# ' "$f" | head -1 | sed 's/^# //')
  sources=$(grep '^sources:' "$f" | head -1 | sed 's/^sources: //')
  echo "   • $title (${sources:-?} 個來源)"
done

# ── Step 4: Ask demo ──────────────────────────────────────────────────────────
echo ""
echo "━━━ Step 3/3：試試直接提問 ━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""

DEMO_QUERY="AI agent 的記憶架構怎麼設計？"
say "示範問題：\"$DEMO_QUERY\""
echo ""

python3 "$SCRIPTS/xkb_ask.py" "$DEMO_QUERY" --format chat

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""
echo "🎉 Demo 完成！你可以繼續提問："
echo ""
echo "   python3 scripts/xkb_ask.py \"你的問題\""
echo "   python3 scripts/xkb_ask.py \"LLM 成本怎麼優化\" --format full"
echo ""
echo "   或匯入你自己的筆記："
echo "   python3 scripts/local_ingest.py path/to/your/notes/"
echo ""
