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

### 4. Prepare for Future Cloud Workflows
- Export seamlessly for Google NotebookLM.
- Auto-sync to Google Drive.

## 💻 Usage & Main Entry Points

### Full ingest + summarize flow
```bash
bash scripts/fetch_and_summarize.sh
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
📂 Repository Structure
Plaintext
x-knowledge-base/
├── SKILL.md                 # Core behavioral prompt for AI Agents
├── assets/                  # Media and UI assets
├── config/                  # System configurations
├── evals/                   # Evaluation metrics for recall accuracy
├── references/              # Documentation (e.g., conversation-recall.md)
├── scripts/                 # Core executable flows
└── tools/                   # Helper utilities
🗺️ Roadmap
v1 (MVP): Working bookmark ingestion, local knowledge cards, keyword search index, and v1 conversation recall rules. (Completed)

v2 (Quality & Context): Better summary quality, robust source URL coverage, stronger ranking, and enhanced recall usefulness in real chats.

v3 (Semantic Upgrade): Implementing semantic / vector recall (e.g., integrating LanceDB + Google Embedding models) for deeper query understanding and wording-agnostic relevance matching.

v4 (The Cloud Library): Polished NotebookLM integration, advanced library sync, and laying the groundwork for an opt-in collective knowledge network.

🤝 Notes & Contributing
This repository is the public skill repo. If you want to understand the architectural design rather than just copying scripts, we highly recommend starting with:

SKILL.md

references/conversation-recall.md

Your knowledge deserves to be remembered. Join us in building the next generation of Personal Knowledge Management (PKM) systems! PRs and issues are welcome.
