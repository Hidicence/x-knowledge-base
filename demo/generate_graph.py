#!/usr/bin/env python3
"""
Generate graph-data.json for the XKB demo UI from search_index.json.

Usage:
    python3 demo/generate_graph.py
    OPENCLAW_WORKSPACE=/custom/path python3 demo/generate_graph.py
    OUT_PATH=/custom/output.json python3 demo/generate_graph.py
"""
import json
import os
import re
from pathlib import Path
from collections import Counter

WORKSPACE = Path(os.getenv(
    "OPENCLAW_WORKSPACE",
    os.getenv("WORKSPACE_DIR", str(Path.home() / ".openclaw" / "workspace"))
))
INDEX_PATH = Path(os.getenv(
    "XKB_INDEX_PATH",
    str(WORKSPACE / "memory" / "bookmarks" / "search_index.json")
))
_SKILL_DIR = Path(__file__).resolve().parent
OUT_PATH = Path(os.getenv(
    "XKB_GRAPH_OUT",
    str(_SKILL_DIR / "xkb-demo-ui" / "public" / "graph-data.json")
))

data = json.loads(INDEX_PATH.read_text(encoding="utf-8"))
items = data.get("items", data) if isinstance(data, dict) else data

# Use enriched cards with titles + summaries
cards = [i for i in items if i.get("title") and i.get("summary") and len(i.get("title", "")) > 5]
cards = cards[:120]  # cap for performance

# Top tags → concept nodes
all_tags = [t for i in cards for t in (i.get("tags") or [])]
top_tags = [t for t, _ in Counter(all_tags).most_common(25)]
top_tag_set = set(top_tags)

# Categories → topic nodes
categories = sorted({i["category"] for i in cards if i.get("category") and i["category"] != "other"})


def clean_cat(cat: str) -> str:
    cat = re.sub(r"^\d+-", "", cat)
    cat = cat.replace("-", " ").strip()
    return cat.title() if cat else cat


nodes = []
edges = []

# Topic nodes
for cat in categories:
    nodes.append({
        "id": f"topic-{cat}",
        "type": "topic",
        "label": clean_cat(cat),
        "rawCat": cat,
        "val": 8,
        "color": "#7c3aed",
    })

# Concept (tag) nodes
for tag in top_tags:
    nodes.append({
        "id": f"concept-{tag}",
        "type": "concept",
        "label": tag,
        "val": 4,
        "color": "#0891b2",
    })

# Card nodes
for i, item in enumerate(cards):
    nid = f"card-{i}"
    title = item.get("title", "")
    nodes.append({
        "id": nid,
        "type": "card",
        "label": title[:45] + ("…" if len(title) > 45 else ""),
        "val": 2,
        "color": "#3b82f6",
        "data": {
            "title": title,
            "summary": item.get("summary", ""),
            "tags": item.get("tags", [])[:6],
            "category": item.get("category", ""),
            "source_url": item.get("source_url", ""),
            "searchable": item.get("searchable", ""),
        },
    })

    # Edge: card → topic
    cat = item.get("category", "")
    if cat and cat != "other" and any(n["id"] == f"topic-{cat}" for n in nodes):
        edges.append({"source": nid, "target": f"topic-{cat}", "type": "belongs_to"})

    # Edge: card → concepts
    for tag in (item.get("tags") or []):
        if tag in top_tag_set:
            edges.append({"source": nid, "target": f"concept-{tag}", "type": "mentions"})

OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
OUT_PATH.write_text(json.dumps({"nodes": nodes, "edges": edges}, ensure_ascii=False, indent=2))
print(f"Generated: {len(nodes)} nodes, {len(edges)} edges → {OUT_PATH}")
