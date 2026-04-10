# X Knowledge Base
> **讓知識重新浮現 | Make Knowledge Reappear**
>
> A personal knowledge lifecycle system for AI agents -- from raw bookmarks to structured, reusable wiki.
> Works with any AI agent (Claude Code, OpenClaw, or any agent that can read a system prompt).

[![Watch the Pitch Video](https://img.youtube.com/vi/JWgm6ky_pys/maxresdefault.jpg)](https://youtu.be/JWgm6ky_pys)
*(Click to watch the concept presentation)*

---

## The Problem

Every day we consume dozens of articles, threads, and insights. We bookmark them because they feel important. Six months later -- we cannot find them, cannot recall them, and have no idea what we actually learned.

Existing tools all assume you will manually retrieve knowledge when you need it. **But knowledge should know when you need it.**

XKB is built on a different premise: knowledge has a lifecycle. The goal is not to archive more -- it is to make what you have already consumed *reappear at the right moment* and *gradually sediment into durable understanding*.

---

## How It Works

Every knowledge card -- regardless of source -- uses the same 9-section structure:

1. **核心問題與結論** -- What question does this answer?
2. **Claim 等級** -- Attested / Scholarship / Inference
3. **關鍵論點** -- Key arguments
4. **False Friends** -- Terms with specific technical meaning here
5. **驚訝點** -- What might surprise the reader?
6. **與現有知識的關係** -- How does this relate to existing cards?
7. **雙語摘要** -- ZH + EN bilingual summary (used for search index)
8. **對使用者的價值** -- Actionable directions
9. **原始來源** -- Source URL and related links

The pipeline:

```
Input sources
├── X/Twitter bookmarks        →  run_scan_worker.py / bookmark_enhancer.py
├── YouTube playlists          →  fetch_youtube_playlist.py
├── GitHub forks/stars         →  fetch_github_repos.py
├── Local notes / markdown     →  local_ingest.py
└── PubMed / academic papers   →  fetch_pubmed.py + local_ingest.py
        │
        ▼
  scripts/_card_prompt.py   ← shared by ALL ingest scripts
  (unified 9-section card format, same prompt regardless of source)
        │
        ▼
knowledge cards + search index + vector index
        │
        ▼
sync_cards_to_wiki.py + Absorb Gate
        │
        ▼
wiki/topics/*.md  ←  durable, readable knowledge pages
        │
     ┌──┴───────────────────────────────────────┐
     │                                           │
     ▼                                           ▼
xkb_ask.py                         Active Recall Layer
natural-language Q&A               knowledge surfaces during conversations
     │
     ▼
demo/xkb-demo-ui/  ← Interactive graph explorer (Next.js)
Knowledge Graph | Chat | Evidence Panel
```

---

## Demo UI -- Interactive Knowledge Graph

```
demo/
├── xkb-demo-ui/              Next.js app -- three-column explorer
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

## Quick Start

```bash
export LLM_API_KEY="your-key"
export LLM_API_URL="https://api.minimax.io/anthropic"
export LLM_MODEL="MiniMax-M2.5"
bash scripts/xkb_demo.sh
```

---

## Scripts Reference

### Unified Ingest Pipeline

All scripts share `_card_prompt.py` -- one prompt, one LLM call, one card format.

| Script | Source | What it does |
|--------|--------|-------------|
| `run_scan_worker.py` | X/Twitter | Scans bookmarks for unenriched files → cards |
| `bookmark_enhancer.py` | X/Twitter inbox | Processes inbox bookmarks |
| `fetch_youtube_playlist.py` | YouTube | Playlist subtitles → knowledge cards |
| `fetch_github_repos.py` | GitHub | Forks/stars → repo-level knowledge cards |
| `local_ingest.py` | Local / PubMed | Markdown/txt/papers → cards |
| `fetch_pubmed.py` | PubMed Central | Fetch open-access papers as markdown |
| `_card_prompt.py` | *(shared)* | Unified prompt, LLM call, summary extraction |

### Active Recall Layer

| Script | What it does |
|--------|-------------|
| `recall_router.py` | Message → classify → route → structured output |
| `conversation_state_parser.py` | Trigger classifier (no LLM, <5ms) |
| `continuity_recall.py` | MEMORY.md + wiki lookup |
| `contrarian_recall.py` | Surfaces warnings, failures, counter-examples |
| `xkb_recall_server.py` | MCP server for AI agents |

### Wiki and Knowledge Tools

| Script | What it does |
|--------|-------------|
| `xkb_ask.py` | Natural-language Q&A over your knowledge base |
| `sync_cards_to_wiki.py` | Cards → wiki topic pages (absorb gate) |
| `build_vector_index.py` | Build/update semantic vector index |
| `health_check.py` | Semantic conflict detection, gap analysis |
| `status_knowledge_pipeline.py` | Full pipeline status in one view |

---

## Setup Guide

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

### 2. Build and explore

```bash
python3 scripts/build_vector_index.py --incremental
python3 demo/generate_graph.py
cd demo/xkb-demo-ui && npm run dev
```

### 3. Sync to wiki

```bash
python3 scripts/sync_cards_to_wiki.py --apply --limit 20
```

### 4. Ask

```bash
python3 scripts/xkb_ask.py "What are the alternatives to RAG?"
```

---

## Active Recall as MCP Tool

**Claude Code** -- add to `.claude/settings.json`:
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

## Requirements

- Python 3.10+
- Node.js 18+ (demo UI only)

```bash
export LLM_API_KEY="your-api-key"
export LLM_API_URL="https://api.minimax.io/anthropic"
export LLM_MODEL="MiniMax-M2.5"
export OPENCLAW_WORKSPACE="~/.openclaw/workspace"
# optional: GEMINI_API_KEY for semantic vector search
```

**Local-only mode** (no API calls):
```bash
python3 scripts/run_scan_worker.py --local-only
```

---

## Roadmap

| Version | Status | What it delivered |
|---------|--------|-------------------|
| v0.1 | ✅ | Bookmark ingestion, knowledge cards, keyword search |
| v0.2 | ✅ | Multi-layer extraction, enrichment worker, vector index |
| v0.3 | ✅ | Wiki pipeline: absorb gate, topic pages, memory distillation |
| v0.4 | ✅ | Local notes ingest, ask layer, demo mode, auto topic-map |
| v0.5 | ✅ | Absorb gate explainability |
| v0.6 | ✅ | Active Recall Layer: proactive recall, MCP server, telemetry |
| v0.7 | ✅ | Claim levels, False Friends, bilingual summaries, academic PDF pipeline |
| v0.8 | ✅ | Unified ingest pipeline (_card_prompt.py shared); demo UI (graph + chat) |
| v0.9 | 🔜 | Proactive cross-linking on ingest, onboarding wizard |

---

## Design Principles

- **One card format, many sources.** Every source produces the same 9-section card.
- **Layers, not one database.** Working memory, consolidation, capture, and output are separate problems.
- **Quality gates over quantity.** The absorb gate keeps the wiki as a distilled output layer.
- **Understanding over summarization.** Cards answer what question this solves, not what it says.
- **Personal data stays local.** Graph data, cards, and wiki are gitignored.

---

## Contributing

Start with [`SKILL.md`](SKILL.md) and [`docs/xkb-wiki-architecture.md`](docs/xkb-wiki-architecture.md).

PRs and issues welcome. Your knowledge deserves to be remembered.
