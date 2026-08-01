#!/bin/bash
set -euo pipefail

WORKSPACE_DIR="${WORKSPACE_DIR:-${OPENCLAW_WORKSPACE:-$HOME/.openclaw/workspace}}"
SKILL_DIR="${SKILL_DIR:-$WORKSPACE_DIR/skills/x-knowledge-base}"
BOOKMARKS_DIR="${BOOKMARKS_DIR:-$WORKSPACE_DIR/memory/bookmarks}"
SECRETS_FILE="${SECRETS_FILE:-$WORKSPACE_DIR/.secrets/x-knowledge-base.env}"
TOPIC_CONFIG="${TOPIC_CONFIG:-$SKILL_DIR/config/recommendation-topics.json}"
OUT_DIR="${OUT_DIR:-$WORKSPACE_DIR/memory/x-knowledge-base/recommendations}"

mkdir -p "$OUT_DIR"

if [[ -f "$SECRETS_FILE" ]]; then
  source "$SECRETS_FILE"
fi

# 兼容現有 bird 憑證命名
TWITTER_AUTH_TOKEN="${TWITTER_AUTH_TOKEN:-${BIRD_AUTH_TOKEN:-}}"
TWITTER_CT0="${TWITTER_CT0:-${BIRD_CT0:-}}"
export TWITTER_AUTH_TOKEN TWITTER_CT0

export PATH="$HOME/.local/bin:$PATH"
if ! command -v twitter >/dev/null 2>&1; then
  echo "❌ 找不到 twitter-cli（command: twitter）"
  exit 1
fi

if [[ -z "${TWITTER_AUTH_TOKEN:-}" || -z "${TWITTER_CT0:-}" ]]; then
  echo "❌ 缺少 TWITTER_AUTH_TOKEN / TWITTER_CT0（或 BIRD_AUTH_TOKEN / BIRD_CT0）"
  exit 1
fi

TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT

SEARCH_INDEX="$BOOKMARKS_DIR/search_index.json"
[[ -f "$SEARCH_INDEX" ]] || echo '{"items":[]}' > "$SEARCH_INDEX"

python3 - <<'PY' "$TOPIC_CONFIG" "$SEARCH_INDEX" "$TMP_DIR/profile.json"
import json, re, sys
cfg_path, idx_path, out_path = sys.argv[1:4]
cfg = json.load(open(cfg_path, 'r', encoding='utf-8'))
try:
    idx = json.load(open(idx_path, 'r', encoding='utf-8'))
except Exception:
    idx = {"items": []}
items = idx if isinstance(idx, list) else idx.get('items', [])

def flatten(item):
    if isinstance(item, str):
        return item
    if isinstance(item, list):
        return ' '.join(flatten(x) for x in item)
    if isinstance(item, dict):
        return ' '.join(flatten(v) for v in item.values())
    return ''

text_blob = '\n'.join(flatten(x).lower() for x in items[:2000])
profile = {}
for t in cfg.get('topics', []):
    k = t['key']
    score = 0
    for kw in t.get('keywords', []):
        if not kw:
            continue
        score += len(re.findall(re.escape(kw.lower()), text_blob))
    profile[k] = score

# 平滑，避免 0 分全部沒排序
for k in list(profile.keys()):
    profile[k] = max(profile[k], 1)

json.dump({"topicScore": profile}, open(out_path, 'w', encoding='utf-8'), ensure_ascii=False)
PY

python3 - <<'PY' "$TOPIC_CONFIG" "$TMP_DIR/plan.json"
import json, sys
cfg = json.load(open(sys.argv[1], 'r', encoding='utf-8'))
feeds = cfg.get('feeds', ['following'])
max_per_feed = int(cfg.get('maxPerFeed', 40))
json.dump({"feeds": feeds, "max": max_per_feed}, open(sys.argv[2], 'w', encoding='utf-8'))
PY

FEEDS=$(python3 - <<'PY' "$TMP_DIR/plan.json"
import json,sys
p=json.load(open(sys.argv[1]))
print('\n'.join(p['feeds']))
PY
)
MAXN=$(python3 - <<'PY' "$TMP_DIR/plan.json"
import json,sys
p=json.load(open(sys.argv[1]))
print(p['max'])
PY
)

for feed in $FEEDS; do
  if [[ "$feed" == "following" ]]; then
    twitter feed -t following --max "$MAXN" --json > "$TMP_DIR/feed-following.json" || echo '{"data":[]}' > "$TMP_DIR/feed-following.json"
  else
    twitter feed --max "$MAXN" --json > "$TMP_DIR/feed-foryou.json" || echo '{"data":[]}' > "$TMP_DIR/feed-foryou.json"
  fi
done

