# X Knowledge Base（XKB）

[**English**](./README.md) · 繁體中文

> **讓知識重新浮現 | Make Knowledge Reappear**
>
> 具有語義主動召回功能的個人知識生命週期系統——書籤、對話、筆記透過雙層檢索引擎（XBrain 混合搜尋 + wiki 蒸餾）轉化為可持久回用的知識。附帶互動式知識圖譜介面。

[![觀看簡報影片](https://img.youtube.com/vi/JWgm6ky_pys/maxresdefault.jpg)](https://youtu.be/JWgm6ky_pys)
*（點擊觀看概念介紹）*

---

## 問題所在

每天我們消費數十篇文章、討論串、資訊。當下收藏是因為感覺重要。六個月後——找不到、無法想起來、壓根不記得當初學到了什麼。

現有工具都預設你需要時得自己手動取出知識。**但知識應該自己能判斷你何時需要它。**

XKB 建立在不同的前提上：知識有生命週期。目標不是存更多，而是讓你已經消費過的內容*在適當的時機重新浮現*，並*逐步沉澱為持久理解*。

---

## 如何運作

### 完整 Pipeline

```
輸入來源
├── X/Twitter 書籤         →  xkb_minion_submit.py / xkb_minion_worker.py
├── YouTube 播放清單       →  fetch_youtube_playlist.py
├── GitHub forks / stars   →  fetch_github_repos.py
├── 本地筆記 / markdown    →  local_ingest.py
└── PubMed / 學術論文      →  fetch_pubmed.py + local_ingest.py
        │
        ▼
  scripts/_card_prompt.py   ← 所有 ingest 腳本共享
  （統一 9-section 卡片格式，不論來源）
        │
        ▼（所有 LLM 呼叫經過 scripts/_llm.py）
        │
┌─────────────────────────────────────────────────────────────┐
│  知識產物（永久，gitignored）                                │
│                                                             │
│  memory/cards/*.md          結構化 9-section 卡片           │
│  wiki/topics/*.md           蒸餾後的長期知識                 │
└─────────────────────────────────────────────────────────────┘
        │
        ▼ 每次寫卡時自動觸發
┌─────────────────────────────────────────────────────────────┐
│  主要檢索 — XBrain                                          │
│  （XKB 的語意搜尋層，由 GBrain 驅動）                        │
│                                                             │
│  • pgvector + Postgres-backed GBrain                        │
│  • Gemini 向量嵌入                                          │
│  • RRF 混合搜尋（向量 + 關鍵字）                             │
│  • Minions job runtime 作為 durable internal pipeline       │
│  • xbrain_recall.py  ← 所有腳本自動呼叫                     │
└─────────────────────────────────────────────────────────────┘
        │  XBrain 不可用時自動降級
        ▼
┌─────────────────────────────────────────────────────────────┐
│  降級檢索                                                    │
│                                                             │
│  search_index.json          關鍵字 + 摘要搜尋               │
│  vector_index.json          平面 Gemini 向量索引           │
│  build_vector_index.py      按需重建平面索引                │
└─────────────────────────────────────────────────────────────┘
        │
        ▼
  ┌─────────────────────────────────────────────────────────┐
  │  Wiki 層（wiki/topics/*.md）                             │
  │                                                         │
  │  sync_cards_to_wiki.py     外部書籤知識蒸餾              │
  │  distill_memory_to_wiki.py 對話記憶蒸餾                  │
  │                            （每日 cron，自動 staging）    │
  └─────────────────────────────────────────────────────────┘
        │
        ▼
xkb_ask.py / 主動召回層
雙層召回：wiki 主題（蒸餾知識）→ 卡片（XBrain 混合搜尋）
        │
        ▼
demo/xkb-demo-ui/  ← 互動式圖譜瀏覽器（Next.js）
Knowledge Graph | Chat | Evidence Panel
```

### 每張卡片都使用相同的 9-Section 結構

| # | 區塊 | 用途 |
|---|------|------|
| 1 | **核心問題與結論** | 回答什麼問題？結論是什麼？ |
| 2 | **Claim 等級** | Attested / Scholarship / Inference — 可信度？ |
| 3 | **關鍵論點** | 從來源萃取的 3–5 個核心論點 |
| 4 | **False Friends** | 在此脈絡下有特殊技術含義的術語 |
| 5 | **驚訝點** | 專業讀者可能會驚訝的點 |
| 6 | **與現有知識的關係** | 與現有卡片的關聯 |
| 7 | **雙語摘要** | ZH + EN（用於搜尋索引） |
| 8 | **對使用者的價值** | 可行動的方向、相關專案 |
| 9 | **原始來源** | 來源 URL 與相關連結 |

一種格式，適用所有來源。YouTube 影片、GitHub repo、PubMed 論文都產生相同結構的卡片。

---

## LLM 設定

XKB 使用**單一統一 LLM 設定**。所有腳本共享同一模型，無需分散管理環境變數。

### `config/llm.json` — 改這一個檔案就夠了

```json
{
  "model": "openai-codex/gpt-5.4"
}
```

可用模型格式（任何支援 `openclaw capability model run` 的模型）：

| 數值 | 供應商 |
|------|--------|
| `openai-codex/gpt-5.4` | ChatGPT via OpenClaw OAuth |
| `openai-codex/gpt-5.4-mini` | ChatGPT Mini via OpenClaw OAuth |
| `MiniMax-M2.7` | MiniMax via API key |
| `MiniMax-M2.5` | MiniMax M2.5 via API key |

> **運作方式：** 所有腳本呼叫 `scripts/_llm.py`，由 `openclaw capability model run` 執行。OpenClaw 自動處理所有認證（OAuth token 刷新、API key）。腳本不再需要管理 API key。

> **Embedding 是獨立的。** 語意向量搜尋使用 Gemini（`GEMINI_API_KEY`），不受 `config/llm.json` 影響。

### 獨立 / 非 OpenClaw 安裝

若不使用 OpenClaw，透過環境變數覆寫模型：

```bash
export LLM_MODEL="MiniMax-M2.5"
export LLM_API_URL="https://api.minimax.io/anthropic"
export LLM_API_KEY="your-minimax-key"
```

> 環境變數 `LLM_MODEL` 優先於 `config/llm.json`。

---

## Minions Pipeline

**X/Twitter 書籤富化主路徑，現在已預設跑在 Minions-native queue pipeline 上**，底層為 Postgres-backed GBrain。這代表主書籤富化流程已從舊的 cron-spawn scan-worker 模式切換出去。

### 已部署元件
- `scripts/xkb_minion_submit.py`
  - 掃描尚未 enrich 的書籤
  - 每張書籤提交一個具 idempotency 的 Minion job
  - 適合由 cron 週期性觸發（例如每小時一次）
- `scripts/xkb_minion_worker.py`
  - 長駐 worker daemon
  - 從 `minion_jobs` claim job
  - 執行 LLM 富化
  - 寫入最終卡片並更新 job 狀態

### 為何取代舊 cron scan-worker 模式
舊模式：
- 每 10 分鐘啟動一個新的 Python process
- 高負載時容易產生 `openclaw-infer` 殭屍 subprocess
- retry、狀態觀測、失敗恢復都不夠穩

目前 Minions-native 模式：
- 一個長駐 worker daemon
- 預設 sequential 處理（一次一個 job）
- 內建 timeout
- retry + exponential backoff
- 以 bookmark/card id 為基礎做 idempotent submission
- 可用 `gbrain jobs list` 觀察狀態

### Smoke test 已驗證
目前環境已驗證：
- worker 可成功 claim job
- LLM inference 能正常啟動
- job 會按預期流轉狀態
- 在刻意縮短 timeout 的測試下，已觀察到 active → dead 流程
- production timeout 可設為每 job 300 秒

### 監控方式
```bash
# 查看 job 狀態
gbrain jobs list

# 驗證 Minions 健康度
gbrain jobs smoke
```

## Wiki 層

Wiki 是**蒸餾輸出層**——由兩個來源建構的可讀、長期知識庫：

| 來源 | 腳本 | 作用 |
|------|------|------|
| 外部書籤 | `sync_cards_to_wiki.py` | 透過 absorb gate 蒸餾卡片洞見 |
| 對話記憶 | `distill_memory_to_wiki.py` | 從每日記憶日誌萃取決策、工作流、原則 |

### 單一 canonical source

Wiki 位於 skill 目錄內的 `wiki/`。workspace 符號連結至此：

```
~/.openclaw/workspace/wiki/  →  skills/x-knowledge-base/wiki/  (symlink)
```

這防止雙 wiki 漂移：所有工具從同一處讀取。

### Memory → Wiki 蒸餾

`distill_memory_to_wiki.py` 讀取近期 `memory/YYYY-MM-DD.md` 日誌，使用 LLM 萃取值得長期保存的洞見（決策、工作流、原則），然後 either stages them for review or applies them to wiki topic pages。

```bash
# 預覽最近 3 天會萃取什麼
python3 scripts/distill_memory_to_wiki.py --dry-run --days 3

# Stage 待審候選
python3 scripts/distill_memory_to_wiki.py --stage --days 2

# 套用所有 staged 候選（自動批准）
python3 scripts/distill_memory_to_wiki.py --apply \
  --staging-file wiki/_staging/YYYY-MM-DD-candidates.md \
  --approve-all
```

Cron job 每天 15:30 與 21:30 TST 自動執行。

### 健康檢查

```bash
python3 scripts/health_check_pipeline.py
```

檢查三件事：
1. `workspace/wiki` 是 canonical wiki 的符號連結（不是副本）
2. Recall 從正確的 wiki 路徑讀取
3. `search_index.json` 摘要覆蓋率 ≥ 70%，年齡 < 26h；向量索引新舊

---

## 主動召回層

當使用者發送訊息，XKB 使用**雙層召回**：

1. **Layer 1 — Wiki 主題**（`wiki/topics/*.md`）：蒸餾過的持久知識。回答概念性問題。
2. **Layer 2 — 卡片**（XBrain 混合搜尋，降級到 `search_index.json`）：原始證據。提供具體引用與來源。

```bash
# 對你的知識庫提問
python3 scripts/xkb_ask.py "What are alternatives to RAG?"
python3 scripts/xkb_ask.py "什麼是 absorb gate？" --format chat
python3 scripts/xkb_ask.py "agent memory design" --json
```

### 作為 MCP 工具（Claude Code / 任何 MCP 用戶端）

添加到 `.claude/settings.json`：

```json
{
  "mcpServers": {
    "xkb-recall": {
      "command": "python3",
      "args": ["/path/to/x-knowledge-base/scripts/xkb_recall_server.py"],
      "env": { "OPENCLAW_WORKSPACE": "/path/to/workspace" }
    }
  }
}
```

---

## vNext 方向

下一階段路線草案在這裡：
- `docs/xkb-vnext-roadmap-draft.md`

短版摘要：
- 把 wiki 維持為 human-readable 的成品層
- 把 graph / relations 放在 wiki 下面的 structured knowledge layer
- 把 Minions 當作大規模內部工作流的預設執行基底
- 既然書籤富化主路徑已經 Minions-native，下一步優先補 knowledge governance：confidence、staleness、supersession、typed relationships

## 快速開始

### 使用 OpenClaw

```bash
# 1. Clone 到你的 OpenClaw skills 目錄
git clone https://github.com/Hidicence/x-knowledge-base \
  ~/.openclaw/workspace/skills/x-knowledge-base

# 2. 安裝 XBrain（混合搜尋引擎）— 一次性設定
bash ~/.openclaw/workspace/skills/x-knowledge-base/scripts/setup_xbrain.sh

# 3. 將 API key 加入 ~/.openclaw/openclaw.json
#    { "env": { "GEMINI_API_KEY": "...", "LLM_API_KEY": "..." } }

# 4. 執行 demo
bash scripts/xkb_demo.sh
```

### 獨立安裝

```bash
export LLM_API_KEY="your-minimax-or-openai-key"
export LLM_API_URL="https://api.minimax.io/anthropic/v1"
export LLM_MODEL="MiniMax-M2.7"
export OPENCLAW_WORKSPACE="~/.openclaw/workspace"
export GEMINI_API_KEY="your-gemini-key"

# 安裝 XBrain（一次性）
bash scripts/setup_xbrain.sh

bash scripts/xkb_demo.sh
```

### XBrain 手動設定

若不使用設定腳本：

```bash
# 1. 安裝 Bun  https://bun.sh
curl -fsSL https://bun.sh/install | bash

# 2. Clone GBrain 執行環境
git clone https://github.com/garrytan/gbrain ~/gbrain
cd ~/gbrain && bun install && bun run src/cli.ts init

# 3. 告訴 XKB GBrain 在哪
# 加入 ~/.openclaw/openclaw.json → "env":
#   "gbrain_dir": "/absolute/path/to/gbrain"
#   "GEMINI_API_KEY": "your-key"   ← 向量嵌入必需

# 4. 驗證
python3 scripts/xbrain_recall.py "test query"
```

---

## 腳本參考

### Ingest Pipeline

所有腳本共享 `_card_prompt.py` 與 `_llm.py`——一個 prompt、一個 LLM call、一個卡片格式。

| 腳本 | 來源 | 功能 |
|------|------|------|
| `run_scan_worker.py` | X/Twitter | 掃描 bookmarks 目錄中尚未生成 card 的項目 → 卡片 |
| `run_bookmark_worker.py` | X/Twitter 佇列 | 一次處理 tiege-queue.json 中一項 |
| `fetch_youtube_playlist.py` | YouTube | 播放清單字幕 → 知識卡片 |
| `fetch_github_repos.py` | GitHub | forks/stars → repo 層級知識卡片 |
| `local_ingest.py` | 本地 / PubMed | Markdown/txt/論文 → 卡片 |
| `fetch_pubmed.py` | PubMed Central | 抓取開放論文為 markdown |
| `_card_prompt.py` | *(共用)* | 統一 prompt、卡片格式、摘要萃取 |
| `_llm.py` | *(共用)* | 統一 LLM 呼叫 via `openclaw capability model run` |

### 索引與富化

| 腳本 | 功能 |
|------|------|
| `sync_enriched_index.py` | 將富化卡片的摘要/tags 回填至 search_index.json |
| `build_vector_index.py` | 建立/更新平面 JSON 向量索引（XBrain 不可用時的降級方案） |
| `xbrain_recall.py` | XBrain 搜尋橋接——混合 RRF（pgvector + 關鍵字）；所有 recall 腳本自動使用 |

### Wiki Pipeline

| 腳本 | 功能 |
|------|------|
| `sync_cards_to_wiki.py` | 卡片 → wiki 主題頁面（經 LLM absorb gate） |
| `distill_memory_to_wiki.py` | 每日記憶日誌 → wiki 主題洞見（stage/apply 工作流） |
| `sync_cards_to_wiki.py --review` | 審查待處理 absorb 決策 |
| `lint_wiki.py` | 驗證 wiki 結構、偵測缺口主題 |
| `topic_guide_generator.py` | 生成新 wiki 主題框架 |
| `suggest_topic_map.py` | 從未覆蓋的卡片建議 topic map 更新 |

### 主動召回層

| 腳本 | 功能 |
|------|------|
| `xkb_ask.py` | 自然語言問答：wiki（Layer 1）→ 卡片經 XBrain 混合搜尋（Layer 2） |
| `recall_for_conversation.py` | 對話觸發召回（wiki + XBrain 卡片搜尋） |
| `continuity_recall.py` | MEMORY.md + wiki 查詢，確保對話連續性 |
| `contrarian_recall.py` | 浮現警告、失敗案例、反例 |
| `action_recall.py` | 行動導向召回（下一步要做什麼） |
| `xkb_recall_server.py` | MCP server，將 recall 暴露為工具 |

### 維護與可觀測性

| 腳本 | 功能 |
|------|------|
| `health_check_pipeline.py` | Wiki 符號連結完整性、recall 來源路徑、索引新舊 |
| `status_knowledge_pipeline.py` | 一眼看見全 pipeline 狀態 |
| `health_check.py` | 語意衝突偵測、缺口分析 |

---

## Demo UI — 互動式知識圖譜

```
demo/
├── xkb-demo-ui/              Next.js app — 三欄瀏覽器
│   ├── app/page.tsx          主版面：graph | chat | evidence
│   ├── components/
│   │   ├── KnowledgeGraph.tsx    力導向圖（react-force-graph-2d）
│   │   ├── ChatPanel.tsx         自然語言問答 via xkb_ask.py
│   │   └── EvidencePanel.tsx     來源卡片 + wiki 引用
│   └── public/
│       ├── graph-data.json       ← 你的個人資料（gitignored）
│       └── graph-data.sample.json  schema 參考
└── generate_graph.py         從 search_index.json 建構 graph-data.json
```

**執行 demo：**
```bash
python3 demo/generate_graph.py
cd demo/xkb-demo-ui && npm install && npm run dev
# → http://localhost:3000
```

> `graph-data.json` 是個人資料，gitignored。你的知識永遠留在本機。

---

## 逐步設定

### 1. Ingest 內容

```bash
# 本地筆記
python3 scripts/local_ingest.py notes/ --category learning

# X/Twitter 書籤
python3 scripts/run_scan_worker.py --limit 20

# YouTube 播放清單
python3 scripts/fetch_youtube_playlist.py --playlist "URL"

# GitHub repos
python3 scripts/fetch_github_repos.py --forks --stars

# PubMed 論文
python3 scripts/fetch_pubmed.py "antimicrobial resistance" --limit 20 --out /tmp/papers
python3 scripts/local_ingest.py /tmp/papers/ --category research --tag pubmed
```

### 2. 富化索引

```bash
# 將富化卡片的摘要回填至 search_index.json（每次都執行）
python3 scripts/sync_enriched_index.py

# 只在 XBrain 未設定時需要（降級模式）
python3 scripts/build_vector_index.py --incremental
```

> **XBrain（主要）：** 每個 ingest 腳本在寫卡時自動推送至 XBrain。
> 所有 recall 腳本自動呼叫 `xbrain_recall.py`——無需額外步驟。
> 在 `~/.openclaw/openclaw.json` 中設定 `gbrain_dir` 指向 GBrain 執行環境目錄。
>
> **降級：** 若 XBrain 不可用，recall 自動降級至 `search_index.json` 關鍵字搜尋。

### 3. 同步至 wiki

```bash
# 同步外部知識（書籤卡片 → wiki 主題）
python3 scripts/sync_cards_to_wiki.py --apply --limit 20

# 蒸餾對話記憶至 wiki 主題
python3 scripts/distill_memory_to_wiki.py --stage --days 3
python3 scripts/distill_memory_to_wiki.py --apply \
  --staging-file wiki/_staging/YYYY-MM-DD-candidates.md --approve-all
```

### 4. 提問

```bash
python3 scripts/xkb_ask.py "RAG 的替代方案有哪些？"
```

### 5. 檢查 pipeline 健康

```bash
python3 scripts/health_check_pipeline.py
```

預期輸出：
```
✅  wiki_canonical      workspace/wiki → skills/x-knowledge-base/wiki (symlink 正確)
✅  recall_wiki_source  Recall 從 canonical wiki 讀取
✅  index_freshness     摘要覆蓋率：212/270 (79%) | 富化：218 | 向量：471
```

---

## 自動化 Pipeline（OpenClaw Cron）

使用 OpenClaw 執行時，完整 pipeline 自動運作：

| 排程 | Job | 功能 |
|------|-----|------|
| 13:30 TST | `daily:xkb-ingestion-batch` | Ingest 新 X/Twitter 書籤 → 卡片 → 自動推送至 XBrain → sync_enriched_index |
| 15:30 TST | `daily:wiki-distill-afternoon` | 蒸餾今日記憶為 wiki 候選 |
| 21:30 TST | `daily:wiki-distill-evening` | 第二輪蒸餾，套用高信心候選 |

Pipeline 確保每次 ingestion 執行後：
1. 每張卡片自動推送至 XBrain——混合 RRF 搜尋立即可用
2. `sync_enriched_index.py` 將摘要回填至降級搜尋索引
3. 對話新洞見自動 staging 等待納入 wiki

---

## 需求

- Python 3.10+
- Node.js 18+（僅 demo UI 需要）
- OpenClaw（推薦）——處理所有 LLM 認證與 cron 自動化
- `GEMINI_API_KEY`——XBrain 語意向量嵌入必需；設定於 `~/.openclaw/openclaw.json`
- [Bun](https://bun.sh) + [GBrain](https://github.com/garrytan/gbrain) 執行環境（可選）——驅動 XBrain 混合搜尋（pgvector/PGLite + RRF）；在 `openclaw.json` 設定 `gbrain_dir` 啟用。若未設定則降級至關鍵字搜尋。

---

## Roadmap

| 版本 | 狀態 | 內容 |
|------|------|------|
| v0.1 | ✅ | 書籤 ingestion、知識卡片、關鍵字搜尋 |
| v0.2 | ✅ | 多層萃取、富化 worker、向量索引 |
| v0.3 | ✅ | Wiki pipeline：absorb gate、主題頁、memory 蒸餾 |
| v0.4 | ✅ | 本地筆記 ingest、ask 層、demo 模式、auto topic-map |
| v0.5 | ✅ | Absorb gate 可解釋性、審查決策日誌 |
| v0.6 | ✅ | 主動召回層：proactive recall、MCP server、遙測 |
| v0.7 | ✅ | Claim 等級、False Friends、雙語摘要、學術 PDF pipeline |
| v0.8 | ✅ | 統一 ingest pipeline（_card_prompt.py）；demo UI（graph + chat） |
| v0.9 | ✅ | 雙層召回（wiki 優先）；統一 LLM 設定；memory→wiki 蒸餾 pipeline；單一 canonical wiki；pipeline 健康檢查 |
| v1.0 | ✅ | XBrain 混合搜尋（pgvector + RRF）全面整合至所有 ingest 腳本；統一路徑解析；優雅的關鍵字降級 |
| v1.1 | 🔜 | **主動召回品質升級**——soft-trigger 重排序；Claim 等級浮現在召回輸出中；觸發策略擴展（超越 rule-based regex） |
| v1.2 | 🔜 | **Agent-to-Agent 知識交換**——標準化卡片格式（9-section + Claim 等級）作為 A2A 協定交換單位；`receive_card` MCP 工具；XBrain 作為接收卡片的本地消化層 |

---

## 設計原則

- **一種卡片格式，適用所有來源。** 每個來源都產生相同的 9-section 卡片。
- **分層，而非一個資料庫。** 工作記憶、整合、捕捉、輸出是不同的問題。
- **品質閘門勝過數量。** Absorb gate 保持 wiki 為蒸餾輸出層。
- **理解勝過摘要。** 卡片回答這個解決什麼問題，而非它說了什麼。
- **單一事實來源。** 一個 canonical wiki 路徑、一個 LLM 設定檔——無分散設定。
- **OpenClaw 處理認證。** 腳本呼叫 `openclaw capability model run`；token 管理不是它們的問題。
- **優雅降級。** XBrain 混合搜尋是主要檢索路徑；當 XBrain 不可用時自動啟動關鍵字降級。一切都不會壞。
- **個人資料留本機。** 圖譜資料、卡片、wiki 皆 gitignored。

---

## 貢獻

從 [`SKILL.md`](SKILL.md) 與 [`docs/xkb-wiki-architecture.md`](docs/xkb-wiki-architecture.md) 開始。

歡迎 PR 與 issues。你的知識值得被記住。
