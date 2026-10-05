# Shared recall and connection checks

XKB's intended behavior is proactive reuse during conversation. A user can reveal
a need by stating a plan, describing a difficulty, adding a constraint, or changing
direction. Question answering alone does not validate that behavior.

## Intervention decisions and acceptance

Delivery resolves the current task in a separate request that sees only the
original utterance and bounded conversation, never candidate evidence. It records
at most two information needs and explicit constraints. The service overlaps
this preparation with retrieval IO when judging is configured. The selector then
receives that fixed task and may return only paragraph references; it cannot
rewrite the task's object or direction. Jev continues to judge merged relevance,
without a separate need-score threshold or boolean gate.

Repeat/recheck requests and new obstacles can qualify without a question mark.
An explicit current request to restate or summarize remains a need even after
completion; closing the earlier work does not cancel the new request.
Executing already agreed steps is distinct from requesting their explanation;
the task planner must name missing information, not merely the action to perform.
An empty prepared task means quiet: completion, declined advice and execution
of an agreed plan normally require no selection call. A failed preparation is
unknown, not quiet. `intervention.need` is boolean/null and the task's needs and
constraints remain inspectable even when paragraph selection fails.

The selector returns only validated integer references. The service copies the
original paragraphs locally, preserving list markers, conditions and related
steps. Wiki keyword and semantic adapters apply paragraph bounds before losing
source text; semantic bookkeeping removal precedes these bounds. Memory sections
retain newlines. Truncated human-CLI fallback snippets are labelled and withheld
from automatic delivery; normal JSON chunk bodies pass through unchanged. Heading-only, ordinal-only and partial trailing paragraphs are not eligible.
The two-paragraph budget permits complementary steps for the same need. Duplicate
assignments are collapsed; sharing a need identifier does not discard a distinct
step. A paragraph supporting two needs is rendered only once.
The request keeps separate bounds for the current utterance (1,200 characters),
history (four messages of 600), and each source/title/body (120/80/1,600).
The default selection model is `claude-sonnet-4-6`, independently configurable
with `XKB_DELIVERY_MODEL` through the existing direct LLM gateway credentials.
Preparation and selection each use a 512-output-token budget, a 12-second
request timeout and one attempt; there is no CLI fallback or automatic retry.
Merged Jev relevance retains its six-second timeout and serialized 32-KiB guard. These socket timeouts
are not a strict end-to-end wall-clock SLA.
Within one relevance call, identical effective question text shares one verdict,
which is mapped back to every original candidate key. Different constraints or
other differences visible to the judge remain separate questions. This does not
merge retrieval support or cache results across requests; an unavailable shared
verdict leaves all its candidate aliases unknown.

Incomplete relevance judgement withholds all automatic suggestions and skips
paragraph selection (an overlapped task preparation may already have run); the surviving judged subset cannot displace a better
procedure from a failed batch. An unknown need or a failed selector for a positive
need also withholds suggestions
and reports degraded. A known quiet task never starts paragraph selection. All candidate records remain inspectable. The delivery copy
contains only selected paragraphs, with source identity/provenance retained.
Neither presentation nor silence creates an adoption/rejection preference.

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
branch degrades overall quality. Relevance and intervention still judge the
original utterance with its dialogue; sentence splitting never decides intent.
This costs up to three retrievals and remains a bounded heuristic: needs buried
in a middle sentence or written without sentence boundaries can still be missed.

The public fixed-evidence suites can be run separately from retrieval. They use
real provider calls only with explicit `--live --intervention`; offline tests
never make these purchases. Create `tmp/` first and choose a new receipt name:

```bash
python scripts/xkb_eval.py --live --intervention --cases evals/intervention-cases.json --output tmp/intervention-trial.json
python scripts/xkb_eval.py --live --intervention --cases evals/intervention-applicability-cases.json --output tmp/applicability-trial.json
python scripts/xkb_eval.py --live --intervention --cases evals/intervention-selection-cases.json --output tmp/selection-trial.json
```

