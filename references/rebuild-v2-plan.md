# X Knowledge Base Rebuild v2 Plan

## Goal

Rebuild the bookmark knowledge base from scratch into a clean v2 dataset, then cut over only after validation passes.

## Why v2 Instead of Patching v1

The current dataset mixes multiple generations of artifacts:
- raw tweet/bookmark markdown
- slug-based legacy files
- Jina raw dumps
- AI summaries from different eras
- enriched cards
- digest/report files that are not single-bookmark records

Because the data model is inconsistent, continuing to patch v1 is higher risk than rebuilding into one clean schema.

## Core Rule

Do not overwrite the current production dataset during rebuild.

Use parallel v2 paths first:
- `memory/bookmarks_v2/`
- `memory/cards_v2/`
- `memory/x-knowledge-base-v2/`

Only replace the production paths after validation and smoke tests pass.

## Target v2 Data Model

### Raw bookmark record

One source bookmark = one canonical raw record.

Required fields:
- `tweet_id` or other stable platform ID
- `source_url`
- `source_type`
- `fetched_at`
- `content acquisition method`

Preferred properties:
- no date-slug pretending to be tweet id
- no digest/report file in the same dataset
- no duplicate entries for the same `source_url`

### Enriched card

One canonical raw record may produce one enriched card.

Required fields:
- stable `id`
- `type: x-knowledge-card`
- `source_type`
- `source_url`
- title
- summary
- category
- tags

### Search index

Derived only from v2 canonical records/cards.

Must support:
- `excluded`
- `exclude_reasons`
- `duplicate_of`
- stable `path`
- truly relative `relative_path`

## Recommended Rebuild Phases

### Phase R1 — Scaffold v2

Create:
- `memory/bookmarks_v2/`
- `memory/cards_v2/`
- `memory/x-knowledge-base-v2/`

Create separate v2 runtime files:
- `search_index_v2.json`
- `vector_index_v2.json`
- `topic_profile_v2.json`
- `tiege-queue-v2.json`

### Phase R2 — Refetch bookmarks

Fetch bookmarks again from the source of truth.

Requirements:
- prioritize stable tweet ids and source URLs
- fail closed on malformed ids
- do not invent `https://x.com/i/status/2026`
- store ambiguous imports separately instead of mixing them into canonical raw records

### Phase R3 — Enrich into cards

Run enrichment only on v2 raw records.

Rules:
- one canonical input → one canonical output
- malformed source metadata should skip or go to quarantine, not enter the main dataset

### Phase R4 — Build v2 index and vectors

Run:
1. sync enriched index
2. normalize low-quality entries
3. canonicalize duplicates
4. cleanup titles conservatively
5. build vector index

### Phase R5 — Validate

Minimum checks before cutover:
- invalid source URL rate
- duplicate source URL rate
- low-signal summary rate
- recall quality on representative queries
- sample coverage across major categories

### Phase R6 — Cutover

1. backup current production paths
2. atomically rename/swap v2 into production paths
3. rebuild production index/vector once more
4. run recall smoke tests
5. keep rollback backup until stable

## Quarantine Rules

Do not let these enter canonical v2 silently:
- digest/report markdown
- malformed tweet ids
- missing source URLs
- Jina raw dumps without stable source metadata
- duplicate entries with no clear canonical candidate

Put them into a quarantine path for later review.

Suggested path:
- `memory/x-knowledge-base-v2/quarantine/`

## Suggested Validation Queries

Use at least these smoke tests after v2 build:
- `OpenClaw workflow memory recall`
- `AI SEO case study content system`
- `Seedance prompt workflow`
- `agent tool research`

## Migration Principle

Prefer safe parallel rebuild over clever in-place repair.

Do not optimize for preserving every historical filename.
Optimize for a clean, canonical, maintainable dataset.
