---
name: x-knowledge-base
description: 將 X/Twitter 書籤整理成可檢索、可關聯、可匯出到 NotebookLM 的個人知識庫系統。使用於：抓取與整理 X 書籤、補抓 thread 與作者補充、擷取外部文章與 GitHub 內容、生成知識卡、更新搜尋索引、依主題回用既有知識，或規劃/匯出 NotebookLM 圖書館來源。
---

# X Knowledge Base

把 X 書籤從「收藏」升級成「可回用知識庫」。

## 主入口

完整流程：

```bash
bash scripts/fetch_and_summarize.sh
```

依「累積書籤偏好」做個人化推薦（從 feed / bookmarks 挑你可能有興趣的內容）：

```bash
bash scripts/recommend_from_profile.sh
```

只抓新書籤：

```bash
bash scripts/fetch_bookmarks.sh
```

建立或增量更新搜尋索引：

```bash
bash scripts/build_search_index.sh
bash scripts/build_search_index.sh --incremental
```

搜尋既有知識卡／書籤：

```bash
bash scripts/search_bookmarks.sh "openclaw seo"
```

對話主動召回（給當前問題一段 query，找最該主動提的書籤）：

```bash
python3 scripts/recall_for_conversation.py "agent workflow 記憶召回"
python3 scripts/recall_for_conversation.py "AI SEO 案例" --json
python3 scripts/recall_for_conversation.py "OpenClaw workflow" --format chat
```

匯出 NotebookLM：

```bash
python3 scripts/export_notebooklm.py
python3 scripts/export_notebooklm.py 50
```

同步本地書籤 md 到 Google Drive（透過 `rclone`）：

```bash
bash scripts/sync_to_drive.sh
DRY_RUN=1 bash scripts/sync_to_drive.sh
```

## 工作流程

執行 `fetch_and_summarize.sh` 時，依序做這些事：

1. 抓近 28 天新書籤並以 tweet id 去重
2. 取得 tweet 內容（bird / Jina / fallback）
3. 補抓 thread、作者補充、外部文章、GitHub 內容
4. 過濾登入頁、404、首頁噪音與低價值內容
5. 呼叫 `tools/bookmark_enhancer.py` 生成摘要與分類
6. 更新 `search_index.json`
7. （可選）執行 `recommend_from_profile.sh`，用累積書籤推估興趣權重，從 following/for-you 自動產生推薦

## 補完與品質規則

- 優先抓完整 thread；失敗時回退 tweet-only
- GitHub 連結優先走 `gh`
- 外部文章優先抽正文，不保留登入頁或雜訊頁
- 內容不足時維持簡化卡，不硬補不存在的資訊
- 失敗時要 fallback，不中斷整批入庫

## 對話主動召回（v1）

把這個 skill 當成對話中的第二層記憶：當前對話若需要案例、做法、脈絡或可行動參考，先用主動召回找你過去存過的相關書籤，再決定要不要主動提給 Pan。

### 什麼時候用

優先用在這些情境：
- Pan 在問做法、workflow、案例、靈感、決策方向
- Pan 在整理想法、脈絡、比較不同路線，或需要外部案例幫忙決策
- 當前主題明顯落在常見收藏領域：OpenClaw / agent / workflow、SEO / GEO / AEO、AI 影片 / prompts / content、automation / tools / GitHub、startup / SaaS / GTM
- 你判斷書籤庫裡很可能有可回用內容，且主動提醒會提升當前對話品質

### 觸發規則（v1）

只有同時滿足這兩件事才主動召回：
1. 當前對話值得查書籤
2. 查到的結果真的能推進對話

實際判斷時，至少先命中以下其中一項：
- 問法像在找做法、案例、workflow、決策方向
- 問法像在整理脈絡、觀點、下一步
- 主題落在高頻收藏領域
- 你主觀判斷：Pan 的書籤庫比通用知識更可能提供有價值的參考

召回結果再過第二層過濾；至少符合其中兩項才值得提：
- 有清楚標題
- 有像樣摘要
- 有原文連結
- relevance 高
- 能幫 Pan 推進當前任務或決策

### 什麼時候不要用

