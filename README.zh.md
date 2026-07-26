<p align="right">
  <a href="./README.md">English</a> · <strong>繁體中文</strong>
</p>

<p align="center">
  <img src="./assets/readme/hero.svg" width="100%" alt="X Knowledge Base 把零散來源轉化為結構化知識與主動召回">
</p>

<p align="center">
  <a href="#快速開始"><strong>快速開始</strong></a> ·
  <a href="#運作方式"><strong>運作方式</strong></a> ·
  <a href="#選擇運行模式"><strong>運行模式</strong></a> ·
  <a href="./docs/data-flow.md"><strong>隱私與資料流</strong></a> ·
  <a href="#授權"><strong>授權</strong></a> ·
  <a href="https://youtu.be/JWgm6ky_pys"><strong>概念影片</strong></a>
</p>

<p align="center">
  <a href="./LICENSE"><img alt="授權：PolyForm Noncommercial 1.0.0" src="https://img.shields.io/badge/license-PolyForm%20Noncommercial%201.0.0-E07A3F"></a>
</p>

## 知識不該在收藏後消失

書籤、筆記、影片、repository、論文與對話累積得很快。多數知識工具能幫你保存，但真正困難的是後半段：在實際工作時，連同證據一起找回正確的想法。

**X Knowledge Base（XKB）** 是給人與 AI Agent 使用的 local-first 知識生命週期系統。它把異質來源轉成統一格式的結構化卡片，透過混合搜尋找回內容，把值得長期保留的洞見蒸餾成人類可讀的 wiki，並在對話中主動浮現相關脈絡。

這個 repository 放可重用的工具；你的卡片、索引、wiki、圖譜、憑證與 runtime state 留在自己的 workspace，不會進入公開 repo。

<p align="center">
  <img src="./assets/readme/lifecycle.svg" width="100%" alt="XKB 知識生命週期：捕捉、結構化、檢索、蒸餾與重新浮現">
</p>

## XKB 有什麼不同

### 多種來源，共用一種知識格式

本地 Markdown、X/Twitter 書籤、YouTube 字幕、GitHub repository、PDF 與 PubMed 論文，都會收斂到同一種九段式核心卡片 schema。檢索與綜合因此有穩定單位，而不是一堆彼此不相容的來源摘要。

### 先檢索，再生成

XKB 先搜尋已蒸餾的 wiki，再找底層證據卡片。Full 模式由 XBrain/GBrain 提供向量＋關鍵字的 Reciprocal Rank Fusion（RRF）混合檢索；runtime 不可用時，則自動降級到本地關鍵字與平面向量索引。

### 蒸餾必須通過 gate

卡片是證據單位，wiki 是持久理解。`sync_cards_to_wiki.py` 與 `distill_memory_to_wiki.py` 使用 absorb/staging 工作流，不會讓每一筆收藏都自動變成長期知識。

### 為 Agent 對話而生

`recall_for_conversation.py`、`xkb_ask.py` 與 MCP server 會連同來源連結一起提供相關知識。目標不是再造一個要你主動翻找的倉庫，而是讓脈絡在 Agent 或人需要時回來。

## 快速開始

最小可用流程只需匯入本地 Markdown 並搜尋，不需要 X/Twitter cookies、Bun、GBrain、Postgres 或 OpenClaw cron。

### 1. Clone 並建立私人 workspace

```bash
git clone https://github.com/Hidicence/x-knowledge-base.git
cd x-knowledge-base

export OPENCLAW_WORKSPACE="$HOME/.openclaw/workspace"
mkdir -p \
  "$OPENCLAW_WORKSPACE/memory/cards" \
  "$OPENCLAW_WORKSPACE/memory/bookmarks"
```

### 2. 設定 LLM

如果已使用 OpenClaw，請在 `config/llm.json` 選擇你的 OpenClaw 安裝中可用的模型。

獨立使用時，設定 OpenAI-compatible endpoint：

```bash
export LLM_API_URL="https://your-provider.example/v1"
export LLM_API_KEY="your-key"
export LLM_MODEL="your-model"
```

> 不要 commit 真實憑證。環境變數與私人 OpenClaw 設定屬於 runtime state，不是 repository 內容。

### 3. 匯入、建索引、提問

```bash
# 把 demo/sample-notes 換成自己的 Markdown 目錄。
python3 scripts/local_ingest.py demo/sample-notes \
  --category learning --limit 3

bash scripts/build_search_index.sh
bash scripts/search_bookmarks.sh "agent memory"
python3 scripts/xkb_ask.py "這些筆記之間浮現了哪些模式？"
```

產生的卡片與索引會寫入 `$OPENCLAW_WORKSPACE/memory/`，不會寫進這個 repository。

## 運作方式

