#!/usr/bin/env python3
"""
書籤增強工具
1. AI 濃縮 - 自動產生摘要（使用 MiniMax API）
2. 交叉連結 - 自動建立 wiki-link
"""

import os
import re
import json
import requests
from pathlib import Path
from datetime import datetime

BOOKMARKS_DIR = Path("/home/ubuntu/clawd/memory/bookmarks")

# MiniMax API 配置
MINIMAX_API_KEY = "sk-cp-encGVZFWOPh2brSxqTlBVvs4RTOWFQg1vQOsZt4uZLpZ9z5wn4nnpy-3LP7cfdMJ2TKeE-3KwBnslc32z3JNPgY_2RNzYW4WLKiAViP7gnIaYGxMMWniAWA"
MINIMAX_ENDPOINT = "https://api.minimax.io/anthropic/v1/messages"

def call_minimax(prompt, system_prompt="你是一個專業的AI內容分析師，擅長產生簡潔的濃縮摘要。"):
    """呼叫 MiniMax API"""
    headers = {
        "Authorization": f"Bearer {MINIMAX_API_KEY}",
        "Content-Type": "application/json"
    }
    
    data = {
        "model": "MiniMax-M2.5",
        "messages": [
            {"role": "user", "content": f"{system_prompt}\n\n{prompt}"}
        ],
        "temperature": 0.7,
        "max_tokens": 800
    }
    
    try:
        response = requests.post(MINIMAX_ENDPOINT, headers=headers, json=data, timeout=30)
        result = response.json()
        
        # MiniMax Anthropic-compatible API response format
        if "content" in result and isinstance(result["content"], list):
            text_content = ""
            for item in result["content"]:
                if item.get("type") == "text":
                    text_content += item.get("text", "")
                elif item.get("type") == "thinking":
                    text_content += item.get("thinking", "")
            return text_content if text_content else None
        
        if "choices" in result and len(result["choices"]) > 0:
            return result["choices"][0]["message"]["content"]
        else:
            print(f"❌ API 錯誤: {result}")
            return None
    except Exception as e:
        print(f"❌ 請求錯誤: {e}")
        return None

def get_all_bookmarks():
    """取得所有書籤"""
    bookmarks = []
    for f in BOOKMARKS_DIR.rglob("*.md"):
        if f.name.startswith("."): continue
        if f.name in ["INDEX.md", "urls.txt"]: continue
        if "test-" in f.name: continue  # 跳過測試檔
        
        content = f.read_text(encoding='utf-8')
        
        # 擷取標題
        title_match = re.search(r'^#\s+(.+)$', content, re.MULTILINE)
        title = title_match.group(1) if title_match else f.stem
        
        # 擷取標籤
        tags = re.findall(r'#(\w+)', content)
        
        # 擷取 URL
        url_match = re.search(r'\*\*原始連結\*\*：(.+)', content)
        url = url_match.group(1) if url_match else ""
        
        bookmarks.append({
            "path": str(f),
            "filename": f.name.replace('.md', ''),
            "title": title,
            "tags": tags,
            "url": url,
            "content": content
        })
    
    return bookmarks

def find_related_bookmarks(current_bookmark, all_bookmarks, limit=3):
    """找出相關書籤（根據標籤）"""
    current_tags = set(current_bookmark["tags"])
    related = []
    
    for b in all_bookmarks:
        if b["path"] == current_bookmark["path"]: continue
        
        b_tags = set(b["tags"])
        overlap = current_tags & b_tags
        
        if overlap:
            related.append({
                "filename": b["filename"],
                "title": b["title"],
                "overlap": len(overlap),
                "tags": list(overlap)
            })
    
    related.sort(key=lambda x: x["overlap"], reverse=True)
    return related[:limit]

def generate_ai_summary(bookmark):
    """用 AI 產生濃縮摘要"""
    content = bookmark["content"]
    title = bookmark["title"]
    
    # 取前 3000 字元（避免太長）
    truncated = content[:3000] if len(content) > 3000 else content
    
    prompt = f"""請為以下文章產生濃縮摘要，格式如下：

## 📌 一句話摘要
（一句話概括文章核心，20字以內）

## 🎯 三個重點
1. （重點一）
2. （重點二）
3. （重點三）

## 💡 應用場景
（這篇文章的實際應用場景，2-3個）

---

文章標題：{title}

文章內容：
{truncated}

---

請用繁體中文回覆，格式要清晰。"""

    return call_minimax(prompt)

def add_ai_summary(bookmark, summary):
    """將 AI 濃縮摘要加入書籤"""
    content = bookmark["content"]
    
    # 檢查是否已有 AI 濃縮摘要
    if "## 📌 一句話摘要" in content:
        print(f"  ⏭️  跳過（已有摘要）")
        return False
    
    # 加入 AI 濃縮摘要區塊
    summary_block = f"\n\n---\n\n## 📝 AI 濃縮\n\n{summary}\n"
    
    new_content = content + summary_block
    Path(bookmark["path"]).write_text(new_content, encoding='utf-8')
    return True

def add_cross_links(bookmarks):
    """為所有書籤加入交叉連結"""
    updated = 0
    
    for bookmark in bookmarks:
        related = find_related_bookmarks(bookmark, bookmarks)
        
        if not related: continue
        
        content = Path(bookmark["path"]).read_text(encoding='utf-8')
        
        # 產生交叉連結區塊
        links_block = "\n\n## 🔗 相關書籤\n\n"
        for r in related:
            links_block += f"- [[{r['filename']}|{r['title']}]] ({', '.join(r['tags'])})\n"
        
        if "## 🔗 相關書籤" not in content:
            new_content = content + links_block
            Path(bookmark["path"]).write_text(new_content, encoding='utf-8')
            updated += 1
    
    return updated

def process_bookmarks(limit=5, skip_ai=False):
    """處理書籤"""
    print("📚 書籤增強工具")
    print("=" * 50)
    
    # 取得所有書籤
    bookmarks = get_all_bookmarks()
    print(f"✅ 找到 {len(bookmarks)} 個書籤")
    
    # 加入交叉連結
    print("\n🔗 加入交叉連結...")
    updated = add_cross_links(bookmarks)
    print(f"✅ 已更新 {updated} 個書籤的交叉連結")
    
    if skip_ai:
        print("\n⏭️  跳過 AI 濃縮")
        return
    
    # AI 濃縮
    print("\n🤖 AI 濃縮處理...")
    count = 0
    for i, bookmark in enumerate(bookmarks[:limit]):
        print(f"\n[{i+1}/{limit}] {bookmark['title'][:40]}...")
        
        # 檢查是否已有摘要
        content = Path(bookmark["path"]).read_text(encoding='utf-8')
        if "## 📝 AI 濃縮" in content or "## 📌 一句話摘要" in content:
            print(f"  ⏭️  跳過（已有摘要）")
            continue
        
        # 產生摘要
        summary = generate_ai_summary(bookmark)
        
        if summary:
            add_ai_summary(bookmark, summary)
            print(f"  ✅ 已加入摘要")
            count += 1
        else:
            print(f"  ❌ 失敗")
        
        # 避免 rate limit
        import time
        time.sleep(1)
    
    print(f"\n✅ 完成！已處理 {count} 個書籤")

if __name__ == "__main__":
    import sys
    limit = int(sys.argv[1]) if len(sys.argv) > 1 else 3
    skip = "--skip-ai" in sys.argv
    
    process_bookmarks(limit=limit, skip_ai=skip)
