# WIKI-SCHEMA.md

## 定位

wiki/ 是 **衍生知識層**，不是新的記憶系統。

- `memory/YYYY-MM-DD.md` 與 `MEMORY.md` 仍然是對話記憶主幹
- `memory/bookmarks/`、`memory/cards/` 仍然是原始知識來源
- wiki 只負責呈現「已確認、值得長期保存、可跨來源綜合」的主題知識
- `raw / memory` 是 source of truth
- wiki 可重建，但不是唯讀；它會被 AI 持續維護

### 終局定位（Pan Cognitive Layer）

wiki 的長期目標，不只是做成「整理後的知識頁」，而是逐步成為 **Pan cognitive layer 的可讀成品層**。

意思是：
- `MEMORY.md` / `memory/YYYY-MM-DD.md` 保留 Pan 與 APAN2 的工作記憶、偏好、事件與決策
- `x-knowledge-base` 持續吸收外部知識（X / YouTube / article / cards）
- `Dreaming` 負責把短期訊號做背景整理與提升
- `wiki` 則負責把 **外部知識 + 內部經驗 / 決策 / workflow** 整理成可讀、可回用、可持續更新的主題頁

換句話說：
- Dreaming 是整理機制，不是知識成品
- x-knowledge-base 是原料庫，不是人格知識本體
- wiki 才是未來承接「像 Pan，但更能吸收與整合外部知識」這個願景的頁面層

因此後續設計原則是：
1. 不把 wiki 當成純 xkb 摘要器
2. 允許 wiki 吸收 Pan 已確認的觀點、workflow、技術決策
3. 讓 wiki 成為高信號、低噪音、跨來源綜合後的認知成品層
4. 若某條內容只屬於短期記憶整理，留在 memory / Dreaming，不強行進 wiki

---

## Policy 1: Topic Naming

1. 一個 topic = 一個你會花 10 分鐘跟朋友解釋的主題
   - 太小：`git rebase` → 應併入 `git-workflow`
   - 太大：`ai` → 應拆成 `llm-prompting`、`ai-agents` 等
   - 剛好：`cursor-ide-workflow`、`content-marketing-for-devtools`

2. 命名格式
   - 使用 kebab-case
   - 2-4 個英文詞
   - 不超過 40 字元
   - 避免 `misc`、`stuff`、`general` 這種垃圾名稱

3. Topic Registry 以 `wiki/index.md` 為唯一真相
   - 新建 topic 前，先在 `index.md` 註冊
   - 禁止 orphan topic（`topics/` 有檔，但 `index.md` 沒列）

4. Merge / Split 規則
   - 一頁超過 300 行 → 考慮 split
   - 一頁只有 1 個來源 → 考慮 merge
   - split / merge 必須在 `log.md` 記錄理由

5. Category → Topic 不是 1:1
   - category 是來源分類標籤，不等於 topic
   - topic 由 `wiki/topic-map.json` 人工映射決定
   - 一個 category 可對應多個 topic
   - 多個 category 也可能合成一個 topic

---

## Policy 2: Source Inclusion

### 來自 x-knowledge-base 的書籤卡

必須至少符合：
- 有 `key_insights` 或 `takeaways`
- `excluded != true`

補充規則：
- 純新聞 / 高時效內容應標記為 `[ephemeral]`
- `[ephemeral]` 內容若 6 個月未更新，lint 應標記檢查

### 來自對話記憶的內容

必須至少符合一項：
- 有明確技術決策與理由
- 有可重複使用的 workflow / 指令 /規則
- 有經 Pan 確認的觀點、結論或原則

排除：
- 流水帳
- 未完成討論
- cron / heartbeat 執行紀錄
- 情緒反應或暫時性表態

### 來自即時對話（query-filed）

- Pan 明確說「存進 wiki」→ 可直接寫入 `topics/`
- 若 APAN2 自判品質夠高，但 Pan 未明確要求 → 先進 `_staging/`

---

## Policy 3: Memory Distillation Gating

### Level 0（Phase C 初期）

- distill 腳本只產出候選到 `wiki/_staging/YYYY-MM-DD-candidates.md`
- APAN2 提示 Pan 有候選待確認
- 只有 approved 的內容才可 upsert 到 `wiki/topics/`
- 至少累積 30 次候選 review 後，才評估升級

### Level 1（需 Pan 明確同意）

- 符合 Source Inclusion Policy 且 confidence 高的內容可自動寫入
- 仍需產出 daily digest 給 Pan
- Pan 可隨時要求降回 Level 0

### Level 2（未來再議）

- 全自動寫入
- 只在 lint / 異常 / 衝突時提醒
- 前提是 Level 1 長期穩定且 Pan 明確批准

---

## Policy 4: Update / Overwrite

1. 預設 append-first
   - 新內容優先補入對應 section
   - 不主動刪除舊內容

2. 遇到衝突
   - 不直接覆蓋舊觀點
   - 先保留雙方觀點，放入「矛盾與未解問題」

