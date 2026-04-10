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

XKB handles three things: **capturing knowledge**, **sedimentation into wiki**, and **proactive recall**.

```
Input sources
├── Local notes / markdown     →  local_ingest.py
├── X/Twitter bookmarks        →  fetch_and_summarize.sh
├── YouTube playlists          →  fetch_youtube_playlist.py
├── GitHub forks/stars         →  fetch_github_repos.py
├── PDF / academic papers      →  pdf_ingest.py
└── PubMed open-access papers  →  fetch_pubmed.py
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
     ┌──┴──────────────────────────────────────────┐
     │                                              │
     ▼                                              ▼
xkb_ask.py                              Active Recall Layer
ask questions, get cited answers        knowledge surfaces automatically
                                        during AI conversations
```

The **absorb gate** is the key quality mechanism: before any card enters the wiki, an LLM evaluates — *"What new dimension does this add to what's already here?"* Only cards that bring a new case, new concept, or contradiction pass through.

The **Active Recall Layer** makes the system proactive: instead of waiting for you to search, it monitors every conversation topic and automatically surfaces relevant knowledge when it detects a match.

Each knowledge card is now **understanding-oriented**, not summary-oriented. Every card answers:
- What question is this trying to answer?
- What is the author's conclusion, and what is the claim level? (Attested / Scholarship / Inference)
- What might surprise the reader?
- How does this relate to existing cards?

**Optional: connect your agent's memory logs**

`distill_memory_to_wiki.py` can read conversation logs from your AI agent and distill insights into wiki candidates.

```bash
python3 scripts/distill_memory_to_wiki.py --stage --days 2
python3 scripts/distill_memory_to_wiki.py --stage --input "Key insight: ..."
```

For the full architecture reference: [`docs/xkb-wiki-architecture.md`](docs/xkb-wiki-architecture.md)

---

## Quick Start — Try It in 10 Minutes

```bash
# 1. Set your LLM API key (any OpenAI-compatible provider)
export LLM_API_KEY="your-key-here"
export LLM_API_URL="https://api.openai.com/v1/chat/completions"  # or your provider
export LLM_MODEL="gpt-4o-mini"

# 2. Run the demo
bash scripts/xkb_demo.sh
```

---

## What's in This Repo

### Active Recall Layer

| Script | What it does |
|--------|-------------|
| `recall_router.py` | Main recall entry point: message → classify → route → structured output + telemetry |
| `conversation_state_parser.py` | Trigger classifier: detects hard/soft/suppress from any message (no LLM) |
| `continuity_recall.py` | Continuity recall: searches MEMORY.md + wiki for project state, decisions, definitions |
| `contrarian_recall.py` | Contrarian recall: surfaces warnings, failures, limitations, counter-examples |
| `action_recall.py` | Action recall: finds reusable scripts, wiki roadmap sections, plan docs |
| `xkb_recall_server.py` | MCP server: exposes `xkb_recall` as an MCP tool for AI agents |

### Knowledge Capture

| Script | What it does |
|--------|-------------|
| `xkb_demo.sh` | Demo mode: sample dataset → cards → wiki → ask in one command |
| `xkb_ask.py` | Ask your knowledge base: query → search wiki + cards → cited answer |
| `local_ingest.py` | Local notes ingest: markdown/txt files → knowledge cards → search index |
| `suggest_topic_map.py` | Auto topic-map: analyze categories → LLM suggests wiki topic slugs |
| `fetch_and_summarize.sh` | Full XKB pipeline: fetch X/Twitter bookmarks → enrich → categorize → wiki sync |
| `fetch_youtube_playlist.py` | Fetch YouTube playlist subtitles → summarize → knowledge cards + search index |
| `run_youtube_sync.sh` | Daily YouTube playlist sync |
| `fetch_github_repos.py` | Fetch GitHub forks/stars → generate repo knowledge cards → search index |
| `run_github_sync.sh` | Daily GitHub sync |
| `sync_cards_to_wiki.py` | XKB cards → wiki topic pages (LLM absorb gate, decision log, explainability) |
| `distill_memory_to_wiki.py` | Conversation memory → staging candidates → wiki |
| `lint_wiki.py` | Wiki health check: orphan pages, stale pages, gap topics |
| `status_knowledge_pipeline.py` | Full pipeline status in one view |
| `smoke_test_pipeline.sh` | End-to-end pipeline verification (10 checks) |

### Academic Research Pipeline

| Script | What it does |
|--------|-------------|
| `fetch_pubmed.py` | Fetch open-access full-text papers from PubMed Central → markdown files |
| `pdf_ingest.py` | PDF / markdown → bilingual (zh+en) knowledge cards → search index |
| `topic_guide_generator.py` | Generate a structured domain guide from existing cards: reading order, key terms, knowledge gaps, key authors |
| `health_check.py` | Knowledge base audit: semantic conflict detection, gap analysis, duplicate detection |

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
```bash
python3 scripts/local_ingest.py path/to/my-notes.md
python3 scripts/local_ingest.py notes/ --dry-run
python3 scripts/local_ingest.py notes/ --category learning --tag personal
```

