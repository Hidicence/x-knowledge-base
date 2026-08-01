#!/usr/bin/env python3
"""Monitor one or more YouTube playlists and prepare transcript packages for APAN2/XKB card generation.

This script intentionally does NOT call any LLM. It only:
1. loads playlist configs
2. lists playlist videos via yt-dlp
3. compares against existing youtube cards/raw packages
4. downloads subtitles for new videos
5. writes raw transcript JSON packages under memory/x-knowledge-base/youtube-raw/

Card generation is handled by OpenClaw/APAN2 cron agent, not by a direct model provider.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent))
import xkb_paths

WORKSPACE = xkb_paths.WORKSPACE
BOOKMARKS_DIR = Path(os.getenv("BOOKMARKS_DIR", str(WORKSPACE / "memory" / "bookmarks")))
YOUTUBE_CARD_DIR = BOOKMARKS_DIR / "youtube"
RAW_DIR = WORKSPACE / "memory" / "x-knowledge-base" / "youtube-raw"
SKILL_DIR = Path(__file__).resolve().parent.parent
DEFAULT_PLAYLISTS_FILE = SKILL_DIR / "config" / "youtube-playlists.json"
COOKIES_FILE = Path.home() / ".config" / "yt-dlp" / "cookies.txt"


def yt_cmd(extra: list[str]) -> list[str]:
    base = [
        sys.executable,
        "-m",
        "yt_dlp",
        "--js-runtimes",
        "node",
        "--remote-components",
        "ejs:github",
    ]
    if COOKIES_FILE.exists():
        base += ["--cookies", str(COOKIES_FILE)]
    return base + extra


def playlist_slug(url: str, fallback: str = "playlist") -> str:
    try:
        parsed = urlparse(url)
        list_id = parse_qs(parsed.query).get("list", [""])[0]
        if list_id:
            return re.sub(r"[^A-Za-z0-9_-]+", "-", list_id).strip("-")[:80]
    except Exception:
        pass
    return re.sub(r"[^A-Za-z0-9_-]+", "-", fallback).strip("-")[:80] or "playlist"


def normalize_playlist(raw: dict | str, idx: int = 0) -> dict:
    if isinstance(raw, str):
        raw = {"url": raw}
    url = str(raw.get("url") or raw.get("playlist_url") or "").strip()
    if not url:
        raise ValueError(f"Playlist config #{idx + 1} missing url")
    pid = str(raw.get("id") or raw.get("slug") or playlist_slug(url, f"playlist-{idx + 1}"))
    return {
        "id": pid,
        "url": url,
        "title": str(raw.get("title") or pid),
        "category": str(raw.get("category") or "ai-tools"),
        "tags": [str(t).strip() for t in raw.get("tags", []) if str(t).strip()],
        "notes": str(raw.get("notes") or ""),
    }


def load_playlists(args: argparse.Namespace) -> list[dict]:
    if args.playlist:
        return [
            normalize_playlist(
                {
                    "id": args.playlist_id or playlist_slug(args.playlist),
                    "url": args.playlist,
                    "title": args.playlist_title or args.playlist_id or playlist_slug(args.playlist),
                    "category": args.category,
                    "tags": args.tags or [],
                }
            )
        ]

    if os.getenv("YOUTUBE_PLAYLISTS_JSON"):
        data = json.loads(os.environ["YOUTUBE_PLAYLISTS_JSON"])
        if isinstance(data, dict) and "playlists" in data:
            data = data["playlists"]
        if not isinstance(data, list):
            raise ValueError("YOUTUBE_PLAYLISTS_JSON must be a list or {playlists: [...]}")
        return [normalize_playlist(item, idx) for idx, item in enumerate(data)]

    playlists_file = Path(os.getenv("YOUTUBE_PLAYLISTS_FILE", str(DEFAULT_PLAYLISTS_FILE)))
    if playlists_file.exists():
        data = json.loads(playlists_file.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            data = data.get("playlists", [])
        if not isinstance(data, list):
            raise ValueError(f"{playlists_file} must contain a list or {{playlists: [...]}}")
        return [normalize_playlist(item, idx) for idx, item in enumerate(data)]

    if os.getenv("YOUTUBE_PLAYLIST_URL"):
        return [normalize_playlist(os.environ["YOUTUBE_PLAYLIST_URL"])]

    return []


def playlist_videos(playlist_url: str) -> list[dict]:
    cmd = yt_cmd([
        "--flat-playlist",
        "--print",
        "%(id)s\t%(title)s\t%(duration)s\t%(channel)s",
        "--quiet",
        playlist_url,
    ])
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "yt-dlp playlist listing failed")

    videos = []
    for line in result.stdout.strip().splitlines():
        parts = line.split("\t")
        if len(parts) < 2:
            continue
        vid = parts[0].strip()
        title = parts[1].strip()
        duration = int(parts[2]) if len(parts) > 2 and parts[2].strip().isdigit() else 0
        channel = parts[3].strip() if len(parts) > 3 else ""
        if vid:
            videos.append({"id": vid, "title": title, "duration": duration, "channel": channel})
    return videos


def parse_vtt(vtt_path: Path) -> str:
    content = vtt_path.read_text(encoding="utf-8")
    seen: set[str] = set()
    texts: list[str] = []
    for line in content.splitlines():
        line = line.strip()
        if (
            not line
            or "-->" in line
            or line.startswith(("WEBVTT", "Kind:", "Language:"))
            or re.match(r"^\d+$", line)
        ):
            continue
        line = re.sub(r"<[^>]+>", "", line)
        line = re.sub(r"\s+", " ", line).strip()
        if line and line not in seen:
            seen.add(line)
            texts.append(line)
    return " ".join(texts)


def download_subtitle(video_id: str, tmp_dir: Path) -> tuple[str, str]:
    cmd = yt_cmd([
        "--write-auto-subs",
        "--sub-langs",
        "zh-Hans,zh-TW,en",
        "--skip-download",
        "--sub-format",
        "vtt",
        "--no-progress",
        "--quiet",
        "-o",
        str(tmp_dir / "%(id)s"),
        f"https://www.youtube.com/watch?v={video_id}",
    ])
    subprocess.run(cmd, capture_output=True, text=True, timeout=180)
    for lang in ["zh-Hans", "zh-TW", "en"]:
        vtt = tmp_dir / f"{video_id}.{lang}.vtt"
        if vtt.exists():
            return parse_vtt(vtt), lang
    return "", ""


def existing_card_ids() -> set[str]:
    if not YOUTUBE_CARD_DIR.exists():
        return set()
    return {p.stem for p in YOUTUBE_CARD_DIR.glob("*.md")}


def existing_raw_ids() -> set[str]:
    if not RAW_DIR.exists():
        return set()
    return {p.stem for p in RAW_DIR.glob("*.json")}


def package_video(video: dict, playlist: dict, tmp_dir: Path) -> tuple[dict | None, dict | None]:
    transcript, lang = download_subtitle(video["id"], tmp_dir)
    base = {
        "id": video["id"],
        "title": video["title"],
        "channel": video.get("channel") or "YouTube",
        "duration": video.get("duration", 0),
        "source_url": f"https://www.youtube.com/watch?v={video['id']}",
        "playlist_id": playlist["id"],
        "playlist_title": playlist["title"],
        "playlist_url": playlist["url"],
        "category": playlist["category"],
        "playlist_tags": playlist["tags"],
        "playlist_notes": playlist.get("notes", ""),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "generator": "apan2-cron-agent",
    }
    out = RAW_DIR / f"{video['id']}.json"
    if not transcript:
        marker = {**base, "status": "no_subtitles"}
        out.write_text(json.dumps(marker, ensure_ascii=False, indent=2), encoding="utf-8")
        return None, {**video, "playlist_id": playlist["id"], "reason": "no_subtitles", "raw_path": str(out)}

    package = {
        **base,
        "lang": lang,
        "transcript_chars": len(transcript),
        "transcript": transcript,
        "status": "pending_card_generation",
    }
    out.write_text(json.dumps(package, ensure_ascii=False, indent=2), encoding="utf-8")
    return {**video, "playlist_id": playlist["id"], "lang": lang, "transcript_chars": len(transcript), "raw_path": str(out)}, None


def main() -> int:
    parser = argparse.ArgumentParser(description="Monitor YouTube playlists and prepare transcript packages")
    parser.add_argument("--playlist", default="", help="Single YouTube playlist URL override")
    parser.add_argument("--playlist-id", default="", help="id/slug for single --playlist")
    parser.add_argument("--playlist-title", default="", help="title for single --playlist")
    parser.add_argument("--category", default="ai-tools", help="default category for single --playlist")
    parser.add_argument("--tag", dest="tags", action="append", default=[], help="default tag for single --playlist; repeatable")
    parser.add_argument("--limit", type=int, default=0, help="max new videos to package across all playlists; 0 = all")
    parser.add_argument("--json", action="store_true", help="print machine-readable JSON summary")
    args = parser.parse_args()

    playlists = load_playlists(args)
    if not playlists:
        print("No YouTube playlists configured. Set config/youtube-playlists.json, YOUTUBE_PLAYLISTS_JSON, YOUTUBE_PLAYLISTS_FILE, YOUTUBE_PLAYLIST_URL, or --playlist.", file=sys.stderr)
        return 2

    RAW_DIR.mkdir(parents=True, exist_ok=True)
    YOUTUBE_CARD_DIR.mkdir(parents=True, exist_ok=True)

    done = existing_card_ids()
    already_raw = existing_raw_ids()
    packaged: list[dict] = []
    skipped: list[dict] = []
    playlist_summaries: list[dict] = []
    remaining = args.limit if args.limit else None

    with tempfile.TemporaryDirectory() as tmp:
        tmp_dir = Path(tmp)
        for playlist in playlists:
            videos = playlist_videos(playlist["url"])
            candidates = [v for v in videos if v["id"] not in done and v["id"] not in already_raw]
            if remaining is not None:
                candidates = candidates[:remaining]

            p_packaged = 0
            p_skipped = 0
            for video in candidates:
                item, skip = package_video(video, playlist, tmp_dir)
                already_raw.add(video["id"])
                if item:
                    packaged.append(item)
                    p_packaged += 1
                if skip:
                    skipped.append(skip)
                    p_skipped += 1
                if remaining is not None:
                    remaining -= 1
                    if remaining <= 0:
                        break

            playlist_summaries.append({
                "id": playlist["id"],
                "title": playlist["title"],
                "url": playlist["url"],
                "playlist_total": len(videos),
                "new_candidates": len(candidates),
                "packaged": p_packaged,
                "skipped": p_skipped,
                "category": playlist["category"],
                "tags": playlist["tags"],
            })
            if remaining is not None and remaining <= 0:
                break

    summary = {
        "playlist_count": len(playlists),
        "playlists": playlist_summaries,
        "existing_cards": len(done),
        "existing_raw": len(already_raw),
        "packaged": packaged,
        "skipped": skipped,
        "raw_dir": str(RAW_DIR),
    }

    if args.json:
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    else:
        print(f"Playlists: {len(playlists)}")
        print(f"Existing YouTube cards: {len(done)}")
        print(f"Packaged new transcripts: {len(packaged)}")
        for ps in playlist_summaries:
            print(f"[{ps['id']}] total={ps['playlist_total']} candidates={ps['new_candidates']} packaged={ps['packaged']} skipped={ps['skipped']}")
        for item in packaged:
            print(f"+ [{item['playlist_id']}] {item['id']} {item['title']} ({item['lang']}, {item['transcript_chars']} chars)")
        for item in skipped:
            print(f"- [{item['playlist_id']}] {item['id']} {item['title']} skipped: {item['reason']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
