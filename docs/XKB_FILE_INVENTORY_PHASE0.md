# XKB Phase 0 Inventory Summary

Generated: 2026-05-04

## Counts by status

- config-or-schema: 8
- core: 19
- docs-or-demo: 49
- integration: 15
- internal: 66
- review: 14
- review-script: 34

## Flagged private/generated risks

- `.secrets/gbrain.env.example` — private-risk; status=internal; size=250
- `.secrets/gbrain.env` — private-risk; status=internal; size=95
- `.secrets/.gitignore` — private-risk; status=review; size=92
- `wiki/review-decisions.json` — private-risk; status=internal; size=34977
- `wiki/log.md` — private-risk; status=internal; size=7565
- `demo/xkb-demo-ui/package-lock.json` — generated-risk; status=docs-or-demo; size=120129
- `demo/xkb-demo-ui/public/graph-data.json` — private-risk; status=docs-or-demo; size=1219922
- `wiki/topics/openclaw-agent-workflows.md` — private-risk; status=review; size=32228
- `wiki/topics/video-prompt-patterns.md` — private-risk; status=review; size=8268
- `wiki/topics/xkb-pipeline-architecture.md` — private-risk; status=review; size=5024
- `wiki/topics/xkb-evolution.md` — private-risk; status=review; size=6262
- `wiki/topics/ai-video-workflows.md` — private-risk; status=review; size=8220
- `wiki/topics/.gitkeep` — private-risk; status=review; size=0
- `wiki/topics/ai-seo-and-geo.md` — private-risk; status=review; size=6093
- `wiki/topics/醫療ai影像診斷-guide.md` — private-risk; status=review; size=8642
- `wiki/topics/learning-base.md` — private-risk; status=review; size=5398
- `wiki/topics/ai-agent-memory-systems.md` — private-risk; status=internal; size=18241
- `wiki/_staging/2026-04-21-evening-candidates.md` — private-risk; status=internal; size=258
- `wiki/_staging/2026-04-11-afternoon-candidates.md` — private-risk; status=internal; size=258
- `wiki/_staging/2026-04-29-evening-candidates.md` — private-risk; status=internal; size=258
- `wiki/_staging/2026-04-23-evening-candidates.md` — private-risk; status=internal; size=258
- `wiki/_staging/2026-04-15-evening-candidates.md` — private-risk; status=internal; size=258
- `wiki/_staging/2026-04-26-evening-candidates.md` — private-risk; status=internal; size=258
- `wiki/_staging/2026-04-25-evening-candidates.md` — private-risk; status=internal; size=258
- `wiki/_staging/2026-04-23-afternoon-candidates.md` — private-risk; status=internal; size=258
- `wiki/_staging/2026-04-08-evening-candidates.md` — private-risk; status=internal; size=258
- `wiki/_staging/2026-04-27-evening-candidates.md` — private-risk; status=internal; size=258
- `wiki/_staging/2026-04-13-evening-candidates.md` — private-risk; status=internal; size=258
- `wiki/_staging/2026-05-03-afternoon-candidates.md` — private-risk; status=internal; size=258
- `wiki/_staging/2026-04-28-afternoon-candidates.md` — private-risk; status=internal; size=258
- `wiki/_staging/2026-04-19-candidates.md` — private-risk; status=internal; size=258
- `wiki/_staging/2026-04-14-afternoon-candidates.md` — private-risk; status=internal; size=1229
- `wiki/_staging/2026-04-14-evening-candidates.md` — private-risk; status=internal; size=258
- `wiki/_staging/2026-04-12-evening-candidates.md` — private-risk; status=internal; size=258
- `wiki/_staging/2026-04-07-candidates.md` — private-risk; status=internal; size=623
- `wiki/_staging/2026-04-11-manual-sync-after-card-backfill-candidates.md` — private-risk; status=internal; size=258
- `wiki/_staging/2026-04-28-healthcheck-candidates.md` — private-risk; status=internal; size=258
- `wiki/_staging/2026-05-03-evening-candidates.md` — private-risk; status=internal; size=258
- `wiki/_staging/2026-04-08-candidates.md` — private-risk; status=internal; size=258
- `wiki/_staging/2026-05-02-evening-candidates.md` — private-risk; status=internal; size=1779
- `wiki/_staging/2026-04-26-afternoon-candidates.md` — private-risk; status=internal; size=258
- `wiki/_staging/2026-04-21-smoke-test-candidates.md` — private-risk; status=internal; size=3811
- `wiki/_staging/2026-05-01-evening-candidates.md` — private-risk; status=internal; size=1187
- `wiki/_staging/2026-04-29-afternoon-candidates.md` — private-risk; status=internal; size=258
- `wiki/_staging/2026-04-12-candidates.md` — private-risk; status=internal; size=2519
- `wiki/_staging/2026-04-28-evening-candidates.md` — private-risk; status=internal; size=258
- `wiki/_staging/2026-05-01-afternoon-candidates.md` — private-risk; status=internal; size=1292
- `wiki/_staging/2026-04-11-evening-candidates.md` — private-risk; status=internal; size=258
- `wiki/_staging/2026-04-13-afternoon-candidates.md` — private-risk; status=internal; size=1779
- `wiki/_staging/2026-05-02-afternoon-candidates.md` — private-risk; status=internal; size=2108
- `wiki/_staging/2026-04-19-afternoon-candidates.md` — private-risk; status=internal; size=2905
- `wiki/_staging/2026-04-19-evening-candidates.md` — private-risk; status=internal; size=1273
- `wiki/_staging/2026-04-24-evening-candidates.md` — private-risk; status=internal; size=258
- `wiki/_staging/2026-04-30-evening-candidates.md` — private-risk; status=internal; size=258
- `wiki/_staging/2026-04-21-afternoon-candidates.md` — private-risk; status=internal; size=2122
- `wiki/_staging/2026-04-12-afternoon-candidates.md` — private-risk; status=internal; size=258
- `wiki/_staging/2026-04-15-afternoon-candidates.md` — private-risk; status=internal; size=655
- `wiki/_staging/2026-04-25-afternoon-candidates.md` — private-risk; status=internal; size=258
- `wiki/_staging/2026-04-17-cc-distill-candidates.md` — private-risk; status=internal; size=9335
- `wiki/_staging/2026-04-24-afternoon-candidates.md` — private-risk; status=internal; size=258

