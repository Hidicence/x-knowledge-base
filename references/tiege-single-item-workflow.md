# 鐵哥單筆書籤處理流程 v1

這份文件定義鐵哥應如何 **一次只處理 1 條書籤**。

## 設計原則

- 一次只吃 1 條，避免過載
- 每條書籤都是一張工單
- 失敗不影響其他書籤
- 輸出必須符合知識卡 schema
- 不確定時保守處理，不亂補內容

## 狀態流

每條書籤只能在以下狀態之間移動：

- `todo` → 待處理
- `processing` → 鐵哥正在處理
- `done` → 已完成知識卡
- `failed` → 本輪失敗，待人工檢查或重試
- `skipped` → 判定為低價值 / 垃圾來源，不納入知識庫

## 單筆輸入

最小輸入應包含：

- `id`
- `source_path`
- `source_url`（若有）
- `category`（若有）
- `priority`（可選）

範例：

```json
{
  "id": "2025746933633974542",
  "source_path": "memory/bookmarks/01-openclaw-workflows/01-clawpal.md",
  "source_url": "https://x.com/QingQ77/status/2025746933633974542",
  "category": "01-openclaw-workflows",
  "priority": "normal"
}
```

## 單筆處理步驟

### 1. claim 工單

- 從 queue 取 1 條 `todo`
- 立即改成 `processing`
- 寫入 `worker=tiege`、`started_at`

### 2. 讀原始內容

優先讀：
- 現有 bookmark markdown
- tweet / thread 補完內容
- 外部文章摘錄
- GitHub 摘錄

若內容明顯屬於以下情況，直接 `skipped`：
- 登入頁
- 404
- 首頁雜訊
- 幾乎沒有有效文本

### 3. 正規化

補齊可推斷欄位：
- title
- source_url
- author
- created_at
- category
- tags

若無法可靠推斷，寧可留空，不要亂猜。

### 4. 生成知識卡

輸出格式必須符合：
- `references/notebooklm-schema.md`
- `assets/knowledge-card-template.md`

最低要求：
- 核心摘要
- 重點整理
- 對 Pan 的價值
- 原始來源

若有資料，再補：
- 作者補充 / Thread 重點
- 外部連結重點
- 關聯主題

### 5. 寫回

寫入兩層：

1. 主知識庫卡片
   - 建議位置：`memory/notebooklm_exports/cards/`
2. 狀態更新
   - 該工單改為 `done`
   - 寫入 `finished_at`

### 6. 失敗處理

若失敗：
- 改為 `failed`
- 記錄 `error`
- 不要卡住下一條

## 鐵哥限制

鐵哥不要做這些事：

- 不要一次處理多條
- 不要批量重寫整個書籤庫
- 不要自行改 schema
- 不要自行新增高風險外部步驟

## APAN2號 的角色

APAN2號 負責：
- 設計 schema
- 維護 queue 規則
- 驗證輸出品質
- 發現問題後調整 prompt / parser / normalizer

鐵哥只負責：
- 單筆執行
- 穩定產出

## 建議節奏

先用小量驗證：
- 先跑 3-5 條
- 確認品質後再長時間慢慢清庫
