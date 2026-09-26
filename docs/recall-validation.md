# Shared recall and connection checks

MCP (`xkb_recall`), the default `recall_router.py` and `xkb_ask.py` CLIs,
and HTTP (`POST /v1/recall`) use
`Store.knowledge_recall`: cards, wiki and conversation evidence go through the
same namespace checks, relevance judge and ranking. `xkb_evidence.py` defines
the title/body/source fallbacks used by the service, judge, hook and ask client.
Original evidence fields remain intact in the packet; MCP preserves it.

The old continuity/action/contrarian presentation is available through
`recall_router.py --legacy-router`; its Python `route()` API and classification-only
`--dry-run` retain their previous behavior. `xkb_ask.py --legacy-search` selects
its previous standalone wiki/card search. Existing per-layer options (`--no-wiki`,
`--no-cards`, `--no-gbrain`, or non-default `--max-wiki`/`--max-cards`) also select
that legacy search. They do not change the shared service's retrieval policy.
Both default CLIs accept `--env-file` and `--limit` (1–50).

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
cases. Include paraphrases, stale/current evidence, contradictions, multilingual
questions and genuinely unanswerable questions. Review labels independently of
the retrieval output; do not label results relevant merely because the system
returned them.

```bash
python scripts/xkb_eval.py --live --cases reviewed-cases.json --require-semantic --require-judge --output live-report.json
```

Live evaluation uses the configured library and providers and can incur provider
costs. Synthetic success cannot establish a production judge threshold; reserve
separate questions for validation rather than tuning and reporting on the same set.
