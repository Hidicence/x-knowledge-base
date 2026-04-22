#!/usr/bin/env python3
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlparse

import requests


BAD_PATTERNS = [
    "Don’t miss what’s happening",
    "Sign in to X",
    "Create account",
    "New to X?",
    "Trending now",
    "What’s happening",
    "Saved searches",
    "Provide feedback",
    "Privacy Policy",
    "Cookie Policy",
    "Target URL returned error 404",
    "github.com/login",
    "Sign in to GitHub",
]


def run(cmd, timeout=45):
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return p.returncode, p.stdout.strip(), p.stderr.strip()
    except Exception as e:
        return 1, "", str(e)


def is_low_value_text(text):
    if not text:
        return True
    hit = sum(1 for x in BAD_PATTERNS if x in text)
    if hit >= 2:
        return True
    if len(text.strip()) < 80:
        return True
    return False


def clean_text(text):
    if not text:
        return ""
    lines = []
    for line in text.splitlines():
        s = line.strip()
        if not s:
            continue
        if any(bad in s for bad in BAD_PATTERNS):
            continue
        lines.append(s)
    cleaned = "\n".join(lines).strip()
    cleaned = re.sub(r'\n{3,}', '\n\n', cleaned)
    return cleaned


def resolve_url(url):
    rc, out, err = run(["curl", "-fsSLI", "-o", "/dev/null", "-w", "%{url_effective}", url], timeout=20)
    if rc == 0 and out:
        return out.strip()
    return url


def extract_urls(text):
    urls = re.findall(r'https?://[^\s)\]>"\']+', text or "")
    cleaned = []
    seen = set()
    for u in urls:
        u = u.rstrip('.,;:!?)])')
        if 't.co/' in u:
            u = resolve_url(u)
        if u not in seen:
            seen.add(u)
            cleaned.append(u)
    return cleaned


def fetch_web(url):
    rc, out, err = run(["curl", "-fsSL", f"https://r.jina.ai/{url}"], timeout=40)
    if rc == 0 and out:
        out = clean_text(out[:12000])
        if not is_low_value_text(out):
            return out
    return ""


def fetch_x_article(url):
    """Fetch X Article content via fxtwitter API.

    Supports both direct X status URLs and already-resolved t.co targets.
    Returns rendered plain text blocks or empty string on failure.
    """
    try:
        resolved = resolve_url(url)
        m = re.search(r'https?://(?:www\.)?(?:x|twitter)\.com/([^/]+)/status/(\d+)', resolved)
        if not m:
            return ""
        username, tweet_id = m.group(1), m.group(2)
        api_url = f"https://api.fxtwitter.com/{username}/status/{tweet_id}"
        resp = requests.get(api_url, timeout=30, headers={"User-Agent": "Mozilla/5.0"})
        if resp.status_code != 200:
            return ""
        data = resp.json()
        tweet = data.get("tweet") or {}
        article = tweet.get("article") or {}
        content = article.get("content") or {}
        blocks = content.get("blocks") or []
        if not blocks:
            return ""
        parts = []
        title = (article.get("title") or "").strip()
        preview = (article.get("preview_text") or "").strip()
        if title:
            parts.append(f"# {title}")
        if preview:
            parts.append(preview)
        for block in blocks:
            if not isinstance(block, dict):
                continue
            text = clean_text((block.get("text") or "").strip())
            if not text:
                continue
            btype = block.get("type")
            if btype == "header-two":
                parts.append(f"## {text}")
            elif btype == "blockquote":
                parts.append(f"> {text}")
            else:
                parts.append(text)
        rendered = "\n\n".join(x for x in parts if x).strip()
        return rendered[:30000]
    except Exception:
        return ""


def fetch_github(url):
    parsed = urlparse(url)
    parts = [p for p in parsed.path.split('/') if p]
    if len(parts) < 2:
        return ""
    owner_repo = '/'.join(parts[:2])
    snippets = []
    rc, out, err = run(["gh", "repo", "view", owner_repo], timeout=25)
    if rc == 0 and out and "Not Found" not in out:
        snippets.append("[GitHub Repo]\n" + clean_text(out[:4000]))
    if len(parts) >= 4 and parts[2] in ("issues", "pull") and parts[3].isdigit():
        num = parts[3]
        cmd = ["gh", "issue", "view", num, "-R", owner_repo] if parts[2] == 'issues' else ["gh", "pr", "view", num, "-R", owner_repo]
        rc, out, err = run(cmd, timeout=25)
        if rc == 0 and out and "Not Found" not in out:
            snippets.append(f"[GitHub {parts[2][:-1].upper()}]\n" + clean_text(out[:4000]))
    merged = "\n\n".join([s for s in snippets if s.strip()])
    return merged if not is_low_value_text(merged) else ""


