# NotebookLM 匯出 Schema v1

這份 schema 定義 x-knowledge-base 匯出到 NotebookLM 時的標準知識卡格式。

## 目標

- 讓 NotebookLM 容易讀懂與引用
- 讓 APAN2號 易於本地檢索與回用
- 保留原始來源，但把主要重點結構化

## 單卡格式

```md
---
id: 2025746933633974542
type: x-knowledge-card
source_type: x-bookmark
source_url: https://x.com/QingQ77/status/2025746933633974542
author: QingQ77
created_at: 2026-02-23
category: openclaw
tags: [openclaw, gui, agent, dashboard]
confidence: medium
---

# ClawPal：OpenClaw 視覺化控制中心

## 1. 核心摘要
一句話說明這張卡的核心價值。

## 2. 重點整理
- 重點 1
- 重點 2
- 重點 3

## 3. 作者補充 / Thread 重點
- 若有 thread 或作者後續補充，整理 2-4 點
- 若沒有，寫「無明顯補充」

## 4. 外部連結重點
### <url>
- 文章 / repo / issue / PR 的核心資訊

## 5. 對 Pan 的價值
- 值得追蹤什麼
- 可以怎麼用
- 適合哪個專案 / 工作流

## 6. 關聯主題
- Topic A
- Topic B

## 7. 原始來源
- Tweet: ...
- Links: ...
```

## Topic 彙整格式

每個主題彙整檔應包含：

1. 主題簡介
2. 代表性卡片清單
3. 重複出現的關鍵觀點
4. 近期值得追的方向

## 匯出目錄建議

```text
memory/notebooklm_exports/
├── cards/
│   └── <card>.md
└── topics/
    └── topic-<slug>.md
```

## 品質規則

- 有效來源優先於覆蓋率
- 沒有 thread / 外鏈時允許輸出簡化卡
- 不要把登入頁、404、首頁噪音輸出到 NotebookLM
- 對 Pan 的價值一定要可執行，不寫空話
