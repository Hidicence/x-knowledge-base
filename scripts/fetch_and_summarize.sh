#!/bin/bash
# 完整流程 v2.3：抓書籤 → bird/curl/Jina/回退 → Agent Reach 補完 thread/外鏈 → AI 濃縮 → 分類儲存

set -euo pipefail
#  腳本結束或被 signal 中斷時，清理本次產生的 openclaw-infer 進程
trap 'pkill -f openclaw-infer 2>/dev/null || true' EXIT TERM INT

WORKSPACE_DIR="${WORKSPACE_DIR:-${OPENCLAW_WORKSPACE:-$HOME/.openclaw/workspace}}"
SKILL_DIR="${SKILL_DIR:-$WORKSPACE_DIR/skills/x-knowledge-base}"
BOOKMARKS_DIR="${BOOKMARKS_DIR:-$WORKSPACE_DIR/memory/bookmarks}"
CARDS_DIR="${CARDS_DIR:-$WORKSPACE_DIR/memory/cards}"
RUNTIME_DIR="${RUNTIME_DIR:-$WORKSPACE_DIR/memory/x-knowledge-base}"
DEFAULT_INDEX_FILE="$BOOKMARKS_DIR/search_index.json"
DEFAULT_VECTOR_INDEX_PATH="$BOOKMARKS_DIR/vector_index.json"
DEFAULT_QUEUE_PATH="$RUNTIME_DIR/tiege-queue.json"
INDEX_FILE="${INDEX_FILE:-$DEFAULT_INDEX_FILE}"
VECTOR_INDEX_PATH="${VECTOR_INDEX_PATH:-$DEFAULT_VECTOR_INDEX_PATH}"
QUEUE_PATH="${XKB_QUEUE_PATH:-$DEFAULT_QUEUE_PATH}"
INBOX_DIR="$BOOKMARKS_DIR/inbox"
MINIMAX_API_KEY="${MINIMAX_API_KEY:-}"
PREPARE_ONLY="${PREPARE_ONLY:-0}"
export BOOKMARKS_DIR CARDS_DIR RUNTIME_DIR INDEX_FILE VECTOR_INDEX_PATH XKB_QUEUE_PATH="$QUEUE_PATH"

# Twitter 認證（從環境變數讀取；若工作區有 secrets 檔則一併載入）
SECRETS_FILE="${SECRETS_FILE:-$WORKSPACE_DIR/.secrets/x-knowledge-base.env}"
if [[ -f "$SECRETS_FILE" ]]; then
    source "$SECRETS_FILE"
fi
BIRD_AUTH_TOKEN="${BIRD_AUTH_TOKEN:-}"
BIRD_CT0="${BIRD_CT0:-}"
export BIRD_AUTH_TOKEN BIRD_CT0

mkdir -p "$INBOX_DIR"

has_cmd() { command -v "$1" >/dev/null 2>&1; }

echo "📥 步驟1：抓取書籤..."
if [[ "${FETCH_SKIP_BOOKMARKS:-0}" == "1" ]]; then
    echo "  ⏭️ 略過 bird bookmark list fetch（使用外部提供的 tweet id 清單）"
else
    bash "$SKILL_DIR/scripts/fetch_bookmarks.sh"
fi

echo ""
echo "📖 步驟2：讀取全文 + Agent Reach 補完..."

