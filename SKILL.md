---
name: x-knowledge-base
description: |
  Turns X/Twitter bookmarks into a searchable, proactive personal knowledge base for AI agents.
  Use for: fetching & organizing X bookmarks, enriching threads, generating knowledge cards,
  building search index, recalling saved knowledge during conversations, exporting to NotebookLM.
  將 X/Twitter 書籤整理成可檢索、可關聯、可匯出到 NotebookLM 的個人知識庫系統。
  使用於：抓取與整理 X 書籤、生成知識卡、更新搜尋索引、對話中主動召回既存知識，或匯出 NotebookLM。
---

# X Knowledge Base

Upgrade X bookmarks from "saved" to a "reusable knowledge base".
把 X 書籤從「收藏」升級成「可回用知識庫」。

## Main Entry Points / 主入口

Full pipeline / 完整流程：

```bash
bash scripts/fetch_and_summarize.sh
```

Personalized recommendations based on accumulated bookmark preferences (picks content from feed/bookmarks you may be interested in) / 依「累積書籤偏好」做個人化推薦（從 feed / bookmarks 挑你可能有興趣的內容）：

```bash
bash scripts/recommend_from_profile.sh
```

Fetch new bookmarks only / 只抓新書籤：

```bash
bash scripts/fetch_bookmarks.sh
```

Build or incrementally update the search index / 建立或增量更新搜尋索引：

```bash
bash scripts/build_search_index.sh
bash scripts/build_search_index.sh --incremental
```

Build per-user topic profile for dynamic recall triggers / 建立每位使用者自己的 topic profile，供動態召回觸發使用：

```bash
python3 scripts/build_topic_profile.py
python3 scripts/build_topic_profile.py --dry-run
```

Search existing knowledge cards / bookmarks / 搜尋既有知識卡／書籤：

```bash
bash scripts/search_bookmarks.sh "openclaw seo"
```

Proactive conversation recall (given a query about the current topic, finds the most relevant saved bookmarks) / 對話主動召回（給當前問題一段 query，找最該主動提的書籤）：

```bash
python3 scripts/recall_for_conversation.py "agent workflow 記憶召回"
python3 scripts/recall_for_conversation.py "AI SEO 案例" --json
python3 scripts/recall_for_conversation.py "OpenClaw workflow" --format chat
```

Export to NotebookLM / 匯出 NotebookLM：

```bash
python3 scripts/export_notebooklm.py
python3 scripts/export_notebooklm.py 50
```

Sync local bookmark markdown files to Google Drive (via `rclone`) / 同步本地書籤 md 到 Google Drive（透過 `rclone`）：

```bash
bash scripts/sync_to_drive.sh
DRY_RUN=1 bash scripts/sync_to_drive.sh
```

## Workflow / 工作流程

When `fetch_and_summarize.sh` runs, it performs these steps in order / 執行 `fetch_and_summarize.sh` 時，依序做這些事：

1. Fetch new bookmarks from the past 28 days, deduplicated by tweet ID / 抓近 28 天新書籤並以 tweet id 去重
2. Retrieve tweet content (bird / Jina / fallback) / 取得 tweet 內容（bird / Jina / fallback）
3. Enrich with thread, author supplements, external articles, GitHub content / 補抓 thread、作者補充、外部文章、GitHub 內容
4. Filter out login pages, 404s, homepage noise, and low-value content / 過濾登入頁、404、首頁噪音與低價值內容
5. Call `tools/bookmark_enhancer.py` to generate summaries and categories / 呼叫 `tools/bookmark_enhancer.py` 生成摘要與分類
6. Update `search_index.json` / 更新 `search_index.json`
7. (Optional) Run `recommend_from_profile.sh` to estimate interest weights from accumulated bookmarks and auto-generate recommendations from following/for-you / （可選）執行 `recommend_from_profile.sh`，用累積書籤推估興趣權重，從 following/for-you 自動產生推薦

## Enrichment & Quality Rules / 補完與品質規則

- Prioritize fetching full threads; fall back to tweet-only on failure / 優先抓完整 thread；失敗時回退 tweet-only
- GitHub links go through `gh` first / GitHub 連結優先走 `gh`
- External articles extract body text first; avoid login pages or noise pages / 外部文章優先抽正文，不保留登入頁或雜訊頁
- When content is insufficient, keep a simplified card rather than fabricating missing info / 內容不足時維持簡化卡，不硬補不存在的資訊
- Always fallback on failure; never interrupt an entire batch import / 失敗時要 fallback，不中斷整批入庫

