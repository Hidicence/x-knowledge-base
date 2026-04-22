# XKB Inference Adapter Spec v0

## 目標

把 XKB 的 inference 從單一 runtime 綁定中抽離，改成 request/result artifact + adapter interface。

這樣 XKB core 不需要知道底下是：
- OpenClaw auth session
- MiniMax API
- Claude Code / Claude CLI
- 其他未來 runtime

## 設計原則

1. XKB core 只負責 chunking、request artifact、result artifact、consolidate
2. inference runtime 由 adapter 決定，不內嵌在 core worker
3. 所有 backend 都要輸出同一份 normalized result schema
4. request/result artifact 應可離線檢查、重跑、替換 executor

## 分層

### 1. XKB Core
負責：
- input cleaning
- chunking
- prompt building
- request artifact 寫入
- result artifact 讀取
- rerank / consolidate
- staging / apply

### 2. Inference Adapter
負責：
- 讀 request artifact
- 呼叫對應 runtime / provider
- 寫出 standardized result artifact

### 3. Executor / Runner
負責：
- 決定使用哪個 adapter
- 排程 / 執行 adapter
- 管理 job status

## Adapter interface

第一版先用同步最小介面：

```python
class XKBInferenceAdapter:
    name: str

    def run(self, request_path: str) -> dict:
        """Run one request and return normalized result"""
```

之後可擴充成 submit / poll / collect：

```python
class XKBInferenceAdapter:
    name: str

    def submit(self, request_path: str) -> str:
        ...

    def poll(self, job_id: str) -> dict:
        ...

    def collect(self, job_id: str) -> dict:
        ...
```

## Request artifact schema

檔名範例：
- `runtime/memory-distill/2026-04-19/requests/chunk-0001.json`

```json
{
  "version": "xkb.infer.v1",
  "task": "memory_distill_extract",
  "source_date": "2026-04-19",
  "chunk_index": 1,
  "input": {
    "system": "system prompt...",
    "user": "user prompt..."
  },
  "expect": {
    "format": "json",
    "schema": {
      "insights": "array"
    }
  },
  "meta": {
    "runtime_dir": "...",
    "created_at": "2026-04-21T00:00:00Z"
  }
}
```

## Result artifact schema

檔名範例：
- `runtime/memory-distill/2026-04-19/results/chunk-0001.json`

成功：

```json
{
  "version": "xkb.result.v1",
  "ok": true,
  "backend": "openclaw-auth",
  "model": "openai-codex/gpt-5.4",
  "source_date": "2026-04-19",
  "chunk_index": 1,
  "output": {
    "insights": []
  },
  "raw_text": "{\"insights\":[]}",
  "timing_ms": 18420,
  "finished_at": "2026-04-21T00:00:00Z"
}
```

失敗：

```json
{
  "version": "xkb.result.v1",
  "ok": false,
  "backend": "openclaw-auth",
  "model": "openai-codex/gpt-5.4",
  "source_date": "2026-04-19",
  "chunk_index": 1,
  "error": {
    "type": "timeout",
    "message": "openclaw model run timed out after 60s"
  },
  "finished_at": "2026-04-21T00:00:00Z"
}
```

## 第一批 adapter

### OpenClaw Auth Adapter
用途：
- 使用 OpenClaw provider auth session 的環境
- 例如 `openai-codex/gpt-5.4`

注意：
- 不建議在 Python worker 內同步 shell `openclaw infer model run`
- 應優先考慮 OpenClaw 原生 task / agent execution path

### MiniMax API Adapter
用途：
- `LLM_API_URL` + `LLM_API_KEY` 可用
- 純 API key 執行環境

### Claude Code Adapter
用途：
- Claude Code / Claude CLI / 類似 harness

第一版可先做 placeholder，重點是 request/result schema 對齊。

## 建議的最小落地順序

### Phase 1
- chunk worker 改成只產 request artifact
- 不直接做 inference

### Phase 2
- 新增 OpenClaw adapter
- 讀 request，寫 result

### Phase 3
- consolidate worker 改讀 results/*.json
- 不再依賴 chunk worker 直接寫 partials

## 現況記錄（2026-04-21）

目前已驗證：
- memory distill 已拆成 prep / chunk / consolidate 三段式骨架
- prep worker 可正常完成並產出 runtime artifact
- 卡點收斂到 chunk-level `llm_call()`
- 問題不只是模型，而是 Python worker 內同步整合 OpenClaw auth-based model execution 不穩
- 因此 XKB 下一步應轉向 adapter-based inference architecture
