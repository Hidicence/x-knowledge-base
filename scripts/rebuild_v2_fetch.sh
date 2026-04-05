#!/bin/bash
# Rebuild v2 fetch pipeline scaffold:
# fetch bookmarks into bookmarks_v2 without touching production bookmarks/

set -euo pipefail

WORKSPACE_DIR="${WORKSPACE_DIR:-${OPENCLAW_WORKSPACE:-$HOME/.openclaw/workspace}}"
SKILL_DIR="${SKILL_DIR:-$WORKSPACE_DIR/skills/x-knowledge-base}"
BOOKMARKS_DIR="${BOOKMARKS_DIR:-$WORKSPACE_DIR/memory/bookmarks_v2}"
CARDS_DIR="${CARDS_DIR:-$WORKSPACE_DIR/memory/cards_v2}"
RUNTIME_DIR="${RUNTIME_DIR:-$WORKSPACE_DIR/memory/x-knowledge-base-v2}"
BOOKMARKS_TMP_FILE="${BOOKMARKS_TMP_FILE:-/tmp/new_bookmarks_v2.txt}"
SECRETS_FILE="${SECRETS_FILE:-$WORKSPACE_DIR/.secrets/x-knowledge-base.env}"
PREPARE_ONLY="${PREPARE_ONLY:-1}"
FETCH_MODE="${FETCH_MODE:-full}"
FETCH_DAYS="${FETCH_DAYS:-28}"
MAX_PAGES="${MAX_PAGES:-100}"
SKIP_DEDUP="${SKIP_DEDUP:-0}"

mkdir -p "$BOOKMARKS_DIR" "$CARDS_DIR" "$RUNTIME_DIR"

export WORKSPACE_DIR SKILL_DIR BOOKMARKS_DIR CARDS_DIR RUNTIME_DIR BOOKMARKS_TMP_FILE SECRETS_FILE PREPARE_ONLY FETCH_MODE FETCH_DAYS MAX_PAGES SKIP_DEDUP

echo "🧱 v2 rebuild fetch starting"
echo "  BOOKMARKS_DIR=$BOOKMARKS_DIR"
echo "  CARDS_DIR=$CARDS_DIR"
echo "  RUNTIME_DIR=$RUNTIME_DIR"

bash "$SKILL_DIR/scripts/fetch_and_summarize.sh"

echo ""
echo "✅ v2 rebuild fetch finished"