Receipts preserve source/effective conversation, frozen evidence, decision packets
and pending progress before each call. Existing outputs are refused; interrupted
runs must be inspected rather than silently retried. Strict acceptance requires
all cases to pass, including missing/unexpected sources, overdelivery and degraded
judgements. A fixed-evidence success does not prove retrieval, downstream agent
behavior, or production readiness. Live-library and cached-candidate replay
results must remain separate, and failures must not be relabelled after a run.

Evaluation must inspect delivered content, not just document IDs. Delivery cases
reject content-free excerpts and may declare `required_excerpt_patterns` and
`forbidden_excerpt_patterns` (regular expressions) before running. Those checks
ignore self-derived provenance footnotes, which are not substantive data-lineage
instructions. They catch known failures but cannot establish semantic usefulness: review the actual
quote against the current task and constraints as well. A page introduction is
not a procedure. `timing_ms` reports retrieval, task-preparation wait, conversation merge, relevance,
fusion/usage, delivery and total durations; each retrieval backend also reports
its own elapsed time.

The initial grounded-selection replay demonstrated why this matters: ID checks
reported 32/32 fixed and 11/12 cached passes, but independent excerpt review found
two ordinal-only deliveries and three carbon page introductions. That result was
rejected. A subsequent run was stopped on this finding before its new held-out
group; partial outcomes and uncertain pending requests were retained. The fixes
preserve paragraphs and retrieve a second section, rather than changing those
old labels or treating document hits as useful delivery.

The subsequent paragraph-preserving trial scored **39/52**: fixed 28/32,
full regression 6/12 and new wordings 5/8. Twelve failures were degraded;
the remaining ready-state failure delivered timestamps/owners/change history
when the need specifically required original data sources and versions. This
motivated the explicit-term companion section, not a relabelling of the miss.
A later trial also exposed a `0.49` versus `0.50` need veto and a dated status
promoted during partial relevance availability. Categorical intent and
complete-comparison admission were tested against those two mechanisms. Their earlier
outcomes remain failures; regression tests do not retroactively change them.

The hook now preserves whole selected entries under its 4,000-character budget
instead of cutting each paragraph at 600 characters and potentially losing a
late condition. A separate replay passed 52/52 saved live packets through fresh
hook processes, HTTP session opening and turn-start capture, with zero provider
calls. This proves context transport, not new retrieval or a generated answer's
usefulness. No automatic invocation by Codex/Orca is established by this test.

The categorical-intent trial reached **54/56** (fixed 32/32, full regression
22/24). One intent request remained unknown. The other failure had all relevance
verdicts and the correct queue principle as the first candidate, but the selector
rewrote "reduce unchecked backlog" into "resume production" from an old status
trace. A boolean admission check cannot prevent that direction change. The
current independent task preparation and selection-only response schema replace
that design; the 54/56 result remains a failed historical trial, not acceptance
for the new implementation.

The first independent-task trial scored **45/56** (fixed 30/32, full regression
15/24). Nine cases degraded. Two ready-state misses were independently traced to
different boundaries: the selector returned both complementary steps but local
deduplication discarded the second because it shared the same need; and the task
planner incorrectly treated an explicit post-completion restatement request as
quiet. The current implementation deduplicates assignments instead of goals and
gives current information requests precedence over completion. These fixes do
not change the old labels or convert the earlier misses into passes.

The next trial scored **49/56** by frozen automated criteria (fixed 31/32,
full regression 18/24): six degraded cases and one unnecessary interruption
while executing an agreed plan. Independent excerpt review additionally found
three cases with repetitive backlog-control paragraphs; those ID-level passes
do not establish nonredundant delivery. The task prompt now distinguishes
executing an agreed action from requesting missing information. Separately,
inspection of the independent-task receipts found 1,075 relevance questions but
only 815 unique effective questions within their respective conversation states.
The adapter now shares identical questions within a call without changing
candidate identity, retrieval support, missing-answer handling or byte limits.