## Proactive Conversation Recall / 對話主動召回

> **Current Technical Note**: Recall now supports **two modes**. Default behavior is **semantic recall when `vector_index.json` exists**, otherwise it automatically falls back to **keyword token matching**. Keyword mode is zero-dependency and fast; semantic mode improves recall for synonyms and mixed Chinese/English phrasing.
>
> **目前技術說明**：召回目前支援 **兩種模式**。預設行為是：**若 `vector_index.json` 存在就走語意召回**，否則自動降級為 **關鍵字 token 比對**。關鍵字模式零依賴、速度快；語意模式則更適合同義詞與中英混用場景。

Treat this skill as a second layer of memory in conversation. When the current conversation needs examples, approaches, context, or actionable references, use proactive recall to find relevant saved bookmarks first, then decide whether to surface them to the user.
把這個 skill 當成對話中的第二層記憶：當前對話若需要案例、做法、脈絡或可行動參考，先用主動召回找你過去存過的相關書籤，再決定要不要主動提給使用者。

### When to Use / 什麼時候用

Prioritize these situations / 優先用在這些情境：
- The user is asking about approaches, workflows, case studies, inspiration, or decision direction / 使用者在問做法、workflow、案例、靈感、決策方向
- The user is organizing ideas, context, comparing options, or needs external examples to aid decision-making / 使用者在整理想法、脈絡、比較不同路線，或需要外部案例幫忙決策
- The current topic clearly falls within frequently bookmarked domains: OpenClaw / agent / workflow, SEO / GEO / AEO, AI video / prompts / content, automation / tools / GitHub, startup / SaaS / GTM / 當前主題明顯落在常見收藏領域：OpenClaw / agent / workflow、SEO / GEO / AEO、AI 影片 / prompts / content、automation / tools / GitHub、startup / SaaS / GTM
- You judge the bookmark library likely has reusable content, and proactively surfacing it would improve the conversation quality / 你判斷書籤庫裡很可能有可回用內容，且主動提醒會提升當前對話品質

### Trigger Rules (v1) / 觸發規則（v1）

Default stance / 預設立場：
- Do **not** recall on every turn. Recall only when the current question is the kind of thing where saved knowledge is likely to beat generic knowledge. / **不要每輪都查**；只有當前問題屬於「查既有知識很可能比通用知識更有幫助」的類型時，才優先召回。
- The first goal is deterministic behavior on the right question types, not maximum recall volume. / 第一目標是讓「對的題型」觸發更穩定，而不是把召回次數拉滿。

Only proactively recall when **both** conditions are met / 只有同時滿足這兩件事才主動召回：
1. The current conversation is worth checking bookmarks / 當前對話值得查書籤
2. The results retrieved can genuinely advance the conversation / 查到的結果真的能推進對話

Treat the following as **strong trigger classes** / 以下題型視為 **強觸發類型**：
- How-to / workflow / SOP / framework questions / 做法、workflow、SOP、framework 類問題
- Case study / inspiration / reference / comparison questions / 案例、靈感、參考、對照類問題
- Strategy / decision / prioritization questions / 策略、決策、優先順序類問題
- Topics that clearly fall into high-frequency bookmark domains / 明顯落在高頻收藏主題的問題

In practice, at least one of the following must match / 實際判斷時，至少先命中以下其中一項：
- The question is looking for approaches, case studies, workflows, or decision direction / 問法像在找做法、案例、workflow、決策方向
- The question is organizing context, perspectives, or next steps / 問法像在整理脈絡、觀點、下一步
- The topic falls within high-frequency bookmark domains / 主題落在高頻收藏領域
- Your subjective judgment: the user's bookmark library is more likely to provide valuable references than general knowledge / 你主觀判斷：使用者的書籤庫比通用知識更可能提供有價值的參考

Generate a **short, intention-focused query** before recall / 召回前先產生**短而聚焦意圖的 query**：
- Extract topic words / 抽主題詞
- Extract intent words (how-to / compare / case study / planning) / 抽意圖詞（做法、比較、案例、規劃）
- Add 1–2 task-context words if useful / 若有幫助，再補 1–2 個任務上下文字
- Do not dump the entire conversation into recall / 不要把整段對話直接塞進 recall

Example query shapes / query 例子：
- `OpenClaw memory recall workflow`
- `AI SEO case study content system`
- `agent workflow planning`

