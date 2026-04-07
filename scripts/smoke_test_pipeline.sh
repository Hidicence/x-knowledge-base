#!/bin/bash
# Pipeline smoke test — verifies each wiki pipeline component produces expected output.
# Usage: bash smoke_test_pipeline.sh
# Exit code: 0 = all pass, 1 = one or more failures

WORKSPACE_DIR="${OPENCLAW_WORKSPACE:-/root/.openclaw/workspace}"
SKILL_DIR="$WORKSPACE_DIR/skills/x-knowledge-base"
SCRIPTS="$SKILL_DIR/scripts"

PASS=0
FAIL=0

check() {
    local name="$1"
    local result="$2"
    local expect="$3"
    if echo "$result" | grep -qE "$expect"; then
        echo "  ✅  $name"
        PASS=$((PASS + 1))
    else
        echo "  ❌  $name"
        echo "      expected: $expect"
        echo "      got: $(echo "$result" | head -3)"
        FAIL=$((FAIL + 1))
    fi
}

echo "🔬 Pipeline Smoke Test — $(date -u '+%Y-%m-%d %H:%M UTC')"
echo "   WORKSPACE: $WORKSPACE_DIR"
echo ""

# 1. search_index.json exists and has items
out=$(python3 -c "
import json
d=json.load(open('$WORKSPACE_DIR/memory/bookmarks/search_index.json'))
items=d.get('items',d) if isinstance(d,dict) else d
print(f'items={len(items)}')
" 2>&1 || echo "ERROR")
check "search_index.json has items" "$out" "items=[0-9]+"

# 2. topic-map.json valid
out=$(python3 -c "
import json
d=json.load(open('$WORKSPACE_DIR/wiki/topic-map.json'))
print(f'mappings={len(d.get(\"mapping\",{}))}')
" 2>&1 || echo "ERROR")
check "topic-map.json valid" "$out" "mappings=[0-9]+"

# 3. Wiki topics exist on disk
count=$(ls "$WORKSPACE_DIR/wiki/topics/"*.md 2>/dev/null | wc -l || echo 0)
check "wiki topics seeded" "topics=$count" "topics=[1-9]"

# 4. lint_wiki.py
out=$(OPENCLAW_WORKSPACE="$WORKSPACE_DIR" python3 "$SCRIPTS/lint_wiki.py" 2>&1 || echo "ERROR")
check "lint_wiki.py runs" "$out" "Topics on disk: [1-9]"

# 5. status_knowledge_pipeline.py
out=$(OPENCLAW_WORKSPACE="$WORKSPACE_DIR" python3 "$SCRIPTS/status_knowledge_pipeline.py" 2>&1 || echo "ERROR")
check "status_knowledge_pipeline.py runs" "$out" "Total: [0-9]+"

# 6. sync_cards_to_wiki --review --no-llm
out=$(OPENCLAW_WORKSPACE="$WORKSPACE_DIR" python3 "$SCRIPTS/sync_cards_to_wiki.py" --review --no-llm --topic openclaw-agent-workflows 2>&1 || echo "ERROR")
check "sync_cards_to_wiki --review" "$out" "candidates"

# 7. distill_memory_to_wiki --no-llm
out=$(OPENCLAW_WORKSPACE="$WORKSPACE_DIR" python3 "$SCRIPTS/distill_memory_to_wiki.py" --dry-run --no-llm --days 3 2>&1 || echo "ERROR")
check "distill_memory_to_wiki --no-llm" "$out" "(No memory files|Loaded [0-9]+ file)"

# 8. review-decisions.json has decisions written
out=$(python3 -c "
import json
d=json.load(open('$WORKSPACE_DIR/wiki/review-decisions.json'))
print(f'decisions={len(d.get(\"decisions\",{}))}')
" 2>&1 || echo "ERROR")
check "review-decisions.json has absorb records" "$out" "decisions=[0-9]+"

# 9. wiki index.md lists topic links
count=$(grep -c 'topics/.*\.md' "$WORKSPACE_DIR/wiki/index.md" 2>/dev/null || echo 0)
check "wiki index.md has topic links" "links=$count" "links=[1-9]"

# 10. fetch_and_summarize.sh has Step 7 (wiki sync)
count=$(grep -c '步驟7' "$SKILL_DIR/scripts/fetch_and_summarize.sh" 2>/dev/null || echo 0)
check "fetch_and_summarize.sh has wiki sync step" "count=$count" "count=[1-9]"

# --- Summary ---
echo ""
echo "─────────────────────────────────────"
echo "  Results: $PASS passed, $FAIL failed"
echo "─────────────────────────────────────"

[[ $FAIL -gt 0 ]] && exit 1 || exit 0
