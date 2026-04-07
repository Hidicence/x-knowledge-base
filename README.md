# X Knowledge Base
> **讓知識重新浮現 | Make Knowledge Reappear**
>
> A personal knowledge lifecycle system for AI agents — from raw bookmarks to structured, reusable wiki.
> Works with any AI agent (Claude Code, OpenClaw, or any agent that can read a system prompt).

[![Watch the Pitch Video](https://img.youtube.com/vi/JWgm6ky_pys/maxresdefault.jpg)](https://youtu.be/JWgm6ky_pys)
*(Click to watch the concept presentation)*

---

## The Problem

Every day we consume dozens of articles, threads, and insights. We bookmark them because they feel important. Six months later — we can't find them, can't recall them, and have no idea what we actually learned.

Existing tools (bookmark apps, note-taking apps, search) all assume you'll manually retrieve knowledge when you need it. **But knowledge should know when you need it.**

XKB is built on a different premise: knowledge has a lifecycle. The goal is not to archive more — it's to make what you've already consumed *reappear at the right moment* and *gradually sediment into durable understanding*.

---

## How It Works

XKB handles two things: **capturing external knowledge** and **sedimentation into wiki**.

```
External content sources
├── X/Twitter bookmarks  →  fetch_and_summarize.sh
└── YouTube playlists    →  fetch_youtube_playlist.py
        │
        ▼
(fetch → enrich → summarize → categorize)
        │
        ▼
knowledge cards + search index + vector index
        │
        ▼
sync_cards_to_wiki.py + Absorb Gate
(LLM asks: "What new dimension does this add?")
        │
        ▼
wiki/topics/*.md  ←  durable, readable knowledge pages
```

The **absorb gate** is the key quality mechanism: before any card enters the wiki, an LLM evaluates — *"What new dimension does this add to what's already here?"* Only cards that bring a new case, new concept, or contradiction pass through. Everything else is logged and skipped.

**Optional: connect your agent's memory logs**

`distill_memory_to_wiki.py` can also read conversation logs from your AI agent and distill insights into wiki candidates. This bridges your agent's internal memory (managed by the agent, not XKB) into the same wiki output layer — but the memory management itself is outside XKB's scope.

```bash
# Distill from agent memory log files
python3 scripts/distill_memory_to_wiki.py --stage --days 2

# Or distill inline — paste any text you want to preserve
python3 scripts/distill_memory_to_wiki.py --stage --input "Key insight: ..."
```

For the full architecture reference: [`docs/xkb-wiki-architecture.md`](docs/xkb-wiki-architecture.md)

---

## What's in This Repo

### Core Scripts

| Script | What it does |
|--------|-------------|
| `fetch_and_summarize.sh` | Full XKB pipeline: fetch X/Twitter bookmarks → enrich → summarize → categorize → wiki sync |
| `fetch_youtube_playlist.py` | Fetch YouTube playlist subtitles → summarize → add to knowledge cards + search index |
| `run_youtube_sync.sh` | Daily YouTube playlist sync (wraps fetch_youtube_playlist.py) |
| `sync_cards_to_wiki.py` | XKB cards → wiki topic pages (LLM absorb gate, decision log) |
| `distill_memory_to_wiki.py` | Conversation memory → staging candidates → wiki (with `--input` for ad-hoc distillation) |
| `lint_wiki.py` | Wiki health check: orphan pages, stale pages, gap topics |
| `status_knowledge_pipeline.py` | Full pipeline status in one view |
| `smoke_test_pipeline.sh` | End-to-end pipeline verification (10 checks) |

### Wiki Template

```
wiki/
├── WIKI-SCHEMA.md      # Page format spec and absorb gate policy
├── topic-map.json      # Category → topic mapping (configure this)
├── index.md            # Topic registry (auto-maintained)
└── topics/             # Your wiki pages (gitignored — personal content)
```

---

## Quick Start

### 1. Capture external content and build knowledge cards
```bash
# Capture X/Twitter bookmarks
bash scripts/fetch_and_summarize.sh
# This runs the full pipeline: fetch → enrich → summarize → categorize → wiki sync

# Capture YouTube playlists (subtitles → knowledge cards)
python3 scripts/fetch_youtube_playlist.py --playlist "YOUR_PLAYLIST_URL"
bash scripts/run_youtube_sync.sh   # daily sync
```

### 2. Search your knowledge base
```bash
bash scripts/search_bookmarks.sh "your query"

# Semantic search (requires Gemini API key)
python3 scripts/recall_for_conversation.py "query" --format chat
```

### 3. Set up your wiki topic map
Edit `wiki/topic-map.json` to map your XKB categories to wiki topic slugs:
```json
{
  "mapping": {
    "your-xkb-category": {
      "topics": ["your-wiki-topic-slug"],
      "status": "active"
    }
  }
}
```

### 4. Sync cards into wiki topics
```bash
# Preview what would be added (no LLM)
python3 scripts/sync_cards_to_wiki.py --review --no-llm

# Apply with LLM absorb gate
python3 scripts/sync_cards_to_wiki.py --apply --limit 20
```

### 5. Distill conversation memory into wiki
```bash
# Distill recent memory files (e.g. morning run)
python3 scripts/distill_memory_to_wiki.py --stage --days 2 --label morning

# Distill a specific insight right now (no waiting)
python3 scripts/distill_memory_to_wiki.py --stage --input "Key insight: ..."

# Apply approved staging candidates
python3 scripts/distill_memory_to_wiki.py --apply \
  --staging-file wiki/_staging/YYYY-MM-DD-morning-candidates.md
```

### 6. Monitor pipeline health
```bash
python3 scripts/status_knowledge_pipeline.py        # Full status
python3 scripts/lint_wiki.py [--fix]                # Wiki health check
bash scripts/smoke_test_pipeline.sh                 # End-to-end test
```

---

## Requirements

### Agent compatibility
XKB runs as a **skill on top of any AI agent** — Claude Code, OpenClaw, or any agent that can read a system prompt and run shell scripts. The scripts are plain Python/bash with no agent-specific dependencies.

Set up: read `SKILL.md`, create the workspace directory structure, schedule the scripts.

### Environment
- Python 3.10+
- `OPENCLAW_WORKSPACE` — path to your workspace directory (e.g. `~/.openclaw/workspace`)

### LLM API key (required)
The scripts call any OpenAI-compatible LLM for summarization, absorb gate judgments, and memory distillation. Configure your provider at the top of each script:

```python
# In sync_cards_to_wiki.py / distill_memory_to_wiki.py
LLM_API_URL = "https://..."        # your provider's chat completions endpoint
LLM_MODEL   = "your-model-name"    # e.g. gpt-4o-mini, claude-3-haiku, gemini-flash
```

Set your API key as the `LLM_API_KEY` environment variable.

### Optional
- `GEMINI_API_KEY` — semantic vector index (falls back to keyword search without it)
- `BIRD_AUTH_TOKEN` + `BIRD_CT0` — X/Twitter bookmark fetching via [bird CLI](https://github.com/zedeus/nitter); falls back to curl/Jina without it

---

## Roadmap

| Version | Status | What it delivered |
|---------|--------|-------------------|
| v1 | ✅ | Bookmark ingestion, knowledge cards, keyword search, v1 recall |
| v2 | ✅ | Multi-layer content extraction, enrichment worker, vector index |
| v3 | ✅ | Wiki pipeline: absorb gate, topic pages, memory distillation, staging review |
| v4 | 🔜 | NotebookLM integration, Drive sync, collective knowledge network |

---

## Design Principles

- **Layers, not one database.** Working memory, consolidation, external capture, and output are separate problems requiring separate solutions.
- **Quality gates over quantity.** The absorb gate ensures the wiki stays a distilled output layer, not a second inbox.
- **Human in the loop for internal knowledge.** Conversation memory goes through staging and human review before entering the wiki. External bookmarks can use LLM auto-filtering.
- **Proactive over reactive.** Knowledge should surface when relevant, not when searched.

---

## Contributing

Start with [`SKILL.md`](SKILL.md) to understand the behavioral design, and [`docs/xkb-wiki-architecture.md`](docs/xkb-wiki-architecture.md) for the full architecture.

PRs and issues welcome. Your knowledge deserves to be remembered.
