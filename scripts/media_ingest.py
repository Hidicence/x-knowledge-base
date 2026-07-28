#!/usr/bin/env python3
"""
XKB media ingest — upgraded with structured image_type, thumbnail scoring,
and best_thumbnail_url selection.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import xkb_paths

WORKSPACE = xkb_paths.WORKSPACE
MEDIA_ROOT = Path(os.getenv("XKB_MEDIA_DIR", str(WORKSPACE / "memory" / "x-knowledge-base" / "media")))

TWEET_ID_RE = re.compile(r"(?:tweet_id:\s*\"?(\d{15,20})\"?|/(?:status|i/status)/(\d{15,20}))")
MEDIA_BLOCK_START = "<!-- xkb-media-evidence:start -->"
MEDIA_BLOCK_END = "<!-- xkb-media-evidence:end -->"
SECRETS = WORKSPACE / ".secrets" / "x-knowledge-base.env"


def _load_secrets() -> None:
    """Load .secrets/x-knowledge-base.env into os.environ if not already set."""
    if not SECRETS.exists():
        return
    for line in SECRETS.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k = k.strip()
        v = v.strip().strip('"').strip("'")
        if k and k not in os.environ:
            os.environ[k] = v


_load_secrets()

# Image type priority for thumbnail selection (higher = better thumbnail)
IMAGE_TYPE_PRIORITY = {
    "result_image": 10,
    "storyboard": 9,
    "comparison": 8,
    "product_mockup": 7,
    "diagram": 5,
    "prompt_screenshot": 3,
    "article_cover": 2,
    "unknown": 1,
}

VISION_PROMPT = """You are analyzing an image for a visual AI prompt knowledge base. Be conservative and precise.
Return Traditional Chinese Markdown with these EXACT sections in this order:

### image_type
Write EXACTLY ONE of these values (no other text on this line):
result_image | prompt_screenshot | comparison | storyboard | diagram | product_mockup | article_cover | unknown

Definitions:
- result_image: an actual AI-generated image output (the end creative result the user wanted)
- prompt_screenshot: a screenshot of prompt text, ChatGPT interface, or app UI showing settings
- comparison: shows before/after or side-by-side style variants
- storyboard: a grid of panels showing a sequential narrative or shot list
- diagram: a technical diagram, flowchart, infographic with data or labels
- product_mockup: a product in a commercial presentation or packaging context
- article_cover: a header/thumbnail/banner for an article, blog post, or social media post header (NOT the actual generated output)
- unknown: cannot determine

### thumbnail_score
Write a single integer 1-10 indicating how suitable this image is as a pattern library thumbnail.
result_image=8-10, storyboard=7-9, comparison=6-8, product_mockup=6-8, diagram=4-6, prompt_screenshot=2-4, article_cover=1-3, unknown=1-4

### OCR
Transcribe all readable text exactly. Preserve prompt text, labels, UI text, tables, Chinese/English wording.
If text is unreadable, write: cannot reliably identify

### Vision Notes
- layout: brief layout description (one line)
- key_elements: key visual elements as bullets
- reusable_pattern: what reusable prompt/visual pattern can be extracted (one line)
- risks: what may be wrong or unreadable

