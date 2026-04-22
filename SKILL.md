---
name: x-knowledge-base
description: |
  Evolve scattered inputs into a proactive, reusable personal knowledge base for AI agents.
  Use for: ingesting X/Twitter bookmarks, local notes, YouTube, GitHub, PDFs, and PubMed papers;
  generating knowledge cards; building search/vector indexes; surfacing relevant knowledge during conversations;
  and distilling durable wiki pages.
  將分散資料來源整理成可檢索、可主動召回、可沉澱成 wiki 的個人知識系統。
  使用於：抓取與整理 X/Twitter 書籤、本地筆記、YouTube、GitHub、PDF、PubMed 論文；生成知識卡；更新搜尋/向量索引；在對話中主動召回既有知識；以及沉澱長期 wiki。
---

# X Knowledge Base

From saved fragments to a reusable, proactive knowledge system.
從零散收藏走向可回用、可主動召回的知識系統。

> XKB 從 X/Twitter 書籤出發，現在已演化成多來源知識生命週期系統：ingest → card/index → recall → wiki。

## Canonical Model

XKB 目前可視為四層：

1. **Ingest layer** — 從各來源抓進原始內容，每個來源有專屬腳本
2. **Card / Index layer** — 所有來源統一走 `scripts/_card_prompt.py`，輸出相同的 9-section knowledge card
3. **Recall / Ask layer** — 在對話或明確提問時，主動浮現最相關的既有知識
4. **Wiki layer** — 經 absorb gate 後，把高價值內容沉澱為長期可讀的 wiki topics

**關鍵設計**：不管來源是書籤、論文、影片還是 GitHub repo，產出的卡片結構完全一致。
`source_type` 欄位（x-bookmark / youtube / github_fork / github_star / local-paper / pubmed）是 metadata，不是不同 schema。

---

## Unified Ingest Pipeline

所有 ingest 腳本都 import `scripts/_card_prompt.py`，共用相同的 prompt、LLM call、extract_summary、find_related_context。

| 來源 | 抓取工具 | 統一產出 |
|------|---------|---------|
| X/Twitter 書籤（scan） | `scripts/run_scan_worker.py` | `_card_prompt.py` → 9-section card → `memory/cards/` |
| X/Twitter 書籤（inbox） | `tools/bookmark_enhancer.py` | `_card_prompt.py` → 9-section card |
| YouTube 播放清單 | `scripts/fetch_youtube_playlist.py` | `_card_prompt.py` → 9-section card |
| GitHub forks/stars | `scripts/fetch_github_repos.py` | `_card_prompt.py` → 9-section card |
| 本地筆記 / 論文 | `scripts/local_ingest.py` | `_card_prompt.py` → 9-section card |
| PubMed 開放論文 | `scripts/fetch_pubmed.py` 抓 → `local_ingest.py` ingest | `_card_prompt.py` → 9-section card |

### 9-Section Card Format

每張卡固定包含：

1. 核心問題與結論
2. Claim 等級（Attested / Scholarship / Inference）
3. 關鍵論點
4. False Friends
5. 驚訝點
6. 與現有知識的關係
7. 雙語摘要（ZH + EN，用於 search index）
8. 對使用者的價值
9. 原始來源

---

## Main Entry Points

### X/Twitter 書籤

```bash
# 掃描 memory/bookmarks/ 找尚未生成 card 的書籤，批次生成
python3 scripts/run_scan_worker.py --limit 20
python3 scripts/run_scan_worker.py --dry-run          # 預覽，不呼叫 API
python3 scripts/run_scan_worker.py --local-only       # 只列出，不送 API
python3 scripts/run_scan_worker.py --category 01-openclaw-workflows --limit 5
```

### 學術論文 / 本地筆記

```bash
# 直接 ingest 資料夾
python3 scripts/local_ingest.py /path/to/notes/ --category learning
python3 scripts/local_ingest.py /path/to/papers/ --category research --tag pubmed

# 從 PubMed 抓開放論文再 ingest
python3 scripts/fetch_pubmed.py "antimicrobial resistance" --limit 20 --out /tmp/papers
python3 scripts/local_ingest.py /tmp/papers/ --category research --tag pubmed,amr
```

### YouTube

```bash
python3 scripts/fetch_youtube_playlist.py --dry-run
python3 scripts/fetch_youtube_playlist.py --playlist "URL" --limit 5
bash scripts/run_youtube_sync.sh
```

### GitHub

