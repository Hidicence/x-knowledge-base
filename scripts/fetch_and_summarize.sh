#!/bin/bash
# 完整流程 v2.3：抓書籤 → bird/curl/Jina/回退 → Agent Reach 補完 thread/外鏈 → AI 濃縮 → 分類儲存

set -euo pipefail

WORKSPACE_DIR="${WORKSPACE_DIR:-${OPENCLAW_WORKSPACE:-$HOME/.openclaw/workspace}}"
SKILL_DIR="${SKILL_DIR:-$WORKSPACE_DIR/skills/x-knowledge-base}"
BOOKMARKS_DIR="${BOOKMARKS_DIR:-$WORKSPACE_DIR/memory/bookmarks}"
INBOX_DIR="$BOOKMARKS_DIR/inbox"
MINIMAX_API_KEY="${MINIMAX_API_KEY:-}"
PREPARE_ONLY="${PREPARE_ONLY:-0}"
export BOOKMARKS_DIR

# Twitter 認證（從環境變數讀取；若有自訂 env 檔則一併載入）
ENV_FILE="${XKB_ENV_FILE:-${SECRETS_FILE:-}}"
if [[ -n "$ENV_FILE" && -f "$ENV_FILE" ]]; then
    source "$ENV_FILE"
fi
BIRD_AUTH_TOKEN="${BIRD_AUTH_TOKEN:-}"
BIRD_CT0="${BIRD_CT0:-}"
export BIRD_AUTH_TOKEN BIRD_CT0

mkdir -p "$INBOX_DIR"

has_cmd() { command -v "$1" >/dev/null 2>&1; }

echo "📥 步驟1：抓取書籤..."
bash "$SKILL_DIR/scripts/fetch_bookmarks.sh"

echo ""
echo "📖 步驟2：讀取全文 + Agent Reach 補完..."

BOOKMARKS_TEXT_CACHE="/tmp/bookmarks_text_cache.json"
if [[ -n "$BIRD_AUTH_TOKEN" && -n "$BIRD_CT0" ]]; then
    bird --auth-token "$BIRD_AUTH_TOKEN" --ct0 "$BIRD_CT0" bookmarks --all --max-pages 5 --json 2>/dev/null > "$BOOKMARKS_TEXT_CACHE" || true
fi

