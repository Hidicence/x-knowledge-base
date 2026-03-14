#!/usr/bin/env python3
import json
from pathlib import Path

p = Path('/root/.openclaw/workspace/memory/x-knowledge-base/recommendations/latest.json')
if not p.exists():
    print('❌ latest.json not found')
    raise SystemExit(1)

data = json.loads(p.read_text(encoding='utf-8'))
recs = data.get('recommendations', [])[:5]
if not recs:
    print('❌ no recommendations')
    raise SystemExit(1)

target_topics = {'vibe-coding', 'ai-agents', 'esg-carbon'}
seen = set()
dup = 0
noise = 0
hit = 0

for r in recs:
    tid = str(r.get('id', ''))
    if tid in seen:
        dup += 1
    seen.add(tid)

    text = (r.get('text') or '').strip()
    urls = r.get('urls') or []
    if (text.startswith('http') and len(text) < 80) or (not text and urls):
        noise += 1

    topics = set((r.get('_topic_hits') or {}).keys())
    if topics & target_topics:
        hit += 1

n = len(recs)
report = {
    'n': n,
    'interest_hit_rate': round(hit / n, 3),
    'duplicate_rate': round(dup / n, 3),
    'noise_rate': round(noise / n, 3),
    'pass': {
        'interest_hit_rate': hit / n >= 0.8,
        'duplicate_rate': dup / n <= 0.05,
        'noise_rate': noise / n <= 0.2,
    }
}

print(json.dumps(report, ensure_ascii=False, indent=2))
