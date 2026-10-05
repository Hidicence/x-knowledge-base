# Wiki writes and recovery

`xkb_review.py --governance --write-governance` prepares an immutable batch before
changing topic pages, the candidate registry, or the audit log. A v2 manifest
records the original snapshots, planned outputs, and both sets of hashes.
The next apply resumes an interrupted prepared batch before selecting new
candidates. It refuses recovery if any affected artifact has changed to an
unrecognized version. Staging evidence is snapshotted and never rewritten by
governance. Empty batches, dry runs, and health counts do not create topic pages.

Rollback verifies **all** affected outputs and original snapshots before its
first restore. A later manual edit or another batch causes a conflict instead
of being overwritten. If rollback itself is interrupted, repeat the same
rollback command to finish it before starting another batch. A rolled-back
candidate can be applied again, with a new batch ID and separate snapshots.
Legacy v1 manifests lack output hashes and cannot be safely rolled back by this
command; inspect their snapshots manually. A rollback conflict also needs human
reconciliation; do not delete the manifest to suppress it.

The scheduler serializes governance runs with its existing `flock`. Planned
writes check hashes before each atomic file replacement, but the wiki is not a
transactional database: avoid editing affected files while an apply or rollback
is actively running. Publication to the vector index remains the next scheduled
stage, separate from topic-page promotion.

Manual distillation uses the same source-file/candidate-number identity as
governance. Repeating it does not append the same candidate twice, and a missing
topic is reported as skipped rather than applied.

Synthesis drafts are tied to the full source page, including human prose. Apply
requires that version and preserves previous conclusions, multiline archived
notes, and source explanations. Drafts produced before the full-page fingerprint
was introduced need to be generated and reviewed again. LLM absorb failures are
recorded as `unavailable`, so they are distinguishable from an actual rejection.
Malformed decision files stop the run and remain untouched.

Reviewed candidate routing can be stored in the private governance directory as
`topic-resolutions.json`: an object keyed by candidate ID, with `fingerprint`,
`topic`, and `reason`. The fingerprint must match the staged candidate content;
stale decisions fail visibly. Routing preserves the staging text and candidate ID,
and records the original topic and review reason in the registry. It does not
override confidence, provenance, duplicate, or age checks. Archived and redirect
pages remain readable but cannot receive new candidates. New distillation prompts
list only writable topics; an unknown output slug is a proposal, not an apparently
existing destination.

The fixture tests in `test_wiki_write_integrity.py` exercise interruption,
recovery, rollback conflicts, repeated synthesis, and the decision gate without
paid model calls or production knowledge writes.