3. Overwrite 條件
   - Pan 明確說「這個過期了」
   - 或 lint 標記 stale，且 Pan 確認可改

4. 每次修改 wiki 頁面後，必須同步：
   - 更新 frontmatter 的 `last_updated`
   - 更新 `wiki/index.md`
   - 在 `wiki/log.md` 記一行
   - 在來源區塊補上新的 source entry

5. 例行批次更新
   - 一天最多一次 git commit
   - commit 訊息格式：`wiki: sync N pages from <source> (YYYY-MM-DD)`
   - 腳本本身不自帶 git commit

---

## Policy 5: Topic Registry / Status

`wiki/index.md` 不只是 topic registry，也要維護 topic 狀態。

允許狀態：
- `draft`：已註冊，但尚未形成穩定頁面
- `seeded`：已建立第一版內容，但仍在驗證
- `active`：持續更新中的主題頁
- `stale`：長時間未更新，或內容需要檢查

每個 topic 在 `index.md` 至少要有：
- title / slug
- 一行摘要
- status
- sources 數量
- last_updated

---

## Topic Page 建議格式

```md
---
title: Topic Title
slug: topic-slug
status: seeded
tags: [tag1, tag2]
sources: 3
last_updated: 2026-04-05
---

# Topic Title

## 核心概念

## 做法 / Workflow

## 案例

## 矛盾與未解問題

## 相關頁面
- [[another-topic]]

## 來源
- [Source title](https://example.com) — 2026-04-05, xkb
```

---

## Meta Topic 備註

- `ai-agent-memory-systems` 除了收錄外部知識來源，也可能承接這套 wiki / knowledge base 自身的設計決策、memory pattern、gating 原則與維護方法。
- 也就是說，這個 topic 既是一般知識主題，也可能成為這套系統的 meta 知識頁。
- 但仍應遵守 Source Inclusion 與 Update / Overwrite policy，避免把臨時討論直接灌進去。

---

## topic-map.json 建議格式

```json
{
  "mapping": {
    "OpenClaw Workflow": {
      "topics": ["openclaw-agent-design"],
      "reason": "與 AI Agent Architecture 高度重疊，先合併驗證"
    },
    "Misc / Low-count": {
      "topics": null,
      "reason": "來源太少或太雜，暫不映射"
    }
  },
  "unmapped_threshold": 3
}
```

---

## Policy 6: Absorb 判斷標準（入庫品質閘門）

在任何內容寫入 wiki 之前，必須回答這個問題：

**「這個 entry 對這頁已有的理解，增加了什麼新維度？」**

| 判斷結果 | 處理方式 |
|---|---|
| 沒有新維度（與已有內容重複） | 不入，記錄在 review-decisions.json 為 skip |
| 有新維度但屬於具體案例 | 入 案例 section，補一行來源 |
| 有新維度且更新核心概念 | 入 核心概念 section，更新 sources 數量與 last_updated |
| 與現有觀點矛盾 | 入 矛盾與未解問題 section，保留雙方觀點 |

入庫品質三條底線（同時符合才算值得入）：
1. 在 2 個以上獨立來源中出現，或有明確可執行結論
2. 對這頁現有理解有實質差異（不只是換句話說）
3. 這個知識 6 個月後仍然有用（排除純時效性新聞）

---

## Policy 7: Wiki 使用方式（Consumption）

### 對話中的查詢優先序

回答問題前，依此順序查找：
1.  — 已跨來源綜合，信號最高
2.  recall — 原始卡片，需要自行推斷
3.  — 對話記錄，輔助參考

若 wiki 已有相關頁面，應直接引用 wiki 結論，不重新翻書籤卡。

### Wiki 更新觸發條件

以下情況 APAN2 應主動判斷並更新 wiki：
- 對話結束後，出現了可執行結論、技術決策、或確認的原則 → absorb 判斷是否入 wiki
- x-knowledge-base 有新書籤卡，且 absorb 判斷有新維度 → 更新對應 topic 頁
- Pan 明確說「記住這個」或「存進 wiki」→ 直接寫入，不需判斷

### 不應觸發 wiki 更新的情況
- 對話只是問答，無新結論
- 書籤卡只重複已有內容
- 討論未收斂（仍在探索階段）

---

## Policy 8: 頁面生命週期



| 狀態 | 定義 | 升級條件 |
|---|---|---|
| draft | 已在 index.md 註冊，但頁面空白或只有 frontmatter | 寫入至少 核心概念 + 1 個來源 |
| seeded | 有基本內容（核心概念 + 案例 + 來源） | 有 2 次以上 absorb 更新且內容穩定 |
| active | 持續有新內容 absorb 進來 | — |
| stale | last_updated 超過 60 天，或主要來源已過期 | lint 提示後 Pan 確認是否保留 |

降級規則：
- active → stale：60 天無更新自動標記
- stale 頁面不刪除，等 Pan 決定是否 archive 或更新



---

