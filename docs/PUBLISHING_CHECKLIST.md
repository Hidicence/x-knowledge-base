# XKB Publishing Checklist

Use this before publishing XKB to a skill marketplace, GitHub release, or public package.

## 1. Runtime data separation

- [ ] `wiki/` inside the skill contains only schema/readme/sample files.
- [ ] private wiki topics live under `memory/x-knowledge-base/wiki/topics/`.
- [ ] staging files live under `memory/x-knowledge-base/wiki/_staging/`.
- [ ] generated graph data is not included.
- [ ] real `.env` files are not included.

## 2. Build/cache cleanup

- [ ] No `.next/` directories.
- [ ] No `node_modules/` directories.
- [ ] No `__pycache__/` directories.
- [ ] No `*.pyc` files.
- [ ] Package size is sane. Target: under 10–20MB unless intentionally bundling demo assets.

## 3. Docs accuracy

- [ ] `README.md` describes runtime data zone.
- [ ] `README.zh.md` describes runtime data zone.
- [ ] `SKILL.md` describes runtime data zone.
- [ ] Any `wiki/topics` examples are labeled as runtime paths, not shipped files.
- [ ] `docs/RUNTIME_PATHS.md` is up to date.

## 4. Safety scan

Run from workspace root:

```bash
cd skills/x-knowledge-base
find . -type f \
  -not -path './.git/*' \
  -not -path './demo/xkb-demo-ui/.next/*' \
  -not -path './demo/xkb-demo-ui/node_modules/*' \
  -print0 \
| xargs -0 grep -nE '/root/|Hidicence|Pan|APAN|BIRD_AUTH_TOKEN|BIRD_CT0|auth_token|ct0|\.secrets|gbrain.env' || true
```

Investigate every match. Some docs may mention generic examples, but no real secrets or personal data should ship.

## 5. Smoke tests

Run from skill root:

```bash
python3 scripts/status_knowledge_pipeline.py
python3 scripts/lint_wiki.py
python3 scripts/recall_for_conversation.py "agent workflow 記憶召回" --json
python3 scripts/xkb_ask.py "xkb cleanup" --json
```

Expected:

- health check can find runtime wiki topics
- recall reads from `memory/x-knowledge-base/wiki`
- ask can return wiki/card references

## 6. Release archive

Prefer an explicit allowlist or `git archive` from a clean branch. Do not package from a live workspace with runtime data.

Suggested excluded paths:

```text
.git/
.secrets/
.env
*.env
node_modules/
.next/
__pycache__/
*.pyc
demo/xkb-demo-ui/public/graph-data.json
```

## 7. Marketplace positioning

Lead with the reusable workflow:

> Turn scattered sources into structured knowledge cards, searchable recall, and durable wiki pages.

Avoid leading with fragile/private integrations such as personal X/Twitter auth. Put those in optional/internal setup docs.
