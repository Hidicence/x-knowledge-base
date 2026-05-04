# XKB Cleanup & Productization Plan

Date: 2026-05-04
Owner: Pan / APAN2號

## Goal

把 `skills/x-knowledge-base` 從「能用但雜亂的內部工作流」整理成三種可交付形態：

1. **Internal XKB**：Pan 自用版，保留完整資料與進階流程。
2. **Public/Open Source XKB Core**：乾淨、可安裝、無私密資料的核心 skill。
3. **Paid/Marketplace XKB Pack**：包裝成可販售的 skill / workflow pack，主打可複用知識管線。

## Non-goals

- 不重寫整套 XKB。
- 不在清理前上架販售。
- 不把 Pan 的私人 wiki、graph、書籤資料一起包進產品。
- 不為了漂亮而砍掉仍在 cron / recall / OpenClaw runtime 使用的腳本。

## Phase 0 — Freeze & Inventory

Status: started

### Tasks

- [ ] 建立目前 XKB 檔案 inventory。
- [ ] 標記每個 script 的狀態：`core` / `optional` / `internal` / `archive` / `delete-candidate`。
- [ ] 找出 docs 中引用但不存在的檔案。
- [ ] 找出 private/generated artifacts。
- [ ] 盤點目前 cron / PM2 / OpenClaw skill 實際呼叫哪些入口。

### Current findings

Critical packaging blockers:

- `demo/xkb-demo-ui/.next/` 讓 demo 目錄膨脹到約 670MB，不能出貨。
- `wiki/_staging/*.md`、`wiki/topics/*.md`、`wiki/log.md`、`wiki/review-decisions.json` 混在 skill repo 裡，包含 Pan 自用知識，不應公開。
- `demo/xkb-demo-ui/public/graph-data.json` 是個人/generated graph data，不應出貨。
- `.secrets/gbrain.env` 出現在 skill repo 中，即使未必含真 secret，也不該存在。
- docs 有 stale references，例如 `scripts/build_vector_index.sh`、`scripts/recall_semantic.py` 等不存在。

## Phase 1 — Define Product Surface

Status: planned

### Canonical core surface

Keep as public/core:

- `SKILL.md`
- `README.md`
- `README.zh.md`
- `.env.example`
- `assets/knowledge-card-template.md`
- `scripts/_card_prompt.py`
- `scripts/_llm.py`
- `scripts/local_ingest.py`
- `scripts/build_search_index.sh`
- `scripts/search_bookmarks.sh`
- `scripts/build_vector_index.py`
- `scripts/recall_for_conversation.py`
- `scripts/recall_router.py`
- `scripts/xkb_recall_server.py`
- `scripts/xkb_ask.py`
- `scripts/sync_cards_to_wiki.py`
- `scripts/suggest_topic_map.py`
- `scripts/lint_wiki.py`
- `scripts/status_knowledge_pipeline.py`

### Optional integration packs

Move or label clearly:

- X/Twitter ingestion:
  - `scripts/fetch_bookmarks.sh`
  - `scripts/crawl_bookmarks_graphql.py`
  - `scripts/import_bookmarks_v2_from_graphql.py`
  - `scripts/run_scan_worker.py`
  - `tools/bookmark_enhancer.py`
- YouTube:
  - `scripts/fetch_youtube_playlist.py`
  - `scripts/run_youtube_sync.sh`
  - `scripts/monitor_youtube_playlist.py`
- GitHub:
  - `scripts/fetch_github_repos.py`
  - `scripts/run_github_sync.sh`
- Research/PDF/PubMed:
  - `scripts/pdf_ingest.py`
  - `scripts/fetch_pubmed.py`

### Internal / advanced mode

Keep but separate from public default:

- XBrain / GBrain setup and recall
- minion / inference worker queue
- PM2 configs
- memory distillation workers
- OpenClaw-specific cron/runtime wiring

## Phase 2 — Repo Hygiene Cleanup

Status: planned

### Safe cleanup actions

- [ ] Remove generated build artifacts from skill repo:
  - `demo/xkb-demo-ui/.next/`
  - `demo/xkb-demo-ui/node_modules/` if present
- [ ] Ensure `.gitignore` excludes:
  - `.next/`
  - `node_modules/`
  - runtime indexes
  - generated graph data
  - `.secrets/*.env`
- [ ] Move private/generated data out of product surface:
  - `wiki/_staging/`
  - `wiki/topics/`
  - `wiki/log.md`
  - `wiki/review-decisions.json`
  - `demo/xkb-demo-ui/public/graph-data.json`