Do not invent text that is not visible. If uncertain, say uncertain.
"""


def normalize_url(url: str) -> str:
    url = url.strip().rstrip(".,;:!?)]}")
    if "pbs.twimg.com/media/" in url:
        parsed = urllib.parse.urlsplit(url)
        qs = urllib.parse.parse_qs(parsed.query)
        if "format" in qs:
            fmt = qs.get("format", ["jpg"])[0]
            query = urllib.parse.urlencode({"format": fmt, "name": "large"})
            return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, parsed.path, query, ""))
    return url


def extract_image_urls(text: str) -> list[str]:
    urls: list[str] = []
    seen: set[str] = set()
    for raw in re.findall(r"https?://pbs\.twimg\.com/media/[^\s)\]>\"']+", text):
        u = normalize_url(raw)
        if u not in seen:
            seen.add(u)
            urls.append(u)
    for raw in re.findall(r"https?://[^\s)\]>\"']+", text):
        if not re.search(r"\.(?:png|jpe?g|webp|gif)(?:\?|$)", raw, re.I):
            continue
        u = normalize_url(raw)
        if u not in seen:
            seen.add(u)
            urls.append(u)
    return urls


def source_id_for(path: Path, text: str) -> str:
    for m in TWEET_ID_RE.finditer(text):
        tid = m.group(1) or m.group(2)
        if tid:
            return tid
    return path.stem


def ext_for(url: str, content_type: str = "") -> str:
    qs = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)
    fmt = (qs.get("format") or [""])[0].lower()
    if fmt in {"jpg", "jpeg", "png", "webp", "gif"}:
        return ".jpg" if fmt == "jpeg" else f".{fmt}"
    if "png" in content_type:
        return ".png"
    if "webp" in content_type:
        return ".webp"
    if "gif" in content_type:
        return ".gif"
    return ".jpg"


def download(url: str, out_dir: Path, index: int) -> tuple[Path, str]:
    out_dir.mkdir(parents=True, exist_ok=True)
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = resp.read()
        ctype = resp.headers.get("content-type", "")
    sha = hashlib.sha256(data).hexdigest()
    ext = ext_for(url, ctype)
    out_path = out_dir / f"image-{index:02d}-{sha[:12]}{ext}"
    if not out_path.exists():
        out_path.write_bytes(data)
    return out_path, sha


def run_vision(image_path: Path, model: str = "", timeout_ms: int = 120000) -> str:
    """
    Analyze an image using sub2api vision endpoint directly.
    Falls back to openclaw capability image describe if sub2api is not available.
    """
    # Determine image MIME type
    ext = image_path.suffix.lower()
    mime = {"png": "image/png", "webp": "image/webp", "gif": "image/gif"}.get(ext.lstrip("."), "image/jpeg")

    # Try sub2api direct HTTP call first (most reliable on this VPS)
    sub2api_url = os.getenv("SUB2API_URL", "http://localhost:8080")
    sub2api_key = os.getenv("SUB2API_KEY", "")
    vision_model = model or os.getenv("XKB_VISION_MODEL_API", "gpt-5.6")

    if sub2api_key:
        try:
            img_bytes = image_path.read_bytes()
            b64 = base64.b64encode(img_bytes).decode()
            body = {
                "model": vision_model,
                "messages": [{
                    "role": "user",
                    "content": [
                        {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}},
                        {"type": "text", "text": VISION_PROMPT},
                    ],
                }],
                "max_tokens": 800,
            }
            req = urllib.request.Request(
                f"{sub2api_url}/v1/chat/completions",
                data=json.dumps(body).encode(),
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {sub2api_key}",
                },
            )
            with urllib.request.urlopen(req, timeout=max(30, timeout_ms // 1000)) as resp:
                data = json.loads(resp.read())
                return data["choices"][0]["message"]["content"]
        except Exception as e:
            print(f"sub2api vision failed, trying openclaw: {e}", file=sys.stderr)

    # Fallback: openclaw capability image describe
    cmd = [
        "openclaw", "capability", "image", "describe",
        "--file", str(image_path),
        "--prompt", VISION_PROMPT,
        "--json",
        "--timeout-ms", str(timeout_ms),
    ]
    if model:
        cmd.extend(["--model", model])
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=max(30, int(timeout_ms / 1000) + 20))
    if p.returncode != 0:
        raise RuntimeError((p.stderr or p.stdout or "image describe failed")[:1000])
    raw = (p.stdout or "").strip()
    try:
        data = json.loads(raw)
        candidates = []
        def walk(x):
            if isinstance(x, dict):
                for k, v in x.items():
                    if k in {"text", "content", "description", "output"} and isinstance(v, str):
                        candidates.append(v)
                    else:
                        walk(v)
            elif isinstance(x, list):
                for it in x:
                    walk(it)
        walk(data)
        text = "\n\n".join(c.strip() for c in candidates if c and c.strip())
        return text.strip() or raw
    except Exception:
        return raw


def parse_image_type(analysis: str) -> str:
    m = re.search(r"###\s*image_type\s*\n+([^\n#]+)", analysis, re.IGNORECASE)
    if m:
        raw = m.group(1).strip().lower()
        for key in IMAGE_TYPE_PRIORITY:
            if key in raw:
                return key
    # Fallback: legacy format
    m2 = re.search(r"[圖图]像類型[:：]\s*([^\n]+)", analysis)
    if m2:
        raw = m2.group(1).strip().lower()
        for key in IMAGE_TYPE_PRIORITY:
            if key.replace("_", " ") in raw or key in raw:
                return key
    return "unknown"


def parse_thumbnail_score(analysis: str) -> int:
    m = re.search(r"###\s*thumbnail_score\s*\n+\s*(\d+)", analysis, re.IGNORECASE)
    if m:
        return min(10, max(1, int(m.group(1))))
    return 0


def select_best_thumbnail(items: list[dict]) -> int:
    best_idx = 0
    best_score = -1
    for i, it in enumerate(items):
        itype = it.get("image_type", "unknown")
        tscore = it.get("thumbnail_score", 0)
        priority = IMAGE_TYPE_PRIORITY.get(itype, 1)
        combined = priority * 10 + tscore
        if combined > best_score:
            best_score = combined
            best_idx = i
    return best_idx


def strip_existing_block(text: str) -> str:
    pattern = re.compile(rf"\n*{re.escape(MEDIA_BLOCK_START)}[\s\S]*?{re.escape(MEDIA_BLOCK_END)}\n*", re.M)
    return pattern.sub("\n", text).rstrip() + "\n"


def build_block(items: list[dict]) -> str:
    best_idx = select_best_thumbnail(items)
    best_url = items[best_idx]["source_url"] if items else ""

    lines = [MEDIA_BLOCK_START, "", "## 10. Media Evidence", ""]
    lines.append("> Images are source evidence for the knowledge base; local downloads, OCR, and vision analysis below.")
    lines.append("")
    if best_url:
        lines.append(f"**best_thumbnail_url**: {best_url}")
        lines.append(f"**best_thumbnail_index**: {best_idx + 1}")
        lines.append("")
    for i, it in enumerate(items, 1):
        tag = " <- best_thumbnail" if (i - 1) == best_idx else ""
        lines.extend([
            f"### Image {i}{tag}",
            f"- source_url: {it['source_url']}",
            f"- image_type: {it.get('image_type', 'unknown')}",
            f"- thumbnail_score: {it.get('thumbnail_score', 0)}",
            f"- local_path: {it['local_path']}",
            f"- sha256: {it['sha256']}",
            f"- extracted_at: {it['extracted_at']}",
            "",
            it.get("analysis", "").strip() or "Parse failed",
            "",
        ])
    lines.append(MEDIA_BLOCK_END)
    lines.append("")
    return "\n".join(lines)


def process_file(path: Path, *, limit: int, force: bool, model: str, dry_run: bool) -> int:
    text = path.read_text(encoding="utf-8", errors="ignore")
    if MEDIA_BLOCK_START in text and not force:
        print(f"Already has Media Evidence: {path}")
        return 0
    urls = extract_image_urls(text)
    if limit > 0:
        urls = urls[:limit]
    if not urls:
        print(f"No image URLs found: {path}")
        return 0
    sid = source_id_for(path, text)
    out_dir = MEDIA_ROOT / sid
    print(json.dumps({"file": str(path), "source_id": sid, "image_count": len(urls)}, ensure_ascii=False))
    if dry_run:
        for u in urls:
            print(u)
        return 0
    items = []
    for idx, url in enumerate(urls, 1):
        try:
            local_path, sha = download(url, out_dir, idx)
            print(f"Image {idx}: {local_path}")
            analysis = run_vision(local_path, model=model)
            itype = parse_image_type(analysis)
            tscore = parse_thumbnail_score(analysis)
            items.append({
                "source_url": url,
                "image_type": itype,
                "thumbnail_score": tscore,
                "local_path": str(local_path.relative_to(WORKSPACE) if str(local_path).startswith(str(WORKSPACE)) else local_path),
                "sha256": sha,
                "extracted_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "analysis": analysis,
            })
        except Exception as e:
            items.append({
                "source_url": url,
                "image_type": "unknown",
                "thumbnail_score": 0,
                "local_path": "",
                "sha256": "",
                "extracted_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "analysis": f"Parse failed: {e}",
            })
    new_text = strip_existing_block(text) if force else text.rstrip() + "\n"
    new_text += "\n" + build_block(items)
    path.write_text(new_text, encoding="utf-8")
    print(f"Written Media Evidence: {path}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Add multimodal Media Evidence to XKB markdown files")
    ap.add_argument("paths", nargs="+", help="Bookmark/card markdown files")
    ap.add_argument("--limit", type=int, default=4, help="Max images per file, default 4")
    ap.add_argument("--force", action="store_true", help="Replace existing media evidence block")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--model", default=os.getenv("XKB_VISION_MODEL", ""), help="Optional OpenClaw vision model override")
    args = ap.parse_args()
    rc = 0
    for raw in args.paths:
        p = Path(raw)
        if not p.exists():
            print(f"not found: {p}", file=sys.stderr)
            rc = 1
            continue
        try:
            rc = max(rc, process_file(p, limit=args.limit, force=args.force, model=args.model, dry_run=args.dry_run))
        except Exception as e:
            print(f"Error {p}: {e}", file=sys.stderr)
            rc = 1
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
