# Conversation Recall v1

讓 `x-knowledge-base` 不只是收藏整理器，而是對話中的第二層記憶。

## 目的

這套設計要解決的不是「怎麼存更多書籤」，而是：

- 把值得留下的內容留下來
- 把留下來的內容整理成可回用知識
- 在對的對話時機主動提起，而不是等使用者自己想起來

一句話：

> x-knowledge 不是收藏夾，而是對話中的知識回用層。

---

## 設計原則

### 1. 有幫助才主動提
不是查到就提。
只有當召回結果真的能推進當前對話、決策或行動時才提。

### 2. 不打擾比多提醒更重要
如果關聯勉強、內容普通、或當前只是閒聊，就不要插話。

### 3. 先短提，再展開
第一次主動召回只給短格式：
- 一句話摘要
- 為什麼相關
- 原文連結

使用者追問時再展開。

### 4. 優先可行動內容
優先召回：
- 具體案例
- workflow / SOP
- 具可執行性的觀點
- 能幫當前專案前進的資料

低優先：
- 太空泛的趨勢文
- 只有情緒或態度、沒有方法的貼文
- 沒來源的二手整理

---

## 觸發規則 v1

只有同時滿足以下兩件事，才主動召回：

1. 當前對話值得查書籤
2. 查到的內容真的值得提

### 第一層：當前對話值得查

命中以下任一情況即可進入召回：

- 使用者在問做法、workflow、案例、靈感、決策方向
- 使用者在整理脈絡、比較路線、釐清下一步
- 當前主題明顯落在高頻收藏領域
- 代理判斷：書籤庫比通用知識更可能提供有價值的答案

### 高頻收藏領域（v1）

- OpenClaw / agent / workflow
- SEO / GEO / AEO / AI search
- AI 影片 / prompts / content
- automation / tools / GitHub
- startup / SaaS / GTM

### 第二層：結果值得提

召回結果至少符合以下其中兩項才值得主動提：

- 有清楚標題
- 有像樣摘要
- 有原文連結
- relevance 高
- 對當前任務 / 決策 / 思考真的有幫助

---

## 不觸發情境

以下情況預設不要主動召回：

- 純閒聊
- 簡單事務題
- 僅字面沾邊、沒有實質關聯
- 同一輪已主動提醒過且使用者沒追問
- 結果缺摘要、缺來源、內容太空或明顯低價值

---

## 回覆格式 v1

預設只用短格式：

```text
你之前 X 書籤裡有篇相關的：
- 標題：一句話摘要
  為什麼相關：...
  原文：...
```

### 回覆限制

- 每輪對話最多主動提 1 次
- 預設最多帶 1–2 篇
- 除非使用者追問，否則不主動貼長摘要

---

## 實際對話範例（v1）

### 範例 1：OpenClaw / workflow

**當前對話：**
- Pan：我想優化 OpenClaw workflow，有沒有值得抄的做法？

**主動召回：**
```text
你之前 X 書籤裡有篇相關的：
- OpenClaw + Codex Agent Swarm: 一人開發團隊完整設定：這篇整理了一人開發團隊怎麼拆 agent workflow。
  為什麼相關：你現在就在問 workflow 做法，這篇是可直接參考的案例。
  原文：https://x.com/elvissun/status/2025920521871716562
```

### 範例 2：AI SEO / AEO

**當前對話：**
- Pan：最近 AI SEO 這條路到底值不值得繼續深挖？

**主動召回：**
```text
你之前 X 書籤裡有篇相關的：
- AEO 流量藍圖 - AI 時代的 SEO 攻略：這篇核心在講從傳統 SEO 轉向 AEO，讓 AI 搜尋更容易推薦你的品牌。
  為什麼相關：你現在在評估方向，這篇剛好是 AI SEO / AEO 的具體案例。
  原文：<原始來源若有>
```

### 範例 3：Agent memory / 知識回用

**當前對話：**
- Pan：我不想存一堆書籤最後還是忘掉，這東西要怎麼真的回用？

**主動召回：**
```text
你之前 X 書籤裡有篇相關的：
- OpenClaw Memory 終極指南：現狀、方案與未來：這篇在講 agent memory 的現狀與實作方向。
  為什麼相關：你現在問的是「怎麼回用知識」，這篇正好提供 memory / recall 的脈絡。
  原文：https://x.com/lijiuer92/status/2025678747509391664
```