- [ ] Keep only sanitized samples:
  - `wiki/WIKI-SCHEMA.md`
  - `wiki/topics/.gitkeep`
  - `demo/xkb-demo-ui/public/graph-data.sample.json`
- [ ] Remove `.secrets/gbrain.env`; keep env examples only.

### Safety rule

Before moving/deleting anything, create a recoverable backup or use `trash`/archive folder. Do not permanently delete Pan data.

## Phase 3 — Script Classification & Dead Code Review

Status: planned

### Review candidates

Scripts that need classification before shipping:

- `scripts/_session_dedup.py`
- `scripts/build_topic_profile.py`
- `scripts/crawl_bookmarks_coverage.py`
- `scripts/distill_memory_minion_submit.py`
- `scripts/eval_recommendations.py`
- `scripts/export_notebooklm.py`
- `scripts/full_sync_v2.py`
- `scripts/gbrain_recall.py`
- `scripts/monitor_youtube_playlist.py`
- `scripts/push_to_github.sh`
- `scripts/rebuild_v2_fetch.sh`
- `scripts/recommend_from_profile.sh`
- `scripts/sync_to_drive.sh`
- `scripts/wiki_health.py`

### Classification labels

- `core`: ships in public core
- `integration`: optional source connector
- `internal`: Pan/OpenClaw-specific, not public default
- `experimental`: keep in `experiments/` or docs-labeled
- `archive`: move to `archive/` with reason
- `delete-candidate`: only delete after no runtime references

## Phase 4 — Docs Repair

Status: planned

### Tasks

- [ ] Rewrite `SKILL.md` around the actual product surface.
- [ ] Add `docs/ARCHITECTURE.md` with current pipeline:
  - ingest → card → index/vector → recall/ask → wiki
- [ ] Add `docs/RUNTIME_PATHS.md` explaining generated/private paths.
- [ ] Add `docs/PUBLISHING_CHECKLIST.md`.
- [ ] Fix stale references:
  - `build_vector_index.sh` → `build_vector_index.py`
  - remove/replace `recall_semantic.py`
  - label workspace/OpenClaw-specific files as internal examples
- [ ] Clearly separate:
  - public install instructions
  - OpenClaw internal setup
  - XBrain/GBrain advanced mode

## Phase 5 — Validation Gates

Status: planned

Minimum checks before publishing:

- [ ] `python3 scripts/local_ingest.py --help`
- [ ] `python3 scripts/xkb_ask.py --help` or equivalent smoke test
- [ ] `bash scripts/build_search_index.sh` dry/safe test if possible
- [ ] `python3 scripts/status_knowledge_pipeline.py`
- [ ] No private files in release archive
- [ ] Release archive size sane, target under 10–20MB without demo assets
- [ ] Fresh install test in clean temp directory
- [ ] README examples actually run

## Phase 6 — Marketplace Packaging

Status: planned

### Product positioning

Working name:

**X Knowledge Base — turn scattered sources into reusable AI memory**

Core pitch:

> A reusable AI-agent skill that ingests scattered materials, converts them into structured knowledge cards, builds searchable recall, and distills durable wiki pages.

### Sellable bundles

Recommended packaging:

1. **Free/Core**
   - local notes → cards
   - search index
   - basic recall / ask
   - sanitized examples

2. **Paid Pro Pack**
   - multi-source ingestion patterns
   - YouTube/GitHub/PDF/PubMed connectors
   - wiki distillation workflow
   - quality gates
   - marketplace-ready docs and templates

3. **Internal Pan Edition**
   - X/Twitter auth workflow
   - XBrain/GBrain full mode
   - cron/PM2/OpenClaw runtime integration
   - private wiki and graph data

### Marketplace risks

- Need confirm YouMind supports paid creator submission, pricing, and revenue share. Current public `/skills` page confirms reusable skills/packs, but does not clearly expose seller onboarding terms.
- Do not list X/Twitter scraping/token features as the public headline; riskier and platform-dependent.
- Lead with general personal knowledge workflow, not private X bookmark automation.

## Execution Order

Recommended order:

1. Package hygiene first: remove generated/private artifacts from public surface.
2. Classify scripts and update docs to match reality.
3. Create release allowlist.
4. Run smoke tests.
5. Build sanitized demo/sample.
6. Prepare marketplace landing copy.
7. Only then evaluate YouMind/Agensi/ClawHub listing.

## Open Questions

- Should public XKB support OpenClaw only, or be agent-agnostic SKILL.md compatible?
- Is XBrain/GBrain a public dependency, optional advanced mode, or internal-only?
- Should YouMind be the target marketplace, or should XKB first ship on ClawHub / GitHub / Agensi?
- How much of X/Twitter ingestion should be public, given token/auth fragility?
