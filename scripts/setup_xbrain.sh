#!/usr/bin/env bash
# setup_xbrain.sh — Install and configure the XBrain (GBrain) runtime for XKB
#
# Usage:
#   bash scripts/setup_xbrain.sh
#   bash scripts/setup_xbrain.sh --dir /opt/gbrain    # custom install path
#
# What it does:
#   1. Locates or installs Bun
#   2. Clones GBrain runtime if not present
#   3. Installs dependencies and initialises the PGLite database
#   4. Writes gbrain_dir + LLM env into ~/.openclaw/openclaw.json
#   5. Verifies the installation with a test query

set -euo pipefail

# ── Config ────────────────────────────────────────────────────────────────────
GBRAIN_REPO="https://github.com/garrytan/gbrain"
GBRAIN_DEFAULT_DIR="$HOME/gbrain"
OPENCLAW_JSON="${OPENCLAW_JSON:-$HOME/.openclaw/openclaw.json}"

# ── Args ──────────────────────────────────────────────────────────────────────
GBRAIN_DIR="$GBRAIN_DEFAULT_DIR"
while [[ $# -gt 0 ]]; do
  case $1 in
    --dir) GBRAIN_DIR="$2"; shift 2 ;;
    *) echo "Unknown arg: $1"; exit 1 ;;
  esac
done

# ── 1. Find or install Bun ────────────────────────────────────────────────────
echo "▶ Checking Bun..."
if command -v bun &>/dev/null; then
  BUN="bun"
elif [ -f "$HOME/.bun/bin/bun" ]; then
  BUN="$HOME/.bun/bin/bun"
  echo "  Found at $BUN (not in PATH — that's OK)"
else
  echo "  Bun not found. Installing..."
  curl -fsSL https://bun.sh/install | bash
  BUN="$HOME/.bun/bin/bun"
  echo "  Installed: $BUN"
fi
echo "  Bun: $($BUN --version)"

# ── 2. Clone GBrain runtime ───────────────────────────────────────────────────
echo "▶ GBrain runtime..."
if [ -f "$GBRAIN_DIR/src/cli.ts" ]; then
  echo "  Already present at $GBRAIN_DIR — pulling latest..."
  git -C "$GBRAIN_DIR" pull --ff-only
else
  echo "  Cloning $GBRAIN_REPO → $GBRAIN_DIR"
  git clone "$GBRAIN_REPO" "$GBRAIN_DIR"
fi
echo "  Version: $(cat "$GBRAIN_DIR/VERSION" 2>/dev/null || echo 'unknown')"

# ── 3. Install dependencies ───────────────────────────────────────────────────
echo "▶ Installing dependencies..."
(cd "$GBRAIN_DIR" && "$BUN" install --frozen-lockfile 2>&1 | tail -3)

# ── 4. Initialise PGLite database ────────────────────────────────────────────
echo "▶ Initialising XBrain database..."
if (cd "$GBRAIN_DIR" && "$BUN" run src/cli.ts health 2>/dev/null | grep -q "pages"); then
  echo "  Database already initialised — skipping"
else
  (cd "$GBRAIN_DIR" && "$BUN" run src/cli.ts init)
fi

# ── 5. Update openclaw.json ───────────────────────────────────────────────────
echo "▶ Updating $OPENCLAW_JSON..."
mkdir -p "$(dirname "$OPENCLAW_JSON")"

if [ ! -f "$OPENCLAW_JSON" ]; then
  echo '{"env":{}}' > "$OPENCLAW_JSON"
fi

python3 - "$OPENCLAW_JSON" "$GBRAIN_DIR" <<'EOF'
import json, sys
path, gbrain_dir = sys.argv[1], sys.argv[2]
with open(path) as f:
    cfg = json.load(f)
env = cfg.setdefault("env", {})
env["gbrain_dir"] = gbrain_dir
# Only set LLM defaults if not already present
env.setdefault("LLM_API_URL", "https://api.minimax.io/anthropic/v1")
env.setdefault("LLM_MODEL",   "MiniMax-M2.7")
with open(path, "w") as f:
    json.dump(cfg, f, ensure_ascii=False, indent=2)
print(f"  gbrain_dir = {gbrain_dir}")
print(f"  LLM_API_URL = {env['LLM_API_URL']}")
print(f"  LLM_MODEL = {env['LLM_MODEL']}")
EOF

# ── 6. Verify ─────────────────────────────────────────────────────────────────
echo "▶ Verifying XBrain integration..."
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RESULT=$(OPENCLAW_JSON="$OPENCLAW_JSON" python3 -c "
import sys; sys.path.insert(0, '$SCRIPT_DIR')
from xbrain_recall import GBRAIN_AVAILABLE, GBRAIN_DIR, BUN
print('GBRAIN_AVAILABLE:', GBRAIN_AVAILABLE)
print('GBRAIN_DIR:', GBRAIN_DIR)
print('BUN:', BUN)
" 2>&1)
echo "$RESULT"

if echo "$RESULT" | grep -q "GBRAIN_AVAILABLE: True"; then
  echo ""
  echo "✅ XBrain ready. Next: push your existing cards to the index."
  echo "   python3 scripts/sync_cards_to_xbrain.py  # (or re-run ingest)"
else
  echo ""
  echo "❌ XBrain not available after setup. Check errors above."
  exit 1
fi
