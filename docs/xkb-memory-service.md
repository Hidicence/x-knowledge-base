# XKB Knowledge Service（Stage 3B）

XKB 的跨 Agent 共享入口是 local-first HTTP service。它不是只有對話記憶服務，而是完整 XKB knowledge backend 的 API facade：OpenClaw、Claude Code、Codex 等 adapter 不直接讀 workspace 檔案，而是透過同一套 API 讀取對話、外部來源、evidence、knowledge cards、wiki 與 pipeline 狀態。

## 啟動

```bash
python3 scripts/xkb_memory_service.py
```

預設值：

- bind：`127.0.0.1`
- port：`18972`
- database：`${HOME}/.xkb-runtime/memory.sqlite`

可用環境變數覆寫：

- `XKB_SERVICE_HOST`
- `XKB_SERVICE_PORT`
- `XKB_SERVICE_DB`

服務拒絕非 loopback bind；只有在已經配置 VPN、TLS、authentication 與 network policy 後，才可明確設定 `XKB_ALLOW_NON_LOOPBACK=1`。

## API

```text
GET  /v1/health
POST /v1/sessions/open
POST /v1/turns/start
POST /v1/turns/{turn_id}/complete
POST /v1/recall
POST /v1/context
GET  /v1/sources/{source_id}
GET  /v1/evidence/{evidence_id}
GET  /v1/cards/{card_id}
GET  /v1/cards/{card_id}/relations
GET  /v1/wiki/topics/{topic_id}
GET  /v1/ingest/status
GET  /v1/pipeline/snapshot?days=7
GET  /v1/pipeline/jobs?stage=index&status=failed&limit=50
POST /v1/pipeline/jobs/events
POST /v1/candidates/query
GET  /v1/artifacts/{trace_id}
```

最小流程：

```text
sessions/open
  ↓
turns/start（同時執行 semantic retrieval，回傳 retrieval packet）
  ↓
直接使用 start 回傳的 context；不可對同一 turn 重複搜尋
  ↓
Agent 執行
  ↓
turns/{id}/complete
```

## 每個 turn 的雙重處理

任何接入的 Agent turn 都必須同時完成：

1. **Capture / persistence**：保存 query、answer、tool data、session、episode、agent、namespace、時間與 provenance，先成為 L1 observed evidence。
2. **Semantic retrieval**：解析當前 query，優先透過既有 XBrain/Gemini hybrid vector search，必要時降級 keyword search，從整個 XKB knowledge plane 召回 context。

```text
user turn
  ├── capture → L1 trace / replayable evidence
  └── semantic query → XBrain hybrid vector search → context packet
```

`/v1/recall` 與 `/v1/context` 的回應會包含 `retrieval_mode`：

- `xbrain_hybrid`：已使用既有 XBrain/Gemini hybrid search
- `keyword_fallback`：semantic backend 不可用或無結果，已明確降級

不能在 semantic backend 不可用時假稱完成向量搜尋。

### 為什麼召回不到：ACL 過濾統計

回應中的 `filtered_counts` 會說明**哪一層**被 ACL 擋掉，而不是只給一個總數：

```json
{
  "filtered_counts": {
    "total": 3,
    "by_layer": { "card": 1, "wiki": 2, "semantic": 0, "conversation": 0 },
    "semantic": 0,
    "keyword": 3
  }
}
```

- `by_layer.card` / `by_layer.wiki`：keyword 路徑可以判斷來源層
- `by_layer.semantic`：semantic backend 不回報層級，只能歸到通道
- `by_layer.conversation`：對話召回在 SQL 階段就依 namespace 過濾，結構上恆為 0
- `semantic` / `keyword`：舊有的通道統計，保留給既有呼叫端

有東西被擋掉時，`warnings` 會出現 `records_filtered_by_acl`。

這一項的用意是排除歧義：「沒有資料」與「有資料但被 ACL 擋掉」
在沒有分層統計時看起來完全一樣，而這正是 XKB 過去讓故障靜默數週的模式。

## Adapter 設定

