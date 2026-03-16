#!/usr/bin/env python3
"""
書籤增強工具
1. AI 濃縮 - 自動產生摘要（使用 MiniMax API）
2. 交叉連結 - 自動建立 wiki-link
"""

import os
import re
import time
import json
import subprocess
import requests
from pathlib import Path
from collections import Counter

BOOKMARKS_DIR = Path(os.getenv("BOOKMARKS_DIR", str(Path.home() / ".openclaw" / "workspace" / "memory" / "bookmarks")))
MINIMAX_API_KEY = os.getenv("MINIMAX_API_KEY", "")
MINIMAX_ENDPOINT = os.getenv("MINIMAX_ENDPOINT", "https://api.minimax.io/anthropic/v1/messages")
MINIMAX_MODEL = os.getenv("MINIMAX_MODEL", "MiniMax-M2.5")
OPENCLAW_FALLBACK_ENABLED = os.getenv("OPENCLAW_MINIMAX_FALLBACK", "1") not in ("0", "false", "False")
# 保留 session + agent 兩種路由控制；新版 openclaw agent CLI 不支援 --model 旗標。
OPENCLAW_FALLBACK_SESSION = os.getenv("OPENCLAW_MINIMAX_SESSION", "xkb-summarizer")
OPENCLAW_FALLBACK_AGENT = os.getenv("OPENCLAW_MINIMAX_AGENT", "")


def call_openclaw_minimax(prompt, system_prompt="你是一個專業的AI內容分析師，擅長產生簡潔的濃縮摘要。"):
    """當沒有 MINIMAX_API_KEY 時，改走 OpenClaw agent（相容新版 CLI，避免 --model 參數錯誤）。"""
    if not OPENCLAW_FALLBACK_ENABLED:
        return None

    user_msg = f"{system_prompt}\n\n{prompt}"
    cmd = [
        "openclaw", "agent",
        "--session-id", OPENCLAW_FALLBACK_SESSION,
        "--message", user_msg,
        "--json",
    ]
    if OPENCLAW_FALLBACK_AGENT:
        cmd.extend(["--agent", OPENCLAW_FALLBACK_AGENT])

    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        if p.returncode != 0:
            print(f"❌ OpenClaw fallback 失敗: {p.stderr.strip()[:300]}")
            return None

        data = json.loads((p.stdout or "{}").strip() or "{}")
        payloads = (((data.get("result") or {}).get("payloads")) or [])
        text_chunks = [x.get("text") for x in payloads if isinstance(x, dict) and isinstance(x.get("text"), str)]
        merged = "\n\n".join([t.strip() for t in text_chunks if t and t.strip()]).strip()
        return merged or None
    except Exception as e:
        print(f"❌ OpenClaw fallback 異常: {e}")
        return None


def call_minimax(prompt, system_prompt="你是一個專業的AI內容分析師，擅長產生簡潔的濃縮摘要。"):
    """呼叫 MiniMax API（優先環境變數，失敗再 fallback 到 OpenClaw minimax）"""
    if not MINIMAX_API_KEY:
        print("⚠️ 未設定 MINIMAX_API_KEY，嘗試走 OpenClaw minimax fallback")
        return call_openclaw_minimax(prompt, system_prompt)

    headers = {
        "Authorization": f"Bearer {MINIMAX_API_KEY}",
        "Content-Type": "application/json"
    }

    data = {
        "model": MINIMAX_MODEL,
        "messages": [
            {"role": "user", "content": f"{system_prompt}\n\n{prompt}"}
        ],
        "temperature": 0.4,
        "max_tokens": 800
    }

    try:
        response = requests.post(MINIMAX_ENDPOINT, headers=headers, json=data, timeout=45)
        if response.status_code >= 400:
            print(f"❌ MiniMax API 錯誤 {response.status_code}: {response.text[:300]}")
            print("  ↪ 改走 OpenClaw minimax fallback")
            return call_openclaw_minimax(prompt, system_prompt)

        result = response.json() if response.content else {}

        # Anthropic-compatible 回應
        if isinstance(result.get("content"), list):
            text_chunks = []
            for item in result.get("content", []):
                if item.get("type") in ("text", "thinking"):
                    val = item.get("text") or item.get("thinking")
                    if val:
                        text_chunks.append(val)
            return "\n".join(text_chunks).strip() or None

        # OpenAI-ish 回應
        choices = result.get("choices") or []
        if choices:
            return (((choices[0] or {}).get("message") or {}).get("content") or "").strip() or None

        print(f"❌ 無法解析 API 回應: {result}")
        print("  ↪ 改走 OpenClaw minimax fallback")
        return call_openclaw_minimax(prompt, system_prompt)
    except Exception as e:
        print(f"❌ 請求錯誤: {e}")
        print("  ↪ 改走 OpenClaw minimax fallback")
        return call_openclaw_minimax(prompt, system_prompt)


