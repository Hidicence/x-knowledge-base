# Shared recall and connection checks

XKB's intended behavior is proactive reuse during conversation. A user can reveal
a need by stating a plan, describing a difficulty, adding a constraint, or changing
direction. Question answering alone does not validate that behavior.

## Proactive delivery

The suggestions are read by the agent answering the turn, not by the user. That
agent already sees the whole conversation and is the strongest judge of whether
a piece of knowledge applies. XKB therefore does only what the agent cannot:
search the library and keep the injected context small.

Per turn: up to three retrieval queries (the utterance, its bounded dialogue
context and the last substantive sentence) run concurrently; Jev judges the
merged candidates once against the utterance with its dialogue; `xkb_delivery`
then applies local rules and calls no generation model:

- verdict at least 0.5 (`DELIVERY_FLOOR`), at most three items;
- nothing already suggested in the session's last 10 turns
  (`RECENT_SESSION_TURNS`), which are still in the answering agent's context;
  older suggestions may return after compaction or an explicit recap request.
  Wiki sections, notes and traces also store a fingerprint of their normalized
  body, so an edited source can be suggested again. Cards do not: each
  retrieval returns whichever chunk matched, so their text is not a version,
  and a card is blocked by evidence key alone;
- none of the session's own conversation traces from those same recent turns,
  excluded in the conversation query itself so that they cannot fill its
  500-row window or result limit; earlier decisions in a long session remain
  recallable;
- no exact duplicate text and nothing already quoted in a recent assistant reply;
- display-only truncated snippets are never injected;
- a title with no letters (bookmark cards titled by tweet ID) is replaced by the
  first sentence of the body; long bodies keep whole paragraphs and are labelled
  as excerpts.

Delivery chooses from every candidate that passed ACL and per-layer quotas, not
only from the response's `limit`: if the top ten were already suggested, the
eleventh relevant candidate can still be delivered. `records` keeps its limit.

A partial judgement still delivers its positive verdicts and marks the packet
degraded; no judgement delivers nothing and marks it degraded. The hook header
tells the agent to ignore unrelated items without mentioning them. All
candidates remain inspectable in `records`. Presentation does not imply
acceptance, and silence does not imply rejection.

The hook skips only sessions explicitly marked unattended: interactive Claude
Code sets `CLAUDE_CODE_SESSION_ATTENDED=1`, while `claude -p` scheduled agents
set `0`. Those turns neither recall nor write a conversation trace. The entry
point is not used as a guess, because interactive front ends built on the
Agent SDK also report `sdk-*` entry points. Suggestions picked from beyond the
response limit count as returned in usage accounting and `source_memory_ids`.

When a turn completes, `delivery_outcomes` records, for each suggestion, the
share of its excerpt terms that reappear in the answer.
`python scripts/xkb_delivery_report.py --days 7` summarizes it. This is a
lexical proxy: answers on the same topic share words, so read the distribution
and the relatively unused repeated items, not a single threshold.

### Why the generation-model selector was removed (2026-10-06)

From 2026-10-05 a separate model call (default `claude-sonnet-4-6` on the
paid LLM gateway) first resolved the turn's information needs, and a second
selected at most two paragraphs. Its fixed-evidence and live trials remain in the
Git history of this file before 2026-10-06. In the first day
of real use (26 turns) the whole recall took a median 12.8 s per turn, both
calls were billed every turn, the same advice was injected three times in one
session and the agent's own earlier replies came back as knowledge.

Recomputing those 26 stored turns with Jev verdicts and the local rules above,
at no provider cost: on substantive turns the Sonnet selection was almost always
within Jev's top three; the repeats and self-echoes disappeared; what Sonnet
added was staying quiet on turns such as "好 直接送", where the local rules
instead deliver one to three items for the agent to ignore. That trade (a few
hundred characters of context against two paid calls and several seconds on
every turn) is why the selector was replaced rather than kept behind a switch.

## Retrieval breadth

In shared recall with Jev enabled, Wiki retrieval now supplies count-bounded
nearest candidates before relevance judging. Its legacy 0.65 cosine cutoff no
longer removes natural-language candidates before Jev can see them. Legacy callers
and `XKB_JEV_DECIDE=0` retain their existing cutoff. Shared judging can inspect
two distinct sections per document, with up to 2,000 characters per excerpt.
For leading Wiki documents the companion slot prefers the strongest explicit-term
heading match after the first semantic section, retaining its real cosine score;
legacy callers keep one section and 200 characters. Section identities prevent
an introduction from erasing an actionable paragraph from the same document. Keyword fallback operates per
missing layer, so a Wiki hit cannot suppress an exact card while card semantic
retrieval is unavailable. Namespace checks and per-layer budgets still apply.

Retrieval keeps the original utterance, its bounded dialogue context, and the
last substantive sentence as at most three distinct queries. Each receives its
own candidate budget, so closing an old topic cannot consume the new topic's
entire retrieval budget. These read branches and their card/Wiki reads run concurrently; vector cache
initialization is synchronized. All branch diagnostics are retained, and a failed
branch degrades overall quality. Relevance is still judged against the
original utterance with its dialogue; sentence splitting never decides intent.
This costs up to three retrievals and remains a bounded heuristic: needs buried
in a middle sentence or written without sentence boundaries can still be missed.

## Conversation input

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
The prompt hook allows 40 seconds for turn-start recall, matching the HTTP recall
client, while ordinary session/capture requests retain their 6-second timeout.
`XKB_HOOK_RECALL_TIMEOUT` overrides recall; `XKB_HOOK_TIMEOUT` overrides both when
no recall-specific value is supplied. The installer gives the hook 50 seconds
overall. Existing installations need their XKB hook entries refreshed as well as
the script update; a shorter outer timeout still terminates the hook early.

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
Legacy cosine gates remain on legacy callers; shared Wiki candidates reach the merged judge under the count budget. `XKB_JEV_DECIDE=0`
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