def xreach_base_cmd():
    cmd = ["xreach"]
    auth = os.getenv("BIRD_AUTH_TOKEN", "").strip()
    ct0 = os.getenv("BIRD_CT0", "").strip()
    if auth and ct0:
        cmd.extend(["--auth-token", auth, "--ct0", ct0])
    return cmd


def parse_thread(raw):
    if not raw:
        return {"thread_text": "", "author_additions": [], "urls": []}
    try:
        data = json.loads(raw)
    except Exception:
        cleaned = clean_text(raw[:12000])
        return {"thread_text": cleaned, "author_additions": [], "urls": extract_urls(cleaned)}

    tweets = []
    if isinstance(data, list):
        tweets = data
    elif isinstance(data, dict):
        if isinstance(data.get("tweets"), list):
            tweets = data["tweets"]
        elif isinstance(data.get("thread"), list):
            tweets = data["thread"]
        elif isinstance(data.get("data"), list):
            tweets = data["data"]
        else:
            tweets = [data]

    parts = []
    author_additions = []
    urls = []
    seen_urls = set()
    for i, tw in enumerate(tweets):
        if not isinstance(tw, dict):
            continue
        text = clean_text((tw.get("text") or tw.get("full_text") or tw.get("note_tweet") or "").strip())
        if not text or is_low_value_text(text):
            continue
        parts.append(f"[{i+1}] {text}")
        if i > 0:
            author_additions.append(text)
        for u in extract_urls(text):
            if u not in seen_urls:
                seen_urls.add(u)
                urls.append(u)
    return {
        "thread_text": "\n\n".join(parts)[:20000],
        "author_additions": author_additions[:10],
        "urls": urls[:10],
    }


def fetch_thread(tweet_id):
    base = xreach_base_cmd()
    candidates = [
        base + ["thread", tweet_id, "--json"],
        base + ["tweet", tweet_id, "--json"],
    ]
    for cmd in candidates:
        rc, out, err = run(cmd, timeout=35)
        if rc != 0 or not out:
            continue
        parsed = parse_thread(out)
        if parsed.get("thread_text") or parsed.get("urls"):
            return parsed
    return {"thread_text": "", "author_additions": [], "urls": []}


def enrich(tweet_id, base_content):
    parsed = fetch_thread(tweet_id)
    combined_urls = []
    for u in extract_urls(base_content) + parsed["urls"]:
        if 'x.com/' in u or 'twitter.com/' in u:
            continue
        if u not in combined_urls:
            combined_urls.append(u)
    link_summaries = []
    for url in combined_urls[:5]:
        host = urlparse(url).netloc.lower()
        text = ""
        if "github.com" in host:
            if shutil_which("gh"):
                text = fetch_github(url)
            # GitHub 連結若 gh 抓不到有效內容，就直接略過，避免吃到 GitHub 首頁/登入頁雜訊
            if not text:
                continue
        elif host.endswith("x.com") or host.endswith("twitter.com"):
            text = fetch_x_article(url)
            if not text:
                text = fetch_web(url)
        else:
            text = fetch_web(url)
        text = clean_text(text)
        if text and not is_low_value_text(text):
            link_summaries.append({"url": url, "content": text[:12000]})
    return {
        "thread_text": parsed["thread_text"],
        "author_additions": parsed["author_additions"],
        "extracted_links": link_summaries,
    }


def shutil_which(cmd):
    from shutil import which
    return which(cmd)


if __name__ == "__main__":
    tweet_id = sys.argv[1]
    path = Path(sys.argv[2]) if len(sys.argv) > 2 else None
    base = path.read_text(encoding="utf-8", errors="ignore") if path and path.exists() else ""
    print(json.dumps(enrich(tweet_id, base), ensure_ascii=False, indent=2))
