# XKB Embedding 現況與工作樹盤點（sanitized）

- 盤點日期：2026-08-26（UTC）
- 盤點範圍：XKB skill repository；本文件只記錄狀態與可重現觀察，不修改 runtime data、queue、index、wiki 或 cards。
- 敏感資料處理：未記錄 API key、token、個資、credential 值、私有絕對路徑或 runtime 內容。以下以 `<repo>`、`<workspace>`、`<data>` 代表環境路徑。

## 1. 初始工作樹快照

開始盤點時 repository 位於 `main` branch，工作樹不是 clean。初始 `git status --short --branch` 顯示：

- 已修改（15）：`SKILL.md`、`config/llm.json`、`scripts/_llm.py`、`scripts/build_vector_index.py`、`scripts/conversation_state_parser.py`、`scripts/fetch_github_repos.py`、`scripts/health_check_notify.py`、`scripts/health_check_pipeline.py`、`scripts/run_bookmark_worker.py`、`scripts/run_github_sync.sh`、`scripts/smoke_test_pipeline.sh`、`scripts/xkb_daily_pipeline.py`、`scripts/xkb_import_l1_traces.py`、`scripts/xkb_review.py`、`tools/embedding_providers.py`。
- 未追蹤（11）：`config/embedding.json`、`config/xkb-daily-cron.example`、`memory/`、`scripts/xkb_daily_pipeline.py.bak-20260823`、`scripts/xkb_governance_baseline.py`、`tests/run_xkb_review_governance_tests.py`、`tests/smoke_governance_baseline.py`、`tests/test_conversation_state_parser.py`、`tests/test_xkb_daily_pipeline.py`、`tests/test_xkb_governance_baseline.py`、`tests/test_xkb_review_governance.py`。
- 初始 diff 統計：15 個已追蹤檔案，`730 insertions / 244 deletions`。
- `git diff --check`：通過；本盤點沒有 reset、checkout、clean、commit、push 或覆寫上述既有變更。

這些變更先視為兄弟任務／既有工作樹內容，不由本盤點覆蓋。此文件本身是本盤點新增的 sanitized artifact。

## 2. 目前 embedding 呼叫點與設定

### 呼叫點

- `scripts/build_vector_index.py`：讀取 search index，建立或增量更新 vector index；透過 `tools.embedding_providers.get_provider()` 取得 provider，再呼叫 `embed_batch()`。
- `scripts/continuity_recall.py`：查詢語意向量時透過 provider `embed()`；失敗時目前會記錄 semantic unavailable 並由呼叫端降級。
- `scripts/recall_for_conversation.py`：vector recall 查詢透過 provider `embed()`。
- `scripts/health_check.py`：保留舊的單筆 Gemini helper，但目前分析路徑沿用既有 vector index；該 helper 使用不同舊 model，禁止與目前 index 向量混算。
- `scripts/absorb_gate_semantic.py`：讀取既有 semantic vectors，不自行重新產生向量。
- `scripts/xkb_job_runner.py`、`scripts/xkb_daily_pipeline.py`、`scripts/full_sync_v2.py`、`scripts/run_github_sync.sh` 等是 pipeline/worker orchestration，會啟動 vector-index builder，而不是各自實作 embedding HTTP 呼叫。

### Provider abstraction

`tools/embedding_providers.py` 目前提供：

- `GeminiProvider`：預設 model `gemini-embedding-2-preview`，使用 Gemini `embedContent` / `batchEmbedContents`。
- `OpenAIProvider`：預設 model `text-embedding-3-small`。
- `OllamaProvider`：預設 local endpoint `http://localhost:11434`，預設 model `nomic-embed-text`。
- `get_provider()`：支援 `EMBEDDING_PROVIDER`、`EMBEDDING_MODEL`，並從 repo-relative `config/embedding.json` 讀 sanitized defaults；credential 僅取 runtime environment 或既有 OpenClaw runtime fallback，不寫入 repo/index/log/card。

