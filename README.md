# 🚀 X Knowledge Base
> **Make Knowledge Reappear | A Proactive Personal Knowledge Recall System for AI Agents**

[![Watch the Pitch Video](https://img.youtube.com/vi/JWgm6ky_pys/maxresdefault.jpg)](https://youtu.be/JWgm6ky_pys)
*(Click the image above to watch our 3-minute concept presentation)*

## 📖 The Story: Why This Exists

**"We spend our entire lives accumulating knowledge, but we never truly own it."**

Modern knowledge work creates a constant sense of information overload. We save thousands of bookmarks, threads, and insights because they feel valuable in the moment, only to draw a blank when we actually need them. Browsers and note-taking apps have essentially become a graveyard of links. 

This project started from a personal itch: *Why should we manually search for knowledge? Our knowledge should know when we need it.* **X Knowledge Base** is not just another bookmark saver. It is an infrastructure designed to help AI agents (like OpenClaw) proactively surface your previously saved knowledge right when you are brainstorming or making decisions in a real conversation.

## ✨ Core Breakthrough: Shattering the Context Window

We are in the era of AI Agents, but all agents face the same bottleneck: **limited context windows and expensive memory costs.** Stuffing your entire personal knowledge base into every LLM prompt is highly inefficient. X Knowledge Base acts as a bridge between your "Personal Library" and the "AI Agent". It allows agents to break free from context limitations by utilizing **Proactive Contextual Recall**. 

Instead of waiting for you to search, the skill quietly fetches relevant insights, summaries, and original links, handing them to you exactly when the conversation calls for them.

## 🛠️ What This Skill Does

### 1. Robust Capture & Ingestion
- Automatically fetch new X/Twitter bookmarks.
- Deduplicate by tweet ID.
- Enrich content with thread context, author additions, external links, and GitHub context via multi-layer fallbacks.

### 2. Turn Bookmarks into Knowledge Cards
- Generate LLM-powered structured summaries.
- Auto-categorize into topics and add cross-links between related cards.
- Maintain a highly searchable, privacy-first local knowledge base in Markdown.

### 3. Proactive Conversation Recall (Current Focus)
- Search existing bookmark knowledge based on semantic intent.
- Rank relevant cards for the current topic.
- Provide chat-ready recall output to help an agent *proactively* bring back useful saved knowledge without interrupting your workflow.

### 4. Wiki Knowledge Layer (v3+)
XKB now ships with a full **wiki pipeline** — a cognitive output layer that distills your bookmarks and conversations into durable, readable topic pages.

- **Two ingestion paths**: bookmarks (via `sync_cards_to_wiki.py`) + conversation memory (via `distill_memory_to_wiki.py`)
- **LLM absorb gate**: every candidate is evaluated — *"What new dimension does this add?"* — before entering the wiki
- **Staging & review**: proposed insights go to `wiki/_staging/` for human approval before being committed
- **Health tools**: `lint_wiki.py` checks for orphan/stale pages; `status_knowledge_pipeline.py` shows the full pipeline in one view

### 5. Prepare for Future Cloud Workflows
- Export seamlessly for Google NotebookLM.
- Auto-sync to Google Drive.

## 💻 Usage & Main Entry Points

### Full ingest + summarize + wiki sync
```bash
bash scripts/fetch_and_summarize.sh
# Step 7 (auto): syncs new cards into wiki topics
Search existing bookmarks
Bash
bash scripts/search_bookmarks.sh "openclaw seo"
Conversation recall (Agent Integration)
Bash
python3 scripts/recall_for_conversation.py "OpenClaw workflow agent memory"
python3 scripts/recall_for_conversation.py "AI SEO 案例" --format chat
Export to NotebookLM
Bash
python3 scripts/export_notebooklm.py
Sync to Google Drive
Bash
bash scripts/sync_to_drive.sh
### Wiki pipeline (v3+)
```bash
# Sync bookmark cards into wiki topics
python3 scripts/sync_cards_to_wiki.py --apply --limit 20

# Distill conversation memory into wiki candidates
python3 scripts/distill_memory_to_wiki.py --stage --days 2 --label morning

# Distill inline text (ad-hoc, no waiting for daily memory)
python3 scripts/distill_memory_to_wiki.py --stage --input "Key insight from today..."

# Apply approved staging candidates
python3 scripts/distill_memory_to_wiki.py --apply --staging-file wiki/_staging/YYYY-MM-DD-candidates.md

# Wiki health check
python3 scripts/lint_wiki.py [--fix]

# Full pipeline status
python3 scripts/status_knowledge_pipeline.py [--json] [--days N]

# End-to-end smoke test
bash scripts/smoke_test_pipeline.sh
```

## 📂 Repository Structure
```
x-knowledge-base/
├── SKILL.md                      # Core behavioral prompt for AI Agents
├── assets/                       # Media and UI assets
├── config/                       # System configurations
├── docs/
│   └── xkb-wiki-architecture.md # Full 4-layer architecture reference
├── evals/                        # Evaluation metrics for recall accuracy
├── references/                   # Documentation
├── scripts/                      # Core executable flows
│   ├── fetch_and_summarize.sh    # Main XKB pipeline (fetch → cards → wiki sync)
│   ├── sync_cards_to_wiki.py     # XKB cards → wiki topics (LLM absorb gate)
│   ├── distill_memory_to_wiki.py # Conversation memory → wiki staging
│   ├── lint_wiki.py              # Wiki health check
│   ├── status_knowledge_pipeline.py  # Full pipeline status view
│   └── smoke_test_pipeline.sh    # End-to-end smoke test
├── tools/                        # Helper utilities
└── wiki/
    ├── WIKI-SCHEMA.md            # Page format spec & absorb gate policy
    ├── topic-map.json            # Category → topic mapping (edit to configure)
    ├── index.md                  # Topic registry (auto-generated)
    └── topics/                   # Your wiki topic pages (gitignored — personal content)
```
## 🗺️ Roadmap

| Version | Status | Description |
|---------|--------|-------------|
| v1 (MVP) | ✅ Done | Bookmark ingestion, knowledge cards, keyword search index, v1 recall |
| v2 (Quality) | ✅ Done | Robust content extraction (bird/curl/Jina/fxtwitter), enrichment worker, vector index |
| v3 (Wiki Layer) | ✅ Done | Wiki pipeline: absorb gate, topic pages, memory distillation, staging review |
| v4 (Cloud Library) | 🔜 Planned | NotebookLM integration, Drive sync, opt-in collective knowledge network |

🤝 Notes & Contributing
This repository is the public skill repo. If you want to understand the architectural design rather than just copying scripts, we highly recommend starting with:

SKILL.md

references/conversation-recall.md

Your knowledge deserves to be remembered. Join us in building the next generation of Personal Knowledge Management (PKM) systems! PRs and issues are welcome.
