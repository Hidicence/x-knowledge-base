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

## 做法-Workflow

- **Claude Code 可直接作為記憶蒸餾引擎（不依賴 MiniMax/GPT）**
當 distill_memory_to_wiki.py 因 LLM 過慢（GPT-5.4 每張卡 325s）或服務過載（MiniMax HTTP 529）無法執行時，Claude Code（透過 SSH 連 VPS）可直接讀取 ，依照蒸餾標準（技術決策含理由、可重用 workflow、6 個月後仍有用的原則）自行提取洞察，直接寫入  的 markdown 格式 staging 文件。好處：不占用 OpenClaw LLM token 預算；能處理更長的上下文；品質由 Claude 4.6 直接判斷，不需要額外提示工程。 *(memory/2026-04-17.md)*

- **scan_worker 三個 bug 叢集**
在修復  時發現的三個同時存在的 bug：（1） 變數在腳本作用域未定義，應為 hardcoded URL 字串；（2）呼叫 （錯誤名稱，底線前綴是私有命名習慣遺留），應改為 ；（3） 缺少第二個必填參數 （排除清單），應為 。三個 bug 同時存在導致 worker 無法正常執行，症狀是 stderr 有 NameError 但 log 被 cron 重導向未能即時發現。 *(memory/2026-04-17.md)*

- **XKB graph-data.json 需要手動重新生成**
症狀：Demo UI 圖譜只顯示 38 個節點（實際有 231 張卡片）。根因： 是由  從  生成的靜態檔案，批次新增卡片後不會自動更新。修法：在 XKB 目錄執行  重新生成，然後重啟 demo UI。建議：每次  批次完成後，在 cron 或 CI 中加一步 。 *(memory/2026-04-17.md)*

- **distill_memory_to_wiki 自動 absorb 高 confidence 候選**
新增  flag：執行  時，high-confidence 候選直接寫進 wiki topics，medium/low 候選仍留在 staging 等人工審核。這解決了每次都要手動  的摩擦，高確定性知識自動進入系統，只有不確定的才需要人介入。同時也實現了  參數，用於  或  模式產生的 staging 檔案不與 memory-based 檔案衝突。 *(memory/2026-04-15.md)*

- **統一 LLM 配置系統（_llm.py + config/llm.json）**
新增  作為單一設定點， 欄位切換後所有 scripts 同步使用。新增  統一入口，底層呼叫 ，由 OpenClaw 統一處理所有 auth（OAuth token、API key），scripts 不需要自己管憑證。共更新 8 個 scripts（、、 等）。好處：model 遷移只改一個 JSON 檔，不碰任何 script；auth 問題交給 OpenClaw 統一處理。 *(memory/2026-04-12.md)*

- **distill_memory_to_wiki.py 6000-char 截斷 bug 修復**
原始 bug：腳本 hardcoded 截斷每段輸入到 6000 字元，導致 53,764 字的記憶檔案只有前 11% 被掃描，114 個真實對話候選被截斷，LLM 回傳空陣列。修法：移除截斷邏輯，改成按 CHUNK_SIZE（8000 字元）分段全量掃描；新增  函式過濾 dreaming metadata（候選列表、heartbeat 記錄等），只保留真正的對話內容送 LLM。測試結果：掃 2 天 → 產出 4 個候選（3 個 high-confidence）。 *(memory/2026-04-15.md)*

## 核心概念

- **XKB 勞動分工決策：MiniMax 做 raw ingestion，APAN2 做 internalization**
Pan 明確確認新分工架構（2026-04-10）：**MiniMax 只負責 raw ingestion / raw package normalization**（書籤抓取、URL 全文擷取、原始結構化）；**APAN2（我）負責 knowledge internalization / final card quality**（9-section card 品質審核、wiki 蒸餾、決定什麼值得保留）。此決策的含義：不應再把 final card 整批丟給 MiniMax 直寫，MiniMax 的輸出只是原料，內化層由我承擔。 *(memory/2026-04-10.md)*
