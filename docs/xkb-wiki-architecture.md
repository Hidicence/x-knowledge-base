# XKB + Wiki 知識系統架構文件

> 版本：2026-04-08  
> 維護者：Pan / CC（Claude Code）/ APAN2號

---

## 一、系統定位

**X Knowledge Base（XKB）+ Wiki** 是一套以 AI agent 為核心的個人知識生命週期系統，解決的核心問題是：

> 人類每天消費大量外部資訊（X、YouTube、文章）和內部決策（對話、workflow、判斷），但大多數內容消費後就消失了，留下來的東西也是雜亂倉庫。

這套系統把知識從「消費即遺忘」轉變為「有結構地沉澱、浮現、回用」。

---

## 二、四層架構總覽

```
┌─────────────────────────────────────────────────────┐
│  Layer 4: Wiki（知識成品層）                          │
│  wiki/topics/*.md  ←  absorb gate  ←  XKB + Memory  │
├─────────────────────────────────────────────────────┤
│  Layer 3: X Knowledge Base（外部知識吸收層）           │
│  X bookmarks → fetch_and_summarize → cards → index   │
├─────────────────────────────────────────────────────┤
│  Layer 2: Dreaming（背景整理機制）                    │
│  memory-core plugin，每 6 小時，降噪 + 提升            │
├─────────────────────────────────────────────────────┤
│  Layer 1: 工作記憶主幹（短期情境層）                   │
│  MEMORY.md + memory/YYYY-MM-DD.md + memorySearch     │
└─────────────────────────────────────────────────────┘
```

各層解決不同問題，**不可混用、不可省略中間層**。

---

## 三、各層詳細說明

### Layer 1 — 工作記憶主幹

**負責：** 承接 Pan 和 APAN2號 的短期偏好、事件、決策與對話脈絡。

**核心檔案：**
- `workspace/MEMORY.md` — 持久偏好、長期規則、關係脈絡
- `workspace/memory/YYYY-MM-DD.md` — 每日對話記錄，自動 append
- memorySearch — OpenClaw 的語意記憶召回功能

**特性：**
- 高頻寫入（每次對話都可能更新）
- 時間敏感（今天的決策明天就可能過期）
- 不做整理，只做記錄

**限制：** 原始記錄容易堆積，訊號/雜訊比低，需要 Layer 2 整理。

---

### Layer 2 — Dreaming（背景整理機制）

**負責：** 把短期記憶訊號整理、降噪、提升為較長期可用的知識。

**設定：**
```json
"dreaming": {
  "enabled": true,
  "frequency": "0 */6 * * *",
  "temporalDecay": { "halfLifeDays": 30 }
}
```

**特性：**
- 背景自動運作，不需人工觸發
- `halfLifeDays: 30` — 記憶半衰期 30 天，超過的訊號逐漸淡出
- 整理結果仍留在 memory 系統，不直接寫 wiki

**限制：** 輸出不易觀測，品質難以直接驗證。

---

### Layer 3 — X Knowledge Base（外部知識吸收層）

**負責：** 把 X 書籤和外部文章轉化為結構化、可檢索的知識資產。

**Pipeline（輸入來源）：**
```
X/Twitter 書籤
  → fetch_bookmarks.sh（抓取）
  → fetch_and_summarize.sh（bird/curl/Jina/fxtwitter 四層抓全文）
  → bookmark_enhancer.py（AI 摘要 + 分類）

YouTube 播放清單
  → fetch_youtube_playlist.py（字幕抓取 → AI 摘要）
  → run_youtube_sync.sh（每日自動同步）

兩者共同輸出 →
  → search_index.json（結構化索引）
  → vector_index.json（語意向量索引，Gemini embeddings）
  → memory/cards/*.md（知識卡片）
```

**核心檔案：**
- `memory/bookmarks/search_index.json` — 主搜尋索引（243 條，含 category/tags/title/summary）
- `memory/bookmarks/vector_index.json` — 語意向量索引
- `memory/cards/*.md` — 每張書籤的知識卡片
- `wiki/topic-map.json` — category → wiki topic 的映射規則

**品質保障：**
- 每次 run 後執行 `normalize_index_quality.py`、`canonicalize_duplicates.py`
- enrichment worker 補充 thread + 外鏈內容

---

### Layer 4 — Wiki（知識成品層）

**負責：** 把經過 absorb gate 篩選後的知識沉澱為可讀、可回用、持續更新的 topic pages。

**兩條進入路徑：**

```
路徑 A（外部知識）：
search_index → sync_cards_to_wiki.py → LLM absorb gate → topics/*.md

路徑 B（內部記憶）：
memory/YYYY-MM-DD.md → distill_memory_to_wiki.py → _staging/ → Pan 審核 → topics/*.md
```

**核心檔案：**
- `wiki/topics/*.md` — 主題頁（目前 5 個）
- `wiki/index.md` — 主題索引
- `wiki/WIKI-SCHEMA.md` — 頁面規範、absorb gate policy
- `wiki/topic-map.json` — XKB category 到 wiki topic 的映射
- `wiki/review-decisions.json` — absorb 決策記錄（LLM + 人工）
- `wiki/_staging/*.md` — 待審核的記憶蒸餾候選

