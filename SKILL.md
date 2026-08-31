---
name: "x-knowledge-base"
description: "Evolve scattered inputs into a proactive, reusable personal knowledge base for AI agents.\nUse for: ingesting X/Twitter bookmarks, local notes, YouTube, GitHub,"
---

# X Knowledge Base

From saved fragments to a reusable, proactive knowledge system.
從零散收藏走向可回用、可主動召回的知識系統。

> XKB 從 X/Twitter 書籤出發，現在已演化成多來源知識生命週期系統：ingest → card/index → recall → wiki → ClawHub release.
>
> **圖文卡原則**：XKB knowledge card 可以是 multimodal。圖片不是附件裝飾，而是 source evidence；對 X/Twitter 這類 prompt screenshot、結果圖、對比圖，先用 `scripts/media_ingest.py` 下載圖片並產生 `## 10. Media Evidence`（OCR + Vision Notes），再進 card/index/wiki。
>
> **發佈原則**：`skills/x-knowledge-base/` 只放 reusable skill code；個人資料與執行期資料放在 `memory/x-knowledge-base/`、`memory/cards/`、`memory/bookmarks/`。要對外發佈時，優先確認 release package 不含私密資料，再用 ClawHub 同步版本。詳見 `docs/RUNTIME_PATHS.md`。

## Personal data paths

- `memory/cards/` — generated cards
- `memory/bookmarks/` — raw bookmarks, `search_index.json`, `vector_index.json`
- `memory/x-knowledge-base/wiki/` — wiki runtime data (`index.md`, `log.md`, `topic-map.json`, `review-decisions.json`, `topics/`, `_staging/`)
- `wiki/` — compatibility symlink to `memory/x-knowledge-base/wiki/`
- `skills/x-knowledge-base/` — code/doc/sample zone only

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

每張卡固定包含 9 個主 section；若來源含圖片，追加第 10 節 `Media Evidence`：

1. 核心問題與結論
2. Claim 等級（Attested / Scholarship / Inference）
3. 關鍵論點
4. False Friends
5. 驚訝點
6. 與現有知識的關係
7. 雙語摘要（ZH + EN，用於 search index）
8. 對使用者的價值
9. 原始來源
10. Media Evidence（如有）：圖片來源、local path、OCR、vision notes、可複用圖像 / prompt pattern、不確定處

### Multimodal Media Ingest

對 X/Twitter 圖文來源，先把圖片變成可索引文字證據：

```bash
# 對單一 bookmark/card 補上圖片 OCR + vision notes
python3 scripts/media_ingest.py memory/bookmarks/03-video-prompts/2049049884284858826.md --limit 4

# 已有 Media Evidence 時重跑
python3 scripts/media_ingest.py memory/bookmarks/03-video-prompts/2049049884284858826.md --force --limit 4

# 只檢查圖片 URL，不下載、不呼叫 vision
python3 scripts/media_ingest.py memory/bookmarks/03-video-prompts/2049049884284858826.md --dry-run
```

輸出會寫回原 markdown：

- `## 10. Media Evidence`
- `### OCR`：圖片內可讀 prompt / UI / 表格 / 文字
- `### Vision Notes`：圖像類型、版面結構、可複用 pattern、風險與不確定

圖片本體存到：`memory/x-knowledge-base/media/<source-id>/`。


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

### ClawHub 發佈 / 同步

```bash
# 先確認登入
clawhub whoami

# 檢查目前 registry 狀態
clawhub inspect x-knowledge-base --json --no-input

# 發佈新版（從 skill 目錄）
clawhub publish . --slug x-knowledge-base --name "X Knowledge Base" --version 1.0.x --changelog "..."

# 若只是本地變更後同步 registry
clawhub sync
```

**發佈前檢查：**
- `skills/x-knowledge-base/` 內是否只剩可公開內容
- `.secrets/`、私密 token、個人資料、runtime 產物都不應進 release package
- README 類文件不必額外堆；SKILL.md 要能獨立說清楚怎麼用、怎麼發佈

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

