# Shared recall and connection checks

XKB's intended behavior is proactive reuse during conversation. A user can reveal
a need by stating a plan, describing a difficulty, adding a constraint, or changing
direction. Question answering alone does not validate that behavior.

`xkb_recall` accepts optional `conversation` messages (`role`: `user`/`assistant`,
`content`: text), in chronological order, excluding the current `message`. HTTP
recall/context accepts the same field. Inputs accept at most eight messages; the
runtime retains the last four eligible messages, up to 600 characters each. The
current utterance stays separate in captured turns and takes precedence over prior
goals. Responses acknowledge the normalized context with `conversation_fingerprint`
and `conversation_messages`; a client refuses an older server that ignores it.
Current-message retrieval and contextual retrieval each keep their candidate
budget, so a long previous topic cannot crowd out current-message candidates
before judging. Ranking fuses their evidence identities after Jev; contextual
backend diagnostics are reported separately in `context_retrieval`.

The Claude hook reads a bounded transcript tail, omits tool output and harness
messages, and passes recent dialogue on prompt submission. Without supplied
dialogue, turn-start uses completed turns from that same session only. MCP alone
does not create automatic invocation; the calling agent must notice conversational
needs and invoke the tool. Refresh tool discovery after a schema update.

Run `python scripts/xkb_eval.py --cases evals/conversation-cases.json` for the
offline conversational checkpoints. These preserve statements and follow-ups
instead of converting them to questions. `need` describes the intended assistance;
`expected_delivery` distinguishes useful evidence from a turn that should stay
quiet. `expected_any_ids` accepts equivalent sources for one evidence requirement.
It measures coverage of labelled requirements, not exhaustive corpus recall.
Unexpected records still fail precision checks. The offline corpus exposes weak
keyword overlaps; it does not certify semantic usefulness or the answering agent's
actual use of evidence. Live suites and full responses for a private library should
stay outside the public repository, with labels frozen before requests and any
later review recorded separately.

For live acceptance, include non-question plans and obstacles, context-dependent
follow-ups, new constraints, explicit topic changes, and ordinary acknowledgements.
Inspect timing and evidence content, including contradictions and irrelevant or
repeated advice. A complete judge response is a readiness check, not proof that the
agent recognized the user's need or used the evidence well.

MCP (`xkb_recall`), the default `recall_router.py` and `xkb_ask.py` CLIs,
and HTTP (`POST /v1/recall`) use
`Store.knowledge_recall`: cards, wiki and conversation evidence go through the
same namespace checks, relevance judge and ranking. `xkb_evidence.py` defines
the title/body/source fallbacks used by the service, judge, hook and ask client.
Original evidence fields remain intact in the packet; MCP preserves it.

The old continuity/action/contrarian presentation is available through
`recall_router.py --legacy-router`; its Python `route()` API and classification-only
`--dry-run` retain their previous behavior. Its special hints have not been
removed. The ask client's standalone search and answer branches have been retired;
`--legacy-search` prints a migration notice and uses the common core.
Both default CLIs accept `--env-file` and `--limit` (1–50).

`xkb_ask.py --no-wiki`, `--no-cards`, `--no-conversations`, `--no-gbrain`,
`--max-wiki` and `--max-cards` now send validated options to the service. Quotas
are upper bounds (0–50), applied after identity fusion and before the global
limit. Disabled layers are not searched. `--no-gbrain` disables vector retrieval
and uses the core's keyword search; the shared relevance judge still applies.
`wiki` selects the wiki/daily-note index; `cards` selects the card backend.

HTTP clients can send the same contract in `POST /v1/recall` or `/v1/context`:

```json
{"query":"deployment","limit":10,"options":{"wiki":false,"semantic":false,"max_cards":3}}
```

The response echoes the complete effective `options`. The client rejects an old
server that silently ignores options instead of falling back to standalone search.
Omitting options preserves the common defaults: all layers, semantic retrieval,
and no additional per-layer cap beyond the request's global limit.