```bash
python3 scripts/fetch_github_repos.py --forks --stars
python3 scripts/fetch_github_repos.py --forks --limit 20 --dry-run
bash scripts/run_github_sync.sh
```

### 建立索引 / 更新圖譜

```bash
python3 scripts/build_vector_index.py --incremental   # 新增 card 後執行
python3 demo/generate_graph.py                         # 重新生成 demo 圖譜
```

### 問答

```bash
python3 scripts/xkb_ask.py "What are the alternatives to RAG?"
python3 scripts/xkb_ask.py "你的問題" --format chat
python3 scripts/xkb_ask.py "你的問題" --json
```

### Wiki 同步

```bash
python3 scripts/suggest_topic_map.py --review
python3 scripts/sync_cards_to_wiki.py --apply --limit 20
python3 scripts/sync_cards_to_wiki.py --review-rejects
python3 scripts/sync_cards_to_wiki.py --explain "https://example.com/article"
```

### 搜尋

```bash
bash scripts/search_bookmarks.sh "openclaw seo"
python3 scripts/recall_for_conversation.py "agent workflow 記憶召回"
python3 scripts/recall_for_conversation.py "AI SEO 案例" --json
```

---

## Demo UI

互動式知識圖譜（Next.js 三欄式：Knowledge Graph | Chat | Evidence Panel）。

```bash
python3 demo/generate_graph.py           # 從 search_index.json 生成圖譜資料
cd demo/xkb-demo-ui && npm run dev       # → http://localhost:3000
```

> `demo/xkb-demo-ui/public/graph-data.json` 是個人資料，已 gitignore，不會進 repo。

---

## Proactive Conversation Recall

把這個 skill 當成對話中的第二層記憶：當前對話若需要案例、做法、脈絡或可行動參考，先用主動召回找你過去存過的相關知識，再決定要不要主動提給使用者。

### 什麼時候觸發

只有同時滿足以下兩件事才主動召回：
1. 當前對話值得查既有知識庫
2. 查到的結果真的能推進對話

強觸發類型：
- 做法、workflow、SOP、framework 類問題
- 案例、靈感、參考、對照類問題
- 策略、決策、優先順序類問題
- 明顯落在高頻知識領域：OpenClaw / agent / workflow、SEO / GEO、AI 影片、automation、startup、research

不要觸發：
- 純閒聊
- 簡單事務題
- 同一輪已提醒過，使用者沒追問
- 結果沒有原文連結、摘要太空

### 使用方式

```bash
# 自動模式（有向量索引走語意，無則降級 keyword）
python3 scripts/recall_for_conversation.py "主動召回 既有知識 對話回用"
python3 scripts/recall_for_conversation.py "OpenClaw workflow agent memory" --limit 5 --json

# 強制 keyword
python3 scripts/recall_for_conversation.py "query" --no-semantic
```

### 回覆原則

- 每輪最多主動提 1 次，最多帶 1–2 篇
- 格式：一句話摘要 + 為什麼相關 + 原文連結
- 結果普通就不要硬插話

---

## Semantic Recall (Optional)

```bash
# 建立向量索引（首次 or 重建）
export EMBEDDING_PROVIDER=gemini
export GEMINI_API_KEY=your_key
python3 scripts/build_vector_index.py

# 增量更新（新增 card 後）
python3 scripts/build_vector_index.py --incremental
```

`vector_index.json` 是個人資料，不進 repo。

---

## Schema Migration

如果舊資料（用舊流程產生的 card）需要對齊新 schema：

```bash
python3 scripts/migrate_schema.py --dry-run   # 預覽
python3 scripts/migrate_schema.py             # 執行
```

執行內容：
- `search_index.json`：補 `source_type`、`enriched` 欄位
- card frontmatter：`type: x-knowledge-card` → `knowledge-card`，補 `sensitivity: public`、`source_type`
- 移除 `<think>` 前綴（舊版 LLM 輸出殘留的思考 block）

---

## Security & Privacy

> Full data flow reference: `docs/data-flow.md`

**BIRD_AUTH_TOKEN / BIRD_CT0** 是 X/Twitter session cookie，**不是**一般 API key。
- 任何持有者都能以你的身份讀取私人書籤
- 只存在系統環境變數或 `.secrets/x-knowledge-base.env`（已 gitignore）
- 若外洩：立刻登出 X 讓 session 失效

哪些資料會送到外部：
- 書籤 / 論文文字 → LLM API（enrichment）
- 外部文章 URL → r.jina.ai（擷取正文）
- card title + summary → embedding API（向量索引）
- PubMed 搜尋詞 → NCBI public API