twitter bookmarks --max "$MAXN" --json > "$TMP_DIR/bookmarks.json" || echo '{"data":[]}' > "$TMP_DIR/bookmarks.json"

python3 - <<'PY' "$TOPIC_CONFIG" "$TMP_DIR/profile.json" "$TMP_DIR/feed-following.json" "$TMP_DIR/feed-foryou.json" "$TMP_DIR/bookmarks.json" "$OUT_DIR"
import json, re, sys
from datetime import datetime, timezone

cfg_path, profile_path, following_path, foryou_path, bookmarks_path, out_dir = sys.argv[1:7]
cfg = json.load(open(cfg_path, 'r', encoding='utf-8'))
profile = json.load(open(profile_path, 'r', encoding='utf-8')).get('topicScore', {})

def load_items(path, source):
    try:
        d = json.load(open(path, 'r', encoding='utf-8'))
    except Exception:
        return []
    arr = d if isinstance(d, list) else d.get('data', d.get('items', []))
    out = []
    for it in arr or []:
        if not isinstance(it, dict):
            continue
        x = dict(it)
        x['_source'] = source
        out.append(x)
    return out

feed_items = load_items(following_path, 'following') + load_items(foryou_path, 'for-you')
bookmark_items = load_items(bookmarks_path, 'bookmarks')

# 取最近書籤當「你最近偏好」依據
recent_bookmark_text = ' '.join((it.get('text') or '') for it in bookmark_items[:50]).lower()

topic_map = {t['key']: t for t in cfg.get('topics', [])}

def score_item(it):
    text = ((it.get('text') or '') + ' ' + ' '.join(it.get('urls', []) or [])).lower()
    topic_hits = {}
    score = 0.0
    for k, t in topic_map.items():
        hits = 0
        for kw in t.get('keywords', []):
            kw_l = kw.lower()
            if kw_l in text:
                hits += 1
        if hits:
            weighted = hits * float(profile.get(k, 1))
            topic_hits[k] = weighted
            score += weighted

    # 與最近書籤關聯（簡單 keyword overlap）
    overlap_bonus = 0
    for w in set(re.findall(r'[a-z][a-z0-9\-]{3,}', text)):
        if w in recent_bookmark_text:
            overlap_bonus += 0.08
    score += min(overlap_bonus, 2.5)

    # engagement bonus
    m = it.get('metrics') or {}
    likes = float(m.get('likes') or 0)
    rts = float(m.get('retweets') or 0)
    replies = float(m.get('replies') or 0)
    score += (likes * 0.002 + rts * 0.008 + replies * 0.006)

    return score, topic_hits

ranked = []
for it in feed_items:
    s, hits = score_item(it)
    if s <= 0:
        continue
    it['_score'] = round(s, 4)
    it['_topic_hits'] = hits
    ranked.append(it)

ranked.sort(key=lambda x: x.get('_score', 0), reverse=True)
topk = int(cfg.get('topK', 12))
top = ranked[:topk]

now = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')

summary = {
    'generatedAt': now,
    'profile': profile,
    'recommendations': top,
    'candidateCount': len(feed_items)
}

with open(f"{out_dir}/latest.json", 'w', encoding='utf-8') as f:
    json.dump(summary, f, ensure_ascii=False, indent=2)

lines = []
lines.append(f"# X 推薦摘要（{now}）")
lines.append("")
lines.append("## 你的興趣權重（由累積書籤推估）")
for k, v in sorted(profile.items(), key=lambda kv: kv[1], reverse=True):
    label = topic_map.get(k, {}).get('label', k)
    lines.append(f"- {label}: {v}")

lines.append("")
lines.append(f"## 推薦內容 Top {len(top)}（候選 {len(feed_items)}）")
for i, it in enumerate(top, 1):
    author = (it.get('author') or {}).get('screenName', 'unknown')
    tid = it.get('id', '')
    url = f"https://x.com/{author}/status/{tid}" if tid else ""
    text = (it.get('text') or '').replace('\n', ' ').strip()
    text = text[:120] + ('…' if len(text) > 120 else '')
    topics = sorted((it.get('_topic_hits') or {}).items(), key=lambda kv: kv[1], reverse=True)
    topic_txt = ', '.join(topic_map.get(k, {}).get('label', k) for k, _ in topics[:2])
    lines.append(f"{i}. [{it.get('_source')}] @{author}｜{text}")
    lines.append(f"   - score: {it.get('_score')} | topics: {topic_txt or 'general'}")
    if url:
      lines.append(f"   - {url}")

with open(f"{out_dir}/latest.md", 'w', encoding='utf-8') as f:
    f.write('\n'.join(lines) + '\n')

print(f"✅ 已產生推薦：{out_dir}/latest.md")
PY

echo "📌 完成：$OUT_DIR/latest.md"