> `demo/xkb-demo-ui/public/graph-data.json` 是個人/generated 資料，不應進入 release package。公開版本只保留 sanitized sample。

---

## Recall Mandate

**每次回應實質訊息前，必須先呼叫 `xkb_recall` tool。**

傳入用戶的原始訊息作為 `message`。tool 會自動判斷要不要撈、撈什麼。

- 有結果 → 把相關知識融入回答（一句摘要 + 為什麼相關）
- 空字串 → 直接回答，不提知識庫

**實質訊息定義：** 凡涉及做法、策略、決策、案例、工具、AI、SEO、影片、自動化、專案、知識領域的問題。

**可跳過 recall：** 純問候（早安、哈哈）、單字確認（好、收到、OK）、笑話。

`xkb_recall` 不呼叫 LLM，只做 keyword + wiki 搜尋，latency < 2s，token 開銷為零。

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

For a portable setup from a clean GitHub checkout, read `docs/embedding-configuration.md`. It documents the sanitized `.env.example` and `config/embedding.example.json`, workspace isolation, provider/model compatibility, credential rotation, redaction, fail-fast behavior, and mock/dry-run verification. No Hermes or OpenClaw private path is required.

```bash
# 建立向量索引（首次 or 重建）
export EMBEDDING_PROVIDER=gemini
export GEMINI_API_KEY=your_key
python3 scripts/build_vector_index.py

# 增量更新（新增 card 後）
python3 scripts/build_vector_index.py --incremental
```

`vector_index.json` 是個人/generated 資料，預設位於 `memory/bookmarks/`，不進 release package。

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

以下永遠留在本機資料區：raw bookmark 檔、search_index.json、vector_index.json、wiki pages、staging candidates。

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

## Before you push

Run `/code-review high <base>..HEAD` over the diff, and act on what comes back.

This is not ceremony. On 2026-08-31 a 48-file change had been through six
rounds of self-checking, including one billed as a complete sweep, and 233
tests passed. An outside review found eleven issues; eight were real and none
had been found from the inside. Two of them silently destroyed knowledge:
synthesis erasing every claim governance had promoted since the last run, while
reporting that the page had nothing to digest, and deduplicated cards no longer
counting as processed, so the worker would regenerate the duplicates
deduplication had just removed.

Tests catch what used to work and stopped. They cannot catch something that was
wrong from the first line — both of those bugs were green. And the owner of
this system does not read code, so there is no second reader by default.

What makes it work is that the reviewer starts cold. It does not inherit the
author's reasoning, so it cannot be persuaded by intent; it only sees what the
code does. A reviewer given the author's context agrees with the author, and
carries the same blind spots.

Verify each finding before acting. Three of the eleven were not worth changing,
and its read of three failing tests as Windows environment artefacts was
correct. It is wrong sometimes — differently from how you are wrong, which is
the entire value.

`/code-review ultra` is a deeper cloud review, but it is billed and only the
owner can start it; never attempt to launch it.

## What the schedules run

Schedules live on the host, not in this repository, so nothing here would
otherwise say which scripts they call. These are the entry points; everything
else runs because one of them, or one of the tools below, calls it.

Every one of them is a script except the YouTube sync, which genuinely needs
a model — it reads transcripts and writes cards. The rest were agent prompts
until 2026-08-31, and each carried the same defect: a job that hands a model a
fixed command and asks it to report back can report success without having run
it. That is how the 08-28 ingestion "succeeded" while writing nothing, how
governance absorbed a batch it could not embed, and how distillation ran for
seven days reading an inbox nobody writes to. Models make judgements here;
they do not carry pipelines.