- 純閒聊
- 簡單事務題（除非和收藏知識高度相關）
- 只有很勉強的關聯
- 同一輪已主動提醒過，Pan 沒追問
- 結果沒有原文連結、摘要太空、或明顯低價值

### 使用方式

先確保 `search_index.json` 存在且是新的，再執行：

```bash
python3 scripts/recall_for_conversation.py "主動召回 書籤知識 對話回用"
python3 scripts/recall_for_conversation.py "OpenClaw workflow agent memory" --limit 5 --json
python3 scripts/recall_for_conversation.py "AI SEO 案例" --format chat
```

### 回覆原則

- 每輪對話預設最多主動提 1 次
- 預設最多帶 1–2 篇；除非 Pan 追問，再展開到 3 篇
- 只提真的能幫上忙的結果
- 優先提可落地案例、接近當前專案、帶 workflow / SOP、且有原文連結的內容
- 回覆格式維持短：一句話摘要 + 為什麼相關 + 原文連結
- 若結果普通，就不要硬插話

## 何時讀額外參考檔

- 想深入理解主動召回的設計原則、觸發規則、回覆格式與公開教學定位時，讀 `references/conversation-recall.md`
- 調整 NotebookLM 卡片格式或匯出欄位時，讀 `references/notebooklm-schema.md`
- 使用鐵哥單筆慢清舊庫時，讀：
  - `references/tiege-single-item-workflow.md`
  - `references/tiege-prompt.md`
  - `config/tiege-queue.example.json`
- 實際執行中的 queue 狀態檔不要放在 skill 內；改放工作區資料路徑，例如：`memory/x-knowledge-base/tiege-queue.json`

## 環境需求

至少確認下列變數或工具可用：

- `BIRD_AUTH_TOKEN`
- `BIRD_CT0`
- `TWITTER_AUTH_TOKEN`（可選；若未提供，推薦腳本會回退使用 `BIRD_AUTH_TOKEN`）
- `TWITTER_CT0`（可選；若未提供，推薦腳本會回退使用 `BIRD_CT0`）
- `BOOKMARKS_DIR`（可選）
- `MINIMAX_API_KEY`（可選；未提供時走 fallback）
- `agent-reach`（建議）
- `xreach`（建議）
- `gh`（建議）
- `rclone`（若要同步到 Google Drive）
- `RCLONE_REMOTE`（可選；預設 `pan-drive:OpenClaw-Bookmarks`）

不要把 `.env` 或其他 secrets 放進 skill 目錄；改由工作區環境變數、外部環境管理，或工作區 `.secrets/x-knowledge-base.env`。

## 重要檔案

- `scripts/fetch_and_summarize.sh` — 主流程入口
- `scripts/fetch_bookmarks.sh` — 抓新書籤
- `scripts/auto_categorize.sh` — 依 `config/category-rules.json` 將 inbox 書籤歸類
- `scripts/build_search_index.sh` — 建索引
- `scripts/search_bookmarks.sh` — 搜尋
- `scripts/recommend_from_profile.sh` — 由累積書籤推估興趣，從 feed 產生推薦
- `scripts/export_notebooklm.py` — 匯出 NotebookLM
- `scripts/sync_to_drive.sh` — 透過 `rclone` 同步本地 md 到 Google Drive
- `config/category-rules.json` — 分類規則設定
- `config/recommendation-topics.json` — 推薦主題與關鍵字設定
- `tools/bookmark_enhancer.py` — 摘要 / 分類 / 交叉連結
- `tools/agent_reach_enricher.py` — thread / 外鏈 / GitHub 補完

## Evals（建議）

- 測試案例：`evals/evals.json`
- 推薦品質快速評估：

```bash
python3 scripts/eval_recommendations.py
```

評估指標：
- `interest_hit_rate`（Top5 命中目標主題比例，目標 >= 0.8）
- `duplicate_rate`（重複率，目標 <= 0.05）
- `noise_rate`（低價值內容比例，目標 <= 0.2）

## 工作原則

- 先保資料品質，再追求覆蓋率
- 先結構化，再談同步到 NotebookLM
- 知識卡要服務回用，不只是保存
