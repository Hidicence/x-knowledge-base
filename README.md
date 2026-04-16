# X Knowledge Base (XKB)

[**繁體中文**](./README.zh.md) · English

> **讓知識重新浮現 | Make Knowledge Reappear**
>
> A personal knowledge lifecycle system with semantic active recall — bookmarks, conversations, and notes flow through a two-layer retrieval engine (XBrain hybrid search + wiki distillation) into durable, reusable knowledge. Ships with an interactive graph UI.

[![Watch the Pitch Video](https://img.youtube.com/vi/JWgm6ky_pys/maxresdefault.jpg)](https://youtu.be/JWgm6ky_pys)
*(Click to watch the concept presentation)*

---

## The Problem

Every day we consume dozens of articles, threads, and insights. We bookmark them because they feel important. Six months later — we cannot find them, cannot recall them, and have no idea what we actually learned.

Existing tools all assume you will manually retrieve knowledge when you need it. **But knowledge should know when you need it.**

XKB is built on a different premise: knowledge has a lifecycle. The goal is not to archive more — it is to make what you have already consumed *reappear at the right moment* and *gradually sediment into durable understanding*.

---

## How It Works

### The Full Pipeline

```
Input sources
├── X/Twitter bookmarks        →  run_scan_worker.py / run_bookmark_worker.py
├── YouTube playlists          →  fetch_youtube_playlist.py
├── GitHub forks/stars         →  fetch_github_repos.py
├── Local notes / markdown     →  local_ingest.py
└── PubMed / academic papers   →  fetch_pubmed.py + local_ingest.py
        │
        ▼
  scripts/_card_prompt.py   ← shared by ALL ingest scripts
  (unified 9-section card format, same prompt regardless of source)
        │
        ▼ (all LLM calls go through scripts/_llm.py)
        │
┌─────────────────────────────────────────────────────────────┐
│  Knowledge Artifacts (permanent, gitignored)                │
│                                                             │
│  memory/cards/*.md          structured 9-section cards      │
│  wiki/topics/*.md           distilled long-term knowledge   │
└─────────────────────────────────────────────────────────────┘
        │
        ▼ on every card write (auto)
┌─────────────────────────────────────────────────────────────┐
│  Primary Retrieval — XBrain                                 │
│  (XKB's semantic search layer, powered by GBrain)           │
│                                                             │
│  • pgvector + PGLite embedded DB                            │
│  • Gemini embeddings                                        │
│  • RRF hybrid search (vector + keyword)                     │
│  • xbrain_recall.py  ← used automatically by all scripts   │
└─────────────────────────────────────────────────────────────┘
        │  falls back to when XBrain unavailable
        ▼
┌─────────────────────────────────────────────────────────────┐
│  Fallback Retrieval                                         │
│                                                             │
│  search_index.json          keyword + summary search        │
│  vector_index.json          flat Gemini vector index        │
│  build_vector_index.py      rebuilds flat index on demand   │
└─────────────────────────────────────────────────────────────┘
        │
        ▼
  ┌─────────────────────────────────────────────────────────┐
  │  Wiki Layer (wiki/topics/*.md)                          │
  │                                                         │
  │  sync_cards_to_wiki.py     external bookmark knowledge  │
  │  distill_memory_to_wiki.py conversation memory insights │
  │                            (daily cron, auto-staged)    │
  └─────────────────────────────────────────────────────────┘
        │
        ▼
xkb_ask.py / Active Recall Layer
Two-layer recall: wiki topics (synthesized) → cards (XBrain hybrid search)
        │
        ▼
demo/xkb-demo-ui/  ← Interactive graph explorer (Next.js)
Knowledge Graph | Chat | Evidence Panel
```

### Every Card Uses the Same 9-Section Structure

| # | Section | Purpose |
|---|---------|---------|
| 1 | **Core Question & Conclusion** | What question does this answer? What is the conclusion? |
| 2 | **Claim Level** | Attested / Scholarship / Inference — how reliable? |
| 3 | **Key Arguments** | 3–5 key arguments extracted from the source |
| 4 | **False Friends** | Terms with specific technical meaning in this context |
| 5 | **Surprises** | What might surprise a knowledgeable reader? |
| 6 | **Relation to Existing Knowledge** | How does this relate to existing cards? |
| 7 | **Bilingual Summary** | ZH + EN (used for search index) |
| 8 | **Value to User** | Actionable directions, relevant projects |
| 9 | **Source** | Source URL and related links |

One format, every source. A YouTube video, a GitHub repo, and a PubMed paper all produce the same card structure.

---

## LLM Configuration

XKB uses a **single unified LLM config**. All scripts share the same model — no scattered environment variables.

### `config/llm.json` — change this one file to switch all scripts

```json
{
  "model": "openai-codex/gpt-5.4"
}
```

Available model formats (anything supported by `openclaw capability model run`):

| Value | Provider |
|-------|----------|
| `openai-codex/gpt-5.4` | ChatGPT via OpenClaw OAuth |
| `openai-codex/gpt-5.4-mini` | ChatGPT Mini via OpenClaw OAuth |
| `MiniMax-M2.7` | MiniMax via API key |
| `MiniMax-M2.5` | MiniMax M2.5 via API key |

> **How it works:** All scripts call `scripts/_llm.py`, which invokes `openclaw capability model run`. OpenClaw handles all auth (OAuth token refresh, API keys) automatically. Scripts no longer need to manage API keys.

> **Embedding is separate.** Semantic vector search uses Gemini (`GEMINI_API_KEY`) and is not affected by `config/llm.json`.

### Standalone / non-OpenClaw setup

If you are not using OpenClaw, override the model via environment variables:

```bash
export LLM_MODEL="MiniMax-M2.5"
export LLM_API_URL="https://api.minimax.io/anthropic"
export LLM_API_KEY="your-minimax-key"
```

> `LLM_MODEL` env var takes priority over `config/llm.json`.

---

## Wiki Layer

The wiki is the **distilled output layer** — a readable, long-term knowledge base built from two sources:

| Source | Script | What it adds |
|--------|--------|--------------|
| External bookmarks | `sync_cards_to_wiki.py` | Synthesized insights from cards via absorb gate |
| Conversation memory | `distill_memory_to_wiki.py` | Decisions, workflows, principles from daily memory logs |

### Single Canonical Source

The wiki lives at `wiki/` inside the skill directory. The workspace symlinks to it:

```
~/.openclaw/workspace/wiki/  →  skills/x-knowledge-base/wiki/  (symlink)
```

This prevents dual-wiki drift: every tool reads from one place.

### Memory → Wiki Distillation

`distill_memory_to_wiki.py` reads recent `memory/YYYY-MM-DD.md` logs, uses LLM to extract insights worth long-term preservation, and either stages them for review or applies them to wiki topic pages.

```bash
# Preview what would be extracted from the last 3 days
python3 scripts/distill_memory_to_wiki.py --dry-run --days 3

# Stage candidates for review
python3 scripts/distill_memory_to_wiki.py --stage --days 2

# Apply all staged candidates (auto-approve)
python3 scripts/distill_memory_to_wiki.py --apply \
  --staging-file wiki/_staging/YYYY-MM-DD-candidates.md \
  --approve-all
```

Cron jobs run this automatically at 15:30 and 21:30 UTC+8 daily.

### Health Check

```bash
python3 scripts/health_check_pipeline.py
```

Checks three things:
1. `workspace/wiki` is a symlink to the canonical wiki (not a duplicate)
2. Recall reads from the correct wiki path
3. `search_index.json` summary coverage ≥ 70%, age < 26h; vector index freshness

---

## Active Recall Layer

When a user sends a message, XKB uses **two-layer recall**:

1. **Layer 1 — Wiki topics** (`wiki/topics/*.md`): synthesized, durable knowledge. Answers conceptual questions.
2. **Layer 2 — Cards** (XBrain hybrid search, falls back to `search_index.json`): raw evidence. Provides specific citations and sources.

```bash
# Ask a question over your knowledge base
python3 scripts/xkb_ask.py "What are alternatives to RAG?"
python3 scripts/xkb_ask.py "What is the absorb gate?" --format chat
python3 scripts/xkb_ask.py "agent memory design" --json
```

### As an MCP Tool (Claude Code / any MCP client)

Add to `.claude/settings.json`:

```json
{
  "mcpServers": {
    "xkb-recall": {
      "command": "python3",
      "args": ["/path/to/x-knowledge-base/scripts/xkb_recall_server.py"],
      "env": { "OPENCLAW_WORKSPACE": "/path/to/workspace" }
    }
  }
}
```

---

## Quick Start

### With OpenClaw

```bash
# 1. Clone into your OpenClaw skills directory
git clone https://github.com/Hidicence/x-knowledge-base \
  ~/.openclaw/workspace/skills/x-knowledge-base

# 2. Install XBrain (hybrid search engine) — one-time setup
bash ~/.openclaw/workspace/skills/x-knowledge-base/scripts/setup_xbrain.sh

# 3. Add API keys to ~/.openclaw/openclaw.json
#    { "env": { "GEMINI_API_KEY": "...", "LLM_API_KEY": "..." } }

# 4. Run the demo
bash scripts/xkb_demo.sh
```

### Standalone

```bash
export LLM_API_KEY="your-minimax-or-openai-key"
export LLM_API_URL="https://api.minimax.io/anthropic/v1"
export LLM_MODEL="MiniMax-M2.7"
export OPENCLAW_WORKSPACE="~/.openclaw/workspace"
export GEMINI_API_KEY="your-gemini-key"

# Install XBrain (one-time)
bash scripts/setup_xbrain.sh

bash scripts/xkb_demo.sh
```

### XBrain Setup (manual)

If you prefer not to use the setup script:

```bash
# 1. Install Bun  https://bun.sh
curl -fsSL https://bun.sh/install | bash

# 2. Clone GBrain runtime
git clone https://github.com/garrytan/gbrain ~/gbrain
cd ~/gbrain && bun install && bun run src/cli.ts init

# 3. Tell XKB where to find it
# Add to ~/.openclaw/openclaw.json → "env":
#   "gbrain_dir": "/absolute/path/to/gbrain"
#   "GEMINI_API_KEY": "your-key"   ← required for embeddings

# 4. Verify
python3 scripts/xbrain_recall.py "test query"
```

---

## Scripts Reference

### Ingest Pipeline

All scripts share `_card_prompt.py` and `_llm.py` — one prompt, one LLM call, one card format.

| Script | Source | What it does |
|--------|--------|-------------|
| `run_scan_worker.py` | X/Twitter | Scans bookmarks for unenriched files → cards |
| `run_bookmark_worker.py` | X/Twitter queue | Processes tiege-queue.json one item at a time |
| `fetch_youtube_playlist.py` | YouTube | Playlist subtitles → knowledge cards |
| `fetch_github_repos.py` | GitHub | Forks/stars → repo-level knowledge cards |
| `local_ingest.py` | Local / PubMed | Markdown/txt/papers → cards |
| `fetch_pubmed.py` | PubMed Central | Fetch open-access papers as markdown |
| `_card_prompt.py` | *(shared)* | Unified prompt, card format, summary extraction |
| `_llm.py` | *(shared)* | Unified LLM call via `openclaw capability model run` |

### Index & Enrichment

| Script | What it does |
|--------|-------------|
| `sync_enriched_index.py` | Backfill summaries/tags from enriched cards into search_index.json |
| `build_vector_index.py` | Build/update flat JSON vector index (fallback when XBrain unavailable) |
| `xbrain_recall.py` | XBrain search bridge — hybrid RRF (pgvector + keyword); auto-used by all recall scripts |

### Wiki Pipeline

| Script | What it does |
|--------|-------------|
| `sync_cards_to_wiki.py` | Cards → wiki topic pages via LLM absorb gate |
| `distill_memory_to_wiki.py` | Daily memory logs → wiki topic insights (stage/apply workflow) |
| `sync_cards_to_wiki.py --review` | Review pending absorb decisions |
| `lint_wiki.py` | Validate wiki structure, detect gap topics |
| `topic_guide_generator.py` | Generate new wiki topic stubs |
| `suggest_topic_map.py` | Suggest topic map updates from uncovered cards |

### Active Recall Layer

| Script | What it does |
|--------|-------------|
| `xkb_ask.py` | Natural-language Q&A: wiki (Layer 1) → cards via XBrain hybrid search (Layer 2) |
| `recall_for_conversation.py` | Conversation-triggered recall (wiki + XBrain card search) |
| `continuity_recall.py` | MEMORY.md + wiki lookup for session continuity |
| `contrarian_recall.py` | Surfaces warnings, failures, counter-examples |
| `action_recall.py` | Action-oriented recall (what to do next) |
| `xkb_recall_server.py` | MCP server exposing recall as a tool |

### Maintenance & Observability

| Script | What it does |
|--------|-------------|
| `health_check_pipeline.py` | Wiki symlink integrity, recall source path, index freshness |
| `status_knowledge_pipeline.py` | Full pipeline status in one view |
| `health_check.py` | Semantic conflict detection, gap analysis |

---

## Demo UI — Interactive Knowledge Graph

```
demo/
├── xkb-demo-ui/              Next.js app — three-column explorer
│   ├── app/page.tsx          Main layout: graph | chat | evidence
│   ├── components/
│   │   ├── KnowledgeGraph.tsx    Force-directed graph (react-force-graph-2d)
│   │   ├── ChatPanel.tsx         Natural-language Q&A via xkb_ask.py
│   │   └── EvidencePanel.tsx     Source cards + wiki references
│   └── public/
│       ├── graph-data.json       ← your personal data (gitignored)
│       └── graph-data.sample.json  schema reference
└── generate_graph.py         Builds graph-data.json from search_index.json
```

**Run the demo:**
```bash
python3 demo/generate_graph.py
cd demo/xkb-demo-ui && npm install && npm run dev
# → http://localhost:3000
```

> `graph-data.json` is gitignored. Your personal knowledge never leaves your machine.

---

## Step-by-Step Setup

### 1. Ingest content

```bash
# Local notes
python3 scripts/local_ingest.py notes/ --category learning

# X/Twitter bookmarks
python3 scripts/run_scan_worker.py --limit 20

# YouTube playlists
python3 scripts/fetch_youtube_playlist.py --playlist "URL"

# GitHub repos
python3 scripts/fetch_github_repos.py --forks --stars

# PubMed papers
python3 scripts/fetch_pubmed.py "antimicrobial resistance" --limit 20 --out /tmp/papers
python3 scripts/local_ingest.py /tmp/papers/ --category research --tag pubmed
```

### 2. Enrich the index

```bash
# Backfill summaries from enriched cards into search_index.json (always run this)
python3 scripts/sync_enriched_index.py

# Only needed if XBrain is not configured (fallback mode)
python3 scripts/build_vector_index.py --incremental
```

> **XBrain (primary):** every ingest script auto-pushes cards to XBrain on write.
> `xbrain_recall.py` is used automatically by all recall scripts — no extra steps.
> Set `gbrain_dir` in `~/.openclaw/openclaw.json` to point at your GBrain runtime directory.
>
> **Fallback:** if XBrain is unavailable, recall falls back to `search_index.json` keyword search automatically.

### 3. Sync to wiki

```bash
# Sync external knowledge (bookmark cards → wiki topics)
python3 scripts/sync_cards_to_wiki.py --apply --limit 20

# Distill conversation memory into wiki topics
python3 scripts/distill_memory_to_wiki.py --stage --days 3
python3 scripts/distill_memory_to_wiki.py --apply \
  --staging-file wiki/_staging/YYYY-MM-DD-candidates.md --approve-all
```

### 4. Ask

```bash
python3 scripts/xkb_ask.py "What are the alternatives to RAG?"
```

### 5. Check pipeline health

```bash
python3 scripts/health_check_pipeline.py
```

Expected output:
```
✅  wiki_canonical      workspace/wiki → skills/x-knowledge-base/wiki (symlink correct)
✅  recall_wiki_source  Recall reads from canonical wiki
✅  index_freshness     summary coverage: 212/270 (79%) | enriched: 218 | vectors: 471
```

---

## Automated Pipeline (OpenClaw Cron)

When running with OpenClaw, the full pipeline runs automatically:

| Schedule | Job | What it does |
|----------|-----|-------------|
| 13:30 UTC+8 | `daily:xkb-ingestion-batch` | Ingest new X/Twitter bookmarks → cards → auto-push to XBrain → sync_enriched_index |
| 15:30 UTC+8 | `daily:wiki-distill-afternoon` | Distill today's memory into wiki candidates |
| 21:30 UTC+8 | `daily:wiki-distill-evening` | Second distillation pass, apply high-confidence candidates |

The pipeline ensures that after each ingestion run:
1. Each card is auto-pushed to XBrain on write — hybrid RRF search immediately available
2. `sync_enriched_index.py` backfills summaries into the fallback search index
3. New insights from conversations are automatically staged for wiki inclusion

---

## Requirements

- Python 3.10+
- Node.js 18+ (demo UI only)
- OpenClaw (recommended) — handles all LLM auth and cron automation
- `GEMINI_API_KEY` — required for XBrain semantic embeddings; set in `~/.openclaw/openclaw.json`
- [Bun](https://bun.sh) + [GBrain](https://github.com/garrytan/gbrain) runtime (optional) — powers XBrain hybrid search (pgvector/PGLite + RRF); set `gbrain_dir` in `openclaw.json` to activate. Falls back to keyword search if not configured.

---

## Roadmap

| Version | Status | What it delivered |
|---------|--------|-------------------|
| v0.1 | ✅ | Bookmark ingestion, knowledge cards, keyword search |
| v0.2 | ✅ | Multi-layer extraction, enrichment worker, vector index |
| v0.3 | ✅ | Wiki pipeline: absorb gate, topic pages, memory distillation |
| v0.4 | ✅ | Local notes ingest, ask layer, demo mode, auto topic-map |
| v0.5 | ✅ | Absorb gate explainability, review-decisions log |
| v0.6 | ✅ | Active Recall Layer: proactive recall, MCP server, telemetry |
| v0.7 | ✅ | Claim levels, False Friends, bilingual summaries, academic PDF pipeline |
| v0.8 | ✅ | Unified ingest pipeline (_card_prompt.py); demo UI (graph + chat) |
| v0.9 | ✅ | Two-layer recall (wiki first); unified LLM config; memory→wiki distillation pipeline; single canonical wiki; pipeline health check |
| v1.0 | ✅ | XBrain hybrid search (pgvector + RRF) fully integrated across all ingest scripts; unified path resolution; graceful fallback to keyword search |
| v1.1 | 🔜 | **Active Recall quality upgrade** — soft-trigger re-ranking; Claim level surfaced in recall output; trigger strategy expansion beyond rule-based regex |
| v1.2 | 🔜 | **Agent-to-Agent knowledge exchange** — standardized card format (9-section + Claim level) as exchange unit over A2A protocol; `receive_card` MCP tool; XBrain as local digestion layer for received cards |

---

## Design Principles

- **One card format, many sources.** Every source produces the same 9-section card.
- **Layers, not one database.** Working memory, consolidation, capture, and output are separate problems.
- **Quality gates over quantity.** The absorb gate keeps the wiki as a distilled output layer.
- **Understanding over summarization.** Cards answer what question this solves, not what it says.
- **Single source of truth.** One canonical wiki path, one LLM config file — no scattered settings.
- **OpenClaw handles auth.** Scripts call `openclaw capability model run`; token management is not their problem.
- **Graceful degradation.** XBrain hybrid search is the primary retrieval path; keyword fallback activates automatically when XBrain is unavailable. Nothing breaks.
- **Personal data stays local.** Graph data, cards, and wiki are gitignored.

---

## Contributing

Start with [`SKILL.md`](SKILL.md) and [`docs/xkb-wiki-architecture.md`](docs/xkb-wiki-architecture.md).

PRs and issues welcome. Your knowledge deserves to be remembered.
