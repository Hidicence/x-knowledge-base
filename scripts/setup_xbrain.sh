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
#   4. Uses the shared portable runtime environment (process env, or XKB_ENV_FILE)
#   5. Verifies the installation with a test query
#
# Runtime configuration is intentionally never read from or written to a
# host-specific Hermes/OpenClaw config.  Pass --env-file to select a dotenv
# file for this invocation; otherwise an inherited XKB_ENV_FILE is used.  The
# shared loader gives process environment variables precedence over file values.

set -euo pipefail

# ── Config ────────────────────────────────────────────────────────────────────
GBRAIN_REPO="https://github.com/garrytan/gbrain"
GBRAIN_DEFAULT_DIR="$HOME/gbrain"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# ── Args ──────────────────────────────────────────────────────────────────────
GBRAIN_DIR="$GBRAIN_DEFAULT_DIR"
ENV_FILE="${XKB_ENV_FILE:-}"
while [[ $# -gt 0 ]]; do
  case $1 in
    --dir) GBRAIN_DIR="$2"; shift 2 ;;
    --env-file) ENV_FILE="$2"; shift 2 ;;
    *) echo "Unknown arg: $1"; exit 1 ;;
  esac
done

if [[ -n "$ENV_FILE" && ! -f "$ENV_FILE" ]]; then
  echo "❌ XKB env file not found: $ENV_FILE" >&2
  echo "   Provide an existing dotenv file with --env-file, or unset XKB_ENV_FILE." >&2
  exit 1
fi

# Validate before any install/clone/database side effects.  Use the same shared
# loader as the runtime; never print the resulting values.
echo "▶ Validating portable XKB runtime configuration..."
if [[ -n "$ENV_FILE" ]]; then
  echo "  Credentials/config: selected XKB env file (values not displayed)"
else
  echo "  Credentials/config: process environment (no host config fallback)"
fi
RUNTIME_ENV_FILE="$ENV_FILE" GBRAIN_DIR="$GBRAIN_DIR" python3 - "$SCRIPT_DIR" <<'EOF'
import os, sys
from pathlib import Path

script_dir = Path(sys.argv[1])
sys.path.insert(0, str(script_dir.parent / "tools"))
from runtime_config import runtime_env

settings = runtime_env(os.environ.get("RUNTIME_ENV_FILE") or None)
if not settings.get("GEMINI_API_KEY"):
    raise SystemExit(
        "XBrain verification requires GEMINI_API_KEY via process environment "
        "or the selected XKB env file (process environment takes precedence)."
    )
EOF

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

# ── 5. Report portable runtime configuration ─────────────────────────────────
echo "  gbrain_dir = $GBRAIN_DIR"

# ── 6. Verify ─────────────────────────────────────────────────────────────────
echo "▶ Verifying XBrain integration..."
RESULT=$(XKB_ENV_FILE="$ENV_FILE" GBRAIN_DIR="$GBRAIN_DIR" python3 -c "
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