The bounded-relevance trial scored **50/56** on frozen automated criteria:
fixed 30/32 and full regression 20/24, with all six misses degraded. Independent
review of every task and delivered paragraph still found three ready-state
quality problems: repetitive backlog-control advice, project-specific video
restrictions attached to a reusable identity method, and unrelated narrative
framing attached to another identity method. Thus 50 automated passes are not
50 fully satisfactory interventions, and that trial was **not accepted for
production**. The agreed-execution case stayed quiet and successful lineage
cases contained source/version procedures in their substantive text.

The same 24 live-library cases now submitted 815 distinct Jev questions, with no
repeated effective question within a state; the earlier 1,075 included 260
duplicates. The largest serialized Jev body was 32,713 bytes. There were 142
recorded provider requests overall, eight with transport/HTTP failures; these
affected six cases. Full-retrieval median was **12,423.5 ms** and nearest-rank p95
**28,851 ms**, so the 12,000-ms median target remains unmet. These sequential
measurements do not establish a causal latency improvement.

Final engineering checks passed **652 Windows tests** (17 skipped, four existing
warnings) and **170 focused Linux tests** across eleven suites, including reruns
of the changed boundaries. A fresh hook-process/HTTP replay preserved all **56/56**
saved packets with zero provider calls. This confirms context transport, not new
retrieval, automatic Codex/Orca invocation, or downstream answer quality. Cold
code review found no outstanding implementation defect. The candidate remained
separate from production pending another live check and severity review of the
semantic and availability limitations.

### Release recheck (2026-10-05)

A fresh run of the same frozen 56 cases, without runtime changes or relabelling,
scored **53/56**: fixed evidence 31/32 and full retrieval 22/24. The failures
remain in the denominator:

- One task-planning response contained invalid JSON; delivery conservatively
  degraded and emitted no suggestions.
- One Jev batch timed out; incomplete relevance judgement withheld suggestions.
- One carbon handoff case returned relevant governance guidance but omitted the
  specifically requested per-figure sources and versions. An embedding HTTP 429
  also affected one retrieval branch, while another semantic branch succeeded.
  This is a real content-coverage miss, not solely a connection failure.

Independent review of all tasks and excerpts found no critical or high-severity
defect. All 45 selected quotes matched their candidate originals exactly.
Declined advice, agreed execution and closed topics produced no unsolicited
suggestions. Occasional repeated advice and optional narrative details remain
non-blocking precision limitations; the carbon coverage miss remains an open
quality issue. The release assessment permits these disclosed limitations; it
does **not** claim the stricter all-cases benchmark has passed.

Windows again passed 652 tests (17 skipped), Linux passed the 170 focused tests,
and all 56 saved packets passed fresh hook/HTTP replay. Full-retrieval median was
**11,002 ms**, nearest-rank p95 **22,257 ms**. Of 145 recorded decision-provider
requests, 144 completed at transport level and one timed out; the invalid JSON
was among the completed responses. Embedding traffic is outside that request
count. The largest serialized Jev body remained 32,713 bytes. These results do
not establish that every earlier failure originated at the provider server.

## Follow-up trials (2026-10-05): still experimental

Trials used isolated code checkouts, read-only production indexes and disposable
SQLite snapshots. Full retrieval cases exercised fresh MCP initialize/list/call
against the temporary HTTP service. Raw case packets, provider responses and
pre-call archive/suite hashes are retained privately under `tmp/`; no production
code or knowledge was changed. These are sequential diagnostic trials, not a
randomized comparison of versions.

| Trial | Fixed evidence | Full retrieval / cached replay |
| --- | --- | --- |
| Initial intervention gate | 15/16 | Existing 5/8; new 12/16 |
| Repeat-request correction | 24/24 | Cached replay 18/24 |
| Wiki candidate correction | — | Existing 1/4; new 6/8 |
| Applicability judgement | 24/24 | Cached replay 9/12; fresh regression 3/4 |
| Bounded topic-shift retrieval | 24/24 | Fresh regression 4/4; new 6/8 |

