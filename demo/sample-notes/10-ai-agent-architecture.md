# AI Agent 架構設計筆記

## 什麼是 Agent？

Agent = LLM + Tools + Memory + Planning

和普通 LLM 的差異：
- **Tools**：可以執行程式碼、查資料庫、呼叫 API
- **Memory**：記得之前做了什麼（超越 context window）
- **Planning**：可以把複雜目標拆解成多步驟執行

## 幾種常見的 Agent 架構

### ReAct（Reasoning + Acting）
最基礎的架構：Thought → Action → Observation → Thought → ...
優點：簡單、可解釋
缺點：容易陷入循環，不擅長平行任務

### Plan-and-Execute
先規劃所有步驟，再執行。
優點：長程任務更穩定
缺點：規劃阶段可能出錯，且難以動態調整

### Multi-Agent
多個 agent 協作，有 orchestrator 分工。
優點：可處理複雜、需要多種技能的任務
缺點：協調開銷大，debug 困難

## 實際遇到的問題

1. **Context window 滿了**：長任務容易超出限制，需要摘要機制
2. **工具調用失敗**：需要 retry + fallback 邏輯
3. **Prompt injection**：外部資料可能包含惡意指令
4. **Evaluation 困難**：很難自動化評估 agent 表現

## 我的設計原則

- **小而精的工具**：每個工具只做一件事，輸入輸出明確
- **Idempotent 操作**：重試安全，避免副作用疊加
- **明確的失敗模式**：agent 必須知道何時該停止並回報
- **Human-in-the-loop**：高風險操作必須要人工確認