### 2. Capture external content
```bash
# X/Twitter bookmarks
bash scripts/fetch_and_summarize.sh

# YouTube playlists
python3 scripts/fetch_youtube_playlist.py --playlist "YOUR_PLAYLIST_URL"
bash scripts/run_youtube_sync.sh

# GitHub forks and starred repos
python3 scripts/fetch_github_repos.py --forks --stars
bash scripts/run_github_sync.sh

# Open-access academic papers from PubMed
python3 scripts/fetch_pubmed.py "AI medical imaging" --limit 20 --out /tmp/papers
python3 scripts/pdf_ingest.py /tmp/papers/ --category research --rebuild-index

# Local PDF files
python3 scripts/pdf_ingest.py /path/to/papers/ --category research
python3 scripts/pdf_ingest.py paper.pdf --tag ai --tag medical
```

### 2b. Research tools
```bash
# Generate a domain guide from collected research cards
python3 scripts/topic_guide_generator.py --topic "AI Medical Imaging" --category research

# Run a knowledge base health check
python3 scripts/health_check.py --category research          # all checks
python3 scripts/health_check.py --mode gaps                  # gap analysis only
python3 scripts/health_check.py --mode conflicts             # conflict detection
```

### 3. Set up your wiki topic map

**Option A — Auto-suggest:**
```bash
python3 scripts/suggest_topic_map.py --review
python3 scripts/suggest_topic_map.py --apply
```

**Option B — Manual:** Edit `wiki/topic-map.json`.

### 4. Sync cards into wiki topics
```bash
python3 scripts/sync_cards_to_wiki.py --review --no-llm
python3 scripts/sync_cards_to_wiki.py --apply --limit 20
```

### 4b. Inspect absorb gate decisions
```bash
python3 scripts/sync_cards_to_wiki.py --review-rejects
python3 scripts/sync_cards_to_wiki.py --explain "https://example.com/article"
python3 scripts/sync_cards_to_wiki.py --force-absorb "https://example.com/article"
```

### 5. Ask your knowledge base
```bash
python3 scripts/xkb_ask.py "What are the alternatives to RAG?"
python3 scripts/xkb_ask.py "How do I design an AI agent memory system?" --format chat
python3 scripts/xkb_ask.py "your question" --json
```

### 6. Distill conversation memory
```bash
python3 scripts/distill_memory_to_wiki.py --stage --days 2 --label morning
python3 scripts/distill_memory_to_wiki.py --stage --input "Key insight: ..."
python3 scripts/distill_memory_to_wiki.py --apply \
  --staging-file wiki/_staging/YYYY-MM-DD-morning-candidates.md
```

### 7. Monitor pipeline health
```bash
python3 scripts/status_knowledge_pipeline.py
python3 scripts/lint_wiki.py [--fix]
bash scripts/smoke_test_pipeline.sh
```

---

## Active Recall Layer

Every incoming message is classified by a lightweight rule-based parser (no LLM, <5ms). Based on the classification, the relevant recall module fires and returns structured output.

```
User message
    │
    ▼
conversation_state_parser.py
    │ → trigger_class: hard | soft | suppress
    │ → state: continuity | brainstorming | strategy | execution
    │
    ├─ hard trigger → continuity_recall.py (MEMORY.md + wiki)
    │                 + action_recall.py (scripts, plans, roadmaps)
    │                 → inline injection
    │
    ├─ soft trigger → recall_for_conversation.py (vector search)
    │                 + contrarian_recall.py (warnings, failures)
    │                 → side hint
    │
    └─ suppress    → nothing (casual chat, greetings)
```

**Trigger examples:**
- Hard: *"XKB 現在在哪個階段？"*, *"之前怎麼定義 active recall？"*, *"下一步是什麼？"*
- Soft: *"AI SEO 值不值得做？"*, *"怎麼設計 agent memory？"*, *"有沒有類似案例？"*
- Suppress: *"你好"*, *"今天幾號？"*, *"幫我翻譯這句話"*

### Use from command line
```bash
python3 scripts/recall_router.py "XKB active recall 現在的架構是什麼？"
python3 scripts/recall_router.py "你的問題" --dry-run
python3 scripts/recall_router.py "你的問題" --json
```

### Use as MCP tool

**OpenClaw setup** — add to `openclaw.json`:
```json
{
  "mcp": {
    "servers": {
      "xkb-recall": {
        "command": "python3",
        "args": ["/path/to/workspace/skills/x-knowledge-base/scripts/xkb_recall_server.py"],
        "env": { "OPENCLAW_WORKSPACE": "/path/to/workspace" }
      }
    }
  }
}
```

