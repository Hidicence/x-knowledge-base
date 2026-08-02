#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SKILL_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
OUT_DIR="${1:-$SKILL_DIR/dist}"
STAMP="$(date +%Y%m%d-%H%M%S)"
PKG_NAME="x-knowledge-base-skill-$STAMP"
STAGE="$OUT_DIR/$PKG_NAME"
ARCHIVE="$OUT_DIR/$PKG_NAME.tar.gz"

mkdir -p "$OUT_DIR"
rm -rf "$STAGE"
mkdir -p "$STAGE"

copy_path() {
  local src="$1"
  if [ -e "$SKILL_DIR/$src" ]; then
    mkdir -p "$STAGE/$(dirname "$src")"
    cp -a "$SKILL_DIR/$src" "$STAGE/$src"
  fi
}

# Core metadata/docs
copy_path "SKILL.md"
copy_path "README.md"
copy_path "README.zh.md"
copy_path ".env.example"
copy_path ".gitignore"

# Reusable assets/config/docs
copy_path "assets"
copy_path "config/category-rules.json"
copy_path "config/llm.json"
copy_path "config/recommendation-topics.json"
copy_path "config/tiege-queue.example.json"
copy_path "config/examples"
copy_path "docs/data-flow.md"
copy_path "docs/security-audit-response.md"
copy_path "docs/xkb-inference-adapter-spec.md"
copy_path "docs/xkb-vnext-roadmap-draft.md"
copy_path "docs/xkb-memory-service.md"
copy_path "docs/RUNTIME_PATHS.md"
copy_path "docs/PUBLISHING_CHECKLIST.md"
copy_path "references"
copy_path "evals"

# Code
copy_path "scripts"
copy_path "tools"

# Sanitized wiki shell only; no personal topics/staging/logs
mkdir -p "$STAGE/wiki/topics"
copy_path "wiki/WIKI-SCHEMA.md"
copy_path "wiki/README.md"
if [ -f "$SKILL_DIR/wiki/.gitignore" ]; then copy_path "wiki/.gitignore"; fi
: > "$STAGE/wiki/topics/.gitkeep"

# Demo source without generated output/build deps
copy_path "demo/generate_graph.py"
copy_path "demo/sample-notes"
copy_path "demo/xkb-demo-ui/README.md"
copy_path "demo/xkb-demo-ui/package.json"
copy_path "demo/xkb-demo-ui/package-lock.json"
copy_path "demo/xkb-demo-ui/tsconfig.json"
copy_path "demo/xkb-demo-ui/next.config.ts"
copy_path "demo/xkb-demo-ui/postcss.config.mjs"
copy_path "demo/xkb-demo-ui/app"
copy_path "demo/xkb-demo-ui/components"
copy_path "demo/xkb-demo-ui/public/graph-data.sample.json"

# Remove runtime/build/private artifacts defensively.
find "$STAGE" -type d \( -name '.git' -o -name '.next' -o -name 'node_modules' -o -name '__pycache__' -o -name '.secrets' \) -prune -exec rm -rf {} +
find "$STAGE" -type f \( -name '*.pyc' -o -name '*.pyo' -o -name '.env' -o -name '*.env' \) -delete
rm -f "$STAGE/demo/xkb-demo-ui/public/graph-data.json"
rm -f "$STAGE/wiki/log.md" "$STAGE/wiki/review-decisions.json" "$STAGE/wiki/topic-map.json" "$STAGE/wiki/index.md"
rm -rf "$STAGE/wiki/_staging"
find "$STAGE/wiki/topics" -type f ! -name '.gitkeep' -delete

# Safety scan: fail on obvious hardcoded secrets, not env-var placeholders.
python3 - <<'PY' "$STAGE"
import re, sys
from pathlib import Path
stage = Path(sys.argv[1])
patterns = [
    re.compile(r'(?:BIRD_AUTH_TOKEN|BIRD_CT0|auth_token|ct0)\s*[=:]\s*["\']?([A-Za-z0-9_%-]{40,})'),
    re.compile(r'(?:API_KEY|TOKEN|SECRET)\s*[=:]\s*["\']?(sk-[A-Za-z0-9_-]{20,}|[A-Za-z0-9_-]{48,})'),
]
allow_fragments = ['${', '***', 'your_', 'YOUR_', '<', 'example', 'placeholder']
hits=[]
for p in stage.rglob('*'):
    if not p.is_file():
        continue
    try:
        text = p.read_text(errors='ignore')
    except Exception:
        continue
    for i,line in enumerate(text.splitlines(),1):
        for pat in patterns:
            m = pat.search(line)
            if not m:
                continue
            val = m.group(1)
            if any(f in line for f in allow_fragments):
                continue
            hits.append(f'{p}:{i}: {line[:220]}')
if hits:
    print('Refusing to package: potential hardcoded secret found', file=sys.stderr)
    print('\n'.join(hits), file=sys.stderr)
    sys.exit(1)
PY

tar -czf "$ARCHIVE" -C "$OUT_DIR" "$PKG_NAME"
SIZE="$(du -h "$ARCHIVE" | awk '{print $1}')"
COUNT="$(find "$STAGE" -type f | wc -l | tr -d ' ')"

echo "release_dir=$STAGE"
echo "archive=$ARCHIVE"
echo "size=$SIZE"
echo "files=$COUNT"