## Review scripts

- `scripts/topic_guide_generator.py`
- `scripts/contrarian_recall.py`
- `scripts/canonicalize_duplicates.py`
- `scripts/action_recall.py`
- `scripts/sync_tiege_queue.py`
- `scripts/conversation_state_parser.py`
- `scripts/smoke_test_pipeline.sh`
- `scripts/recommend_from_profile.sh`
- `scripts/auto_categorize.sh`
- `scripts/_session_dedup.py`
- `scripts/health_check.py`
- `scripts/xkb_adapter_http.py`
- `scripts/full_sync_v2.py`
- `scripts/run_bookmark_worker.py`
- `scripts/eval_recommendations.py`
- `scripts/health_check_pipeline.py`
- `scripts/monitor_youtube_playlist.py`
- `scripts/migrate_schema.py`
- `scripts/audit_index_quality.py`
- `scripts/xkb_run_request.py`
- `scripts/normalize_index_quality.py`
- `scripts/export_notebooklm.py`
- `scripts/xkb_demo.sh`
- `scripts/wiki_health.py`
- `scripts/rebuild_v2_fetch.sh`
- `scripts/prune_duplicate_index_rows.py`
- `scripts/init_rebuild_v2.py`
- `scripts/cleanup_titles_in_index.py`
- `scripts/push_to_github.sh`
- `scripts/build_topic_profile.py`
- `scripts/sync_to_drive.sh`
- `scripts/recall_gate.py`
- `scripts/sync_enriched_index.py`
- `scripts/continuity_recall.py`