Results then pass a second-layer filter; worth surfacing only if at least two apply / 召回結果再過第二層過濾；至少符合其中兩項才值得提：
- Has a clear title / 有清楚標題
- Has a decent summary / 有像樣摘要
- Has a source URL / 有原文連結
- High relevance / relevance 高
- Can help the user advance the current task or decision / 能幫使用者推進當前任務或決策

### When NOT to Use / 什麼時候不要用

- Pure casual chat / 純閒聊
- Simple factual questions (unless closely related to saved knowledge) / 簡單事務題（除非和收藏知識高度相關）
- Only a very tenuous connection / 只有很勉強的關聯
- Already proactively surfaced this round and the user didn't follow up / 同一輪已主動提醒過，使用者沒追問
- Results have no source URL, too thin a summary, or are clearly low value / 結果沒有原文連結、摘要太空、或明顯低價值

### How to Use / 使用方式

Semantic search is **automatic** — if `vector_index.json` exists, it is used by default.
語意搜尋會**自動啟用**——只要 `vector_index.json` 存在，預設就使用語意召回。

First ensure `search_index.json` exists and is up to date, then run / 先確保 `search_index.json` 存在且是新的，再執行：

```bash
# Auto (semantic if index exists, keyword fallback) / 自動模式（有索引用語意，無索引降級 keyword）
python3 scripts/recall_for_conversation.py "主動召回 書籤知識 對話回用"
python3 scripts/recall_for_conversation.py "OpenClaw workflow agent memory" --limit 5 --json
python3 scripts/recall_for_conversation.py "AI SEO 案例" --format chat

# Force keyword only / 強制 keyword（忽略向量索引）
python3 scripts/recall_for_conversation.py "query" --no-semantic
```

### Response Principles / 回覆原則

- Default: proactively surface at most once per conversation turn / 每輪對話預設最多主動提 1 次
- Default: bring up at most 1–2 items; expand to 3 only if the user follows up / 預設最多帶 1–2 篇；除非使用者追問，再展開到 3 篇
- Only surface results that genuinely help / 只提真的能幫上忙的結果
- Prioritize actionable case studies, content close to the current project, content with workflow/SOP, and content with source URLs / 優先提可落地案例、接近當前專案、帶 workflow / SOP、且有原文連結的內容
- Keep responses short: one-sentence summary + why it's relevant + source URL / 回覆格式維持短：一句話摘要 + 為什麼相關 + 原文連結
- If results are mediocre, don't force them into the conversation / 若結果普通，就不要硬插話


## Semantic Recall (v3, Optional) / 語意向量召回（v3，可選）

> **Upgrade from v1 keyword matching to true semantic search.**
> Find relevant bookmarks even when your query uses completely different words from the card.
> 從 v1 關鍵字比對升級為真正的語意搜尋，查詢用詞和書籤內容不同也能找到。

### Why it matters / 為什麼值得用

| Query / 查詢 | Keyword v1 | Semantic v3 |
|---|---|---|
| "省錢跑 AI" | ❌ 只找到含 "AI" 的不相關卡 | ✅ 找到「先用軟體把 workflow 跑通再買硬體」 |
| "設計品味 anti-slop" | ⚠️ 靠運氣 | ✅ 直接命中 taste-skill |
| "LinkedIn 自動回覆工具" | ❌ 完全找不到 | ✅ 找到 agent-swarm、自動化 workflow |

### Setup / 設定方式

**Step 1: Set environment variables / 設定環境變數**

Choose one provider / 選擇一個 provider：

```bash
# Option A: Gemini（recommended / 推薦）
export EMBEDDING_PROVIDER=gemini
export EMBEDDING_MODEL=gemini-embedding-2-preview
export GEMINI_API_KEY=your_key_here

# Option B: OpenAI
export EMBEDDING_PROVIDER=openai
export EMBEDDING_MODEL=text-embedding-3-small
export OPENAI_API_KEY=your_key_here

# Option C: Ollama（local, completely free / 本地，完全免費）
export EMBEDDING_PROVIDER=ollama
export EMBEDDING_MODEL=nomic-embed-text
export OLLAMA_BASE_URL=http://localhost:11434
```

**Step 2: Build the vector index / 建立向量索引**

```bash
python3 scripts/build_vector_index.py
# First run embeds all cards (~430 cards, costs < $0.05 with Gemini)
# 第一次執行會 embed 所有書籤（約 430 張，Gemini 費用不到 $0.05）

python3 scripts/build_vector_index.py --incremental
# Subsequent runs: only embed new cards / 之後只需 embed 新書籤
```

