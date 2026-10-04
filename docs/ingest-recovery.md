# Saved cards and publication recovery

Source adapters generate cards and save them before publication. `gbrain_publish.py`
owns verification, repair receipts and batch completion for local files, GitHub,
YouTube and both bookmark workers.

For a saved card, the publisher first checks the **current** GBrain database in a
read-only transaction. A matching body and source identity, with embeddings on all
chunks, skips `put` and `embed`. A confirmed mismatch permits repair; a failed
verification connection never authorizes a write. This check does not certify the
embedding model version or semantic retrieval quality.

The outbox stores the path, content hash and failed stage. Recovery reads that file
again, without generating a replacement:

```sh
python3 scripts/gbrain_publish.py --limit 20
```

Card-specific failures remain pending while the batch continues. Backend failures
(including unknown transport errors) stop new generation. A read-only database
probe and embedding credential check run before a generation batch; this cannot
guarantee that an embedding provider stays available later in the batch.

New-generation limits exclude saved-card recovery. Batch summaries distinguish
`generated`, `recovered`, `verified`, `failed`, and `deferred`. The local search
index is rebuilt once after saved cards were handled, even if publication failed.
A batch exits unsuccessfully when publication or index completion fails; saved
cards remain on disk. Local/GitHub exit code 2 continues to mean new cards were
generated, while recovery-only success returns 0.

## Recall quality

The structured recall packet and doctor share one quality interpretation.
`partial` and `no_text` are degraded, not fully ready. Strict `--require-judge`
requires complete judgment. Unknown judgments preserve candidates and do not add
negative usage history. `no_records` is an empty result rather than a judge outage,
but it cannot satisfy a strict requirement for completed judgment.
