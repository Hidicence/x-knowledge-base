#!/usr/bin/env python3
"""
Generate graph-data.json for the XKB demo UI.

Sources:
  - wiki/topics/*.md  → topic nodes (title + wiki content preview)
  - search_index.json → card nodes + concept (tag) nodes

Three-layer graph: Topic → Concept → Card

Usage:
    python demo/generate_graph.py
    OPENCLAW_WORKSPACE=/custom/path python demo/generate_graph.py
"""
import json
import os
import re
from pathlib import Path
from collections import Counter, defaultdict
import networkx as nx

# ── paths ────────────────────────────────────────────────────────────────────
_SCRIPT_DIR   = Path(__file__).resolve().parent
_PROJECT_ROOT = _SCRIPT_DIR.parent

WORKSPACE = Path(os.getenv(
    "OPENCLAW_WORKSPACE",
    os.getenv("WORKSPACE_DIR", str(_PROJECT_ROOT))
))
INDEX_PATH = Path(os.getenv(
    "XKB_INDEX_PATH",
    str(WORKSPACE / "memory" / "bookmarks" / "search_index.json")
))
WIKI_DIR = Path(os.getenv(
    "XKB_WIKI_DIR",
    str(WORKSPACE / "wiki" / "topics")
))
OUT_PATH = Path(os.getenv(
    "XKB_GRAPH_OUT",
    str(_SCRIPT_DIR / "xkb-demo-ui" / "public" / "graph-data.json")
))

CARD_CAP    = 250   # total card nodes
PER_CAT_CAP = 150   # max cards per category

SKIP_CATS = {"<category or empty>", ""}

CAT_TO_WIKI = {
    "01-openclaw-workflows":  "openclaw-agent-workflows",
    "openclaw-workflows":     "openclaw-agent-workflows",
    "02-seo-geo":             "ai-seo-and-geo",
    "03-video-prompts":       "video-prompt-patterns",
    "video":                  "ai-video-workflows",
    "04-ai-tools-agents":     "ai-agent-memory-systems",
    "ai-tools":               "ai-agent-memory-systems",
    "05-knowledge-management":"ai-agent-memory-systems",
    "general":                "learning-base",
    "99-general":             "learning-base",
    "tech":                   "learning-base",
}


# ── helpers ───────────────────────────────────────────────────────────────────
def _strip_frontmatter(text: str) -> str:
    return re.sub(r"^---[\s\S]*?---\n", "", text, count=1)


def _parse_wiki(path: Path) -> dict:
    """
    Return title, description, and a list of {heading, body} sections
    extracted from a wiki topic markdown file.
    """
    text = path.read_text(encoding="utf-8")

    # Title from frontmatter or first H1/H2
    fm_title = re.search(r"^title:\s*(.+)$", text, re.MULTILINE)
    h_title  = re.search(r"^#{1,2}\s+(.+)$", text, re.MULTILINE)
    title_m  = fm_title or h_title
    title    = title_m.group(1).strip() if title_m else path.stem.replace("-", " ").title()

    body = _strip_frontmatter(text)

    # Split into sections by H2 headings
    section_parts = re.split(r"^##\s+(.+)$", body, flags=re.MULTILINE)
    sections: list[dict] = []
    # section_parts = [pre-text, heading1, body1, heading2, body2, ...]
    i = 1
    while i + 1 < len(section_parts):
        heading = section_parts[i].strip()
        content = section_parts[i + 1].strip()
        # Extract bold-headline items within the section (each **X**\ntext block)
        items = []
        for m in re.finditer(r"\*\*(.{4,80}?)\*\*\n+((?:[^\n#*].{0,400}\n?)+)", content):
            items.append({
                "title": m.group(1).strip(),
                "body": m.group(2).strip()[:300],
            })
        if not items:
            # Plain text section — use first 300 chars
            plain = re.sub(r"\*\*|\*|`", "", content)[:300].strip()
            if plain:
                items.append({"title": "", "body": plain})
        sections.append({"heading": heading, "items": items[:6]})
        i += 2

    # Description: first non-heading paragraph
    paragraphs = [p.strip() for p in re.split(r"\n{2,}", body) if p.strip()]
    desc = ""
    for p in paragraphs:
        if not p.startswith("#"):
            desc = re.sub(r"\*\*|\*|`", "", p)[:200].strip()
            break

    return {"title": title, "description": desc, "sections": sections}


