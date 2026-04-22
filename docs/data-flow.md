# XKB Data Flow & Privacy Reference

> **Purpose:** Answer the question "does this data leave my machine?" for every ingestion path.
> If you're evaluating XKB for sensitive use cases, read this first.

---

## Quick Answer Table

| Data | Sent to third party? | Where |
|------|---------------------|-------|
| X/Twitter bookmark URLs | Yes | r.jina.ai (content fetch), xreach/bird (bookmark API) |
| X/Twitter bookmark full text | Yes | LLM API (enrichment/summarization) |
| External article body (from bookmark links) | Yes | r.jina.ai (fetch), then LLM API |
| GitHub repo metadata | Yes | api.github.com (via gh CLI) |
| GitHub repo README/content | Yes | LLM API (card generation) |
| YouTube subtitles | Yes | LLM API (card generation) |
| PDF content | Yes | LLM API (card generation) |
| PubMed full-text papers | Yes | NCBI eutils API (fetch), then LLM API |
| Local markdown / notes | Yes (if using cloud LLM) | LLM API (card generation) |
| Search index (titles, summaries, tags) | Yes (if vector index enabled) | Embedding API (Gemini / OpenAI) |
| Wiki pages | Yes | LLM API (absorb gate, distillation) |
| Conversation memory logs | Yes (if distill is run) | LLM API |
| Generated knowledge cards (local .md files) | **No** | Stays local |
| search_index.json | **No** | Stays local |
| vector_index.json | **No** | Stays local |
| credentials / API keys | **No** | Stays local (if you follow the setup guide) |

---

## Detailed Data Flow by Script

### X/Twitter Bookmarks (`fetch_and_summarize.sh`)

```
Your X account
  [auth] BIRD_AUTH_TOKEN / BIRD_CT0   <- high-sensitivity session cookies (see warning)
  [step 1] xreach or bird CLI         -> fetches bookmark URLs + tweet text from X servers
  [step 2] r.jina.ai/URL              -> extracts article body for linked external pages
  [step 3] LLM API (LLM_API_KEY)     -> generates structured knowledge card (up to 4000 chars sent)
  [local]  memory/cards/*.md          <- card saved locally
  [local]  memory/bookmarks/search_index.json  <- index updated locally
```

**What Jina receives:** The URL of any external article linked in your bookmarks.
**What LLM receives:** Full text of the bookmark + linked content, truncated to ~4000 chars.

### PDF / Local Files (`pdf_ingest.py`, `local_ingest.py`)

```
Local file
  [local]  text extracted locally (pdfminer / direct read)
  [step 1] LLM API (LLM_API_KEY)     -> generates structured knowledge card (up to 4000 chars sent)
  [local]  memory/cards/*.md          <- card saved locally
```

**What LLM receives:** Up to 4000 characters of your document content.

### PubMed Papers (`fetch_pubmed.py`)

```
Search query
  [step 1] NCBI eutils API (public, no auth)  -> fetches PMC open-access full-text
  [step 2] LLM API (LLM_API_KEY)              -> generates knowledge card
  [local]  memory/cards/*.md                   <- card saved locally
```

### Vector Index (`build_vector_index.py`)

```
search_index.json (title + summary fields only)
  [step 1] Embedding API (Gemini / OpenAI / Ollama)  -> converts text to vectors
  [local]  memory/bookmarks/vector_index.json          <- stored locally
```

**What Embedding API receives:** Title and summary of each card (~100-300 chars each).
If you use Ollama, this step is entirely local — no data leaves your machine.

### Wiki Absorb Gate (`sync_cards_to_wiki.py`)

```
Card content + existing wiki page content
  [step 1] LLM API (LLM_API_KEY)   -> LLM decides to absorb/reject + rewrites wiki page
  [local]  wiki/topics/*.md          <- updated wiki page saved locally
```

---

## High-Sensitivity Credentials

### BIRD_AUTH_TOKEN / BIRD_CT0

> **Warning: These are NOT regular API keys. They are session cookies from your logged-in X/Twitter account.**

| Property | Value |
|----------|-------|
| Risk level | **High** — equivalent to your X account login session |
| What they allow | Read your private bookmarks, DMs, browse X as you |
| How XKB uses them | Only for fetching your own bookmarks via bird/xreach CLI |
| Where to store | Local env var or `.secrets/x-knowledge-base.env` (gitignored) |
| Expiry | Weeks to months; invalidated when you log out of X |

**Rules:**
- Never paste these tokens in chat messages, GitHub issues, or wiki pages
- Never commit them to any git repository
- If exposed: **log out of X immediately** to invalidate the session, then re-export fresh cookies
- Store in `.secrets/x-knowledge-base.env` or system env vars only

### LLM_API_KEY / GEMINI_API_KEY / OPENAI_API_KEY

Standard API keys — lower risk than session cookies.
- Exposure leads to billing charges, not account takeover
- Rotate immediately if exposed
- Store in `.secrets/x-knowledge-base.env` or `~/.openclaw/openclaw.json`

---

## Local-Only Mode

If you want to avoid sending content to cloud APIs:

1. **Skip enrichment workers** — don't run `run_bookmark_worker.py` or `run_scan_worker.py`
2. **Use Ollama for embeddings** — set `EMBEDDING_PROVIDER=ollama` (fully local)
3. **Skip wiki sync** — don't run `sync_cards_to_wiki.py`
4. **Use local fallback summaries** — `tools/bookmark_enhancer.py` has `generate_local_summary()` when no API key is set

The raw bookmark files and `search_index.json` are always local-only. Only enrichment and wiki steps require cloud APIs.

---

## Third-Party Services Summary

| Service | Purpose | Auth required | Data sent |
|---------|---------|--------------|-----------|
| X/Twitter (bird/xreach) | Bookmark fetch | BIRD_AUTH_TOKEN, BIRD_CT0 | None — read-only pull from your account |
| r.jina.ai | Article extraction | None (public) | External article URLs from your bookmarks |
| LLM provider (configurable) | Card generation, wiki sync | LLM_API_KEY | Bookmark/document content (up to 4000 chars) |
| Gemini API | Vector embeddings | GEMINI_API_KEY | Card titles + summaries (~100-300 chars each) |
| OpenAI (optional) | Vector embeddings | OPENAI_API_KEY | Card titles + summaries |
| Ollama (optional) | Vector embeddings | None | Nothing — fully local |
| NCBI eutils | PubMed paper fetch | None (public) | Search queries |
| GitHub API (gh CLI) | Repo metadata | GH_TOKEN or gh auth | Repo names (read-only) |
| YouTube / yt-dlp | Subtitle fetch | YouTube cookies | Video IDs |
