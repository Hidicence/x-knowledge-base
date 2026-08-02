<p align="right">
  <strong>English</strong> · <a href="./README.zh.md">繁體中文</a>
</p>

<p align="center">
  <img src="./assets/readme/hero.svg" width="100%" alt="XKB — one knowledge layer your agents share: Claude Code, OpenClaw and Codex connect to a single local-first service over a shared plane of evidence cards, wiki topics, and conversation traces">
</p>

<p align="center">
  <a href="#quick-start"><strong>Quick start</strong></a> ·
  <a href="#how-it-works"><strong>How it works</strong></a> ·
  <a href="#the-nine-section-card"><strong>Card schema</strong></a> ·
  <a href="#four-ways-it-recalls"><strong>Recall layers</strong></a> ·
  <a href="#share-it-across-agents"><strong>Share across agents</strong></a> ·
  <a href="./docs/data-flow.md"><strong>Privacy</strong></a>
</p>

<p align="center">
  <a href="./LICENSE"><img alt="License: PolyForm Noncommercial 1.0.0" src="https://img.shields.io/badge/license-PolyForm%20Noncommercial%201.0.0-E07A3F"></a>
  <img alt="Python 3.10+" src="https://img.shields.io/badge/python-3.10%2B-3776AB">
  <img alt="Local-first" src="https://img.shields.io/badge/data-local--first-59D8C8">
</p>

## Knowledge should not disappear after you save it

Bookmarks, notes, transcripts, papers and repositories pile up. Saving them was never the hard part — getting the right one back, with its evidence, while you are actually working, is.

**XKB is a local-first knowledge lifecycle.** It turns many kinds of source into one structured card format, retrieves them semantically, distils the durable parts into a human-readable wiki, and surfaces what matters during a conversation.

And because your agents keep starting cold, it now serves all of that through **one shared API** — so Claude Code, OpenClaw and Codex read the same memory instead of each keeping their own.

---

## What a recall actually returns

```console
$ curl -s localhost:18972/v1/recall -H "Authorization: Bearer $TOKEN" \
       -H 'Content-Type: application/json' --data-binary @query.json
```

```jsonc
{
  "retrieval_mode": "xbrain_hybrid",       // it really ran vector search
  "count": 5,
  "dropped_as_irrelevant": 5,              // and threw away half as not relevant
  "records": [
    { "record_type": "knowledge_chunk",
      "score": 0.604,                      // measured cosine, not a rank score
      "rank_score": 0.888,                 // what the backend ranked on
      "source_url": "https://…" }
  ],
  "warnings": ["5 semantic results dropped below the relevance floor"]
}
```

Ask something your library has nothing on and it says so, instead of returning the ten least-bad rows:

```jsonc
{ "count": 0, "retrieval_mode": "keyword_fallback",
  "warnings": ["semantic results found but 10 dropped below the relevance floor"] }
```

A silent empty result and a broken retrieval backend look identical from the outside. Telling them apart is most of what makes a knowledge base trustworthy.

---

## How it works

```text
Sources
  local notes · X bookmarks · YouTube · GitHub · PDF / PubMed · conversations
       │
       ▼
One card contract
  source adapters → scripts/_card_prompt.py → scripts/_llm.py
       │
       ▼
Evidence cards
  nine sections · source link · claim level · bilingual summary
       │
       ├──────────────►  hybrid retrieval (vector + keyword + RRF)
       ├──────────────►  flat vector index          ── fallback
       ├──────────────►  keyword index              ── fallback
       │
       ▼
Absorb gate
  cards + conversations → staging → review → durable wiki topics
       │
       ▼
Recall
  four layers, measured relevance, answers carry their sources
       │
       ▼
Knowledge service
  one HTTP API · token-scoped · shared by every connected agent
```

Each stage is a script you can run alone; nothing is a black box.

### The nine-section card

Every supported source produces the same structure, so retrieval has a stable unit instead of a pile of source-specific summaries:

1. Core question and conclusion
2. **Claim level** — `Attested`, `Scholarship`, or `Inference`
3. Key arguments
4. False friends — terms whose technical meaning differs from common usage
5. Surprises
6. Relationship to existing knowledge
7. Bilingual summary, used by the search index
8. Value to the reader
9. Original source and links

Image-bearing sources get a tenth **Media Evidence** section with OCR and vision notes via `scripts/media_ingest.py`.

Claim level is the part that pays off later: when a card resurfaces months on, you can see whether it was something demonstrated, something published, or something inferred.

### Four ways it recalls

Recall is not one search. Depending on what you are doing, XKB draws on different layers:

| Layer | Looks in | Answers |
| --- | --- | --- |
| **Continuity** | wiki topics, daily memory | *What did we already decide or establish?* |
| **Associative** | evidence cards, bookmarks | *What have I collected that touches this?* |
| **Contrarian** | wiki, memory | *What argues against this — limits, conflicts, past failures?* |
| **Action** | scripts, roadmaps, TODO sections | *What can I run, and what was next?* |

The contrarian layer exists because a knowledge base that only ever agrees with you is a liability. When you are converging fast on a plan, it surfaces the counter-evidence you already saved.

---

## Why the results are different

### Relevance is measured, not assumed

Hybrid search returns a **rank** score: it says *this came first*, not *this is relevant*. On a real library the top hit sits near `0.88` whether or not anything on the subject exists — measured here, an off-topic question scored `0.863` against an on-topic one at `0.862`. XKB recomputes the true query/document cosine and drops what falls below the floor, so an unrelated question returns nothing and costs nothing.

### Distillation is gated, in both directions

