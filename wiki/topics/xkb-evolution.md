---
title: XKB Evolution Roadmap
description: X Knowledge Base 未來演進方向
created: 2026-04-12
tags: [xkb, roadmap]
---

# XKB Evolution Roadmap

未來版本的 X Knowledge Base 可以考慮的演進方向。

---

## 1. GEPA Prompt Self-Evolution

**狀態**: 觀察中  
**時間**: 未定

**来源**: NousResearch/hermes-agent-self-evolution（ICLR 2026 Oral）

**核心概念**: 用 GEPA（Genetic-Pareto Prompt Evolution）引擎，讓 XKB 內置的 prompt 自己進化自己，不需要人工手調。成本約 $2-10/次（API tokens），不需要 GPU training。

**前提條件**:
- 需先建立 XKB 各環節的執行軌跡（execution traces）數據集
- 需定義每個環節的品質判定標準（什麼算成功/失敗）
- 需將 OpenClaw skill 格式轉換為 GEPA 相容格式

**待研究**:
- XKB 目前各 prompt 的實際執行數據是否足夠用來建立 eval dataset
- 接入 GEPA 的技術成本

**相關リンク**:
- https://github.com/NousResearch/hermes-agent-self-evolution

---

## 2. 教授級工作者訪談計畫

**狀態**: 規劃中  
**時間**: 未定

**核心目的**: 訪談教授級與高知識密度工作者，研究其知識來源、整理方式、篩選標準與內化流程，作為 XKB 系統設計的重要輸入。

---

## 3. 知識內化流程自動化

**狀態**: 規劃中  
**時間**: 未定

**核心目的**: 讓外部知識攝取到內化輸出的流程更加自動化，減少人工介入點。

---

## 4. XBrain 資料層整合完成（2026-04-14）

**狀態**: 已完成  
**時間**: 2026-04-14

**這次完成的事**:
- XKB skill repo 已升級到 XBrain 整合版主分支
- OpenClaw 端 XBrain runtime 已改用支援 Gemini embedding 的 Hidicence/gbrain fork
- 現有 cards 已回填進 XBrain，query 可正常回結果
- `recall_for_conversation.py` 已修正 auto-detect，會讀 `~/.openclaw/openclaw.json`，不再只依賴 env var
- gbrain schema type 已清理為以 `x-knowledge-card` 為主（267/268），剩餘 1 張 PDF 衍生 concept 屬合理例外

**關鍵修正**:
- P1: 對話召回主流程真正切到 XBrain，而不是停留在舊 semantic/vector fallback
- P2: gbrain fork 新增 type inference 規則，能從 frontmatter、`/bookmarks/` 路徑與純數字 tweet ID 正確判定 `x-knowledge-card`

**目前判斷**:
- 架構已從「能跑但不可信」進到「可日常使用」
- 目前剩餘工作主要是 P3: 精準召回 / ranking / query expansion 微調，不阻塞上線使用

**後續建議**:
- 固定保存一組 smoke test queries，之後每次升級後重跑
- 補一份版本凍結紀錄，記下 x-knowledge-base commit、gbrain fork commit、OpenClaw config 關鍵設定

---

*最後更新: 2026-04-14*
