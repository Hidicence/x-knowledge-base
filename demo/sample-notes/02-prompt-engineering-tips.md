# Prompt Engineering 實戰筆記

## 最有效的幾個技巧

### 1. Role + Context + Task 結構
不要只說"幫我寫一篇文章"，要給：
- 你是誰（role）：「你是一個有十年經驗的 SaaS 產品經理」
- 背景（context）：「我們正在 launch 一個給中小企業的 CRM 工具」
- 任務（task）：「幫我寫一封 cold email 給潛在客戶的 VP of Sales」

### 2. Few-shot examples 勝過指令
說「像這樣寫」然後給 2-3 個範例，比說「要簡潔、要有力、要有數據支持」更有效。

### 3. Chain of Thought for 複雜推理
要模型推理複雜問題時加上「請一步一步思考」。尤其對 math、code debug、多步驟決策有效。

### 4. 讓模型說「我不知道」
明確告訴模型：「如果你不確定，直接說你不確定，不要猜測」。防止 hallucination。

### 5. Temperature 調整
- 創意任務（文案、故事）：temperature 0.7-0.9
- 分析任務（code review、事實查詢）：temperature 0-0.3
- 翻譯：temperature 0

## 常見錯誤

- 指令過長過複雜：模型會忘記前面的指令
- 負面指令（「不要做 X」）：比正面指令（「做 Y」）效果差
- 沒有格式約束：讓模型輸出 JSON/markdown/bullet points 更易處理
