# x-knowledge-base

Turn X/Twitter bookmarks into a reusable knowledge base for OpenClaw.

`x-knowledge-base` is not just a bookmark saver.
It is designed to help you:

- collect valuable X bookmarks and related context
- turn them into reusable knowledge cards
- search and recall them later in real conversations
- prepare the library for future NotebookLM / cloud workflows

## What this skill does

### 1. Capture bookmarks
- fetch new X/Twitter bookmarks
- deduplicate by tweet id
- enrich with thread / author additions / external links / GitHub context

### 2. Turn bookmarks into knowledge cards
- generate structured summaries
- auto-categorize into topics
- add cross-links between related cards
- keep a searchable local knowledge base in Markdown

### 3. Support conversation recall
- search existing bookmark knowledge
- rank relevant cards for the current topic
- provide chat-ready recall output
- help an agent proactively bring back useful saved knowledge

### 4. Prepare for future library workflows
- export for NotebookLM
- sync to Google Drive
- support future semantic / vector recall upgrades

## Why this exists

Modern knowledge work creates a constant sense of information overload.
You save things because they feel valuable, but later you often forget what you saved or why it mattered.

This skill is built around a simple idea:

> bookmarks should become reusable knowledge, not just a graveyard of links.

## Current focus: conversation recall

The current vNext direction focuses on **conversation recall**:

- when a conversation needs examples, workflows, decisions, or context
- the skill can help surface previously saved bookmark knowledge
- the goal is to make saved knowledge reappear at the right moment

See:
- `references/conversation-recall.md`

## Main entry points

### Full ingest + summarize flow

```bash
bash scripts/fetch_and_summarize.sh
```

### Search existing bookmarks

```bash
bash scripts/search_bookmarks.sh "openclaw seo"
```

### Conversation recall

```bash
python3 scripts/recall_for_conversation.py "OpenClaw workflow agent memory"
python3 scripts/recall_for_conversation.py "AI SEO 案例" --format chat
```

### Export NotebookLM

```bash
python3 scripts/export_notebooklm.py
```

### Sync to Drive

```bash
bash scripts/sync_to_drive.sh
```

## Repository structure

```text
x-knowledge-base/
├── SKILL.md
├── assets/
├── config/
├── evals/
├── references/
├── scripts/
└── tools/
```

## Roadmap

### v1
- working bookmark ingestion
- local knowledge cards
- search index
- conversation recall rules v1
- public reference docs and examples

### v2
- better summary quality
- better source URL coverage
- stronger ranking and filtering
- better recall usefulness in real chat

### v3
- semantic / vector recall
- better query understanding
- stronger relevance matching across different wording

### v4
- NotebookLM / cloud library workflow
- more polished library sync and source management

## Notes

This repository is the public skill repo.
The working OpenClaw workspace version may evolve faster before being synced here.

If you want to understand the design rather than only copy scripts, start with:
- `SKILL.md`
- `references/conversation-recall.md`