fetch_x_article_content() {
    local tweet_url="$1"
    python3 - "$tweet_url" <<'PY'
import re
import sys
import requests

url = sys.argv[1]
try:
    m = re.search(r'https?://(?:www\.)?(?:x|twitter)\.com/([^/]+)/status/(\d+)', url)
    if not m:
        sys.exit(0)
    username, tweet_id = m.group(1), m.group(2)
    api_url = f"https://api.fxtwitter.com/{username}/status/{tweet_id}"
    resp = requests.get(api_url, timeout=30, headers={"User-Agent": "Mozilla/5.0"})
    if resp.status_code != 200:
        sys.exit(0)
    data = resp.json()
    tweet = data.get("tweet") or {}
    article = tweet.get("article") or {}
    content = article.get("content") or {}
    blocks = content.get("blocks") or []
    if not blocks:
        sys.exit(0)
    parts = []
    title = (article.get("title") or "").strip()
    preview = (article.get("preview_text") or "").strip()
    if title:
        parts.append(f"# {title}")
    if preview:
        parts.append(preview)
    for block in blocks:
        if not isinstance(block, dict):
            continue
        text = (block.get("text") or "").strip()
        if not text:
            continue
        btype = block.get("type")
        if btype == "header-two":
            parts.append(f"## {text}")
        elif btype == "blockquote":
            parts.append(f"> {text}")
        else:
            parts.append(text)
    rendered = "\n\n".join(x for x in parts if x).strip()
    if rendered:
        print(rendered[:30000])
except Exception:
    pass
PY
}

BOOKMARKS_TEXT_CACHE="/tmp/bookmarks_text_cache.json"
if [[ -n "$BIRD_AUTH_TOKEN" && -n "$BIRD_CT0" ]]; then
    bird --auth-token "$BIRD_AUTH_TOKEN" --ct0 "$BIRD_CT0" bookmarks --all --max-pages 5 --json 2>/dev/null > "$BOOKMARKS_TEXT_CACHE" || true
fi