```text
來源
  本地筆記 · X 書籤 · YouTube · GitHub · PDF/PubMed · 對話記憶
       │
       ▼
共用卡片契約
  來源 adapters + scripts/_card_prompt.py + scripts/_llm.py
       │
       ▼
知識卡片
  統一九段格式 · 來源連結 · Claim 等級 · 雙語摘要
       │
       ├──────────────► XBrain/GBrain 混合檢索（主要）
       │                    向量 + 關鍵字 + RRF
       │
       ├──────────────► search_index.json / vector_index.json（降級）
       │
       ▼
Absorb gate
  卡片 + 對話記憶 → staging/review → 持久 wiki 主題
       │
       ▼
主動召回
  wiki 優先 → 證據卡片 → 帶來源的回答
```

### 九段式知識卡

所有支援來源都產生同樣的知識結構：

1. 核心問題與結論
2. Claim 等級：Attested、Scholarship 或 Inference
3. 關鍵論點
4. False Friends：在技術脈絡中與日常意思不同的術語
5. 驚訝點
6. 與現有知識的關係
7. 供檢索使用的雙語摘要
8. 可執行價值
9. 原始來源與相關連結

來源若包含圖片，可透過 `scripts/media_ingest.py` 追加第十段 **Media Evidence**，保存 OCR 與 vision notes。

## 選擇運行模式

從能解決問題的最小模式開始。

| 模式 | 適用情境 | 檢索方式 | 額外 runtime |
| --- | --- | --- | --- |
| **Lite** | 第一次使用、本地筆記、小型知識庫 | `search_index.json` 關鍵字搜尋 | Python + 一個 LLM |
| **Enhanced** | 不架資料庫服務，但需要語意降級搜尋 | 平面 `vector_index.json` | Gemini、OpenAI 或本地 Ollama embedding |
| **Full / XBrain** | 大型知識庫與 Agent workflow | 向量＋關鍵字 hybrid RRF | OpenClaw + GBrain/XBrain |

### 啟用平面語意檢索

```bash
export EMBEDDING_PROVIDER=gemini
export GEMINI_API_KEY="your-key"
python3 scripts/build_vector_index.py --incremental
```

`build_vector_index.py` 也支援 `.env.example` 中記錄的 embedding provider；若希望 embedding 全程留在本機，可使用 Ollama。

### 啟用 XBrain/GBrain

執行前先閱讀 `scripts/setup_xbrain.sh`：它會安裝或更新 Bun/GBrain，並修改本機 OpenClaw 設定。

```bash
bash scripts/setup_xbrain.sh
python3 scripts/health_check_pipeline.py
```

XBrain 可用時，ingest 腳本會 best-effort 嘗試替新卡片建立索引，召回也可使用 `xbrain_recall.py`。本地卡片寫入不依賴 XBrain；無法連線時，召回會降級使用本地索引。

## 加入不同來源

```bash
# 本地 Markdown / 純文字
python3 scripts/local_ingest.py /path/to/notes --category learning

# 已保存在 workspace 的 X/Twitter 書籤
python3 scripts/run_scan_worker.py --limit 20

# YouTube 播放清單字幕
python3 scripts/fetch_youtube_playlist.py --playlist "PLAYLIST_URL" --limit 5

# GitHub forks 與 stars
python3 scripts/fetch_github_repos.py --forks --stars --limit 20

# PubMed 開放存取論文
python3 scripts/fetch_pubmed.py "retrieval augmented generation" \
  --limit 10 --out /tmp/xkb-papers
python3 scripts/local_ingest.py /tmp/xkb-papers --category research --tag pubmed
```

不同來源 adapter 會收斂到共用卡片契約。多數直接使用 `_card_prompt.py`；本地檔案 ingest 目前保留一份相容 prompt 實作，但仍輸出相同核心 schema。

## 把卡片蒸餾成持久知識

Wiki 是經過篩選的成品層，不是每一筆收藏的鏡像。

```bash
# 讓卡片通過 absorb gate，更新 wiki 主題。
python3 scripts/sync_cards_to_wiki.py --apply --limit 20

# 從近期對話記憶擷取值得長期保留的候選內容。
python3 scripts/distill_memory_to_wiki.py --stage --days 3

# 檢查 staging 檔案後，再套用選定候選。
python3 scripts/distill_memory_to_wiki.py --apply \
  --staging-file "$OPENCLAW_WORKSPACE/memory/x-knowledge-base/wiki/_staging/FILE.md" \
  --approve-all
```

預設 runtime layout 記錄在 [`docs/RUNTIME_PATHS.md`](./docs/RUNTIME_PATHS.md)。

## 提問與召回

```bash
# 對 wiki 主題與證據卡片進行有來源的問答
python3 scripts/xkb_ask.py "我收藏過哪些 RAG 替代方案？"

# 適合聊天工作流的精簡輸出
python3 scripts/xkb_ask.py "absorb gate 是什麼？" --format chat

# 對話時召回
python3 scripts/recall_for_conversation.py \
  "我需要可靠的 Agent memory workflow" --json
```

### MCP Tool

把召回能力提供給 Claude Code 或其他 MCP client：