Cards are evidence; wiki topics are understanding. Nothing crosses that line by itself. The absorb gate scores topical fit and redundancy and **fails closed** — if it cannot run, nothing is absorbed. Conversations captured from agents become *candidates*, never knowledge, until they clear review.

### Knowledge is allowed to age out

Retrieval records whether each item, once surfaced, was ever relevant enough to use. Anything repeatedly retrieved that never clears the floor is reported as a retirement candidate — reported only. Provenance is the product; nothing is deleted to keep things tidy.

---

## Quick start

The smallest useful path ingests local Markdown and searches it. No X cookies, no Postgres, no cron.

**1 · Clone and create a private workspace**

```bash
git clone https://github.com/Hidicence/x-knowledge-base.git
cd x-knowledge-base
python3 scripts/xkb_init.py          # writes .xkb.json, gitignored
```

**2 · Point it at a model**

```bash
export LLM_API_URL="https://your-provider.example/v1"
export LLM_API_KEY="your-key"
export LLM_MODEL="your-model"
```

> Credentials are runtime state, never repository content. XKB is provider-agnostic; local models work.

**3 · Ingest, index, ask**

```bash
python3 scripts/local_ingest.py demo/sample-notes --category learning --limit 3
bash    scripts/build_search_index.sh
python3 scripts/xkb_ask.py "What patterns appear across these notes?"
```

Cards and indexes are written to your workspace, never into this repository.

---

## Add sources

One card contract, many inputs:

```bash
python3 scripts/local_ingest.py ~/notes --category research    # Markdown / text
python3 scripts/pdf_ingest.py paper.pdf                        # PDF / papers
python3 scripts/fetch_youtube_playlist.py <playlist-url>       # transcripts
python3 scripts/fetch_github_repos.py                          # stars and forks
python3 scripts/media_ingest.py <card.md> --limit 4            # image OCR + vision
```

X/Twitter bookmark import, PubMed, and a queue-backed enrichment worker are included; see [`SKILL.md`](./SKILL.md).

## Distil into durable knowledge

```bash
python3 scripts/absorb_gate_semantic.py --review               # what would be absorbed
python3 scripts/sync_cards_to_wiki.py --apply                  # cards → wiki topics
python3 scripts/distill_memory_to_wiki.py --stage              # conversations → staging
python3 scripts/xkb_review.py --list                           # review the queue
```

Nothing reaches a wiki topic without passing the gate, and you can always see what it decided and why.

---

## Share it across agents

Start the service, then install the hook. Recall and capture become automatic — the agent is not trusted to remember to call anything.

```bash
python3 scripts/xkb_knowledge_service.py          # 127.0.0.1:18972
python3 scripts/xkb_install_agent_hook.py --install
```

```text
UserPromptSubmit  →  turns/start     →  recalled knowledge injected as context
Stop              →  turns/complete  →  the exchange becomes L1 evidence
```

Install is idempotent, leaves your other hooks alone, and writes settings atomically. `--uninstall` removes exactly what it added.

**Reading fails open.** If the service is unreachable the hook injects nothing and exits quietly; it never blocks a conversation. That is the deliberate mirror of the absorb gate, where failure must block — writing bad knowledge is worse than writing none.

### From another machine

The service binds loopback and refuses to start on a public interface without tokens. To reach it from a laptop, tunnel rather than expose:

```bash
ssh -N -L 18972:127.0.0.1:18972 your-server
```

Identity comes from a bearer token that pins a namespace and scopes; a request claiming a different namespace is refused, not quietly retargeted. With no token configured the service stays anonymous, which is the single-user default. Full API in [`docs/xkb-memory-service.md`](./docs/xkb-memory-service.md).

---

## Runtimes

Start with the smallest mode that solves your problem.

| Mode | Retrieval | Needs |
| --- | --- | --- |
| **Lite** | keyword over `search_index.json` | Python + an LLM |
| **Enhanced** | flat vector index, semantic recall | an embeddings provider (Gemini, OpenAI, or local Ollama) |
| **Full** | XBrain/GBrain hybrid + RRF | Postgres + pgvector |

Recall degrades in that order and always tells you which one ran.

---

## What this is not

Being explicit is cheaper than disappointment:

- **Not a hosted product.** It runs on your machine, against your files.
- **Not automatic.** Nothing is promoted into the wiki, and nothing is retired from it, without you.
- **Not an agent framework.** It stores and returns knowledge; your agent does the thinking.
- **Early in places.** The knowledge service, cross-agent hooks and retirement signal are new. Those interfaces will move; the card and wiki layers are older and steadier.

Cloud embeddings mean queries leave your machine; set `EMBEDDING_PROVIDER=ollama` to keep everything local. See [`docs/data-flow.md`](./docs/data-flow.md) for exactly what is sent where.

---

## Documentation

| Document | Contents |
| --- | --- |
| [`SKILL.md`](./SKILL.md) | Full command surface |
| [`docs/xkb-memory-service.md`](./docs/xkb-memory-service.md) | Service API, auth, agent hooks, relevance floor |
| [`docs/data-flow.md`](./docs/data-flow.md) | What leaves your machine, and how to stop it |
| [`docs/RUNTIME_PATHS.md`](./docs/RUNTIME_PATHS.md) | Where code ends and your data begins |
| [`wiki/WIKI-SCHEMA.md`](./wiki/WIKI-SCHEMA.md) | Wiki topic contract |
| [`docs/xkb-vnext-roadmap-draft.md`](./docs/xkb-vnext-roadmap-draft.md) | Where this is going |

## License

[PolyForm Noncommercial 1.0.0](./LICENSE) — free for noncommercial use.