The ask client generates an answer from the shared records, including conversation
traces, and returns `evidence_refs` and the complete `recall` packet in JSON.
`wiki_refs` and `card_refs` remain compatible subsets. These are available
evidence, not proof that the generated answer used every item. A remote recall
does not require local embedding credentials; answer generation still needs its
configured LLM credentials. Recall failure stops before answer generation.

## Select the library

Without `XKB_MEMORY_SERVICE_URL`, MCP runs the service core locally in a child
process. `XKB_DATA_DIR` (or `.xkb.json`) selects the cards/wiki/indexes;
`XKB_SERVICE_DB` selects the conversation database, defaulting to
`~/.xkb-runtime/knowledge.sqlite`, the HTTP service's default. Recall can create
that database and records retrieval usage; it does not ingest or promote knowledge.

For agents to share the **same deployed library**, point them at the same HTTP
service, not different local copies. Configure these in the MCP environment or
in the file named by `XKB_ENV_FILE`:

```dotenv
XKB_MEMORY_SERVICE_URL=http://127.0.0.1:18972
XKB_SERVICE_TOKEN=your-service-token
XKB_NAMESPACE=private
```

The namespace must match the token's namespace. A missing token, forbidden
namespace, timeout or invalid response is an error. An explicit remote target
never falls back to local data. Redirects are rejected to keep the token on the
configured endpoint. Use HTTPS or the existing SSH tunnel for remote access.
MCP does not implicitly read the Claude hook's separate configuration file.

Keep credentials in runtime configuration. Process variables override
`XKB_ENV_FILE`; its settings are loaded before the recall worker imports path
or provider modules. Configure the HTTP service with the same data paths and
database when using both local entry points.

MCP still accepts `message`, with an optional integer `limit` (1–50). Its metadata
contains the full HTTP recall packet (`records`, `context`, `retrieval_mode`,
`judge`, `warnings`, ACL and backend diagnostics), plus `connection`.
The older `results` and `formatted_text` names remain aliases for compatibility.
The old router-specific trigger classification is no longer returned: non-skipped
calls use `trigger_class: knowledge`, `state: recall`.

## Evidence identity and source diagnostics

Each merged recall record includes `evidence_key`, produced by the same identity
function used for RRF fusion, usage counting and demotion. Explicit IDs take
precedence over citation URLs. Card identity ignores display titles; wiki and
daily-note identity includes the source document and section. Conversations use
their trace ID. The namespace is part of the identity. Distinct IDs sharing a URL
remain separate, and anonymous records are neither merged by title nor assigned
persistent usage counters. Original IDs and provenance remain available.

The versioned `ev1:` keys are stored in `recall_usage.record_id`. Earlier unversioned
rows remain untouched but no longer drive current demotion or its report. A former
document-level counter cannot be split reliably across sections, so no inferred
backfill is performed. New counters accumulate only observed evidence identities.

`backends.cards`, `backends.wiki` and `backends.conversation` report their own
`attempted`, `used` and `status`. States distinguish `disabled`, `unavailable`,
`empty`, `used`, `filtered`, `error`, `timeout` and `invalid_response`; `unknown`
means an adapter omitted diagnostics. Keyword fallback has its own nested status.
`filtered` means retrieval produced hits but the eligibility/relevance gates removed
them; it is not a backend outage. ACL counts record filtering events, not unique IDs.

Wiki-only semantic results use `retrieval_mode: wiki_semantic`; they no longer
claim that XBrain succeeded. Explicit keyword mode uses `keyword`; disabling both
knowledge indexes uses `conversation_only`. The compatibility `semantic_backend`
field aggregates the semantic legs, while `backends` retains partial failures.
Doctor accepts Wiki semantic retrieval for `--require-semantic`, but reports
degradation if another enabled source failed. A disabled layer is not a failure.

## Relevance and usage accounting