**Step 3: Use semantic recall / 使用語意召回**

```bash
python3 scripts/recall_for_conversation.py "你的問題" --semantic
python3 scripts/recall_for_conversation.py "AI agent workflow" --semantic --limit 5
python3 scripts/recall_for_conversation.py "省錢跑 AI" --semantic --format chat
```

### How it works / 運作原理

1. Each card's `title + summary` is converted to a vector (1–3K floats) via an embedding model
2. At recall time, the query is also embedded
3. Cards are ranked by cosine similarity — meaning nearest in semantic space, not just matching words

每張卡的 `title + summary` 透過 embedding 模型轉成向量，召回時 query 也轉成向量，用 cosine similarity 找語意最近的書籤。

### Notes / 注意事項

- If `vector_index.json` is missing, `--semantic` automatically falls back to keyword search / 若向量索引不存在，`--semantic` 自動降級為關鍵字搜尋
- `vector_index.json` is personal data — it is **not** committed to the repo / 向量索引是個人資料，**不會**進 GitHub repo
- Re-run `build_vector_index.py --incremental` after adding new bookmarks / 新增書籤後記得跑增量更新

## When to Read Additional Reference Files / 何時讀額外參考檔

- To deeply understand the design principles, trigger rules, response format, and public-facing educational positioning of proactive recall, read `references/conversation-recall.md` / 想深入理解主動召回的設計原則、觸發規則、回覆格式與公開教學定位時，讀 `references/conversation-recall.md`
- When adjusting NotebookLM card format or export fields, read `references/notebooklm-schema.md` / 調整 NotebookLM 卡片格式或匯出欄位時，讀 `references/notebooklm-schema.md`
- When using the Tiege single-item slow-clearing workflow, read / 使用鐵哥單筆慢清舊庫時，讀：
  - `references/tiege-single-item-workflow.md`
  - `references/tiege-prompt.md`
  - `config/tiege-queue.example.json`
- The active queue state file should not be placed inside the skill directory; store it in the workspace data path instead, e.g.: `memory/x-knowledge-base/tiege-queue.json` / 實際執行中的 queue 狀態檔不要放在 skill 內；改放工作區資料路徑，例如：`memory/x-knowledge-base/tiege-queue.json`

## Environment Requirements / 環境需求

Ensure at least the following variables or tools are available / 至少確認下列變數或工具可用：

- `BIRD_AUTH_TOKEN`
- `BIRD_CT0`
- `TWITTER_AUTH_TOKEN` (optional; if not set, recommendation scripts fall back to `BIRD_AUTH_TOKEN` / 可選；若未提供，推薦腳本會回退使用 `BIRD_AUTH_TOKEN`)
- `TWITTER_CT0` (optional; if not set, recommendation scripts fall back to `BIRD_CT0` / 可選；若未提供，推薦腳本會回退使用 `BIRD_CT0`)
- `BOOKMARKS_DIR` (optional / 可選)
- `MINIMAX_API_KEY` (optional; falls back if not provided / 可選；未提供時走 fallback)
- `agent-reach` (recommended / 建議)
- `xreach` (recommended / 建議)
- `gh` (recommended / 建議)
- `rclone` (required for Google Drive sync / 若要同步到 Google Drive)
- `RCLONE_REMOTE` (optional; e.g. `my-drive:XKnowledgeBase-Bookmarks` / 可選；例如 `my-drive:XKnowledgeBase-Bookmarks`)

Do not place `.env` or other secrets inside the skill directory. Use workspace environment variables, external environment management, or workspace `.secrets/x-knowledge-base.env` instead.
不要把 `.env` 或其他 secrets 放進 skill 目錄；改由工作區環境變數、外部環境管理，或工作區 `.secrets/x-knowledge-base.env`。

## Key Files / 重要檔案