| When (Asia/Taipei) | Entry point | What it is for |
| --- | --- | --- |
| 02:00 | `run_bookmark_batch.sh` | Turn fetched bookmarks into cards, embed them, and say so when the queue is not shrinking — at five items a night it never was. |
| 03:00 | `run_github_sync.sh` | Sync starred and forked repositories. |
| 03:15 | `run_candidate_governance.sh` | Absorb candidates that clear the gates into topic pages, bounded and reversible, then embed what it wrote — knowledge in the wiki but not in the index is findable by keyword and invisible to recall, so the embedding failing fails the job. |
| 04:00 | `monitor_youtube_playlist.py` | Fetch new transcripts and prepare them for card generation. |
| 09:00 | `health_check_notify.py` | The one message that says whether anything is wrong. Runs from plain cron, without a model, so it still speaks when everything else is down. |
| 13:30 | `run_ingestion_batch.sh` | Sync enriched cards into the search index, embed what changed, and verify the index was actually written. |
| 14:30, 22:30 | `run_wiki_mirror.sh` | Copy the wiki into the OpenClaw memory-wiki vault so agents can reach it through `openclaw wiki search`, and fail loudly if the vault was not written. |
| 15:30, 21:30 | `run_distill_batch.sh` | Extract durable claims from the day's conversations and notes into candidates. Fails when it can read nothing at all — that is how it was blind for seven days without complaining. |
| twice daily | `recommend_from_profile.sh` | Surface reading recommendations from the topic profile. |

## Tools you run by hand

| Tool | When you would reach for it |
| --- | --- |
| `xkb_import_l1_traces.py` | A machine other than this one wrote conversation traces into `runtime/l1-traces/`; this carries them into the shared knowledge service. Nothing on the VPS has written there since 2026-08-24, when Hermes replaced OpenClaw as the scheduler — conversations now reach the service directly through the agent hook. |


Everything else in `scripts/` is either on a schedule or called by something
that is. These six are neither, and that is correct — they answer a question
or set something up, on demand. They are listed because an unlisted script
with no caller is indistinguishable from one that was forgotten.

| Tool | When you reach for it |
| --- | --- |
| `test_recall_regression.py` | After anything that touches recall. 15 cases: six that must find something, nine that must stay quiet. Isolates its own session state, so the result does not depend on what else ran today. |
| `smoke_test_pipeline.sh` | After changing the wiki pipeline — checks each stage still produces what the next one expects. |
| `xkb_synthesize_topic.py` | When a topic page has accumulated more bullets than anyone will read. Writes a review draft; `--apply` merges it back. The daily summary reports how many pages are past that point. |
| `topic_guide_generator.py` | To produce a domain guide from the cards — terminology, reading order, where the consensus is and where it is missing. |
| `setup_xbrain.sh` | Once per machine, to install the hybrid search runtime. |
| `full_sync_v2.py` | To rebuild a workspace from its sources. |
| `build_release_package.sh` | To package the skill for publication, with a secret scan and an allowlist. |

## Maintenance Verification

Use this sequence for backup, health checks, and index maintenance:

1. Create a timestamped backup of wiki data and any existing search/vector indexes before mutation; record the backup path. **Completion criterion:** the backup directory exists and contains each source that was present.
2. Run pipeline status, wiki lint, and health checks before rebuilding indexes; record their counts and warnings separately. **Completion criterion:** a pre-maintenance report distinguishes clean results from actionable warnings.
3. Treat each queue independently: inspect both the external/task queue and wiki `_staging/` pending candidates rather than using one queue’s empty result as proof that all review work is complete. **Completion criterion:** report pending counts for every queue that the status tools expose.
4. Run the incremental vector-index rebuild after source and wiki checks complete. **Completion criterion:** the command reports saved vectors and the output index exists.
5. Run several representative recall smoke tests covering distinct knowledge areas. **Completion criterion:** each test returns an explicit search mode and, when relevant, both wiki and source-card evidence.
6. Report lint warnings and pending staging candidates as remaining work, not as successful indexing; use batch triage before approving a large historical backlog. **Completion criterion:** the final status separates completed maintenance from unresolved review work.