```json
{
  "mcpServers": {
    "xkb-recall": {
      "command": "python3",
      "args": ["/absolute/path/to/x-knowledge-base/scripts/xkb_recall_server.py"],
      "env": {
        "OPENCLAW_WORKSPACE": "/absolute/path/to/your/workspace"
      }
    }
  }
}
```

## 瀏覽知識圖譜

Demo 是 Next.js 三欄探索器：**Knowledge Graph · Chat · Evidence**。

```bash
python3 demo/generate_graph.py
cd demo/xkb-demo-ui
npm install
npm run dev
# http://localhost:3000
```

產生的圖譜資料放在私人 workspace；repository 只保留去識別化的 schema/sample。

## 隱私模型

XKB 是 local-first，不代表自動 local-only。產物保留在本機，但雲端 enrichment 與 embedding 仍會把選定內容送到已設定的服務。

- 知識卡、索引、wiki 主題、圖譜與 queues 留在你的 workspace。
- 執行 enrichment 時，本地文件與抓取的來源文字會送到你設定的 LLM。
- 建立向量索引時，卡片標題與摘要會送到選定的 embedding provider。
- X/Twitter session cookies 是高敏感憑證，絕對不能進 source control。
- 使用 Ollama 可讓 embedding 留在本機；不執行雲端 enrichment 時，raw capture 也能維持本地處理。

處理敏感資料前，請先讀 [`docs/data-flow.md`](./docs/data-flow.md)。它列出每個來源與腳本可能接觸的第三方服務。

## Repository 與 runtime 邊界

```text
x-knowledge-base/                         可重用程式、文件、模板
$OPENCLAW_WORKSPACE/memory/cards/         產生的知識卡
$OPENCLAW_WORKSPACE/memory/bookmarks/     原始來源 + 降級索引
$OPENCLAW_WORKSPACE/memory/x-knowledge-base/wiki/
                                          staging + 蒸餾後的 wiki 主題
```

不要 commit `.env`、session cookies、API keys、私人卡片、索引、wiki、queues、logs 或機器專屬路徑。

## 維運與驗證

```bash
# Pipeline 健康與 canonical wiki 路徑
python3 scripts/health_check_pipeline.py

# 索引品質
python3 scripts/audit_index_quality.py
python3 scripts/prune_duplicate_index_rows.py --dry-run

# Wiki 結構
python3 scripts/lint_wiki.py

# 發布 repository 變更前
git diff --check
python3 scripts/health_check_pipeline.py
```

## 專案地圖

| 區域 | 主要檔案 |
| --- | --- |
| 卡片契約與 LLM | `scripts/_card_prompt.py`, `scripts/_llm.py`, `scripts/local_ingest.py` |
| 來源 adapters | `local_ingest.py`, `fetch_youtube_playlist.py`, `fetch_github_repos.py`, `fetch_pubmed.py` |
| 檢索 | `xbrain_recall.py`, `build_search_index.sh`, `build_vector_index.py` |
| 主動召回 | `xkb_ask.py`, `recall_for_conversation.py`, `xkb_recall_server.py` |
| 蒸餾 | `sync_cards_to_wiki.py`, `distill_memory_to_wiki.py` |
| 維運 | `health_check_pipeline.py`, `status_knowledge_pipeline.py`, `lint_wiki.py` |
| Demo | `demo/generate_graph.py`, `demo/xkb-demo-ui/` |

## 設計原則

- **理解優先於保存。** 卡片應回答來源幫你理解什麼，而不是只摘要它說了什麼。
- **多種來源，共用一種 schema。** 穩定的知識單位讓跨來源檢索成為可能。
- **先有證據，再做綜合。** 持久結論必須能追溯到卡片與原始 URL。
- **用 gate 抵抗自動堆積。** Wiki 透過拒絕低價值內容來維持訊號品質。
- **可優雅降級。** Full hybrid retrieval 是選配；缺少它仍可使用知識庫。
- **私人資料留在私人空間。** 可重用工具進 git，runtime 知識不進 git。

## 參與貢獻

先讀 [`SKILL.md`](./SKILL.md)、[`docs/data-flow.md`](./docs/data-flow.md) 與 [`docs/xkb-wiki-architecture.md`](./docs/xkb-wiki-architecture.md)。歡迎提出 issue 與 pull request。

## 授權

XKB 依 [PolyForm Noncommercial License 1.0.0](./LICENSE) **開放原始碼供非商業用途使用**。依照授權條款，個人研究、學習、實驗、興趣專案，以及符合條件的非商業組織用途均可使用。此授權不包含商業使用；商業使用必須另向授權人取得書面授權。

這是 **source-available 授權，不是 OSI 認可的開源授權**。使用、修改或散布 XKB 前，請先閱讀完整授權條款。

Required Notice: Copyright 2026 Hidicence. Licensed under the PolyForm Noncommercial License 1.0.0.

你的知識值得的不只是被保存，而是在重要時刻重新回來。
