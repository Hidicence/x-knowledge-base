# XKB vNext Roadmap Draft

## Why this exists
XKB is no longer just a bookmark-to-card pipeline. After the GBrain upgrade and Postgres migration, the stack now has a real durable retrieval substrate:
- Postgres-backed GBrain
- pgvector hybrid search
- Minions job runtime
- wiki synthesis layer
- active recall integration

That means the next phase should not focus on collecting more cards alone. It should focus on turning XKB into a governed knowledge system.

## Current Position
XKB already has strong infrastructure:
- raw multi-source ingestion (X, YouTube, GitHub, local notes, papers)
- structured card layer
- wiki as distilled product layer
- XBrain / GBrain retrieval layer
- Postgres + pgvector
- Minions-ready background execution

What is still under-specified is the governance layer:
- confidence
- staleness
- supersession
- typed relationships
- contradiction handling
- quality scoring

## Working Model

### Layer 1 — Raw Sources
Purpose: preserve source material and metadata with minimal interpretation.

Examples:
- X bookmarks
- YouTube transcripts
- GitHub repos/issues
- local markdown notes
- PDFs / papers / articles
- conversation logs / raw packages

Requirements:
- source traceability
- immutable or append-only raw capture
- source metadata normalization

### Layer 2 — Structured Knowledge Layer
Purpose: convert raw sources into machine-usable knowledge artifacts.

Examples:
- cards
- embeddings
- keyword + vector indexes
- graph edges / typed relations
- retrieval metadata
- evidence tracking
- confidence and freshness metadata

This is where graph layer belongs.
It is not the wiki itself. It is the substrate below the wiki.

### Layer 3 — Wiki / Product Layer
Purpose: synthesize high-signal, durable, human-readable knowledge.

Examples:
- wiki topics
- topic syntheses
- durable principles
- cross-source conclusions
- reusable guidance pages

This layer is for people first, not retrieval infrastructure first.

### Layer 4 — Recall / Application Layer
Purpose: put the knowledge to work in live systems.

Examples:
- active recall in chat
- xkb_ask
- answer composition
- Minions job pipelines
- downstream research / writing / execution flows

## vNext Priorities

### Priority 1 — Knowledge Lifecycle Governance
This is the highest-value missing layer.

#### 1.1 Confidence score
Start simple at page/card level.
Suggested inputs:
- number of supporting sources
- source quality / source type
- recency of confirmation
- contradiction count
- access / reuse frequency

Possible fields:
- `confidence_score`
- `confidence_reason`
- `evidence_count`
- `last_confirmed_at`

#### 1.2 Supersession
When new knowledge replaces old knowledge, do not only append notes.
Explicitly model:
- `supersedes`
- `superseded_by`
- `status: active|stale|superseded`

This is critical for tool instructions, workflows, architecture notes, and changing facts.

#### 1.3 Staleness / retention
Not everything should live at full weight forever.

Suggested first pass:
- freshness score by recency
- stale flag after threshold
- slower decay for principles / workflows
- faster decay for transient bug notes or volatile tool facts

### Priority 2 — Typed Relationship Schema
Do not start with an overly broad ontology.
Start with a compact, useful edge set.

Suggested first relationship types:
- `mentions`
- `about`
- `related_to`
- `uses`
- `depends_on`
- `derived_from`
- `contradicts`
- `supersedes`
- `same_topic_as`

Entity classes can also start small:
- person
- project
- tool
- concept
- workflow
- source
- card
- wiki_topic

### Priority 3 — Graph-aware Recall
Graph layer should influence recall, not just exist in storage.

Desired outcomes:
- graph neighbors boost ranking
- typed edges influence traversal
- concept lookup can pull adjacent workflows, tools, and prior conclusions
- recall can surface structurally related knowledge even when lexical similarity is weak

### Priority 4 — Quality Governance
Introduce machine-usable quality checks for generated artifacts.

Suggested controls:
- low-quality card detection
- broken source references
- weak summary detection
- duplicate / near-duplicate card detection
- contradiction candidate detection
- orphan knowledge detection

### Priority 5 — Minions-native Knowledge Workflows
The infra is now ready for durable job pipelines, and the first major use case is already deployed.

Current deployed use case:
- X/Twitter bookmark enrichment now runs through a Minions-native queue pipeline

Why this matters:
- eliminates cron-spawn churn
- reduces zombie subprocess issues
- gives retry / timeout / idempotency
- makes batch work observable
- creates a reusable execution pattern for future knowledge jobs

## Immediate Applied Direction

### A. Stabilize Minions enrichment as the new default
Current completed direction:
- `xkb_minion_submit.py` scans unenriched bookmarks and submits idempotent jobs
- `xkb_minion_worker.py` runs as long-lived worker daemon
- old scan-worker cron pattern is removed in favor of queue-based execution

This should become the canonical enrichment path.

### B. Add lifecycle metadata to cards
First small implementation target:
- add confidence / freshness / evidence metadata to card frontmatter or sidecar metadata

Suggested minimal fields:
- `confidence_score`
- `evidence_count`
- `last_confirmed_at`
- `knowledge_status`

### C. Introduce typed relationships incrementally
Do not wait for a full graph rewrite.
Begin by extracting and storing the most useful relationship edges from:
- card metadata
- wiki topic links
- repeated co-occurrence patterns
- explicit source relations

### D. Separate “knowledge artifact” quality from “source relevance”
A source may be interesting but still produce a low-quality card.
Add explicit QA checkpoints so the system can distinguish:
- important source
- low-value extraction
- duplicate idea
- outdated claim

## Proposed Near-Term Milestones

### Milestone 1 — Stable Postgres + Minions Base
Status: effectively complete
- Postgres active
- pgvector active
- Minions healthy
- XKB recall healthy on GBrain

### Milestone 2 — Minions-first Enrichment Pipeline
Status: deployed
- submitter/worker introduced
- X/Twitter bookmark enrichment moved to Minions-native queue path
- daemonized processing path exists
- retry/backoff/idempotency available

Next checks:
- production timeout tuning
- dead-job recovery workflow
- better operational observability / dashboard habits
- extending the same execution model to additional internal pipelines

### Milestone 3 — Knowledge Lifecycle Metadata
Add:
- confidence
- freshness
- supersession
- evidence counts

### Milestone 4 — Typed Relationship Layer
Add compact schema and edge extraction.

### Milestone 5 — Graph-aware Recall
Inject relationship-aware ranking into recall.

### Milestone 6 — Wiki as High-Signal Product Layer
Use wiki only for durable syntheses, not as a dump target.

## What not to do
- Do not turn graph into a vanity visualization project first.
- Do not create a huge ontology before proving useful relationships.
- Do not shove every transient observation into wiki.
- Do not confuse more cards with better knowledge.
- Do not let lifecycle/governance lag so far behind that the system rots.

## Strategic Summary
LLM Wiki v2 is not mainly about prettier wiki pages. It is about making the knowledge system stay healthy as it scales.

For XKB, that means:
- infrastructure is already strong enough
- the next leverage comes from governance
- wiki remains the product layer
- graph belongs below wiki in the structured layer
- Minions should become the default execution substrate for large-scale internal knowledge work
