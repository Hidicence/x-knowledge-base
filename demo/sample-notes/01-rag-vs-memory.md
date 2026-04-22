# RAG vs Long-term Memory in AI Agents

## 核心差異

RAG（Retrieval-Augmented Generation）是**每次查詢時**從外部知識庫拉取片段塞入 context window，屬於短期、任務型的知識注入。

Long-term Memory 是 agent 跨 session 持續維護的知識結構，包含：
- **Episodic memory**：過去做了什麼（事件記憶）
- **Semantic memory**：知道什麼（概念記憶，通常壓縮成摘要）
- **Procedural memory**：怎麼做（技能、SOP）

## 實際問題

RAG 的痛點：
1. Chunk size 太小會失去脈絡，太大會稀釋相關性
2. 需要 embedding 模型，有成本和延遲
3. 不知道自己不知道什麼（無法主動召回）

Memory 的痛點：
1. 如何決定哪些值得記住？
2. 記憶老化、矛盾如何處理？
3. 長期維護成本高

## 我的結論

兩者不互斥。最佳架構是：
- **Working memory**（context window）：當前任務
- **Episodic + semantic memory**（本地 markdown）：跨 session 知識
- **RAG**：大型外部知識庫的按需查詢

OpenClaw 的 memory-core 就是這樣設計的：daily notes → dreaming（壓縮）→ MEMORY.md（索引）