OpenClaw plugin 與 Claude Code hook 都支援：

```text
XKB_MEMORY_SERVICE_URL=http://127.0.0.1:18972
```

若 service 不可用，adapter 會 fail-open 並回退到本機 L1 artifact / configured recall command；不應因此阻斷 Agent 對話。

## 從另一台機器連進來

service 只綁 loopback，**不要為了讓其他機器連線而改成對外綁定**。
正確做法是用 SSH 通道：對 service 而言連進來的仍是 `127.0.0.1`，
但流量走的是 SSH 已經加密、已經驗證身分的通道。

```bash
# 在本機執行（保持這個行程開著）
ssh -N -L 18972:127.0.0.1:18972 <你的伺服器>
```

之後本機的 `http://127.0.0.1:18972` 就是伺服器上的 service：

```bash
curl -s http://127.0.0.1:18972/v1/health
```

為什麼是 SSH 通道而不是開 port 或做 authentication：

目前 `namespace`（身分）是呼叫端在 request 內自行宣告的，service 並不驗證。
在只綁 loopback 的前提下這是可接受的取捨；一旦對外開放，
任何連得上的人只要宣告 `"namespace": "private"` 就能讀走全部內容。
SSH 通道把身分驗證交給 SSH（金鑰），因此不必先完成一整套 authentication／TLS
就能安全地跨機使用。

**在改成多人／多機共用之前，`namespace` 必須改為從憑證推導，而不是從 request body 讀取。**

送出含中文的 request 時，注意 shell 的編碼；較穩的做法是把 JSON 寫成 UTF-8 檔案再送：

```bash
curl -s -X POST http://127.0.0.1:18972/v1/recall \
  -H "Content-Type: application/json" --data-binary @query.json
```

## Stage 3B 邊界

### Control plane 第一個切片：pipeline snapshot

`GET /v1/pipeline/snapshot?days=7` 是只讀總覽，將既有 pipeline 的階段、負責腳本、目前可觀測的 status 檔與摘要統一放在一個回應中。它只描述已存在的 filesystem/status evidence，不代表 worker 正在執行，也不會啟動、重試或修改任何 worker。

### Worker job 履歷

`GET /v1/pipeline/jobs` 提供可篩選的 job 履歷；目前支援 `stage`、`status` 與 `limit`。`POST /v1/pipeline/jobs/events` 是給未來 worker adapter 回報 `queued`、`running`、`succeeded`、`failed`、`cancelled` 事件的受控入口。它只保存觀測事件，不會依 worker 名稱執行 shell command，也不會自動重試或 promotion。

每筆 job 會保存：

```text
job_id / stage / worker / status
started_at / finished_at
input_ref / output_ref
error / retryable
metadata
```

目前這個 job model 是履歷層，不是 scheduler；既有 worker 尚未自動接入。

目前列出的階段是：

```text
ingest → card_generation → index → distill → promotion → publish
```

這是 control plane 的觀測層，不是執行層；下一步才是為 worker job 建立可持久化的事件與狀態模型。

目前 service 的 data plane 是：

- session identity、turn query / answer、tool/content payload、L1 trace
- 既有 XKB search index 的 sources / cards metadata
- 既有 cards 的完整 markdown 內容
- wiki topics 的完整內容
- read-only evidence/card/source facade
- 對話 + 外部知識的 unified recall/context

目前 control plane 先採 observed-status-only：

- `GET /v1/ingest/status` 只讀取既有 pipeline status，不啟動 ingest
- `/v1/candidates/query` 為唯讀查詢：只回報候選目前的狀態與分析結果，不做 promotion
- 不直接改寫 `MEMORY.md`、wiki topics、cards、indexes 或 production knowledge
- 不自動 promotion、不自動建立 knowledge card、不綁定新的 embedding provider
- 不對外開放服務

下一步才是把既有 ingest、index、distillation、relation、candidate、promotion、
absorb、publish worker 逐步納入 service control plane；目前先保留既有 worker
與檔案作為 source of truth。
