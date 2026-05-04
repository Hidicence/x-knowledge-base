# XKB Script Usage Audit

Generated: 2026-05-04

Method: static references across skill repo + PM2 configs + cron/process evidence. Do not delete scripts based only on this file; archive first if unsure.

## runtime-active (1)

- `scripts/xkb_recall_server.py` — refs: 5 — README.md, README.zh.md, SKILL.md, docs/XKB_CLEANUP_PRODUCTIZATION_PLAN.md, docs/XKB_FILE_INVENTORY_PHASE0.json

## runtime-pm2-worker (5)

- `scripts/distill_memory_chunk_worker.py` — refs: 2 — chunk-worker.pm2.json, docs/XKB_FILE_INVENTORY_PHASE0.json
- `scripts/distill_memory_consolidate_worker.py` — refs: 2 — consolidate-worker.pm2.json, docs/XKB_FILE_INVENTORY_PHASE0.json
- `scripts/distill_memory_minion_worker.py` — refs: 2 — distill-worker.pm2.json, docs/XKB_FILE_INVENTORY_PHASE0.json
- `scripts/xkb_infer_consumer.py` — refs: 2 — docs/XKB_FILE_INVENTORY_PHASE0.json, infer-consumer.pm2.json
- `scripts/xkb_minion_worker.py` — refs: 5 — README.md, README.zh.md, docs/XKB_FILE_INVENTORY_PHASE0.json, docs/xkb-vnext-roadmap-draft.md, xkb-worker.pm2.json

## core (14)

- `scripts/_card_prompt.py` — refs: 5 — README.md, README.zh.md, SKILL.md, docs/XKB_CLEANUP_PRODUCTIZATION_PLAN.md, docs/XKB_FILE_INVENTORY_PHASE0.json
- `scripts/_llm.py` — refs: 10 — README.md, README.zh.md, docs/XKB_CLEANUP_PRODUCTIZATION_PLAN.md, docs/XKB_FILE_INVENTORY_PHASE0.json, scripts/distill_memory_to_wiki.py, scripts/local_ingest.py, +4 more
- `scripts/build_search_index.sh` — refs: 7 — README.md, README.zh.md, docs/XKB_CLEANUP_PRODUCTIZATION_PLAN.md, docs/XKB_FILE_INVENTORY_PHASE0.json, scripts/build_vector_index.py, scripts/fetch_and_summarize.sh, +1 more
- `scripts/build_vector_index.py` — refs: 16 — README.md, README.zh.md, SKILL.md, docs/XKB_CLEANUP_PRODUCTIZATION_PLAN.md, docs/XKB_FILE_INVENTORY_PHASE0.json, docs/data-flow.md, +10 more
- `scripts/lint_wiki.py` — refs: 9 — README.md, README.zh.md, SKILL.md, demo/xkb-demo-ui/public/graph-data.json, docs/PUBLISHING_CHECKLIST.md, docs/XKB_CLEANUP_PRODUCTIZATION_PLAN.md, +3 more
- `scripts/local_ingest.py` — refs: 8 — README.md, README.zh.md, SKILL.md, docs/XKB_CLEANUP_PRODUCTIZATION_PLAN.md, docs/XKB_FILE_INVENTORY_PHASE0.json, docs/data-flow.md, +2 more
- `scripts/recall_for_conversation.py` — refs: 8 — README.md, README.zh.md, SKILL.md, docs/PUBLISHING_CHECKLIST.md, docs/XKB_CLEANUP_PRODUCTIZATION_PLAN.md, docs/XKB_FILE_INVENTORY_PHASE0.json, +2 more
- `scripts/recall_router.py` — refs: 5 — SKILL.md, demo/xkb-demo-ui/app/api/recall/route.ts, docs/XKB_CLEANUP_PRODUCTIZATION_PLAN.md, docs/XKB_FILE_INVENTORY_PHASE0.json, scripts/xkb_recall_server.py
- `scripts/search_bookmarks.sh` — refs: 5 — README.md, README.zh.md, SKILL.md, docs/XKB_CLEANUP_PRODUCTIZATION_PLAN.md, docs/XKB_FILE_INVENTORY_PHASE0.json
- `scripts/status_knowledge_pipeline.py` — refs: 8 — README.md, README.zh.md, SKILL.md, docs/PUBLISHING_CHECKLIST.md, docs/XKB_CLEANUP_PRODUCTIZATION_PLAN.md, docs/XKB_FILE_INVENTORY_PHASE0.json, +2 more
- `scripts/suggest_topic_map.py` — refs: 5 — README.md, README.zh.md, SKILL.md, docs/XKB_CLEANUP_PRODUCTIZATION_PLAN.md, docs/XKB_FILE_INVENTORY_PHASE0.json
- `scripts/sync_cards_to_wiki.py` — refs: 13 — README.md, README.zh.md, SKILL.md, demo/xkb-demo-ui/public/graph-data.json, docs/XKB_CLEANUP_PRODUCTIZATION_PLAN.md, docs/XKB_FILE_INVENTORY_PHASE0.json, +7 more
- `scripts/xkb_ask.py` — refs: 9 — README.md, README.zh.md, SKILL.md, demo/xkb-demo-ui/app/api/ask/route.ts, docs/PUBLISHING_CHECKLIST.md, docs/XKB_CLEANUP_PRODUCTIZATION_PLAN.md, +3 more
- `tools/embedding_providers.py` — refs: 1 — docs/XKB_FILE_INVENTORY_PHASE0.json