Jev decides relevance at the merged recall boundary by default (`XKB_JEV_DECIDE=1`).
The existing cosine gates still apply earlier in retrieval. `XKB_JEV_DECIDE=0`
disables the final judge; optional shadow comparisons then provide observations
for `xkb_jev_shadow_report.py`. A missing/failed judge is reported and retains
the candidates; it is never counted as a negative verdict.

`recall_usage` records one observation per namespace and evidence ID per call:

| Counter | Meaning |
| --- | --- |
| `considered_count` | Reached the merged recall boundary, after upstream retrieval gates. |
| `judged_count` | Received a decisive verdict: at least one positive leg, or every leg explicitly negative. |
| `relevant_count` | At least one leg passed the final judge's relevance floor. |
| `returned_count` | Survived filtering, ranking and the final limit into the recall packet. |

Repeated legs with the same ID do not inflate counts. A missing verdict on one
leg prevents treating a partly judged record as rejected. Returned evidence is
not proof of network receipt, hook injection, answer citation or task success.

Demotion requires at least five explicit judged observations and no positive
verdict in that namespace. One positive verdict lifts it immediately, even if
the result limit prevents returning the item. Unknown verdicts neither add
rejections nor erase prior verdicts. Demotion affects rank, never deletes data.
The unused gain/EMA experiment and its tests were retired; Git retains history.

Existing `knowledge_usage` is preserved and still measures legacy cosine passes.
Its historical `injected_count` and `ever_useful` names do **not** mean final
delivery or answer use. The `/v1/knowledge/cold` report labels that basis explicitly.
No historical count is backfilled into the new counters: current demotion starts
fresh. Inspect it with `python scripts/xkb_evict_report.py --namespace private --json`.
Rolling back the code leaves this additive SQLite table harmlessly in place.

## Check a real connection

```bash
python scripts/xkb_doctor.py --query "a topic in your library" --json
python scripts/xkb_doctor.py --query "a known source" --expect-id source-card-id
python scripts/xkb_doctor.py --query "a known source" --require-semantic --require-judge
```

Doctor starts the actual MCP process and verifies initialization, tool discovery
and a real tool call. It reports the selected local paths or remote endpoint,
namespace, actual search mode, judge status, result IDs, warnings and elapsed time.

- `ready`: the requested checks passed without the reported degradation.
- `degraded`: connection works, but retrieval uses a fallback or the judge is
  unavailable/disabled. This is not proof of semantic retrieval quality.
- `failed`: connection, expected evidence, or a requested capability failed.

Exit code is 0 for a working connection (including explicit degradation), 1 for
failure. Use `--expect-id`, `--require-semantic` and `--require-judge` for stricter
acceptance. An empty successful query alone does not verify a populated library.
Doctor verifies a **fresh subprocess**, not the tool list of an already-open
agent; restart/reconnect that agent after changing its MCP configuration.

## Repeatable evaluation

```bash
python scripts/xkb_eval.py --output recall-report.json
python -m unittest discover -s tests -p 'test_recall_transports.py'
python -m unittest discover -s tests -p 'test_recall_eval.py'
```

The bundled 24 cases use an isolated synthetic library with no inherited
credentials, remote endpoint or production data. They cover multiple subjects,
Chinese queries, both sides of conflicting evidence, cards plus wiki, namespaces,
no-answer queries and greetings. Each case calls the real MCP process. Reports
include expected-evidence recall, allowed-evidence precision, no-answer success,
duplicate evidence, latency, actual retrieval modes and judge availability.
Exit code is 1 if any case fails; transport failures count as failures, not empty
successful retrievals. HTTP/MCP parity and judge/auth behavior are covered by the
transport tests, with a deterministic judge double instead of a paid model.

This suite measures offline keyword/ACL/transport behavior, **not semantic model
quality**. The initial measured baseline is 20/24: all required evidence is found,
but three cases return extra keyword matches, and `好，收到` is not classified as
an acknowledgement. Those are visible failures, not relabelled successes. Keep
the labels intact when improving these behaviors.

After fixing punctuation in compound acknowledgements, the same unchanged cases
score 21/24: `好，收到` is now skipped, while the three extra-keyword-match cases
remain failures. `evals/recall-baseline.json` preserves the initial measurement.