def generate_local_summary(bookmark):
    """無 API Key 時的本地摘要（規則式，省 token 且不中斷流程）"""
    content = bookmark.get("content", "")
    title = bookmark.get("title", "(untitled)")

    # 粗清洗
    text = content
    text = re.sub(r"^---[\s\S]*?---\s*", " ", text)  # frontmatter
    text = re.sub(r"^(Title|URL Source|Published Time|Markdown Content):.*$", " ", text, flags=re.M)
    text = re.sub(r"```[\s\S]*?```", " ", text)
    text = re.sub(r"\[[^\]]+\]\([^\)]+\)", " ", text)
    text = re.sub(r"https?://\S+", " ", text)
    text = re.sub(r"\s+", " ", text).strip()

    # 句子切分（中英混合）
    sentences = [s.strip() for s in re.split(r"[。！？!?\.]+", text) if s.strip()]

    # 關鍵詞（簡單詞頻）
    tokens = re.findall(r"[A-Za-z0-9_\-]{3,}|[\u4e00-\u9fff]{2,}", text)
    stop = {"OpenClaw", "today", "this", "that", "with", "from", "have", "http", "https"}
    words = [w for w in tokens if w not in stop]
    top_keywords = [w for w, _ in Counter(words).most_common(6)]

    # 一句話摘要：優先第一句，截到 24 字內（中文）/ 80 字元
    one_line = (sentences[0] if sentences else title)
    if len(one_line) > 80:
        one_line = one_line[:80].rstrip() + "…"

    # 三個重點：取前三句，不足補關鍵詞句
    points = sentences[:3]
    while len(points) < 3:
        if top_keywords:
            points.append(f"關鍵詞：{'、'.join(top_keywords[:3])}")
        else:
            points.append("內容重點待補充")

    # 應用場景：依關鍵詞做簡單映射
    joined = " ".join(top_keywords).lower()
    scenarios = []
    if any(k in joined for k in ["seo", "geo", "rank", "搜尋"]):
        scenarios.append("SEO / GEO 策略優化")
    if any(k in joined for k in ["openclaw", "agent", "workflow", "自動化"]):
        scenarios.append("Agent 工作流設計")
    if any(k in joined for k in ["video", "seedance", "prompt", "腳本"]):
        scenarios.append("內容與影片生產流程")
    if not scenarios:
        scenarios = ["主題研究與知識整理", "團隊內部分享", "後續行動清單制定"]

    return f"""## 📌 一句話摘要
{one_line}

## 🎯 三個重點
1. {points[0]}
2. {points[1]}
3. {points[2]}

## 💡 應用場景
- {scenarios[0]}
- {scenarios[1] if len(scenarios) > 1 else '快速決策參考'}
- {scenarios[2] if len(scenarios) > 2 else '建立可執行 SOP'}
"""