## integration (16)

- `scripts/crawl_bookmarks_coverage.py` — refs: 2 — docs/XKB_CLEANUP_PRODUCTIZATION_PLAN.md, docs/XKB_FILE_INVENTORY_PHASE0.json
- `scripts/crawl_bookmarks_graphql.py` — refs: 3 — docs/XKB_CLEANUP_PRODUCTIZATION_PLAN.md, docs/XKB_FILE_INVENTORY_PHASE0.json, scripts/full_sync_v2.py
- `scripts/fetch_and_summarize.sh` — refs: 7 — demo/xkb-demo-ui/public/graph-data.json, docs/XKB_FILE_INVENTORY_PHASE0.json, docs/data-flow.md, docs/xkb-wiki-architecture.md, scripts/import_bookmarks_v2_from_graphql.py, scripts/rebuild_v2_fetch.sh, +1 more
- `scripts/fetch_bookmarks.sh` — refs: 4 — docs/XKB_CLEANUP_PRODUCTIZATION_PLAN.md, docs/XKB_FILE_INVENTORY_PHASE0.json, docs/xkb-wiki-architecture.md, scripts/fetch_and_summarize.sh
- `scripts/fetch_github_repos.py` — refs: 7 — README.md, README.zh.md, SKILL.md, docs/XKB_CLEANUP_PRODUCTIZATION_PLAN.md, docs/XKB_FILE_INVENTORY_PHASE0.json, scripts/_card_prompt.py, +1 more
- `scripts/fetch_pubmed.py` — refs: 7 — README.md, README.zh.md, SKILL.md, docs/XKB_CLEANUP_PRODUCTIZATION_PLAN.md, docs/XKB_FILE_INVENTORY_PHASE0.json, docs/data-flow.md, +1 more
- `scripts/fetch_youtube_playlist.py` — refs: 8 — README.md, README.zh.md, SKILL.md, docs/XKB_CLEANUP_PRODUCTIZATION_PLAN.md, docs/XKB_FILE_INVENTORY_PHASE0.json, docs/xkb-wiki-architecture.md, +2 more
- `scripts/import_bookmarks_v2_from_graphql.py` — refs: 3 — docs/XKB_CLEANUP_PRODUCTIZATION_PLAN.md, docs/XKB_FILE_INVENTORY_PHASE0.json, scripts/full_sync_v2.py
- `scripts/monitor_youtube_playlist.py` — refs: 3 — docs/XKB_CLEANUP_PRODUCTIZATION_PLAN.md, docs/XKB_FILE_INVENTORY_PHASE0.json, docs/XKB_FILE_INVENTORY_PHASE0.md
- `scripts/pdf_ingest.py` — refs: 4 — docs/XKB_CLEANUP_PRODUCTIZATION_PLAN.md, docs/XKB_FILE_INVENTORY_PHASE0.json, docs/data-flow.md, scripts/fetch_pubmed.py
- `scripts/run_bookmark_worker.py` — refs: 7 — README.md, README.zh.md, docs/XKB_FILE_INVENTORY_PHASE0.json, docs/XKB_FILE_INVENTORY_PHASE0.md, docs/data-flow.md, docs/security-audit-response.md, +1 more
- `scripts/run_github_sync.sh` — refs: 4 — SKILL.md, docs/XKB_CLEANUP_PRODUCTIZATION_PLAN.md, docs/XKB_FILE_INVENTORY_PHASE0.json, scripts/fetch_github_repos.py
- `scripts/run_scan_worker.py` — refs: 9 — README.md, README.zh.md, SKILL.md, docs/XKB_CLEANUP_PRODUCTIZATION_PLAN.md, docs/XKB_FILE_INVENTORY_PHASE0.json, docs/data-flow.md, +3 more
- `scripts/run_youtube_sync.sh` — refs: 5 — SKILL.md, docs/XKB_CLEANUP_PRODUCTIZATION_PLAN.md, docs/XKB_FILE_INVENTORY_PHASE0.json, docs/security-audit-response.md, docs/xkb-wiki-architecture.md
- `tools/agent_reach_enricher.py` — refs: 2 — docs/XKB_FILE_INVENTORY_PHASE0.json, scripts/fetch_and_summarize.sh
- `tools/bookmark_enhancer.py` — refs: 6 — SKILL.md, docs/XKB_CLEANUP_PRODUCTIZATION_PLAN.md, docs/XKB_FILE_INVENTORY_PHASE0.json, docs/data-flow.md, docs/xkb-wiki-architecture.md, scripts/fetch_and_summarize.sh