這三個範例的重點不是把搜尋結果全部倒出來，而是：
- 只提最 relevant 的 1 篇
- 用一句話說清楚它為什麼值得看
- 讓使用者立刻決定要不要深挖

---

## 工具對應

目前 v1 的可執行入口：

```bash
python3 scripts/recall_for_conversation.py "AI SEO 案例" --format chat
python3 scripts/recall_for_conversation.py "OpenClaw workflow agent memory" --format chat
```

### 目前版本特性（v1）

- 依賴既有 `search_index.json`
- **召回方式：關鍵字 token 比對**（非語意向量搜尋）
  - query 分詞後對 title (+8) / tags (+6) / category (+4) / summary (+3) 做字串命中計分
  - 同義詞、縮寫、中英混用時命中率會下降，屬已知限制
- 可輸出 markdown / json / prompt / chat 格式
- 向量搜尋（真正的語意召回）規劃於 v3 實作

---

## 路線圖（Roadmap）

### v1：先讓主動召回真的能用

目標：
- 讓書籤知識可以在對話中被主動提起
- 先驗證使用者體感，不追求技術最強

內容：
- `search_index.json` 索引
- `recall_for_conversation.py` 基礎召回
- `markdown / json / prompt / chat` 輸出
- 觸發規則 v1
- 公開教學文件與對話範例

### v2：提升召回品質

目標：
- 讓主動召回更穩、更準、更像助手而不是搜尋器

優先升級：
- 更穩定的一句話摘要
- 補齊 `source_url`
- 更好的 relevance ranking
- 更明確的高價值 / 低價值內容過濾
- 讓召回結果更偏向「可落地案例」而不是泛主題匹配

### v3：接入向量搜尋

目標：
- 讓召回從關鍵字匹配升級到語意召回
- 當對話和書籤用字不同但意思接近時，仍能找回相關內容

#### 實作方案（建議）

**選項 A：sqlite-vec（推薦，零外部服務）**
```bash
pip install sqlite-vec
```
- 把每張知識卡的 title + summary 送 embedding API（OpenAI / Jina / 本地 model）
- 向量存進 SQLite，查詢時直接 cosine similarity
- 無需額外服務，整個 DB 就是一個 `.db` 檔
- 適合單人 / 低頻使用場景

**選項 B：chromadb（本地向量 DB）**
```bash
pip install chromadb
```
- 比 sqlite-vec 功能更完整，支援 metadata filtering
- 可搭配 OpenAI / HuggingFace embedding
- 適合未來要擴充多用戶或更複雜查詢的場景

#### 遷移路徑

1. 新增 `scripts/build_vector_index.sh` — 批次 embed 所有知識卡
2. 新增 `scripts/recall_semantic.py` — 語意召回入口（與 recall_for_conversation.py 並存）
3. `recall_for_conversation.py` 加 `--semantic` flag，觸發向量查詢路徑
4. 原有 keyword 召回作為 fallback（當向量 index 不存在時自動降級）

內容：
- embeddings / semantic index（sqlite-vec 或 chromadb）
- query 語意化（直接 embed query text）
- 更自然的 recall ranking（cosine similarity score）
- 減少只靠 title / tags 命中的侷限

### v4：接入 NotebookLM / 雲端圖書館

目標：
- 讓本地知識卡不只存在工作區，也能在 NotebookLM 內作為研究圖書館使用

內容：
- Google Drive 同步策略
- NotebookLM 匯出格式標準化
- 圖書館來源管理
- 本地知識卡與雲端研究庫的雙向使用策略

---

## v1 的定位

這不是最終版智能 recall。
這是最小可用版，目的只有兩個：

1. 讓主動召回能力先真的跑起來
2. 驗證「在對話中主動提書籤」是否符合使用者體感

如果這層體感成立，再往下升級：
- 更穩的摘要品質
- 更完整的 source_url
- 更準的 ranking
- 向量搜尋 / embedding recall

---

## 給公開使用者的理解方式

如果你要把這個 skill 用在自己的知識庫，請先記住：

- 主動召回的重點不是「查很多」
- 而是「在剛好的時候，提最有價值的少數內容」

做對了，它會像第二層記憶。
做錯了，它只會變成愛插話的搜尋器。
