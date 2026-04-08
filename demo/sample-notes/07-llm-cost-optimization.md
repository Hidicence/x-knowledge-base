# LLM Cost Optimization 筆記

## 為什麼成本比你想的重要

一個 AI agent 如果每次對話用 100K tokens，月活 1000 用戶 = 每月 1 億 tokens。
GPT-4o 的價格 $2.5/1M tokens，這就是 $250/月。還好。
但如果用 GPT-4 Turbo，同樣情況 = $3000/月。

## 幾個有效的 cost reduction 策略

### 1. 智慧路由（Smart Routing）
不是每個請求都需要最強的模型。
- 簡單問題 → Haiku / GPT-3.5 ($0.25/1M tokens)
- 中等複雜 → Sonnet / GPT-4o-mini
- 複雜推理 → Opus / GPT-4o

節省空間：60-80%

### 2. Prompt Caching
Anthropic 提供 prompt caching，固定的 system prompt 只算一次。
如果 system prompt 1000 tokens，每次對話都帶，10000 次對話 = 10M tokens。
Cache 之後 = 1000 tokens（初始）+ 10000 * 0（cached）

### 3. Context Window 管理
不要把整個對話歷史都塞進 context。
- 超過 X 輪後，做一次摘要
- 舊的 message 壓縮成「會話摘要」
- 只保留最近 N 輪 + 摘要

### 4. Output Token 控制
輸出 token 通常比輸入貴 3-5 倍。
用 structured output（JSON schema）限制輸出長度。

## 成本監控必備

每個 LLM 請求都要 log：
- model used
- input/output tokens
- latency
- task type

才能知道哪些操作在燒錢。
