## Personal data locations in current XKB setup

If you want the clean mental model, use this split:

### 1) Raw inputs and personal source data

- `memory/bookmarks/`
  - raw bookmark markdown
  - `search_index.json`
  - `vector_index.json`
  - backup snapshots of the index
- `memory/cards/`
  - generated knowledge cards

### 2) Wiki runtime data

- `memory/x-knowledge-base/wiki/index.md`
- `memory/x-knowledge-base/wiki/log.md`
- `memory/x-knowledge-base/wiki/review-decisions.json`
- `memory/x-knowledge-base/wiki/topic-map.json`
- `memory/x-knowledge-base/wiki/topics/`
- `memory/x-knowledge-base/wiki/_staging/`

### 3) Compatibility path

- `wiki/` at workspace root
  - symlink to `memory/x-knowledge-base/wiki`
  - keep for old scripts only

### 4) Skill repo code only

- `skills/x-knowledge-base/`
  - code, docs, templates, sample files
  - should not contain live wiki topics or generated personal runtime data

### 5) Release bundle

- `skills/x-knowledge-base/dist/`
  - packaged allowlist release
  - no personal data, no wiki topics, no staging, no build caches

## Recovering an ingest after an index failure

Local, GitHub and YouTube ingestion save cards before rebuilding the search
index. If rebuilding fails, they exit with status `1` and keep the saved files.
With the same data configuration (`XKB_CONFIG`, `XKB_DATA_DIR`, or explicit path
overrides), run this from the repository root:

```bash
python3 scripts/xkb_index.py --rebuild
```

This rebuilds the search index from existing files without generating cards or
calling an LLM. Repair the index before rerunning ingestion, since ingestion
uses that index to detect sources it has already processed. Vector indexing
remains a separate step. Local and GitHub ingestion retain status `2` for
successful new cards; YouTube retains status `0` on success.

`search_bookmarks.sh` uses the same resolved paths and rebuild function. If its
automatic rebuild fails, it warns before using the old index or the bookmark
full-text fallback; those results may be incomplete.