def _read_card_body(item: dict) -> str:
    """Read full markdown body from the card file (excluding frontmatter)."""
    basename = Path(item.get("path", item.get("relative_path", ""))).name
    rel = item.get("relative_path", "")
    candidates = [
        WORKSPACE / "memory" / "cards" / basename,                # enriched card (highest priority)
        WORKSPACE / rel,                                          # relative to workspace (memory/cards/...)
        WORKSPACE / "memory" / "bookmarks" / rel,                 # relative to bookmarks dir
    ]
    # Fallback: scan all bookmark category subdirs by basename
    bm_root = WORKSPACE / "memory" / "bookmarks"
    if bm_root.exists() and basename:
        for subdir in bm_root.iterdir():
            if subdir.is_dir():
                candidates.append(subdir / basename)
    for p in candidates:
        if p.exists():
            try:
                return _strip_frontmatter(p.read_text(encoding="utf-8")).strip()
            except Exception:
                continue
    return ""


def _clean_cat_label(cat: str) -> str:
    cat = re.sub(r"^\d+-", "", cat)
    return cat.replace("-", " ").strip().title()


# ── load bookmark data ────────────────────────────────────────────────────────
raw      = json.loads(INDEX_PATH.read_text(encoding="utf-8"))
all_items = raw.get("items", raw) if isinstance(raw, dict) else raw

enriched = [
    i for i in all_items
    if i.get("title") and i.get("summary") and len(i.get("title", "")) > 5
    and (i.get("category") or "") not in SKIP_CATS
]

by_cat: dict[str, list] = defaultdict(list)
for item in enriched:
    cat = item.get("category", "general") or "general"
    by_cat[cat].append(item)

# Balanced round-robin sampling
sampled: list[dict] = []
cat_pools = {cat: items[:PER_CAT_CAP] for cat, items in by_cat.items()}
idx = {cat: 0 for cat in cat_pools}
while len(sampled) < CARD_CAP:
    added = False
    for cat in sorted(cat_pools):
        if idx[cat] < len(cat_pools[cat]):
            sampled.append(cat_pools[cat][idx[cat]])
            idx[cat] += 1
            added = True
            if len(sampled) >= CARD_CAP:
                break
    if not added:
        break

# ── build nodes / edges ───────────────────────────────────────────────────────
nodes: list[dict] = []
edges: list[dict] = []
node_id_set: set[str] = set()


def _add_node(n: dict):
    if n["id"] not in node_id_set:
        nodes.append(n)
        node_id_set.add(n["id"])


# 1. Wiki topic nodes — rich content stored for click-to-view
if WIKI_DIR.exists():
    for md in sorted(WIKI_DIR.glob("*.md")):
        slug = md.stem
        wiki = _parse_wiki(md)
        _add_node({
            "id":          f"topic-{slug}",
            "type":        "topic",
            "label":       wiki["title"],
            "description": wiki["description"],
            "wikiSlug":    slug,
            "wikiSections": wiki["sections"],
            "val":         12,
            "color":       "#7c3aed",
        })
else:
    print(f"[warn] wiki/topics not found at {WIKI_DIR}")

# Fallback topic for unmapped categories
for cat in sorted(by_cat):
    slug = CAT_TO_WIKI.get(cat, cat)
    nid  = f"topic-{slug}"
    if nid not in node_id_set:
        _add_node({
            "id":    nid,
            "type":  "topic",
            "label": _clean_cat_label(slug),
            "val":   10,
            "color": "#7c3aed",
        })

# 2. Concept (tag) nodes — top 25 across sampled cards
all_tags = [t for i in sampled for t in (i.get("tags") or [])]
top_tags = [t for t, _ in Counter(all_tags).most_common(25)]
top_tag_set = set(top_tags)