The 2026-09-27 keyword correction passes **24/24 with those labels unchanged**;
`evals/recall-keyword-report.json` records the run. Keyword fallback searches
titles, summaries, tags and keywords, plus Wiki body text. IDs, filenames,
source URLs and ACL metadata are not content matches. Among ACL-allowed
candidates, complete query-term matches take precedence over partial matches;
if none are complete, partial matches remain available. Shared stopwords are
ignored. This preserves natural-language CJK fallback, but can omit useful
partial matches when a complete lexical match exists. It is a lexical policy,
not a replacement for semantic relevance evaluation.

The agent hook renders conversation evidence from `query`/`answer`, preserving
`trace_id` as provenance, as well as cards and wiki records from `title`/`summary`.
MCP availability alone does not establish automatic recall: verify that the
agent's actual `UserPromptSubmit` event invokes the XKB hook. The current
installer targets Claude Code settings; it does not install a Codex/Orca hook.

For a real-library quality evaluation, create labels from reviewed evidence:

```json
{
  "cases": [
    {"id": "known-answer", "query": "Your real question", "expected_ids": ["reviewed-id"]},
    {"id": "no-answer", "query": "An unrelated question", "expected_ids": []}
  ]
}
```

`allowed_ids` can list additional genuinely relevant evidence; it defaults to
`expected_ids`. Optional `namespace`, `limit` and `retrieval_mode` allow targeted
cases. The default `label_unit: "document"` measures recall and precision over
unique display IDs, so multiple sections of one allowed document do not reduce
precision. For section/namespace-specific evaluation, set `label_unit: "evidence"`
and use the shared `ev1:` evidence keys in `expected_ids` and `allowed_ids`.
Duplicate detection always uses evidence identity, regardless of label unit;
unidentified records fail explicitly. Reports include both IDs and evidence keys.
Include paraphrases, stale/current evidence, contradictions, multilingual
questions and genuinely unanswerable questions. Review labels independently of
the retrieval output; do not label results relevant merely because the system
returned them.

```bash
python scripts/xkb_eval.py --live --cases reviewed-cases.json --require-semantic --require-judge --output live-report.json
```

Live evaluation uses the configured library and providers and can incur provider
costs. Synthetic success cannot establish a production judge threshold; reserve
separate questions for validation rather than tuning and reporting on the same set.

## Jev input-limit audit (2026-09-27)

[TypeSafe's model reference](https://docs.typesafe.ai/models) documents 64k tokens
for state plus all questions, and 32k for state plus the longest question.
Question count and JSON byte count are not token counts. The XKB adapter trims
queries to 600 characters at the time of that audit and each candidate to 900 characters before adding
instructions; these are character caps, not a token-budget guarantee.

A read-only copy of the VPS conversation database was used to reproduce the
default `Seedance workflow` retrieval with the production providers. The actual
request contained 25 questions, 31,647 serialized bytes, a 24-character state,
9,695 total instruction characters and a longest question of 922 characters.
The provider returned HTTP 200, all 25 answers, and usage of **7,344 input tokens**
(444 output tokens). Requested model: `jev-1.13`; returned model:
`typesafe/jev-1.13-20260917`. This usage is provider-reported, not an independent
count with TypeSafe's tokenizer. No production conversation/usage rows were
changed by this measurement.

This successful sample is below the documented budgets. It does not establish
the cause of earlier HTTP 400 responses or guarantee that other payloads fit.
No fixed 8/25/30-question limit or batching change was introduced. Larger limits
can produce more input; any future batching must account for both total input
and state-plus-longest-question budgets, including instructions. A previous
small JSON payload producing a provider body-size error must not be described
as proof that the official context limit was reached.

Current implementation: conversational judge input is capped at 4,096 characters
to retain the bounded current utterance and all four context messages. Each
serialized request remains capped at 32 KiB; batches share a deadline. These
character and byte limits are not claims about the provider's token limits.