**Absorb Gate（Policy 6）：**
進入 wiki 之前必須回答：
> 這個 entry 對這頁已有的理解，增加了什麼新維度？

維度類型：`new_case` | `new_concept` | `contradiction`

---

## 四、知識流向圖

```
外部資訊（X / YouTube / articles）
        │
        ▼
    XKB Pipeline（fetch → enhance → index）
        │
        ├──────────────── search_index.json ────────────────┐
        │                                                    │
        ▼                                                    ▼
  knowledge cards                              vector_index（語意搜尋）
        │
        ▼
  sync_cards_to_wiki
        │
        ├── LLM absorb gate ──┐
        │                     │
        │   [new dimension]   │  [no new dimension]
        ▼                     │
  wiki/topics/*.md            ▼
                        review-decisions.json（skip 記錄）

對話 / 決策（內部）
        │
        ▼
  memory/YYYY-MM-DD.md
        │
        ├── Dreaming（背景整理）
        │
        ▼
  distill_memory_to_wiki.py
        │
        ▼
  wiki/_staging/*.md
        │
        ▼ （Pan 審核 approve）
  wiki/topics/*.md
```

---

## 五、常用指令速查

```bash
# 完整 XKB 抓取 + wiki 同步
bash skills/x-knowledge-base/scripts/fetch_and_summarize.sh

# 只做 wiki sync（不重抓書籤）
SKIP_WIKI_SYNC=0 python3 skills/x-knowledge-base/scripts/sync_cards_to_wiki.py --apply --limit 20

# 從記憶蒸餾 wiki 候選
python3 skills/x-knowledge-base/scripts/distill_memory_to_wiki.py --stage --days 2

# 套用審核通過的候選
python3 skills/x-knowledge-base/scripts/distill_memory_to_wiki.py --apply --staging-file wiki/_staging/YYYY-MM-DD-candidates.md

# wiki 健康檢查
python3 skills/x-knowledge-base/scripts/lint_wiki.py [--fix]

# pipeline 整體狀態
python3 skills/x-knowledge-base/scripts/status_knowledge_pipeline.py [--json] [--days N]

# 端對端 smoke test
bash skills/x-knowledge-base/scripts/smoke_test_pipeline.sh
```

---

## 六、目前 Wiki 主題（2026-04-08）

| Slug | 狀態 | Sources | 主要內容 |
|------|------|---------|---------|
| openclaw-agent-workflows | active | 48+ | Agent workflow、skill graph、自動化設計 |
| ai-seo-and-geo | seeded | 13 | GEO、AI 搜尋優化、傳統 SEO 演進 |
| ai-agent-memory-systems | seeded | 9 | 四層架構、absorb gate、兩條沉澱路徑 |
| ai-video-workflows | seeded | 23 | Seedance、角色一致性、4x4 故事板法 |
| video-prompt-patterns | seeded | 21 | 五維度框架、prompt 底層邏輯、工具 |

---

## 七、已知限制與待解問題

### 待解

1. **Dreaming 輸出不可觀測**：目前無法直接看到 Dreaming 整理了什麼，品質難以驗證
2. **topic-map.json 手動維護**：新的 XKB category 出現時，需要人工更新映射，尚無自動化
3. **LLM absorb gate 過寬**：部分 skip ratio 偏低，需要持續觀察 approve 的內容品質
4. **04-ai-tools-agents gap**：8 張卡尚無對應 wiki topic，topic-map 狀態為 `pending`

### 已解決

- **cron 隔離 session 的 setSystemPrompt 問題**（2026-04-07 解決）：HEARTBEAT 改為直接呼叫 distill 腳本，不依賴 setSystemPrompt
- **MiniMax 綁定**（2026-04-07 解決）：所有腳本改用 `LLM_API_KEY / LLM_API_URL / LLM_MODEL`，支援任意 OpenAI-compatible provider
- **HEARTBEAT 早晚重複蒸餾**（2026-04-07 解決）：`--label morning/evening` 區分檔案 + LLM context dedup 防止重複

---

## 八、v4 Roadmap

| 功能 | 狀態 | 說明 |
|------|------|------|
| NotebookLM integration | 🔜 | 匯出 wiki topics 到 NotebookLM 做深度閱讀 |
| Google Drive sync | 🔜 | 從 Drive 抓文件作為知識來源 |
| 集體知識網路 | 🔜 | 多人共用 wiki，跨 agent 知識流通 |
| topic-map 自動化 | 🔜 | 新 XKB category 出現時自動建議 topic 映射 |
| absorb gate 校正 | 🔜 | 觀察 skip ratio，收緊 absorb 標準 |

---

## 九、相關文件

- `wiki/WIKI-SCHEMA.md` — wiki 頁面規範與 absorb gate policy
- `wiki/index.md` — 主題索引
- `wiki/topic-map.json` — category 到 topic 的映射
- `workspace/HEARTBEAT.md` — 每日 cron 任務清單
- `docs/xkb-knowledge-work-interview-template.md` — 訪談模板（競賽用）