NEW_COUNT=0
if [[ -f /tmp/new_bookmarks.txt ]]; then
    while read -r tweet_id; do
        [[ -z "$tweet_id" ]] && continue
        filename="${tweet_id}.md"
        filepath="$INBOX_DIR/$filename"

        if [[ -f "$filepath" ]]; then
            if grep -qE "無法擷取內容|登入頁面|Sign in to X|x.com/\?mx=2" "$filepath"; then
                echo "  🔁 重新抓取失敗檔: $filename"
            else
                echo "  ⏭️  已存在: $filename"
                continue
            fi
        fi

        echo "  📄 處理: $tweet_id"
        content=""
        source_method=""

        if [[ -n "$BIRD_AUTH_TOKEN" && -n "$BIRD_CT0" ]]; then
            content=$(bird --auth-token "$BIRD_AUTH_TOKEN" --ct0 "$BIRD_CT0" read "$tweet_id" 2>/dev/null || echo "")
            if [[ -n "$content" ]]; then
                source_method="bird_read"
                echo "    ✅ Layer 1: bird read 成功"
            fi
        fi

        if [[ -z "$content" || "$content" == *"http"* ]]; then
            if [[ -n "$content" ]]; then
                link=$(echo "$content" | grep -oE 'https?://[^ ]+' | head -1 || true)
                if [[ -n "$link" ]]; then
                    echo "    🔗 Layer 2: 嘗試 curl 抓取連結..."
                    link_content=$(curl -sL --max-time 10 "$link" 2>/dev/null | head -500 || echo "")
                    if [[ -n "$link_content" && ${#link_content} -gt 100 ]]; then
                        content="$link_content"
                        source_method="curl"
                        echo "    ✅ Layer 2: curl 成功"
                    fi
                fi
            fi
        fi

        if [[ -z "$content" || ${#content} -lt 50 ]]; then
            echo "    🔄 Layer 3: 嘗試 Jina AI..."
            jina_content=$(curl -s "https://r.jina.ai/http://x.com/i/status/$tweet_id" 2>/dev/null || echo "")
            if [[ -n "$jina_content" && "$jina_content" != "Not Found" && "$jina_content" != *"Sign in to X"* && "$jina_content" != *"Create account"* && "$jina_content" != *"x.com/?mx=2"* ]]; then
                content="$jina_content"
                source_method="jina"
                echo "    ✅ Layer 3: Jina AI 成功"
            fi
        fi

        if [[ -z "$content" || ${#content} -lt 20 ]]; then
            if [[ -f "$BOOKMARKS_TEXT_CACHE" ]]; then
                fallback_text=$(python3 - <<PY
import json
p = "$BOOKMARKS_TEXT_CACHE"
tid = "$tweet_id"
try:
    data = json.load(open(p, 'r', encoding='utf-8'))
    for it in data.get('items', data.get('tweets', [])):
        if str(it.get('id', '')) == tid:
            text = (it.get('text') or '').strip()
            if text:
                print(text)
            break
except Exception:
    pass
PY
)
                if [[ -n "$fallback_text" ]]; then
                    content="$fallback_text"
                    source_method="bird_bookmarks_text"
                    echo "    ✅ Layer 4: 使用 bird bookmarks text 回退成功"
                fi
            fi
        fi

        if [[ -z "$content" ]]; then
            content="無法擷取內容"
            source_method="failed"
            echo "    ⚠️ 所有層都失敗"
        fi

        {
            echo "---"
            echo "tweet_id: $tweet_id"
            echo "source: $source_method"
            echo "original_url: https://x.com/i/status/$tweet_id"
            echo "---"
            echo ""
            echo "# Tweet $tweet_id"
            echo ""
            echo "$content"
        } > "$filepath"

        if has_cmd agent-reach && has_cmd xreach; then
            echo "    🧩 Agent Reach: 補抓 thread / 外鏈..."
            enrich_json=$(python3 "$SKILL_DIR/tools/agent_reach_enricher.py" "$tweet_id" "$filepath" 2>/dev/null || echo '{}')
            python3 - <<PY
import json
from pathlib import Path
p = Path("$filepath")
text = p.read_text(encoding='utf-8', errors='ignore')
try:
    data = json.loads('''$enrich_json''')
except Exception:
    data = {}
thread_text = (data.get('thread_text') or '').strip()
author_additions = data.get('author_additions') or []
links = data.get('extracted_links') or []
blocks = []
if thread_text:
    blocks.append("## 🧵 Thread 全文\n\n" + thread_text)
if author_additions:
    items = "\n".join(f"- {x}" for x in author_additions[:10] if x)
    if items:
        blocks.append("## ✍️ 作者補充\n\n" + items)
if links:
    rendered = []
    for item in links[:5]:
        url = item.get('url','')
        content = (item.get('content') or '').strip()
        rendered.append(f"### {url}\n\n{content[:2000]}")
    blocks.append("## 🔗 外部連結摘錄\n\n" + "\n\n".join(rendered))
if blocks:
    p.write_text(text.rstrip() + "\n\n" + "\n\n".join(blocks) + "\n", encoding='utf-8')
else:
    print("    ℹ️ 無有效 thread / 外鏈補完內容，維持 tweet-only")
PY
        else
            echo "    ⏭️ Agent Reach / xreach 不可用，略過補完"
        fi

        ((NEW_COUNT++)) || true
    done < /tmp/new_bookmarks.txt
fi

if [[ "$PREPARE_ONLY" == "1" ]]; then
    echo ""
    echo "🧰 PREPARE_ONLY=1：已完成原始抓取與補完，略過摘要/分類/索引"
    exit 0
fi

echo ""
echo "🤖 步驟3：批次處理直到 inbox 清空..."
cd "$SKILL_DIR"

BATCH_SIZE=5
TOTAL_PROCESSED=0

while true; do
    inbox_count=$(find "$INBOX_DIR" -maxdepth 1 -type f -name '*.md' | wc -l)
    if [[ $inbox_count -eq 0 ]]; then
        echo "📭 inbox 已清空，結束批次處理"
        break
    fi

    echo ""
    echo "=== 批次 $((TOTAL_PROCESSED / BATCH_SIZE + 1))：剩餘 $inbox_count 條 ==="

    export BOOKMARKS_DIR
    export MINIMAX_API_KEY
    python3 tools/bookmark_enhancer.py $BATCH_SIZE

    echo ""
    echo "📂 分類本批次..."
    bash "$SKILL_DIR/scripts/auto_categorize.sh"

    TOTAL_PROCESSED=$((TOTAL_PROCESSED + BATCH_SIZE))
    if [[ $TOTAL_PROCESSED -ge 60 ]]; then
        echo "⚠️ 已達最大處理數量 (60)，強制停止"
        break
    fi
    sleep 2
done

echo ""
echo "🔎 更新搜尋索引..."
bash "$SKILL_DIR/scripts/build_search_index.sh" --incremental || true

echo ""
echo "✅ 批次處理完成！共處理約 $TOTAL_PROCESSED 個書籤"