目前 repo 的 `config/embedding.json` 只含非敏感 provider/model defaults；未發現 credential 值。

## 3. 設定讀取與路徑解析

- 程式路徑由 `scripts/xkb_paths.py` 的自身位置推導，不依賴某台機器的固定 skill path。
- data root 優先順序：`XKB_DATA_DIR` → `OPENCLAW_WORKSPACE`/`WORKSPACE_DIR` → `.xkb.json` → 使用者 home 下的預設 workspace；`BOOKMARKS_DIR`、`XKB_WIKI_DIR`、`INDEX_FILE`、`VECTOR_INDEX_PATH` 可由 environment override。
- 本次 live path probe 顯示資料來源為 `.xkb.json`；sanitized 對應為 `<workspace>/memory`、`<data>/bookmarks`、`<data>/x-knowledge-base/wiki`。
- `xkb_paths.subprocess_env()` 會將已解析的資料根與 config path 傳給子行程，降低 parent/child 路徑漂移風險。
- `docs/RUNTIME_PATHS.md` 明確區分 skill code、personal runtime data、workspace compatibility symlink 與 release bundle。個人 cards、wiki、search/vector index 不應進 repo。

## 4. Index / vector pipeline 與 release 邊界

1. 原始 bookmark / local source → card generation / enrichment。
2. `scripts/build_search_index.sh` → `<data>/bookmarks/search_index.json`。
3. `scripts/build_vector_index.py --incremental` → `<data>/bookmarks/vector_index.json`。
4. 同時輸出語意分段 index（`semantic_index.json` + binary）與 card index（`cards_index.json` + binary），供 recall 端快速讀取。
5. `scripts/continuity_recall.py` / `recall_for_conversation.py` / service recall 讀既有 vectors；query embedding 失敗不得宣稱 semantic success，應保留降級訊號。
6. `scripts/xkb_daily_pipeline.py`、`fetch_and_summarize.sh`、GitHub sync 與 job runner 只負責 orchestration；collector 成功與 vector write 成功需分開回報。
7. `scripts/build_release_package.sh` 是 release 邊界：以 allowlist 打包 code、docs、templates、sample files，並拒絕疑似 hardcoded secrets；`dist/` 被 gitignore。不得把 live `memory/`、wiki topics、staging、cards、search/vector index、credential 帶進 release。

## 5. 健檢與測試觀察

### 已實際執行

- `python3 -m compileall -q scripts tools`：通過（exit 0）。
- `git diff --check`：通過。
- `python3 scripts/test_recall_regression.py`：通過，15/15；6 個 should-recall 與 9 個 should-stay-quiet 案例均符合目前知識庫預期，最慢約 12.8 秒。
- `python3 scripts/build_vector_index.py --incremental --dry-run`：通過；讀到 1,562 cards，wiki/memory 101 sections 待處理，既有 vectors 9,368 筆可跳過；未呼叫 API、未寫入 index。
- `python3 scripts/sync_enriched_index.py --dry-run`：通過；1,534 enriched cards、1,555 indexed items，未寫入；報告 11 筆不在 index 的 orphan candidates。
- `python3 scripts/health_check_pipeline.py --json`：執行成功但整體 exit 1，因為它正確揭露 actionable backlog，不是 execution crash：wiki canonical、recall live、telemetry、semantic index integrity/freshness、topic map、index freshness 均通過；staging/governance backlog checks 為紅燈。
- `bash scripts/smoke_test_pipeline.sh`：在目前 isolated worker runtime 的預設 workspace 下 2 passed / 8 failed；失敗主因是 smoke script 使用的 default workspace 與本次資料 root 不一致，導致 search index/topic-map/wiki 路徑找不到，另有既有 `sync_cards_to_wiki` 執行錯誤。這是環境／pipeline 邊界證據，未在本 task 修復。

### Live index / runtime 統計（僅記錄非敏感統計）

