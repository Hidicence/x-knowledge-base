#!/usr/bin/env python3
"""
fetch_youtube_playlist.py

從 YouTube 播放清單抓影片字幕，用 LLM 生成知識卡片，加入 search_index.json。

Usage:
    python3 scripts/fetch_youtube_playlist.py
    python3 scripts/fetch_youtube_playlist.py --playlist "URL"
    python3 scripts/fetch_youtube_playlist.py --limit 5
    python3 scripts/fetch_youtube_playlist.py --dry-run
"""

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path

# ── Paths ─────────────────────────────────────────────────────────────────────
SKILL_DIR = Path(__file__).resolve().parent.parent
WORKSPACE_DIR = Path(
    os.getenv(
        "OPENCLAW_WORKSPACE",
        os.getenv("WORKSPACE_DIR", str(Path.home() / ".openclaw" / "workspace"))
    )
)
BOOKMARKS_DIR = Path(os.getenv("BOOKMARKS_DIR", str(WORKSPACE_DIR / "memory" / "bookmarks")))
INDEX_FILE = BOOKMARKS_DIR / "search_index.json"
YOUTUBE_DIR = BOOKMARKS_DIR / "youtube"
COOKIES_FILE = Path.home() / ".config" / "yt-dlp" / "cookies.txt"

# ── LLM (MiniMax, Anthropic-compatible API) ───────────────────────────────────
MINIMAX_BASE_URL = "https://api.minimax.io/anthropic"
MINIMAX_MODEL    = "MiniMax-M2.5"

CARD_CATEGORIES = [
    "ai-tools", "seo-marketing", "workflows", "video",
    "startup", "design", "tech", "learning", "other"
]

# ── yt-dlp helpers ────────────────────────────────────────────────────────────
def _yt_cmd(extra_args: list) -> list:
    base = [sys.executable, "-m", "yt_dlp",
            "--js-runtimes", "node",
            "--remote-components", "ejs:github"]
    if COOKIES_FILE.exists():
        base += ["--cookies", str(COOKIES_FILE)]
    else:
        print("⚠️  ~/.config/yt-dlp/cookies.txt 不存在，可能被 YouTube 封鎖", file=sys.stderr)
    return base + extra_args


def get_playlist_videos(playlist_url: str) -> list[dict]:
    """回傳播放清單影片列表 [{"id", "title", "duration"}]"""
    cmd = _yt_cmd([
        "--flat-playlist",
        "--print", "%(id)s\t%(title)s\t%(duration)s",
        "--quiet",
        playlist_url
    ])
    result = subprocess.run(cmd, capture_output=True, text=True)
    videos = []
    for line in result.stdout.strip().splitlines():
        parts = line.split("\t")
        if len(parts) >= 2:
            vid_id = parts[0].strip()
            title  = parts[1].strip()
            dur    = int(parts[2]) if len(parts) > 2 and parts[2].strip().isdigit() else 0
            if vid_id:
                videos.append({"id": vid_id, "title": title, "duration": dur})
    return videos


def download_subtitles(video_id: str, tmp_dir: Path) -> str:
    """下載字幕並回傳純文字（優先 zh-Hans，備選 en）"""
    cmd = _yt_cmd([
        "--write-auto-subs",
        "--sub-langs", "zh-Hans,zh-TW,en",
        "--skip-download",
        "--sub-format", "vtt",
        "--no-progress",
        "--quiet",
        "-o", str(tmp_dir / "%(id)s"),
        f"https://www.youtube.com/watch?v={video_id}"
    ])
    subprocess.run(cmd, capture_output=True, text=True)

    for lang in ["zh-Hans", "zh-TW", "en"]:
        vtt = tmp_dir / f"{video_id}.{lang}.vtt"
        if vtt.exists():
            return _parse_vtt(vtt), lang
    return "", ""


def _parse_vtt(vtt_path: Path) -> str:
    """VTT → 去重純文字"""
    content = vtt_path.read_text(encoding="utf-8")
    seen, texts = set(), []
    for line in content.splitlines():
        line = line.strip()
        if (not line
                or "-->" in line
                or line.startswith(("WEBVTT", "Kind:", "Language:"))
                or re.match(r"^\d+$", line)):
            continue
        line = re.sub(r"<[^>]+>", "", line)
        line = re.sub(r"\s+", " ", line).strip()
        if line and line not in seen:
            seen.add(line)
            texts.append(line)
    return " ".join(texts)


# ── LLM card generation ───────────────────────────────────────────────────────
CARD_PROMPT = """你是一個知識卡片生成器。根據以下 YouTube 影片資訊，生成一張繁體中文知識卡片。

影片標題：{title}
影片網址：https://www.youtube.com/watch?v={video_id}
字幕語言：{lang}
字幕內容（前3000字）：
{transcript}

請輸出以下格式的 Markdown（不要加其他文字）：

---
title: {title}
category: <從以下選一：ai-tools / seo-marketing / workflows / video / startup / design / tech / learning / other>
tags: <3-5個標籤，逗號分隔，英文小寫>
source_url: https://www.youtube.com/watch?v={video_id}
---

## 📝 一句話摘要

<一句話說明這個影片的核心價值，繁體中文，20-40字>

## 三個重點

- <重點1，繁體中文，15-30字>
- <重點2，繁體中文，15-30字>
- <重點3，繁體中文，15-30字>
"""