## advanced-runtime (13)

- `scripts/distill_memory_minion_submit.py` — refs: 2 — docs/XKB_CLEANUP_PRODUCTIZATION_PLAN.md, docs/XKB_FILE_INVENTORY_PHASE0.json
- `scripts/distill_memory_to_wiki.py` — refs: 7 — README.md, README.zh.md, SKILL.md, demo/xkb-demo-ui/public/graph-data.json, docs/XKB_FILE_INVENTORY_PHASE0.json, docs/xkb-wiki-architecture.md, +1 more
- `scripts/full_sync_v2.py` — refs: 3 — docs/XKB_CLEANUP_PRODUCTIZATION_PLAN.md, docs/XKB_FILE_INVENTORY_PHASE0.json, docs/XKB_FILE_INVENTORY_PHASE0.md
- `scripts/gbrain_recall.py` — refs: 2 — docs/XKB_CLEANUP_PRODUCTIZATION_PLAN.md, docs/XKB_FILE_INVENTORY_PHASE0.json
- `scripts/init_rebuild_v2.py` — refs: 3 — docs/XKB_FILE_INVENTORY_PHASE0.json, docs/XKB_FILE_INVENTORY_PHASE0.md, scripts/full_sync_v2.py
- `scripts/rebuild_v2_fetch.sh` — refs: 3 — docs/XKB_CLEANUP_PRODUCTIZATION_PLAN.md, docs/XKB_FILE_INVENTORY_PHASE0.json, docs/XKB_FILE_INVENTORY_PHASE0.md
- `scripts/setup_xbrain.sh` — refs: 3 — README.md, README.zh.md, docs/XKB_FILE_INVENTORY_PHASE0.json
- `scripts/xbrain_recall.py` — refs: 3 — README.md, README.zh.md, docs/XKB_FILE_INVENTORY_PHASE0.json
- `scripts/xkb_adapter_http.py` — refs: 2 — docs/XKB_FILE_INVENTORY_PHASE0.json, docs/XKB_FILE_INVENTORY_PHASE0.md
- `scripts/xkb_demo.sh` — refs: 4 — README.md, README.zh.md, docs/XKB_FILE_INVENTORY_PHASE0.json, docs/XKB_FILE_INVENTORY_PHASE0.md
- `scripts/xkb_infer_enqueue.py` — refs: 1 — docs/XKB_FILE_INVENTORY_PHASE0.json
- `scripts/xkb_minion_submit.py` — refs: 4 — README.md, README.zh.md, docs/XKB_FILE_INVENTORY_PHASE0.json, docs/xkb-vnext-roadmap-draft.md
- `scripts/xkb_run_request.py` — refs: 3 — docs/XKB_FILE_INVENTORY_PHASE0.json, docs/XKB_FILE_INVENTORY_PHASE0.md, scripts/xkb_infer_consumer.py

