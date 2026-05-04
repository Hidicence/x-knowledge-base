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