- vector meta：provider `gemini`、model `gemini-embedding-2-preview`、3072 dims、9,428 total vectors、5,459 point vectors。
- semantic binary integrity：1,991 vectors、24,465,408 bytes，health check 通過。
- queue canonical file：1,467 `done`、1 `processing`、7 `skipped`；目前確認存在 1 個 processing queue item，未重置或處理。
- `sync_enriched_index --dry-run` 確認 11 orphan cards；僅記錄為 backlog，沒有加入 index、刪除或修復。
- governance health check：pending 1,132、quarantine/overdue 417、proposal 150、safe promotion 0；這些是既有 backlog，未在本盤點擴大處理。

## 6. 已知風險與明確邊界

- `health_check.py` 內仍有舊 Gemini model helper；目前沒有呼叫端，且其 768-dim model 不可與 3072-dim index 混用。這是待後續 provider/config 整理的風險，不在本盤點修復。
- 部分 orchestration／smoke script 若未傳遞 `XKB_DATA_DIR` 或 `OPENCLAW_WORKSPACE`，可能解析到 worker profile 的 default workspace；這會造成「檔案不存在」而非真正的 index 空庫，需後續由 implementation task 處理。
- `tools/embedding_providers.py` 現有 endpoint 錯誤訊息只保留短 response prefix；尚需在 provider implementation task 以非敏感、actionable error contract 完成 fail-fast/health 行為。
- cloud embedding 會將 query/card title-summary 送至 provider；Ollama 可作為 local-only alternative。資料流細節以 `docs/data-flow.md` 為準。
- 本次沒有使用真實 credential 重新建 vector；沒有把 credential 寫進任何 repo、fixture、log、index、card 或 handoff。也沒有宣稱 production 全綠、GitHub 已發布或外部服務已變更。

## 7. Backlog（只記錄，不修復／不刪除）

- `11 orphan cards`：由 `sync_enriched_index.py --dry-run` 確認；標記為 `backlog/orphan-index-gap`，不加入 index、不刪除。
- `1 processing queue item`：由 canonical queue status count 確認；標記為 `backlog/queue-processing-stale-or-unknown`，不 reset、不重試。
- `2 recall regression cases`：依 parent/task handoff 所述的既有 regression backlog 記錄為 `backlog/recall-regression`；本次重新執行現有 regression suite 為 15/15 通過，但沒有把該兩項歷史案例當成已修復，也未刪除或改寫案例資料。repo 內未找到一個可安全視為 canonical 的獨立兩案例 fixture，因此不臆測其內容。

## 8. 兄弟任務與修改隔離

目前可辨識的 sibling workstreams：

- `t_abd368d9`：provider-agnostic embedding config / Gemini registry implementation；本 inventory 不代替其 implementation，不覆蓋既有 `tools/embedding_providers.py` 或 config changes。
- `t_f193bd5a`：全量 smoke/unit/regression/health/secret/private-path/diff review；本 inventory 提供其可重現的現況與已知 smoke path mismatch，不宣稱其 review 完成。
- parent decomposition 另有 governance、pipeline、test/review workstreams；目前工作樹中上述 15 modified files 與新增 tests/scripts 均保留原狀。

本文件只新增盤點紀錄，未改動 sibling files、runtime queue、cards、wiki、staging 或 indexes。

## 9. 建議的後續 review 輸入（非本 task 實作）

- 先由 embedding/provider implementation task 完成 portability、credential contract、provider/model compatibility、endpoint fail-fast 與 sanitized tests。
- 再由全量 review task 以明確 `XKB_DATA_DIR`/workspace environment 重跑 smoke，並把「collector success / index write success / semantic recall success」拆開判定。
- 對 11 orphan、1 processing、2 regression backlog 保持 append-only provenance；除非另有授權，不做 reset、promotion、刪除或資料庫重建。
- release 前需再次執行 secret scan、private-path scan、diff check 與 allowlist package dry-run；任何真實 credential 只可在 runtime 驗證，不能進 artifact。