def get_all_bookmarks():
    """取得所有書籤（用於交叉連結）"""
    bookmarks = []
    if not BOOKMARKS_DIR.exists():
        return bookmarks

    for f in BOOKMARKS_DIR.rglob("*.md"):
        if f.name.startswith("."):
            continue
        if f.name in ["INDEX.md", "urls.txt"]:
            continue
        if "test-" in f.name:
            continue

        content = f.read_text(encoding="utf-8", errors="ignore")
        title_match = re.search(r"^#\s+(.+)$", content, re.MULTILINE)
        title = title_match.group(1) if title_match else f.stem
        tags = re.findall(r"#(\w+)", content)
        url_match = re.search(r"\*\*原始連結\*\*：(.+)", content)
        url = url_match.group(1).strip() if url_match else ""

        bookmarks.append({
            "path": str(f),
            "filename": f.name.replace(".md", ""),
            "title": title,
            "tags": tags,
            "url": url,
            "content": content,
        })
    return bookmarks


def get_inbox_bookmarks():
    """只處理 inbox 裡的書籤"""
    inbox_dir = BOOKMARKS_DIR / "inbox"
    bookmarks = []
    
    if not inbox_dir.exists():
        return bookmarks

    for f in inbox_dir.glob("*.md"):
        if f.name.startswith("."):
            continue
            
        content = f.read_text(encoding="utf-8", errors="ignore")
        
        # 跳過已濃縮的
        if "## 📝 AI 濃縮" in content or "## 📌 一句話摘要" in content:
            continue
            
        title_match = re.search(r"^#\s+(.+)$", content, re.MULTILINE)
        title = title_match.group(1) if title_match else f.stem
        tags = re.findall(r"#(\w+)", content)
        url_match = re.search(r"\*\*原始連結\*\*：(.+)", content)
        url = url_match.group(1).strip() if url_match else ""

        bookmarks.append({
            "path": str(f),
            "filename": f.name.replace(".md", ""),
            "title": title,
            "tags": tags,
            "url": url,
            "content": content,
        })
    
    # 按修改時間排序（最新的先處理）
    bookmarks.sort(key=lambda x: os.path.getmtime(x["path"]), reverse=True)
    return bookmarks


def find_related_bookmarks(current_bookmark, all_bookmarks, limit=3):
    current_tags = set(current_bookmark.get("tags") or [])
    related = []

    for b in all_bookmarks:
        if b.get("path") == current_bookmark.get("path"):
            continue
        overlap = current_tags & set(b.get("tags") or [])
        if overlap:
            related.append({
                "filename": b.get("filename"),
                "title": b.get("title"),
                "overlap": len(overlap),
                "tags": sorted(list(overlap)),
            })

    related.sort(key=lambda x: x["overlap"], reverse=True)
    return related[:limit]


def generate_ai_summary(bookmark):
    content = bookmark.get("content", "")
    title = bookmark.get("title", "(untitled)")
    truncated = content[:8000]

    prompt = f"""請為以下 X 書籤知識卡產生濃縮摘要。

注意：內容可能包含
- 原始 tweet
- thread 全文
- 作者後續補充
- 外部文章/網站摘錄
- GitHub repo / issue / PR 摘錄

請整合這些上下文，不要只摘要第一段。

格式如下：

## 📌 一句話摘要
（一句話概括核心，20字以內）

## 🎯 三個重點
1. （重點一）
2. （重點二）
3. （重點三）

## 🧵 作者補充 / Thread 重點
- （如果有 thread 或補充，就整理 2-4 點；沒有就寫「無明顯補充」）

## 🔗 外部連結重點
- （如果有文章 / GitHub / 外部連結內容，就整理 2-4 點；沒有就寫「無外部連結內容」）

## 💡 對 Pan 的價值
- （實際應用、可追、可做的方向，2-3 點）

---

書籤標題：{title}

書籤內容：
{truncated}

---

請用繁體中文回覆，格式要清晰、內容要可執行。"""

    ai_summary = call_minimax(prompt)
    if ai_summary:
        return ai_summary

    print("  ℹ️ 改用本地摘要模式（TieGe-style fallback）")
    return generate_local_summary(bookmark)


