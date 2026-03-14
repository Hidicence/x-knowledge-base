#!/usr/bin/env python3
import json
import re
from datetime import datetime, timezone
from pathlib import Path

WORKSPACE = Path('/root/.openclaw/workspace')
BOOKMARKS_DIR = WORKSPACE / 'memory/bookmarks'
QUEUE_PATH = WORKSPACE / 'memory/x-knowledge-base/tiege-queue.json'


def rel(p: Path) -> str:
    return str(p.relative_to(WORKSPACE))


def parse_title(text: str, fallback: str) -> str:
    m = re.search(r'^title:\s*"?(.+?)"?\s*$', text, re.MULTILINE)
    if m:
        return m.group(1).strip()
    m = re.search(r'^#\s+(.+)$', text, re.MULTILINE)
    if m:
        return m.group(1).strip()
    m = re.search(r'^tweet_id:\s*(\d+)\s*$', text, re.MULTILINE)
    if m:
        return fallback
    return fallback


def parse_source_url(text: str, tweet_id: str) -> str:
    m = re.search(r'^original_url:\s*(\S+)\s*$', text, re.MULTILINE)
    if m:
        return m.group(1).strip()
    return f'https://x.com/i/status/{tweet_id}' if tweet_id else ''


def category_for(path: Path) -> str:
    try:
        parts = path.relative_to(BOOKMARKS_DIR).parts
        if len(parts) >= 2:
            return parts[0]
    except Exception:
        pass
    return '99-general'


files = []
for f in BOOKMARKS_DIR.rglob('*.md'):
    if f.name.startswith('.') or 'notebooklm_exports' in f.parts:
        continue
    text = f.read_text(encoding='utf-8', errors='ignore')
    m = re.search(r'^tweet_id:\s*(\d+)\s*$', text, re.MULTILINE)
    tweet_id = m.group(1) if m else f.stem if f.stem.isdigit() else ''
    if not tweet_id:
        continue
    files.append((tweet_id, f, text))

files.sort(key=lambda x: x[0])

payload = {
    'version': 4,
    'updated_at': datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z'),
    'mode': 'single-item',
    'notes': 'Canonical single-item queue synced from memory/bookmarks for tiege processing.',
    'items': []
}

existing = {}
if QUEUE_PATH.exists():
    try:
        data = json.loads(QUEUE_PATH.read_text(encoding='utf-8'))
        for item in data.get('items', []):
            if item.get('id'):
                existing[item['id']] = item
    except Exception:
        pass

for tweet_id, path, text in files:
    old = existing.get(tweet_id, {})
    status = old.get('status', 'todo')
    worker = old.get('worker', '')
    started_at = old.get('started_at', '')
    finished_at = old.get('finished_at', '')
    error = old.get('error', '')
    if status not in {'todo', 'processing', 'done', 'failed', 'skipped'}:
        status = 'todo'
    payload['items'].append({
        'id': tweet_id,
        'title': old.get('title') or parse_title(text, path.stem),
        'source_path': rel(path),
        'source_url': old.get('source_url') or parse_source_url(text, tweet_id),
        'category': old.get('category') or category_for(path),
        'status': status,
        'priority': old.get('priority', 'normal'),
        'worker': worker,
        'started_at': started_at,
        'finished_at': finished_at,
        'error': error,
    })

QUEUE_PATH.parent.mkdir(parents=True, exist_ok=True)
QUEUE_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')
print(f'✅ queue synced: {QUEUE_PATH} ({len(payload["items"])} items)')