## maintenance (16)

- `scripts/audit_index_quality.py` — refs: 3 — SKILL.md, docs/XKB_FILE_INVENTORY_PHASE0.json, docs/XKB_FILE_INVENTORY_PHASE0.md
- `scripts/build_release_package.sh` — refs: 0 — no static refs
- `scripts/build_topic_profile.py` — refs: 3 — docs/XKB_CLEANUP_PRODUCTIZATION_PLAN.md, docs/XKB_FILE_INVENTORY_PHASE0.json, docs/XKB_FILE_INVENTORY_PHASE0.md
- `scripts/canonicalize_duplicates.py` — refs: 6 — docs/XKB_FILE_INVENTORY_PHASE0.json, docs/XKB_FILE_INVENTORY_PHASE0.md, docs/xkb-wiki-architecture.md, scripts/fetch_and_summarize.sh, scripts/full_sync_v2.py, scripts/prune_duplicate_index_rows.py
- `scripts/cleanup_titles_in_index.py` — refs: 4 — docs/XKB_FILE_INVENTORY_PHASE0.json, docs/XKB_FILE_INVENTORY_PHASE0.md, scripts/fetch_and_summarize.sh, scripts/full_sync_v2.py
- `scripts/export_notebooklm.py` — refs: 3 — docs/XKB_CLEANUP_PRODUCTIZATION_PLAN.md, docs/XKB_FILE_INVENTORY_PHASE0.json, docs/XKB_FILE_INVENTORY_PHASE0.md
- `scripts/health_check.py` — refs: 5 — README.md, README.zh.md, SKILL.md, docs/XKB_FILE_INVENTORY_PHASE0.json, docs/XKB_FILE_INVENTORY_PHASE0.md
- `scripts/health_check_pipeline.py` — refs: 4 — README.md, README.zh.md, docs/XKB_FILE_INVENTORY_PHASE0.json, docs/XKB_FILE_INVENTORY_PHASE0.md
- `scripts/migrate_schema.py` — refs: 3 — SKILL.md, docs/XKB_FILE_INVENTORY_PHASE0.json, docs/XKB_FILE_INVENTORY_PHASE0.md
- `scripts/normalize_index_quality.py` — refs: 5 — docs/XKB_FILE_INVENTORY_PHASE0.json, docs/XKB_FILE_INVENTORY_PHASE0.md, docs/xkb-wiki-architecture.md, scripts/fetch_and_summarize.sh, scripts/full_sync_v2.py
- `scripts/prune_duplicate_index_rows.py` — refs: 4 — README.md, README.zh.md, docs/XKB_FILE_INVENTORY_PHASE0.json, docs/XKB_FILE_INVENTORY_PHASE0.md
- `scripts/smoke_test_pipeline.sh` — refs: 3 — docs/XKB_FILE_INVENTORY_PHASE0.json, docs/XKB_FILE_INVENTORY_PHASE0.md, docs/xkb-wiki-architecture.md
- `scripts/sync_enriched_index.py` — refs: 6 — README.md, README.zh.md, docs/XKB_FILE_INVENTORY_PHASE0.json, docs/XKB_FILE_INVENTORY_PHASE0.md, scripts/fetch_and_summarize.sh, scripts/full_sync_v2.py
- `scripts/sync_tiege_queue.py` — refs: 3 — docs/XKB_FILE_INVENTORY_PHASE0.json, docs/XKB_FILE_INVENTORY_PHASE0.md, scripts/fetch_and_summarize.sh
- `scripts/topic_guide_generator.py` — refs: 4 — README.md, README.zh.md, docs/XKB_FILE_INVENTORY_PHASE0.json, docs/XKB_FILE_INVENTORY_PHASE0.md
- `scripts/wiki_health.py` — refs: 3 — docs/XKB_CLEANUP_PRODUCTIZATION_PLAN.md, docs/XKB_FILE_INVENTORY_PHASE0.json, docs/XKB_FILE_INVENTORY_PHASE0.md