NEW_BOOKMARKS_FILE="${BOOKMARKS_TMP_FILE:-/tmp/new_bookmarks.txt}"
NEW_COUNT=0
if [[ -f "$NEW_BOOKMARKS_FILE" ]]; then
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

        if [[ -z "$content" || ( ${#content} -lt 200 && "$content" == *"http"* ) ]]; then
            if [[ -n "$content" ]]; then
                link=$(echo "$content" | grep -oE 'https?://[^ ]+' | head -1 || true)
                if [[ -n "$link" ]]; then
                    resolved_link=$(curl -sLI -o /dev/null -w '%{url_effective}' --max-time 15 "$link" 2>/dev/null || echo "$link")
                    if [[ "$resolved_link" =~ ^https?://(x|twitter)\.com/.+/status/[0-9]+ ]]; then
                        echo "    📰 Layer 2: 偵測到 X Article，嘗試 fxtwitter..."
                        article_content=$(fetch_x_article_content "$resolved_link")
                        if [[ -n "$article_content" && ${#article_content} -gt 200 ]]; then
                            content="$article_content"
                            source_method="fxtwitter_article"
                            echo "    ✅ Layer 2: X Article 抓取成功"
                        fi
                    fi
                    if [[ -z "$content" || ${#content} -lt 100 ]]; then
                        echo "    🔗 Layer 2: 嘗試 curl 抓取連結..."
                        link_content=$(curl -sL --max-time 10 "$resolved_link" 2>/dev/null | head -500 || echo "")
                        if [[ -n "$link_content" && ${#link_content} -gt 100 ]]; then
                            content="$link_content"
                            source_method="curl"
                            echo "    ✅ Layer 2: curl 成功"
                        fi
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
            echo "source_url: https://x.com/i/status/$tweet_id"
            echo "---"
            echo ""
            echo "# Tweet $tweet_id"
            echo ""
            echo "$content"
        } > "$filepath"

        if has_cmd agent-reach && has_cmd xreach; then
            echo "    🧩 Agent Reach: 補抓 thread / 外鏈..."
            ENRICH_TMP=$(mktemp /tmp/enrich_XXXXXX.json)
            python3 "$SKILL_DIR/tools/agent_reach_enricher.py" "$tweet_id" "$filepath" > "$ENRICH_TMP" 2>/dev/null || echo "{}"> "$ENRICH_TMP"
            python3 - <<PY
import json
from pathlib import Path
p = Path("$filepath")
text = p.read_text(encoding='utf-8', errors='ignore')
try:
    data = json.loads(Path("$ENRICH_TMP").read_text(encoding='utf-8'))
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
            rm -f "$ENRICH_TMP"
        else
            echo "    ⏭️ Agent Reach / xreach 不可用，略過補完"
        fi

        ((NEW_COUNT++)) || true
    done < "$NEW_BOOKMARKS_FILE"
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
    if [[ $TOTAL_PROCESSED -ge 20 ]]; then
        echo "⚠️ 已達最大處理數量 (60)，強制停止"
        break
    fi
    sleep 2
done

echo ""
echo "🔎 更新搜尋索引..."
INDEX_FILE="$INDEX_FILE" BOOKMARKS_DIR="$BOOKMARKS_DIR" bash "$SKILL_DIR/scripts/build_search_index.sh" --incremental || true

echo ""
echo "✅ 批次處理完成！共處理約 $TOTAL_PROCESSED 個書籤"

echo ""
echo "📦 步驟4：強化新書籤（Bookmark Enrichment Worker）..."
python3 "$SKILL_DIR/scripts/sync_tiege_queue.py" || true

NEW_TODO=$(python3 -c "
import json, os
q = os.environ.get('XKB_QUEUE_PATH', '$QUEUE_PATH')
d = json.load(open(q))
print(len([i for i in d['items'] if i['status']=='todo']))
" 2>/dev/null || echo "0")

if [[ "$NEW_TODO" -gt 0 ]]; then
    echo "  📋 enrichment queue todo=$NEW_TODO（工單狀態，不等於缺少知識卡），開始處理（最多 15 條）..."
    python3 "$SKILL_DIR/scripts/run_bookmark_worker.py" --limit 5 --worker "pipeline" || true
    echo "  🔄 同步強化索引..."
    python3 "$SKILL_DIR/scripts/sync_enriched_index.py" || true
else
    echo "  ✅ 無待強化工單"
fi

echo ""
echo "🧹 步驟5：治理搜尋索引品質..."
python3 "$SKILL_DIR/scripts/normalize_index_quality.py" || true
python3 "$SKILL_DIR/scripts/canonicalize_duplicates.py" || true
python3 "$SKILL_DIR/scripts/cleanup_titles_in_index.py" || true

echo ""
echo "🧠 步驟6：更新語意向量索引..."
OPENCLAW_JSON="${OPENCLAW_JSON:-$(dirname "$WORKSPACE_DIR")/openclaw.json}"
GEMINI_KEY=$(python3 -c "import json,os; f=os.environ.get('OPENCLAW_JSON', '$OPENCLAW_JSON'); c=json.load(open(f)); print(c.get('env',{}).get('GEMINI_API_KEY',''))" 2>/dev/null || echo "")
VECTOR_INDEX_PATH="$WORKSPACE_DIR/memory/bookmarks/vector_index.json"

if [[ -n "$GEMINI_KEY" ]]; then
    if [[ -f "$VECTOR_INDEX_PATH" ]]; then
        echo "  ♻️ 偵測到既有向量索引，執行 incremental rebuild"
        GEMINI_API_KEY="$GEMINI_KEY" python3 "$SKILL_DIR/scripts/build_vector_index.py" --incremental --index-file "$INDEX_FILE" --vector-file "$VECTOR_INDEX_PATH" || true
    else
        echo "  🆕 尚未找到向量索引，執行首次 full build"
        GEMINI_API_KEY="$GEMINI_KEY" python3 "$SKILL_DIR/scripts/build_vector_index.py" --index-file "$INDEX_FILE" --vector-file "$VECTOR_INDEX_PATH" || true
    fi
else
    echo "  ⏭️ 略過（GEMINI_API_KEY 未設定）"
fi


echo ""
echo "📚 步驟7：同步書籤卡到 wiki..."
if [ "${SKIP_WIKI_SYNC:-0}" != "1" ]; then
    python3 "$SKILL_DIR/scripts/sync_cards_to_wiki.py" --apply --limit 20 \
        || echo "Wiki sync failed (non-fatal), continuing..."
else
    echo "  ⏭️ SKIP_WIKI_SYNC=1，略過"
fi