The Wiki-candidate trial's three regression failures were unavailable relevance
judgements. Cached replay preserves those failures; it does not retest provider
availability. None were removed from the reported denominators.

In the final topic-shift trial, all five new information needs found a labelled
source among candidates, but only four delivered one. All three quiet cases
stayed quiet. Strict acceptance was **6/8**: one case preferred an old project
status trace over the reusable guidance, and another added an unrelated repository
sync note to carbon-data guidance. These are real selection failures despite
complete provider responses. The new eight cases had median **12,470 ms** and
nearest-rank p95 **15,668 ms**; the median misses the predeclared 12,000 ms target.
All 71 recorded Jev calls completed; largest serialized body was 32,764 bytes.
That observed byte count is not an official token-limit claim.

Windows regression validation passed **633 tests**, with 17 skipped and four
existing deprecation warnings. The seven focused Linux suites passed **126
tests**. Cold review found and verified the concurrent cache-initialization fix.
These checks validate implementation boundaries, not model accuracy. The trial
does not establish the answering agent's actual use of suggestions or user benefit,
and it has **not met production acceptance**. Fixed evidence scores must not be
presented as end-to-end success.

## First delivery trial: not accepted for production

The 2026-10-05 trial ran an isolated checkout against read-only production indexes
and a disposable SQLite snapshot. Each case used a fresh MCP initialize/list/call
against the trial HTTP service. Frozen labels and raw responses remain private.
No production deployment or knowledge write was performed for this trial.

| Measure | Existing scenarios | New held-out scenarios |
| --- | --- | --- |
| Strict candidate acceptance | 2/9 | 0/8 |
| Strict delivery acceptance | 4/9 | 2/8 |
| Labelled useful-evidence coverage in delivery | 7/7 | 5/5 |
| Quiet scenarios with no delivered suggestions | 2/2 | 1/3 |

Coverage means at least one labelled core source appeared; it does not establish
that every delivered item helped. Strict acceptance includes unexpected-source
checks. Later content review found some unlabelled equivalent advice, but also
weakly applicable snippets and paraphrased duplicate advice. Labels were not
changed to turn those failures into passes. These counts are within this trial;
they are not a controlled improvement estimate over an earlier live run.

The frozen topic-switch example delivered carbon evidence without video advice.
However, two held-out completion statements still received suggestions. The judge
returned complete verdicts, so those were decision failures, not outages. Held-out
latency ranged from 5.8 to 15.3 seconds (median 9.8 seconds). This trial measured
packets, not the answering agent's actual use or the user's perceived usefulness.

This first trial established the need for a separate current-turn intervention
decision and new held-out conversations, including completion, explicit repeat
requests and changed constraints. Raising the relevance threshold to fit those
known failures would not validate that design.

## Experimental delivery contract

The packet preserves all returned candidates in `records`, while `delivery`
describes proactive suggestions. Current-turn Jev verdicts order judged evidence
after retrieval-leg fusion; unknown verdicts retain their retrieval positions.
The delivery policy reviews at most four candidates scoring at least 0.5, selects
at most two original paragraphs after current-need, applicability and grounding checks,
removes exact repeated claim text, and marks claims already fully
present in a recent assistant reply. Current-turn judging considers novelty,
explicit repeat requests and new applicability; prior presentation alone never
vetoes a positive current judgement. This is an experimental decision boundary, not a calibrated
probability. The broader candidate floor remains 0.1. Partial/unavailable judging
stays visible; all candidates remain inspectable, while the entire proactive
delivery is withheld until relevance comparison is complete.
Presentation does not imply acceptance, and silence does not imply rejection.

Both the hook and MCP presentation use this delivery decision. Existing candidate
evaluation remains unchanged. Set a case's `surface` to `delivery` to separately
evaluate intervention precision, missed useful suggestions and quiet turns.
Freeze new conversational scenarios before provider calls; retain failures and
report candidate coverage separately from delivery success. Recent dialogue is
the current state input; this iteration does not infer a persistent goal registry.

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
