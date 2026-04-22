# XKB Security Audit Response

> **Purpose:** Document how XKB responds to security audit findings — what has been fixed,
> what was already handled, and what is out of scope. For future audits, update this file
> rather than responding verbally.

---

## Audit Summary

XKB has undergone internal security review covering:
1. Credential management (X/Twitter session tokens, API keys)
2. Third-party data transmission boundaries
3. Metadata and dependency disclosure
4. Hardcoded filesystem paths

---

## Findings & Resolutions

### 1. High-Sensitivity Credential Documentation

**Finding:** `BIRD_AUTH_TOKEN` / `BIRD_CT0` were listed alongside regular API keys without
distinguishing their elevated risk (they are X/Twitter session cookies, not API keys).

**Status: Fixed (v0.7)**
- `SKILL.md` now explicitly labels these as high-sensitivity session cookies
- `docs/data-flow.md` explains the risk, what they allow, and how to respond to exposure
- Instructions added: store only in `.secrets/x-knowledge-base.env` or system env vars,
  never paste in chat/issues/wikis
- Incident response documented: log out of X immediately to invalidate the session

---

### 2. Third-Party Data Transmission Boundaries

**Finding:** Users could not easily determine which data leaves their machine.

**Status: Fixed (v0.7)**
- Created `docs/data-flow.md` with a Quick Answer Table, per-script flow diagrams,
  and a complete third-party services summary
- Covers: X/Twitter (bird/xreach), r.jina.ai, LLM API, Gemini embeddings,
  NCBI eutils, GitHub API, YouTube/yt-dlp
- Local-only mode documented: skip enrichment workers + use Ollama for embeddings

---

### 3. Metadata / Dependency Disclosure

**Finding:** `SKILL.md` environment requirements section did not distinguish sensitivity
levels of credentials, or list required binaries and external services completely.

**Status: Fixed (v0.7)**
- `SKILL.md` Environment Requirements section restructured into three tables:
  - Required env vars (with sensitivity column)
  - Optional env vars (with defaults)
  - Required binaries
- External services list added: LLM API, r.jina.ai, Gemini, NCBI, GitHub, YouTube

---

### 4. Hardcoded Filesystem Paths

**Finding:** Multiple scripts had hardcoded `/root/.openclaw/...` paths that would fail
on any non-root Linux installation or macOS.

**Status: Fixed (v0.7)**
- 12 Python scripts patched: fallback defaults changed from `/root/.openclaw/...`
  to `Path.home() / ".openclaw" / ...` (portable across users and OS)
- `run_youtube_sync.sh` patched: all hardcoded `/root/` paths replaced with
  `${OPENCLAW_WORKSPACE:-$HOME/.openclaw/workspace}` and `${OPENCLAW_JSON:-...}`
- All paths remain overridable via env vars (`OPENCLAW_WORKSPACE`, `OPENCLAW_JSON`,
  `BOOKMARKS_DIR`, `CARDS_DIR`)

---

### 5. Data Processing Mode Control

**Finding:** Users had no clear way to choose "local-only" processing.

**Status: Fixed (v0.7)**
- `run_bookmark_worker.py` and `run_scan_worker.py` now accept `--local-only` flag
  (skips all LLM API calls, no content sent externally)
- `--dry-run` mode documented as equivalent for inspection without any writes
- `docs/data-flow.md` includes a Local-Only Mode section
- Ollama support for embeddings documented (`EMBEDDING_PROVIDER=ollama`)

---

### 6. External Fetch Warning

**Finding:** Users were not notified before bookmark content was sent to external APIs.

**Status: Fixed (v0.7)**
- Workers now print a warning at startup when cloud LLM enrichment is active:
  `⚠️  Bookmark content will be sent to LLM API (...) for enrichment.`
- `--local-only` mode prints confirmation that no content leaves the machine

---

### 7. Sensitivity Level Classification

**Finding:** All cards were treated identically regardless of source sensitivity.

**Status: Partially addressed (v0.7)**
- Knowledge card schema now includes `sensitivity` field (default: `public`)
- X/Twitter bookmarks default to `public` (they are public posts)
- Future work: auto-set `sensitivity: internal` for local files, `sensitive` for
  private PDFs; route `private/sensitive` cards to local-only processing

---

## Out of Scope / Known Limitations

- **X/Twitter fetch itself** — XKB cannot control X's server-side behavior; we only
  use the auth tokens locally and do not transmit them to any third party
- **r.jina.ai content caching** — Jina is a public service; we send URLs, not auth tokens;
  users can skip Jina extraction by not running the enrichment pipeline
- **LLM provider privacy policies** — Users should review their chosen LLM provider's
  data retention policies; XKB is provider-agnostic by design
- **git history** — If secrets were ever committed to the repo, they must be rotated
  even after removal from the working tree

---

## XKB Data Governance Principles

1. **Local by default for storage** — all generated files (cards, index, wiki) stay local
2. **Explicit for cloud processing** — enrichment workers require explicit API key setup
   and print a warning before sending data externally
3. **Configurable endpoints** — no vendor lock-in; all API endpoints are overridable via env vars
4. **Minimal data sent** — content is truncated to ~4000 chars before LLM calls
5. **No PII aggregation** — XKB does not collect, transmit, or store user analytics

---

*Last updated: 2026-04-10 | Covers audit findings addressed in v0.7*