**Claude Code setup** — add to `.claude/settings.json`:
```json
{
  "mcpServers": {
    "xkb-recall": {
      "command": "python3",
      "args": ["/path/to/workspace/skills/x-knowledge-base/scripts/xkb_recall_server.py"],
      "env": { "OPENCLAW_WORKSPACE": "/path/to/workspace" }
    }
  }
}
```

### Telemetry
Every recall event is logged to `memory/x-knowledge-base/recall-telemetry.jsonl`:
```json
{"ts": "...", "trigger_class": "hard", "state": "continuity", "recalled": true, "result_count": 2, "duration_ms": 45}
```

---

## Requirements

### Environment
- Python 3.10+
- `OPENCLAW_WORKSPACE` — path to your workspace directory (e.g. `~/.openclaw/workspace`)

### LLM API key (required)
XKB uses **one primary LLM key** for all enrichment, absorb gate judgments, and memory distillation. Configure via environment variables:

```bash
export LLM_API_KEY="your-api-key"
export LLM_API_URL="https://api.openai.com/v1/chat/completions"  # or any compatible endpoint
export LLM_MODEL="gpt-4o-mini"   # e.g. gpt-4o-mini, claude-3-haiku, MiniMax-M2.5
```

Used by: all worker scripts (`run_bookmark_worker.py`, `run_scan_worker.py`), `local_ingest.py`, `fetch_github_repos.py`, `fetch_youtube_playlist.py`, `sync_cards_to_wiki.py`, `distill_memory_to_wiki.py`, `pdf_ingest.py`, `fetch_pubmed.py`, `topic_guide_generator.py`, `xkb_ask.py`

### Optional: Gemini API key
`GEMINI_API_KEY` is only needed for semantic vector search and the health check conflict detector:

```bash
export GEMINI_API_KEY="your-gemini-key"
# Get one free at: https://aistudio.google.com/
```

Used by: `build_vector_index.py`, `health_check.py`

Without it, XKB falls back to CJK-bigram keyword search (still functional, lower recall quality).

### Optional: Twitter/X auth

> ⚠️ **`BIRD_AUTH_TOKEN` and `BIRD_CT0` are X/Twitter session cookies, not regular API keys.**
> Anyone holding these can read your private bookmarks and browse X as you.
> Store only in system env vars or `.secrets/x-knowledge-base.env` — never paste in chat or issues.
> If exposed: log out of X immediately to invalidate the session.

- `BIRD_AUTH_TOKEN` + `BIRD_CT0` — X/Twitter bookmark fetching; falls back to curl/Jina without it

### Privacy & Data Flow

XKB sends bookmark content to your configured LLM API for enrichment. To understand exactly what leaves your machine per script, see [`docs/data-flow.md`](docs/data-flow.md).

**Local-only mode** — process bookmarks without any external API calls:
```bash
python3 scripts/run_bookmark_worker.py --local-only
python3 scripts/run_scan_worker.py --local-only
```

For fully local vector search, use Ollama: `EMBEDDING_PROVIDER=ollama`

---

## Roadmap

| Version | Status | What it delivered |
|---------|--------|-------------------|
| v0.1 | ✅ | Bookmark ingestion, knowledge cards, keyword search, basic recall |
| v0.2 | ✅ | Multi-layer content extraction, enrichment worker, vector index |
| v0.3 | ✅ | Wiki pipeline: absorb gate, topic pages, memory distillation, staging review |
| v0.4 | ✅ | Local notes ingest, ask layer with citations, demo mode, auto topic-map |
| v0.5 | ✅ | Absorb gate explainability: --review-rejects, --explain, --force-absorb |
| v0.6 | ✅ | Active Recall Layer: proactive recall during AI conversations, MCP server, telemetry |
| v0.7 | ✅ | Knowledge quality layer: understanding-oriented cards (Claim levels, False Friends, bilingual summaries, cross-card context injection), academic PDF pipeline, domain guides, health check |
| v0.8 | 🔜 | Proactive linking (cross-reference on ingest), onboarding wizard |

---

## Design Principles

- **Layers, not one database.** Working memory, consolidation, external capture, and output are separate problems requiring separate solutions.
- **Quality gates over quantity.** The absorb gate ensures the wiki stays a distilled output layer, not a second inbox.
- **Understanding over summarization.** Cards answer "what question does this solve?" not "what does this say?".
- **Human in the loop for internal knowledge.** Conversation memory goes through staging and human review before entering the wiki.
- **Proactive over reactive.** Knowledge should surface when relevant, not when searched.
- **Show value first.** New users see the output before they configure the system.

---

## Contributing

Start with [`SKILL.md`](SKILL.md) to understand the behavioral design, and [`docs/xkb-wiki-architecture.md`](docs/xkb-wiki-architecture.md) for the full architecture.

PRs and issues welcome. Your knowledge deserves to be remembered.