def generate_card(title: str, video_id: str, transcript: str, lang: str, api_key: str) -> str:
    """呼叫 MiniMax API 生成知識卡片內容"""
    import urllib.request
    prompt = CARD_PROMPT.format(
        title=title,
        video_id=video_id,
        transcript=transcript[:3000],
        lang=lang or "unknown"
    )
    payload = json.dumps({
        "model": MINIMAX_MODEL,
        "max_tokens": 600,
        "messages": [{"role": "user", "content": prompt}]
    }).encode("utf-8")

    req = urllib.request.Request(
        f"{MINIMAX_BASE_URL}/v1/messages",
        data=payload,
        headers={
            "Content-Type": "application/json",
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
        },
        method="POST"
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    # MiniMax may return a thinking block before the text block
    text = next(item["text"] for item in data["content"] if item.get("type") == "text")
    return text.strip()


# ── Search index helpers ──────────────────────────────────────────────────────
def load_index() -> dict:
    if INDEX_FILE.exists():
        return json.loads(INDEX_FILE.read_text(encoding="utf-8"))
    return {"items": []}


def save_index(data: dict):
    INDEX_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def extract_frontmatter(md_content: str) -> dict:
    """從 markdown frontmatter 解析欄位"""
    m = re.match(r"^---\n(.+?)\n---", md_content, re.DOTALL)
    if not m:
        return {}
    result = {}
    for line in m.group(1).splitlines():
        if ":" in line:
            k, _, v = line.partition(":")
            result[k.strip()] = v.strip()
    return result


def extract_summary(md_content: str) -> str:
    m = re.search(r"## 📝 一句話摘要\s*\n(.+?)(?=\n##|\Z)", md_content, re.DOTALL)
    return m.group(1).strip() if m else ""


# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="Fetch YouTube playlist → knowledge cards")
    parser.add_argument("--playlist", default=os.environ.get("YOUTUBE_PLAYLIST_URL", ""),
                        help="YouTube 播放清單 URL")
    parser.add_argument("--limit", type=int, default=0, help="最多處理幾支新影片（0=全部）")
    parser.add_argument("--dry-run", action="store_true", help="只列出待處理影片，不實際執行")
    args = parser.parse_args()

    if not args.playlist:
        print("❌ 請設定 YOUTUBE_PLAYLIST_URL 環境變數或傳入 --playlist 參數", file=sys.stderr)
        return 1

    # Load API key
    config_path = Path("/root/.openclaw/openclaw.json")
    api_key = ""
    if config_path.exists():
        config = json.loads(config_path.read_text(encoding="utf-8"))
        api_key = config.get("env", {}).get("MINIMAX_API_KEY", "")
    api_key = api_key or os.environ.get("MINIMAX_API_KEY", "")
    if not api_key:
        print("❌ MINIMAX_API_KEY 未設定", file=sys.stderr)
        return 1

    YOUTUBE_DIR.mkdir(parents=True, exist_ok=True)

    # Load existing index to check processed videos
    index_data = load_index()
    existing_ids = {
        item.get("relative_path", "").split("/")[-1].replace(".md", "")
        for item in index_data.get("items", [])
    }

    print(f"📋 抓取播放清單：{args.playlist}")
    videos = get_playlist_videos(args.playlist)
    print(f"   共 {len(videos)} 支影片")

    # Filter new videos
    new_videos = [v for v in videos if v["id"] not in existing_ids]
    print(f"   新影片：{len(new_videos)} 支（已跳過 {len(videos) - len(new_videos)} 支）")

    if args.limit:
        new_videos = new_videos[:args.limit]

    if args.dry_run:
        for v in new_videos:
            dur = f"{v['duration']//60}:{v['duration']%60:02d}" if v["duration"] else "?"
            print(f"  [{dur}] {v['id']} — {v['title']}")
        return 0

    if not new_videos:
        print("✅ 沒有新影片需要處理")
        return 0

    processed = 0
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir)
        for v in new_videos:
            vid_id = v["id"]
            title  = v["title"]
            dur    = v["duration"]
            dur_str = f"{dur//60}:{dur%60:02d}" if dur else "?"
            print(f"\n🎬 [{dur_str}] {title}")

            # Skip very short videos (< 60s, probably shorts)
            if dur and dur < 60:
                print(f"   ⏭️  跳過（影片太短：{dur_str}）")
                continue

            # Download subtitles
            print(f"   📥 下載字幕...")
            transcript, lang = download_subtitles(vid_id, tmp_path)
            if not transcript:
                print(f"   ⚠️  無法取得字幕，跳過")
                continue
            print(f"   ✓ 字幕 ({lang})：{len(transcript)} 字")

            # Generate card
            print(f"   🤖 生成知識卡片...")
            try:
                card_content = generate_card(title, vid_id, transcript, lang, api_key)
            except Exception as e:
                print(f"   ❌ LLM 失敗：{e}")
                continue

            # Save .md
            md_path = YOUTUBE_DIR / f"{vid_id}.md"
            md_path.write_text(card_content, encoding="utf-8")
            print(f"   💾 儲存：youtube/{vid_id}.md")

            # Parse and add to index
            fm = extract_frontmatter(card_content)
            summary = extract_summary(card_content)
            relative_path = f"youtube/{vid_id}.md"
            tags_raw = fm.get("tags", "")
            tags = [t.strip() for t in tags_raw.split(",") if t.strip()]

            index_data["items"].append({
                "path": str(md_path),
                "relative_path": relative_path,
                "title": fm.get("title", title),
                "category": fm.get("category", "other"),
                "tags": tags,
                "summary": summary,
                "source_url": f"https://www.youtube.com/watch?v={vid_id}",
                "searchable": f"{title} {summary} {' '.join(tags)}",
                "mtime": datetime.now().isoformat(),
                "size": md_path.stat().st_size,
                "source": "youtube",
            })
            save_index(index_data)
            processed += 1

    print(f"\n✅ 完成：新增 {processed} 張 YouTube 知識卡片")
    if processed > 0:
        print("💡 執行以下指令更新語意索引：")
        print("   python3 scripts/build_vector_index.py --incremental")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