- `scripts/fetch_and_summarize.sh` — Main pipeline entry / 主流程入口
- `scripts/fetch_bookmarks.sh` — Fetch new bookmarks / 抓新書籤
- `scripts/auto_categorize.sh` — Categorize inbox bookmarks by `config/category-rules.json` / 依 `config/category-rules.json` 將 inbox 書籤歸類
- `scripts/build_search_index.sh` — Build search index / 建索引
- `scripts/search_bookmarks.sh` — Search / 搜尋
- `scripts/recommend_from_profile.sh` — Estimate interests from accumulated bookmarks, generate recommendations from feed / 由累積書籤推估興趣，從 feed 產生推薦
- `scripts/export_notebooklm.py` — Export to NotebookLM / 匯出 NotebookLM
- `scripts/sync_to_drive.sh` — Sync local md to Google Drive via `rclone` / 透過 `rclone` 同步本地 md 到 Google Drive
- `config/category-rules.json` — Category rule configuration / 分類規則設定
- `config/recommendation-topics.json` — Recommendation topics and keyword configuration / 推薦主題與關鍵字設定
- `tools/bookmark_enhancer.py` — Summary / categorization / cross-linking / 摘要 / 分類 / 交叉連結
- `tools/agent_reach_enricher.py` — Thread / external link / GitHub enrichment / thread / 外鏈 / GitHub 補完

## Evals

- Test cases / 測試案例：`evals/evals.json`
- Quick evaluation of recommendation quality / 推薦品質快速評估：

```bash
python3 scripts/eval_recommendations.py
```

Metrics / 評估指標：
- `interest_hit_rate` (proportion of Top 5 hitting target topics, target >= 0.8 / Top5 命中目標主題比例，目標 >= 0.8)
- `duplicate_rate` (target <= 0.05 / 重複率，目標 <= 0.05)
- `noise_rate` (proportion of low-value content, target <= 0.2 / 低價值內容比例，目標 <= 0.2)


## YouTube Playlist Integration (v4, Optional) / YouTube 播放清單整合（v4，可選）

Automatically fetches new videos from a designated YouTube playlist, downloads subtitles, and generates knowledge cards in the same format as X bookmarks.
自動從指定 YouTube 播放清單抓取新影片、下載字幕，生成與 X 書籤相同格式的知識卡。

### Setup / 設定

1. Create a **dedicated public playlist** on YouTube (e.g., "知識庫" / "Knowledge Base")
   在 YouTube 建立一個**公開的專屬播放清單**（例如命名為「知識庫」）

2. Export YouTube cookies from Chrome using the **"Get cookies.txt LOCALLY"** extension
   用 Chrome 插件「Get cookies.txt LOCALLY」匯出 YouTube cookies

3. Upload cookies to VPS / 上傳 cookies 至 VPS：
   ```bash
   scp cookies.txt server:~/.config/yt-dlp/cookies.txt
   ```

4. Set `YOUTUBE_PLAYLIST_URL` in your environment / 設定環境變數：
   ```bash
   YOUTUBE_PLAYLIST_URL=https://www.youtube.com/playlist?list=PLxxx
   ```

### How to Use / 使用方式

```bash
# 預覽有哪些新影片（不實際執行）
python3 scripts/fetch_youtube_playlist.py --dry-run

# 執行（處理全部新影片）
python3 scripts/fetch_youtube_playlist.py

# 限制每次最多處理 N 支
python3 scripts/fetch_youtube_playlist.py --limit 5

# 指定播放清單（覆蓋環境變數）
python3 scripts/fetch_youtube_playlist.py --playlist "URL"

# 跑完更新語意索引（增量，只處理新卡片）
python3 scripts/build_vector_index.py --incremental
```

### How It Works / 運作原理

```
播放清單 URL
  → yt-dlp 讀取影片清單（自動跳過已處理的影片）
  → yt-dlp + cookies 下載字幕（zh-Hans 優先，備選 en）
  → LLM（MiniMax）生成知識卡（與 X 書籤格式完全一致）
  → 儲存至 bookmarks/youtube/VIDEO_ID.md
  → 加入 search_index.json（source 欄位標記為 "youtube"）
```

### Notes / 注意事項

- YouTube cookies 每隔數週會過期，需重新匯出 / YouTube cookies expire periodically, re-export when needed
- 影片長度 < 60 秒（Shorts）自動跳過 / Videos shorter than 60s (Shorts) are skipped automatically
- 需要 `MINIMAX_API_KEY` 才能生成知識卡 / `MINIMAX_API_KEY` required for card generation
- 卡片儲存在 `bookmarks/youtube/`，與 X 書籤統一被語意召回索引 / Cards stored in `bookmarks/youtube/`, unified with X bookmarks in semantic recall

## Operating Principles / 工作原則

- Data quality first, then coverage / 先保資料品質，再追求覆蓋率
- Structure first, then sync to NotebookLM / 先結構化，再談同步到 NotebookLM
- Knowledge cards should serve reuse, not just preservation / 知識卡要服務回用，不只是保存
