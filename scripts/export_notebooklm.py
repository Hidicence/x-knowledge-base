#!/usr/bin/env python3
import re
import shutil
from pathlib import Path

WORKSPACE_DIR = Path('/root/.openclaw/workspace')
BOOKMARKS_DIR = Path(__import__('os').getenv('BOOKMARKS_DIR', str(WORKSPACE_DIR / 'memory/bookmarks')))
EXPORT_DIR = WORKSPACE_DIR / 'memory/notebooklm_exports'
CARDS_DIR = EXPORT_DIR / 'cards'
TOPICS_DIR = EXPORT_DIR / 'topics'


def slugify(text):
    text = text.lower().strip()
    text = re.sub(r'[^a-z0-9\u4e00-\u9fff]+', '-', text)
    return text.strip('-') or 'untitled'


def extract_frontmatter(text):
    m = re.match(r'^---\n([\s\S]*?)\n---\n', text)
    return m.group(1) if m else ''


def extract_field(frontmatter, key):
    m = re.search(rf'^{re.escape(key)}:\s*"?(.+?)"?\s*$', frontmatter, re.M)
    return m.group(1).strip() if m else ''


def extract_title(text, fallback):
    m = re.search(r'^#\s+(.+)$', text, re.M)
    return m.group(1).strip() if m else fallback


def extract_section(text, heading):
    pattern = rf'^{re.escape(heading)}\s*$([\s\S]*?)(?=^##\s|\Z)'
    m = re.search(pattern, text, re.M)
    return m.group(1).strip() if m else ''


BAD_PATTERNS = [
    'Don’t miss what’s happening',
    'Sign in to X',
    'Create account',
    'Trending now',
    'Warning: This page maybe not yet fully loaded',
    'Target URL returned error 404',
]


def looks_bad(text):
    return any(p in (text or '') for p in BAD_PATTERNS)


def to_card(src: Path):
    text = src.read_text(encoding='utf-8', errors='ignore')
    if looks_bad(text):
        return None
    fm = extract_frontmatter(text)
    title = extract_title(text, src.stem)
    source_url = extract_field(fm, 'source') or extract_field(fm, 'source_url') or extract_field(fm, 'original_url')
    author = extract_field(fm, 'author')
    created_at = extract_field(fm, 'date') or extract_field(fm, 'created_at') or extract_field(fm, 'created')
    category = extract_field(fm, 'category') or src.parent.name
    tags_raw = extract_field(fm, 'tags')
    summary = extract_section(text, '## 📌 一句話摘要') or extract_section(text, '## 一句话摘要')
    points = extract_section(text, '## 🎯 三個重點') or extract_section(text, '## 三个重点')
    pan_value = extract_section(text, '## 💡 對 Pan 的價值') or extract_section(text, '## 對 Pan 的可行動價值') or extract_section(text, '## 对 Pan 的价值')
    thread = extract_section(text, '## 🧵 Thread 全文') or extract_section(text, '## ✍️ 作者補充')
    links = extract_section(text, '## 🔗 外部連結摘錄')

    if looks_bad(summary) or looks_bad(points):
        return None

    card = f'''---
id: "{src.stem}"
type: x-knowledge-card
source_type: x-bookmark
source_url: "{source_url}"
author: "{author}"
created_at: "{created_at}"
category: "{category}"
tags: {tags_raw or '[]'}
confidence: medium
---

# {title}

## 1. 核心摘要
{summary or '-'}

## 2. 重點整理
{points or '- 無摘要重點'}

## 3. 作者補充 / Thread 重點
{thread or '- 無明顯補充'}

## 4. 外部連結重點
{links or '- 無外部連結內容'}

## 5. 對 Pan 的價值
{pan_value or '- 待補充'}

## 6. 關聯主題
- {category}

## 7. 原始來源
- Source file: {src}
- Tweet: {source_url}
'''
    return title, category, card


def main(limit=None):
    CARDS_DIR.mkdir(parents=True, exist_ok=True)
    TOPICS_DIR.mkdir(parents=True, exist_ok=True)
    files = [p for p in BOOKMARKS_DIR.rglob('*.md') if 'inbox' not in str(p) and 'search_index.json' not in str(p)]
    files = sorted(files, key=lambda p: p.stat().st_mtime, reverse=True)
    if limit:
        files = files[:limit]
    topics = {}
    count = 0
    for src in files:
        result = to_card(src)
        if not result:
            continue
        title, category, card = result
        out = CARDS_DIR / f'{slugify(src.stem)}.md'
        out.write_text(card, encoding='utf-8')
        topics.setdefault(category, []).append((title, out.name))
        count += 1

    for category, items in topics.items():
        lines = [f'# Topic: {category}', '', '## 卡片列表']
        for title, name in items[:100]:
            lines.append(f'- {title} ({name})')
        (TOPICS_DIR / f'topic-{slugify(category)}.md').write_text('\n'.join(lines) + '\n', encoding='utf-8')

    print(f'EXPORTED {count} cards to {CARDS_DIR}')
    print(f'GENERATED {len(topics)} topic files in {TOPICS_DIR}')


if __name__ == '__main__':
    import sys
    limit = int(sys.argv[1]) if len(sys.argv) > 1 else None
    main(limit)
