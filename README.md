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

XKB handles two things: **capturing knowledge** and **sedimentation into wiki**.

```
Input sources
├── Local notes / markdown     →  local_ingest.py  (✨ new)
├── X/Twitter bookmarks        →  fetch_and_summarize.sh
├── YouTube playlists          →  fetch_youtube_playlist.py
└── GitHub forks/stars         →  fetch_github_repos.py  (repo-level cards only)
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
        │
        ▼
xkb_ask.py  ←  ask questions, get cited answers  (✨ new)
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

## Quick Start — Try It in 10 Minutes

The fastest way to see XKB in action: run the demo mode with the bundled sample dataset.

```bash
# 1. Set your LLM API key (any OpenAI-compatible provider)
export LLM_API_KEY="your-key-here"
export LLM_API_URL="https://api.openai.com/v1/chat/completions"  # or your provider
export LLM_MODEL="gpt-4o-mini"

# 2. Run the demo
bash scripts/xkb_demo.sh
```

This will:
1. Convert 10 sample notes into knowledge cards
2. Sync them through the absorb gate into wiki topic pages
3. Show you a live question-answering demo with citations

No API keys, topic-map config, or prior setup needed beyond the LLM key.

---

## What's in This Repo

### Core Scripts

| Script | What it does |
|--------|-------------|
| `xkb_demo.sh` | ✨ **Demo mode**: sample dataset → cards → wiki → ask in one command |
| `xkb_ask.py` | ✨ **Ask your knowledge base**: query → search wiki + cards → cited answer |
| `local_ingest.py` | ✨ **Local notes ingest**: markdown/txt files → knowledge cards → search index |
| `suggest_topic_map.py` | ✨ **Auto topic-map**: analyze categories → LLM suggests wiki topic slugs |
| `fetch_and_summarize.sh` | Full XKB pipeline: fetch X/Twitter bookmarks → enrich → summarize → categorize → wiki sync |
| `fetch_youtube_playlist.py` | Fetch YouTube playlist subtitles → summarize → add to knowledge cards + search index |
| `run_youtube_sync.sh` | Daily YouTube playlist sync (wraps fetch_youtube_playlist.py) |
| `fetch_github_repos.py` | Fetch GitHub forks/stars → generate repo knowledge cards → add to search index |
| `run_github_sync.sh` | Daily GitHub sync (wraps fetch_github_repos.py) |
| `sync_cards_to_wiki.py` | XKB cards → wiki topic pages (LLM absorb gate, decision log, explainability) |
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

## Full Setup

### 0. Try the demo first (optional but recommended)
```bash
bash scripts/xkb_demo.sh
```

### 1. Ingest local notes
The fastest way to get started with your own content:

```bash
# Single file
python3 scripts/local_ingest.py path/to/my-notes.md

# Entire directory
python3 scripts/local_ingest.py path/to/notes/

# Preview what would be processed (no writes)
python3 scripts/local_ingest.py notes/ --dry-run

# With category and tag overrides
python3 scripts/local_ingest.py notes/ --category learning --tag personal
```

### 2. Capture external content
```bash
# Capture X/Twitter bookmarks
bash scripts/fetch_and_summarize.sh

# Capture YouTube playlists (subtitles → knowledge cards)
python3 scripts/fetch_youtube_playlist.py --playlist "YOUR_PLAYLIST_URL"
bash scripts/run_youtube_sync.sh   # daily sync

# Capture GitHub forks and starred repos
python3 scripts/fetch_github_repos.py --forks --stars
bash scripts/run_github_sync.sh    # daily sync
```

### 3. Set up your wiki topic map

**Option A — Auto-suggest (recommended for new setups):**
```bash
# Review LLM suggestions (no writes)
python3 scripts/suggest_topic_map.py --review

# Apply suggestions to wiki/topic-map.json
python3 scripts/suggest_topic_map.py --apply
```

**Option B — Manual:**
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

### 4b. Inspect absorb gate decisions (v5)
```bash
# See all rejected cards with reasons
python3 scripts/sync_cards_to_wiki.py --review-rejects

# Filter by topic or date
python3 scripts/sync_cards_to_wiki.py --review-rejects --topic ai-memory --since 2025-01-01

# Explain why a specific card was rejected
python3 scripts/sync_cards_to_wiki.py --explain "https://example.com/article"

# Override a rejection — force the card into the wiki on next --apply
python3 scripts/sync_cards_to_wiki.py --force-absorb "https://example.com/article"
python3 scripts/sync_cards_to_wiki.py --force-absorb "https://example.com/article" --topic ai-memory
```

### 5. Ask your knowledge base
```bash
# Ask a question — searches wiki topics + knowledge cards, returns cited answer
python3 scripts/xkb_ask.py "What are the alternatives to RAG?"

# Compact format for chat use
python3 scripts/xkb_ask.py "How do I design an AI agent memory system?" --format chat

# JSON output for programmatic use
python3 scripts/xkb_ask.py "your question" --json
```

### 6. Distill conversation memory into wiki
```bash
# Distill recent memory files (e.g. morning run)
python3 scripts/distill_memory_to_wiki.py --stage --days 2 --label morning

# Distill a specific insight right now (no waiting)
python3 scripts/distill_memory_to_wiki.py --stage --input "Key insight: ..."

# Apply approved staging candidates
python3 scripts/distill_memory_to_wiki.py --apply \
  --staging-file wiki/_staging/YYYY-MM-DD-morning-candidates.md
```

### 7. Monitor pipeline health
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
The scripts call any OpenAI-compatible LLM for summarization, absorb gate judgments, and memory distillation. Configure via environment variables:

```bash
export LLM_API_KEY="your-api-key"
export LLM_API_URL="https://api.openai.com/v1/chat/completions"  # or any compatible endpoint
export LLM_MODEL="gpt-4o-mini"   # e.g. gpt-4o-mini, claude-3-haiku, gemini-flash
```

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
| v4 | ✅ | Local notes ingest, ask layer with citations, demo mode, auto topic-map |
| v5 | ✅ | Absorb gate explainability: --review-rejects, --explain, --force-absorb |
| v6 | 🔜 | Quickstart onboarding wizard, proactive surface layer |

---

## Design Principles

- **Layers, not one database.** Working memory, consolidation, external capture, and output are separate problems requiring separate solutions.
- **Quality gates over quantity.** The absorb gate ensures the wiki stays a distilled output layer, not a second inbox.
- **Human in the loop for internal knowledge.** Conversation memory goes through staging and human review before entering the wiki. External bookmarks can use LLM auto-filtering.
- **Proactive over reactive.** Knowledge should surface when relevant, not when searched.
- **Show value first.** New users see the output before they configure the system.

---

## Contributing

Start with [`SKILL.md`](SKILL.md) to understand the behavioral design, and [`docs/xkb-wiki-architecture.md`](docs/xkb-wiki-architecture.md) for the full architecture.

PRs and issues welcome. Your knowledge deserves to be remembered.