for tag in top_tags:
    _add_node({
        "id":    f"concept-{tag}",
        "type":  "concept",
        "label": tag,
        "val":   4,
        "color": "#0891b2",
    })

# 3. Card nodes + edges
for i, item in enumerate(sampled):
    nid   = f"card-{i}"
    title = item.get("title", "")
    _add_node({
        "id":    nid,
        "type":  "card",
        "label": title[:45] + ("…" if len(title) > 45 else ""),
        "val":   2,
        "color": "#3b82f6",
        "data": {
            "title":      title,
            "summary":    item.get("summary", ""),
            "body":       _read_card_body(item),
            "tags":       (item.get("tags") or [])[:6],
            "category":   item.get("category", ""),
            "source_url": item.get("source_url", ""),
        },
    })

    cat       = item.get("category", "") or ""
    wiki_slug = CAT_TO_WIKI.get(cat, cat)
    topic_nid = f"topic-{wiki_slug}"
    if topic_nid in node_id_set:
        edges.append({"source": nid, "target": topic_nid, "type": "belongs_to"})

    for tag in (item.get("tags") or []):
        if tag in top_tag_set:
            edges.append({"source": nid, "target": f"concept-{tag}", "type": "mentions"})

# ── prune topics with no cards ────────────────────────────────────────────────
connected_targets = {e["target"] for e in edges}
nodes = [n for n in nodes if n["type"] != "topic" or n["id"] in connected_targets]
node_id_set = {n["id"] for n in nodes}
edges = [e for e in edges if e["source"] in node_id_set and e["target"] in node_id_set]

# ── betweenness centrality → bridge nodes ─────────────────────────────────────
G = nx.Graph()
G.add_nodes_from(n["id"] for n in nodes)
G.add_edges_from((e["source"], e["target"]) for e in edges)

centrality = nx.betweenness_centrality(G, normalized=True)

# Store centrality on each node
node_map = {n["id"]: n for n in nodes}
for nid, score in centrality.items():
    if nid in node_map:
        node_map[nid]["centrality"] = round(score, 4)

# Top bridge nodes: concepts + topics with highest centrality
bridge_candidates = [
    n for n in nodes if n["type"] in ("concept", "topic") and n.get("centrality", 0) > 0
]
bridge_candidates.sort(key=lambda n: n["centrality"], reverse=True)
bridge_nodes = []
for n in bridge_candidates[:8]:
    # Count how many distinct topics this node connects to (via cards)
    neighbor_ids = set(G.neighbors(n["id"]))
    connected_topics = [
        node_map[nid]["label"]
        for nid in neighbor_ids
        if nid in node_map and node_map[nid]["type"] == "topic"
    ]
    connected_topics += [
        node_map[e["target"]]["label"]
        for nid in neighbor_ids
        if nid in node_map and node_map[nid]["type"] == "card"
        for e in edges
        if e["source"] == nid and e["target"] in node_map and node_map[e["target"]]["type"] == "topic"
    ]
    bridge_nodes.append({
        "id":         n["id"],
        "label":      n["label"],
        "type":       n["type"],
        "centrality": n["centrality"],
        "degree":     G.degree(n["id"]),
        "topics":     list(dict.fromkeys(connected_topics))[:4],
    })

# ── write ─────────────────────────────────────────────────────────────────────
OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
OUT_PATH.write_text(
    json.dumps(
        {"nodes": nodes, "edges": edges, "bridge_nodes": bridge_nodes},
        ensure_ascii=False, indent=2,
    ),
    encoding="utf-8",
)
topic_count   = sum(1 for n in nodes if n["type"] == "topic")
concept_count = sum(1 for n in nodes if n["type"] == "concept")
card_count    = sum(1 for n in nodes if n["type"] == "card")
print(
    f"Generated: {len(nodes)} nodes "
    f"({topic_count} topics, {concept_count} concepts, {card_count} cards), "
    f"{len(edges)} edges, {len(bridge_nodes)} bridge nodes → {OUT_PATH}"
)