## experimental-or-optional (11)

- `scripts/_session_dedup.py` — refs: 3 — docs/XKB_CLEANUP_PRODUCTIZATION_PLAN.md, docs/XKB_FILE_INVENTORY_PHASE0.json, docs/XKB_FILE_INVENTORY_PHASE0.md
- `scripts/action_recall.py` — refs: 4 — README.md, README.zh.md, docs/XKB_FILE_INVENTORY_PHASE0.json, docs/XKB_FILE_INVENTORY_PHASE0.md
- `scripts/auto_categorize.sh` — refs: 3 — docs/XKB_FILE_INVENTORY_PHASE0.json, docs/XKB_FILE_INVENTORY_PHASE0.md, scripts/fetch_and_summarize.sh
- `scripts/continuity_recall.py` — refs: 4 — README.md, README.zh.md, docs/XKB_FILE_INVENTORY_PHASE0.json, docs/XKB_FILE_INVENTORY_PHASE0.md
- `scripts/contrarian_recall.py` — refs: 4 — README.md, README.zh.md, docs/XKB_FILE_INVENTORY_PHASE0.json, docs/XKB_FILE_INVENTORY_PHASE0.md
- `scripts/conversation_state_parser.py` — refs: 2 — docs/XKB_FILE_INVENTORY_PHASE0.json, docs/XKB_FILE_INVENTORY_PHASE0.md
- `scripts/eval_recommendations.py` — refs: 3 — docs/XKB_CLEANUP_PRODUCTIZATION_PLAN.md, docs/XKB_FILE_INVENTORY_PHASE0.json, docs/XKB_FILE_INVENTORY_PHASE0.md
- `scripts/push_to_github.sh` — refs: 3 — docs/XKB_CLEANUP_PRODUCTIZATION_PLAN.md, docs/XKB_FILE_INVENTORY_PHASE0.json, docs/XKB_FILE_INVENTORY_PHASE0.md
- `scripts/recall_gate.py` — refs: 3 — docs/XKB_FILE_INVENTORY_PHASE0.json, docs/XKB_FILE_INVENTORY_PHASE0.md, references/conversation-recall.md
- `scripts/recommend_from_profile.sh` — refs: 3 — docs/XKB_CLEANUP_PRODUCTIZATION_PLAN.md, docs/XKB_FILE_INVENTORY_PHASE0.json, docs/XKB_FILE_INVENTORY_PHASE0.md
- `scripts/sync_to_drive.sh` — refs: 3 — docs/XKB_CLEANUP_PRODUCTIZATION_PLAN.md, docs/XKB_FILE_INVENTORY_PHASE0.json, docs/XKB_FILE_INVENTORY_PHASE0.md

## needs-review (0)