以下永遠留在本機：raw bookmark 檔、search_index.json、wiki pages。

---

## Environment Variables

### 必要

| 變數 | 用途 | 敏感等級 |
|------|------|---------|
| `LLM_API_KEY` | Card 生成、wiki sync、ask | Standard API key |
| `LLM_API_URL` | LLM endpoint（Anthropic-compatible） | — |
| `LLM_MODEL` | 模型名稱 | — |
| `BIRD_AUTH_TOKEN` | X/Twitter 書籤抓取 | **高 — session cookie** |
| `BIRD_CT0` | X/Twitter 書籤抓取 | **高 — session cookie** |

### 選用

| 變數 | 用途 | 預設值 |
|------|------|-------|
| `GEMINI_API_KEY` | 向量 embedding、health_check | 降級為 keyword search |
| `OPENCLAW_WORKSPACE` | workspace 根路徑 | `~/.openclaw/workspace` |
| `BOOKMARKS_DIR` | 書籤目錄 | `$WORKSPACE/memory/bookmarks` |
| `CARDS_DIR` | 知識卡目錄 | `$WORKSPACE/memory/cards` |
| `YOUTUBE_PLAYLIST_URL` | YouTube 播放清單 URL | — |

---

## Key Files

### Ingest

| 檔案 | 說明 |
|------|------|
| `scripts/_card_prompt.py` | **核心共用模組** — unified prompt、llm_call、extract_summary、find_related_context |
| `scripts/run_scan_worker.py` | X/Twitter：掃描 bookmarks 目錄，批次生成 card |
| `tools/bookmark_enhancer.py` | X/Twitter：處理 inbox 書籤 |
| `scripts/local_ingest.py` | 本地筆記 / 論文 → card |
| `scripts/fetch_pubmed.py` | 從 PubMed Central 抓開放全文 |
| `scripts/fetch_youtube_playlist.py` | YouTube 播放清單字幕 → card |
| `scripts/fetch_github_repos.py` | GitHub forks/stars → card |

### Index & Search

| 檔案 | 說明 |
|------|------|
| `scripts/build_vector_index.py` | 建立 / 增量更新語意向量索引 |
| `scripts/search_bookmarks.sh` | 關鍵字搜尋 |
| `scripts/recall_for_conversation.py` | 對話主動召回（semantic + keyword fallback） |
| `scripts/recall_router.py` | 召回路由：分類 → 派送到對應模組 |
| `scripts/xkb_ask.py` | 自然語言問答（返回有來源的回答） |
| `scripts/xkb_recall_server.py` | MCP server，讓 AI agent 工具呼叫 xkb_recall |

### Wiki

| 檔案 | 說明 |
|------|------|
| `scripts/sync_cards_to_wiki.py` | Cards → wiki topic pages（LLM absorb gate） |
| `scripts/suggest_topic_map.py` | 從現有 cards 自動建議 topic map |
| `scripts/distill_memory_to_wiki.py` | 對話記憶 → staging candidates → wiki |
| `scripts/lint_wiki.py` | wiki 健康檢查：孤立頁、過期頁、gap topics |

### Demo

| 檔案 | 說明 |
|------|------|
| `demo/generate_graph.py` | 從 search_index.json 生成 graph-data.json |
| `demo/xkb-demo-ui/` | Next.js 三欄式互動圖譜 |

### Maintenance

| 檔案 | 說明 |
|------|------|
| `scripts/migrate_schema.py` | 舊資料 schema migration（補欄位、正規化 type） |
| `scripts/health_check.py` | 語意衝突偵測、gap 分析、重複偵測 |
| `scripts/status_knowledge_pipeline.py` | 一眼看全 pipeline 狀態 |
| `scripts/audit_index_quality.py` | 索引品質稽核 |

---

## When to Read Additional Reference Files

- 主動召回設計原則與觸發規則：`references/conversation-recall.md`
- 全量重建計畫：`references/rebuild-v2-plan.md`
- NotebookLM 匯出格式：`references/notebooklm-schema.md`

---

## Operating Principles

- 不管來源是什麼，產出的卡片結構完全一致（`_card_prompt.py` 是唯一的格式定義）
- 個人資料（cards、wiki、graph-data、vector index）永遠留在本機，不進 repo
- Data quality first — 先保品質再追覆蓋率
- Knowledge cards 要服務回用，不只是保存
