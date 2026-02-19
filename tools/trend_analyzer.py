#!/usr/bin/env python3
"""
趨勢分析器 - 自我進化核心
根據書籤傾向自動調整關鍵字和趨勢分析
"""

import os
import re
import json
from pathlib import Path
from datetime import datetime, timedelta
from collections import Counter

BOOKMARKS_DIR = Path("/home/ubuntu/clawd/memory/bookmarks")
TRENDS_FILE = Path("/home/ubuntu/clawd/memory/interest-trends.json")
CONFIG_FILE = Path("/home/ubuntu/clawd/skills/x-knowledge-base/config/interests.yaml")

# 預設標籤配置
DEFAULT_TAGS = {
    "ai": {"weight": 1.0, "category": "tech"},
    "video": {"weight": 1.0, "category": "content"},
    "seo": {"weight": 1.0, "category": "marketing"},
    "marketing": {"weight": 1.0, "category": "marketing"},
    "automation": {"weight": 1.0, "category": "tech"},
    "workflow": {"weight": 1.0, "category": "tech"},
    "prompt": {"weight": 1.0, "category": "tech"},
    "mcp": {"weight": 1.0, "category": "tech"},
}

def get_all_tags():
    """從所有書籤擷取標籤"""
    tags_counter = Counter()
    tag_timeline = {}  # 每個標籤的時間線
    
    for f in BOOKMARKS_DIR.rglob("*.md"):
        if f.name.startswith("."): continue
        if f.name in ["INDEX.md", "urls.txt"]: continue
        
        content = f.read_text(encoding='utf-8')
        
        # 擷取標籤
        tags = re.findall(r'#(\w+)', content)
        
        # 從檔名或日期估算時間
        date_match = re.search(r'(\d{4}-\d{2}-\d{2})', f.name)
        if date_match:
            date = date_match.group(1)
        else:
            # 使用檔案修改時間
            date = datetime.fromtimestamp(f.stat().st_mtime).strftime('%Y-%m-%d')
        
        for tag in tags:
            tags_counter[tag] += 1
            if tag not in tag_timeline:
                tag_timeline[tag] = []
            tag_timeline[tag].append(date)
    
    return tags_counter, tag_timeline

def calculate_trends(tags_counter, tag_timeline):
    """計算趨勢分數"""
    trends = {}
    
    # 取得前一週的數據作為基準
    today = datetime.now()
    week_ago = today - timedelta(days=7)
    
    for tag, count in tags_counter.items():
        dates = sorted(tag_timeline.get(tag, []))
        
        # 計算這週 vs 上週
        recent_count = sum(1 for d in dates if d >= week_ago.strftime('%Y-%m-%d'))
        older_count = count - recent_count
        
        # 趨勢計算
        if older_count > 0:
            change_percent = ((recent_count - older_count/2) / older_count) * 100
        elif recent_count > 0:
            change_percent = 100  # 新標籤
        else:
            change_percent = 0
        
        # 分類
        if change_percent > 50:
            status = "rising"  # 上升
        elif change_percent < -30:
            status = "falling"  # 下降
        else:
            status = "stable"  # 穩定
        
        trends[tag] = {
            "count": count,
            "recent": recent_count,
            "trend": change_percent,
            "status": status
        }
    
    return trends

def detect_emerging_trends(trends, threshold=50):
    """偵測新興趨勢（快速上升的標籤）"""
    emerging = []
    
    for tag, data in trends.items():
        if data["trend"] > threshold and data["count"] >= 3:
            emerging.append({
                "tag": tag,
                "trend": data["trend"],
                "count": data["count"]
            })
    
    # 按趨勢排序
    emerging.sort(key=lambda x: x["trend"], reverse=True)
    return emerging

def generate_recommended_keywords(trends, top_n=5):
    """根據趨勢生成推薦關鍵字"""
    # 權重：上升趨勢 > 穩定 > 下降
    weighted = []
    
    for tag, data in trends.items():
        if data["status"] == "rising":
            weight = 1.5
        elif data["status"] == "falling":
            weight = 0.5
        else:
            weight = 1.0
        
        score = data["count"] * weight
        weighted.append((tag, score))
    
    # 排序
    weighted.sort(key=lambda x: x[1], reverse=True)
    
    # 轉換為關鍵字格式
    keywords = []
    for tag, score in weighted[:top_n]:
        keywords.append(f"{tag} AI")
        keywords.append(f"{tag} trends 2026")
    
    return keywords[:top_n * 2]

def analyze_interest_shift(trends):
    """分析興趣轉變"""
    rising = []
    falling = []
    
    for tag, data in trends.items():
        if data["status"] == "rising" and data["count"] >= 3:
            rising.append(tag)
        elif data["status"] == "falling" and data["count"] >= 5:
            falling.append(tag)
    
    return {
        "rising": rising,
        "falling": falling,
        "summary": f"興趣從 {', '.join(falling[:3]) if falling else '無'} 轉向 {', '.join(rising[:3]) if rising else '無'}"
    }

def save_trends(trends, emerging, keywords, shift):
    """儲存趨勢數據"""
    data = {
        "last_updated": datetime.now().isoformat(),
        "trends": trends,
        "emerging": emerging,
        "recommended_keywords": keywords,
        "interest_shift": shift
    }
    
    TRENDS_FILE.parent.mkdir(parents=True, exist_ok=True)
    TRENDS_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    
    return data

def generate_report():
    """產生趨勢報告"""
    print("📊 興趣趨勢分析報告")
    print("=" * 50)
    
    # 取得標籤數據
    tags_counter, tag_timeline = get_all_tags()
    print(f"✅ 找到 {len(tags_counter)} 個標籤")
    
    # 計算趨勢
    trends = calculate_trends(tags_counter, tag_timeline)
    
    # 偵測新興趨勢
    emerging = detect_emerging_trends(trends)
    
    # 生成推薦關鍵字
    keywords = generate_recommended_keywords(trends)
    
    # 分析興趣轉變
    shift = analyze_interest_shift(trends)
    
    # 儲存
    data = save_trends(trends, emerging, keywords, shift)
    
    # 顯示報告
    print(f"\n🔥 新興趨勢:")
    for e in emerging[:5]:
        print(f"  - {e['tag']}: +{e['trend']:.0f}% ({e['count']} 篇)")
    
    print(f"\n📈 推薦關鍵字:")
    for kw in keywords[:5]:
        print(f"  - {kw}")
    
    print(f"\n🔄 興趣轉變:")
    print(f"  {shift['summary']}")
    
    return data

if __name__ == "__main__":
    generate_report()