def add_ai_summary(bookmark, summary):
    path = Path(bookmark["path"])
    content = path.read_text(encoding="utf-8", errors="ignore")

    if "## 📌 一句話摘要" in content or "## 📝 AI 濃縮" in content:
        print("  ⏭️  跳過（已有摘要）")
        return False

    # 濃縮放最前面（省 token：搜尋時只讀前面就知道重點）
    summary_block = f"## 📝 AI 濃縮\n\n{summary}\n\n---\n\n"
    path.write_text(summary_block + content, encoding="utf-8")
    return True


def add_cross_links(bookmarks):
    updated = 0
    for bookmark in bookmarks:
        related = find_related_bookmarks(bookmark, bookmarks)
        if not related:
            continue

        path = Path(bookmark["path"])
        content = path.read_text(encoding="utf-8", errors="ignore")
        if "## 🔗 相關書籤" in content:
            continue

        links_block = "\n\n## 🔗 相關書籤\n\n"
        for r in related:
            links_block += f"- [[{r['filename']}|{r['title']}]] ({', '.join(r['tags'])})\n"

        path.write_text(content + links_block, encoding="utf-8")
        updated += 1

    return updated


def process_bookmarks(limit=5, skip_ai=False):
    print("📚 書籤增強工具（Inbox 批次處理）")
    print("=" * 50)

    # 只處理 inbox 裡的書籤
    bookmarks = get_inbox_bookmarks()
    print(f"📥 Inbox 裡有 {len(bookmarks)} 個待處理書籤")

    if len(bookmarks) == 0:
        print("✅ Inbox 已清空，沒有需要處理的書籤")
        return

    # 限制處理數量
    bookmarks = bookmarks[:limit]
    print(f"📦 本批次處理 {len(bookmarks)} 條")

    # 也要更新交叉連結（對全部書籤）
    print("\n🔗 更新交叉連結...")
    all_bookmarks = get_all_bookmarks()
    updated = add_cross_links(all_bookmarks)
    print(f"✅ 已更新 {updated} 個書籤的交叉連結")

    if skip_ai:
        print("\n⏭️  跳過 AI 濃縮")
        return

    print("\n🤖 AI 濃縮處理...")
    count = 0
    for i, bookmark in enumerate(bookmarks):
        print(f"\n[{i+1}/{len(bookmarks)}] {bookmark.get('title', '')[:40]}...")
        content = Path(bookmark["path"]).read_text(encoding="utf-8", errors="ignore")
        if "## 📝 AI 濃縮" in content or "## 📌 一句話摘要" in content:
            print("  ⏭️  跳過（已有摘要）")
            continue

        summary = generate_ai_summary(bookmark)
        if summary:
            add_ai_summary(bookmark, summary)
            print("  ✅ 已加入摘要")
            count += 1
        else:
            print("  ⚠️ 略過（API 未設定或回應失敗）")

        time.sleep(1)

    print(f"\n✅ 批次完成！已處理 {count} 個書籤")


def get_quick_summary(bookmark_path, max_chars=500):
    """快速讀取濃縮摘要（低 token，用於搜尋）"""
    path = Path(bookmark_path)
    content = path.read_text(encoding="utf-8", errors="ignore")
    
    # 找 AI 濃縮區塊
    if "## 📝 AI 濃縮" in content:
        start = content.find("## 📝 AI 濃縮")
        end = content.find("---", start)
        if end == -1:
            end = len(content)
        return content[start:end].strip()[:max_chars]
    
    # 沒有濃縮，至少拿標題
    title_match = re.search(r"^#\s+(.+)$", content, re.MULTILINE)
    if title_match:
        return f"# {title_match.group(1)}"
    return content[:max_chars]


if __name__ == "__main__":
    import sys
    limit = int(sys.argv[1]) if len(sys.argv) > 1 else 3
    skip = "--skip-ai" in sys.argv
    process_bookmarks(limit=limit, skip_ai=skip)
