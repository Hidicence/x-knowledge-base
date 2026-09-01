#!/usr/bin/env python3
"""
Build vector index for x-knowledge-base semantic search.

Reads search_index.json, embeds each card's title + summary,
and saves vectors to vector_index.json in BOOKMARKS_DIR.

Usage:
    python3 scripts/build_vector_index.py
    python3 scripts/build_vector_index.py --incremental   # skip already-embedded cards
    python3 scripts/build_vector_index.py --dry-run       # show what would be done

Requires:
    EMBEDDING_PROVIDER=gemini|openai|ollama
    + corresponding API key (see .env.example)
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

# Allow running from scripts/ directory or skill root
sys.path.insert(0, str(Path(__file__).resolve().parent))
import xkb_paths

_SKILL_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_SKILL_DIR))
from tools.embedding_providers import get_provider

WORKSPACE_DIR = xkb_paths.WORKSPACE
BOOKMARKS_DIR = xkb_paths.BOOKMARKS_DIR
INDEX_FILE = xkb_paths.INDEX_FILE
VECTOR_FILE = xkb_paths.VECTOR_FILE
# wiki/memory 的向量另外存一份小檔。召回時只需要這 ~1,900 個向量，
# 但它們混在 8,000 多個卡片向量裡，載入整個 62MB 檔案要 5.4 秒——
# 而召回是每句話都要跑的。分開存之後只需要載入十分之一。
SEMANTIC_FILE = BOOKMARKS_DIR / "semantic_index.json"
# 卡片向量另存一份。它不像 wiki/記憶檔那樣要整份掃過——
# 卡片是先由 gbrain 挑出候選，我們只需要驗證那幾張的相關度，
# 所以讀取端用 seek 取單筆，不必把整份載進來。
CARDS_FILE = BOOKMARKS_DIR / "cards_index.json"


# ── Text extraction ───────────────────────────────────────────────────────────

def _resolve_md_path(item: dict) -> Path | None:
    """索引項路徑解析：relative_path 是相對 workspace（如 memory/cards/x.md），
    舊程式誤用 BOOKMARKS_DIR 拼接導致永遠讀不到卡片（2026-07-14 修正）。"""
    abs_path = item.get("path") or ""
    if abs_path and Path(abs_path).exists():
        return Path(abs_path)
    rel = item.get("relative_path") or ""
    if not rel:
        return None
    for base in (WORKSPACE_DIR, BOOKMARKS_DIR):
        p = base / rel
        if p.exists():
            return p
    return None


def _extract_key_points_from_md(md_path: Path) -> str:
    """Extract the 關鍵論點/三個重點 section from a card markdown file.
    （2026-07-14：新版 9-section 卡片段落名為「關鍵論點」，一併支援舊名）"""
    if not md_path.exists():
        return ""
    try:
        content = md_path.read_text(encoding="utf-8")
        m = re.search(
            r'##\s+[^\n]*(?:關鍵論點|三個重點)[^\n]*\n(.+?)(?=\n##|\Z)',
            content, re.DOTALL
        )
        if m:
            text = re.sub(r'^[\-\*\•]\s*', '', m.group(1).strip(), flags=re.MULTILINE)
            return text.strip()
    except Exception:
        pass
    return ""


def _extract_key_point_list(md_path: Path) -> list[str]:
    """論點級 embedding（TODOS 2026-07-13）：回傳「三個重點」逐條列表，
    每條將單獨成向量（鍵 relpath#kpN），召回粒度從卡片級升到論點級。"""
    text = _extract_key_points_from_md(md_path)
    if not text:
        return []
    points = [ln.strip() for ln in text.splitlines() if len(ln.strip()) >= 10]
    return points[:5]


def extract_card_text(item: dict) -> str:
    """Build embeddable text from a search index item.
    Uses title + summary (already extracted by build_search_index.sh).
    Falls back to reading the .md file if summary is empty.
    """
    title = (item.get("title") or "").strip()
    summary = (item.get("summary") or "").strip()
    key_points = ""

    md_path = _resolve_md_path(item)
    if md_path:
        if not summary:
            summary = _extract_summary_from_md(md_path)
        key_points = _extract_key_points_from_md(md_path)

    parts = [p for p in [title, summary, key_points] if p]
    return ". ".join(parts)[:900]  # cap at 900 chars (expanded for key points)


def _extract_summary_from_md(md_path: Path) -> str:
    if not md_path.exists():
        return ""
    try:
        content = md_path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return ""

    # New format: ## 📝 一句話摘要 / Old format: ## 摘要
    for pattern in [
        r"##\s+[^\n]*一句[話话]摘要\s*\n(.+?)(?=\n##|\Z)",
        r"##\s+摘要\s*\n(.+?)(?=\n##|\Z)",
    ]:
        m = re.search(pattern, content, re.DOTALL)
        if m:
            text = m.group(1).strip().replace("\n", " ")
            text = re.sub(r"\s+", " ", text)
            return text[:300]
    return ""


# ── Cosine similarity (pure Python, no numpy) ─────────────────────────────────

def cosine_similarity(a: list, b: list) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    denom = norm_a * norm_b
    return dot / denom if denom > 1e-9 else 0.0


# ── Load / save ───────────────────────────────────────────────────────────────

def load_vector_index(path: Path) -> dict:
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    return {"meta": {}, "vectors": {}}


def save_vector_index(data: dict, path: Path) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


# ── Wiki sections ─────────────────────────────────────────────────────────────

# 一段太短的內容嵌入後幾乎沒有訊息量，只會製造假命中
MIN_SECTION_CHARS = 80
# 一段太長會把主題稀釋掉，超過就截斷
MAX_SECTION_CHARS = 4000


def _chunks(header: str, body: str) -> list[tuple[str, str]]:
    """Split one section into as many vectors as it needs.

    A section longer than MAX_SECTION_CHARS used to be truncated, so the tail
    was written to the wiki and then silently dropped from the index — 56
    sections were losing their endings that way. Anything past the cap now
    becomes its own vector instead.

    The first chunk is byte-for-byte what truncation produced, so every vector
    already in the index keeps its hash and nothing is re-embedded to catch up;
    only the previously discarded remainder is new.
    """
    room = MAX_SECTION_CHARS - len(header)
    if room <= 0 or len(body) <= room:
        return [("", f"{header}{body}"[:MAX_SECTION_CHARS])]

    out = [("", f"{header}{body}"[:MAX_SECTION_CHARS])]
    start = room
    index = 2
    while start < len(body):
        end = min(start + room, len(body))
        if end < len(body):
            # Prefer a line break near the end of the window so a chunk does
            # not begin mid-sentence. Deterministic, so hashes stay stable.
            split = body.rfind("\n", start + int(room * 0.8), end)
            if split > start:
                end = split
        out.append((f"@{index}", header + body[start:end].strip()))
        start = end
        index += 1
    return out


def knowledge_section_docs() -> list[tuple[str, str, str]]:
    """把 wiki 主題頁與記憶檔切段，每段一個向量。回傳 [(key, text, hash)]。

    為什麼要做這件事：向量索引原本只涵蓋卡片。wiki——也就是使用者自己消化過、
    訊號密度最高的那一層——完全沒有語意搜尋，只能靠字串比對撈。
    中文沒有空格，字串比對本來就弱，於是「我們之前怎麼處理碳盤查的」會撈回
    一堆只命中「之前」「怎麼」「處理」的無關結果。那不是門檻調不好，
    是那一層根本沒有語意能力。

    切到「段」而不是整頁：一頁 680K 的主題頁嵌成單一向量，等於什麼都不像。
    """
    sources: list[tuple[Path, str]] = []
    if xkb_paths.WIKI_TOPICS_DIR.exists():
        sources += [(p, "wiki/topics") for p in sorted(xkb_paths.WIKI_TOPICS_DIR.glob("*.md"))]
    # 記憶檔也要語意化：hard trigger（「我們之前怎麼…」）查的就是這一層，
    # 而它原本同樣只有字串比對，靠功能詞就能命中。
    if xkb_paths.DATA_DIR.exists():
        sources += [(p, "memory") for p in sorted(xkb_paths.DATA_DIR.glob("*.md"))]
    if xkb_paths.MEMORY_MD.exists():
        sources.append((xkb_paths.MEMORY_MD, "memory"))

    docs: list[tuple[str, str, str]] = []
    for path, prefix in sources:
        try:
            content = path.read_text(encoding="utf-8")
        except OSError:
            continue
        content = re.sub(r"^---\n.*?\n---\n", "", content, flags=re.DOTALL)

        title = path.stem
        section = ""
        buffer: list[str] = []
        # A heading can repeat inside one file — memory/2026-05-09.md has
        # "教訓" fifteen times. Every copy produced the same key, so each
        # run kept a vector for the last of them and re-embedded the rest:
        # about a hundred sections that could never go incremental, paying
        # for an embedding every day. Later occurrences get a suffix; the
        # first keeps the original key so existing vectors stay valid.
        seen_keys: dict[str, int] = {}

        def flush(section_name: str, lines: list[str]) -> None:
            body = "\n".join(lines).strip()
            if len(body) < MIN_SECTION_CHARS:
                return
            key = f"{prefix}/{path.name}#{section_name or 'intro'}"
            occurrence = seen_keys.get(key, 0) + 1
            seen_keys[key] = occurrence
            if occurrence > 1:
                key = f"{key}~{occurrence}"
            for suffix, text in _chunks(f"{title} — {section_name}\n", body):
                docs.append((key + suffix, text,
                             hashlib.md5(text.encode("utf-8")).hexdigest()[:12]))

        for line in content.splitlines():
            if re.match(r"^#{1,3} .+", line):
                flush(section, buffer)
                section = re.sub(r"^#{1,3} ", "", line).strip()
                buffer = []
            else:
                buffer.append(line)
        flush(section, buffer)

    return docs


def write_semantic_index(vectors: dict[str, list[float]], path: Path) -> None:
    """寫出 <path>.bin（float32 單位向量）與 <path>（keys + dims 的 JSON）。"""
    import array

    keys = sorted(vectors)
    dims = len(vectors[keys[0]])
    packed = array.array("f")
    for key in keys:
        vec = vectors[key]
        norm = math.sqrt(sum(x * x for x in vec)) or 1.0
        packed.extend(x / norm for x in vec)

    payload = packed.tobytes()
    path.with_suffix(".bin").write_bytes(payload)
    # 記下位元組序、每個浮點數幾個位元組、總筆數：
    # 這三項任何一項對不上，切片出來的向量就是錯的，而且不會拋錯——
    # 只會安靜地變成空向量或錯位資料，讀取端據此驗證後才敢用。
    path.write_text(json.dumps({
        "dims": dims,
        "keys": keys,
        "count": len(keys),
        "normalized": True,
        "byteorder": sys.byteorder,
        "itemsize": packed.itemsize,
        "bytes": len(payload),
    }, ensure_ascii=False), encoding="utf-8")


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser(description="Build vector index for x-knowledge-base")
    parser.add_argument("--incremental", action="store_true",
                        help="Skip cards already in vector_index.json")
    parser.add_argument("--dry-run", action="store_true",
                        help="Show what would be done without calling embedding API")
    parser.add_argument("--batch-size", type=int, default=50)
    parser.add_argument("--index-file", default=str(INDEX_FILE))
    parser.add_argument("--vector-file", default=str(VECTOR_FILE))
    parser.add_argument("--env-file", help="dotenv file for credentials/config (process environment wins)")
    args = parser.parse_args()

    index_path = Path(args.index_file)
    vector_path = Path(args.vector_file)

    # Load search index
    if not index_path.exists():
        print(f"❌ search_index.json not found: {index_path}", file=sys.stderr)
        return 1

    raw = json.loads(index_path.read_text(encoding="utf-8"))
    items = raw.get("items", raw) if isinstance(raw, dict) else raw
    print(f"📚 Loaded {len(items)} cards from {index_path}")

    # Load existing vectors (for incremental mode)
    existing = load_vector_index(vector_path)
    existing_vectors: dict = existing.get("vectors", {})

    # Determine which cards to embed
    existing_hashes: dict = existing.get("text_hashes", {})

    to_embed = []
    # 這一次「看過」的每一個鍵，不管有沒有排進佇列。清理要靠它，不能靠
    # queued_keys：增量模式只是跳過『排進佇列』，來源一樣每個都走過。
    # 把兩者混為一談，就會把沒變動的段落當成消失了而刪掉。
    enumerated_keys: set[str] = set()

    # wiki 段落：把消化過的那一層也納入語意搜尋
    for wiki_key, wiki_text, wiki_hash in knowledge_section_docs():
        enumerated_keys.add(wiki_key)
        if not (args.incremental and wiki_key in existing_vectors
                and existing_hashes.get(wiki_key) == wiki_hash):
            to_embed.append((wiki_key, wiki_text, wiki_hash))
    print(f"📖 Wiki/memory sections queued: {len(to_embed)}")

    for item in items:
        key = item.get("relative_path") or item.get("path") or ""
        card_text = extract_card_text(item)
        if not card_text.strip():
            print(f"  ⚠️  No text for: {key}")
            continue
        text_hash = hashlib.md5(card_text.encode("utf-8")).hexdigest()[:12]
        md_path = _resolve_md_path(item)
        if md_path is None:
            # 找不到 markdown 就列不出論點鍵。這時如果還把卡片鍵算成走過，
            # 這份文件會進 examined_docs，它既有的 #kpN 就全被當成消失而刪掉；
            # 下次路徑正常了再全部重新付費嵌入。那正是這段清理本來要消滅的
            # 振盪，只是換一個觸發條件。
            if args.incremental and key in existing_vectors:
                continue
        else:
            enumerated_keys.add(key)
        if not (args.incremental and key in existing_vectors and existing_hashes.get(key) == text_hash):
            to_embed.append((key, card_text, text_hash))

        # 論點級 embedding（TODOS 2026-07-13）：每條「關鍵論點」單獨成向量
        if md_path:
            title = (item.get("title") or "").strip()
            for pi, point in enumerate(_extract_key_point_list(md_path), 1):
                pkey = f"{key}#kp{pi}"
                enumerated_keys.add(pkey)
                ptext = (f"{title}. {point}" if title else point)[:500]
                phash = hashlib.md5(ptext.encode("utf-8")).hexdigest()[:12]
                if args.incremental and pkey in existing_vectors and existing_hashes.get(pkey) == phash:
                    continue
                to_embed.append((pkey, ptext, phash))

    # Alias for downstream use
    texts = [t for _, t, _ in to_embed]

    # 集合建一次就好。原本寫在生成式的條件裡，於是每一個既有鍵都重建一次
    # 一萬多個元素的集合——完整重跑時是一萬多次。
    queued_keys = {entry[0] for entry in to_embed}
    # 死鍵：這次走過的文件底下，索引裡有、但內容已經不存在的鍵。
    # 跟列舉放在一起算，因為它就是列舉的產物；放到下面去算，會落在
    # 「沒東西要嵌入就提早結束」的後面，而那正是平常的狀態。
    examined_docs = {key.split("#", 1)[0] for key in enumerated_keys}
    stale_keys = [key for key in existing_vectors
                  if key.split("#", 1)[0] in examined_docs
                  and key not in enumerated_keys]
    skipped = sum(1 for k in existing_vectors if k not in queued_keys)
    print(f"🔢 To embed: {len(to_embed)}  |  Skipped (incremental): {skipped}")

    if args.dry_run:
        print("\n[dry-run] First 3 cards that would be embedded:")
        for key, text, _text_hash in to_embed[:3]:
            print(f"  {key}")
            print(f"    → \"{text[:80]}...\"")
        return 0

    # 分區檔不完整就不能提早結束：一旦 semantic_index.bin 或 cards_index.bin
    # 被刪掉或寫到一半，之後每一次增量執行都會「沒有新東西」然後離開，
    # 而召回永遠停在慢的 JSON 路徑上——安靜地、永久地。
    #
    # write_semantic_index 會寫兩個檔（.json 的鍵表與 .bin 的向量），兩個都要在。
    def _partition_ok(path: Path) -> bool:
        return path.exists() and path.with_suffix(".bin").exists()

    # 要問寫入端「你會寫到哪」，不是問常數。下面寫檔時是看
    # XKB_SEMANTIC_INDEX / XKB_CARDS_INDEX 的，測試與備援路徑都會設它們；
    # 檢查常數等於在別的地方確認「檔案還在」，然後對真正在用的那份一無所知。
    _semantic_target = Path(os.getenv("XKB_SEMANTIC_INDEX", str(SEMANTIC_FILE)))
    _cards_target = Path(os.getenv("XKB_CARDS_INDEX", str(CARDS_FILE)))
    _partitions_ok = _partition_ok(_semantic_target) and _partition_ok(_cards_target)
    if stale_keys:
        print(f"🧹 {len(stale_keys)} 個索引鍵已經沒有對應內容，這一輪清掉")
    if not to_embed and _partitions_ok and not stale_keys:
        print("✅ Nothing to embed.")
        return 0
    if not to_embed:
        # 為什麼還要往下走，要說對。只有死鍵要清的時候說「分區索引檔不完整」
        # 是假的，而假的理由會讓下一個人去修一個不存在的問題。
        print("沒有新內容要嵌入，但分區索引檔不完整——從既有向量重新寫出。"
              if not _partitions_ok else
              "沒有新內容要嵌入，只清掉死鍵之後重新寫出。")

    # 沒有東西要嵌入就不要去要嵌入服務。純粹「清死鍵」的那一輪也會走到
    # 這裡，於是憑證不在時整支以 1 收場、而且一個死鍵都沒清掉——
    # 前一天它還只是安靜地印 Nothing to embed。
    provider = None
    if to_embed:
        try:
            provider = get_provider(env_file=args.env_file)
            print(f"🤖 Provider: {provider.__class__.__name__} / model: {getattr(provider, 'model', '?')}")
        except (EnvironmentError, ValueError) as e:
            print(f"❌ {e}", file=sys.stderr)
            return 1

    # Embed in batches
    keys = [k for k, _, _ in to_embed]
    hashes = [h for _, _, h in to_embed]
    vectors_list = []

    batch_size = args.batch_size
    total = len(texts)
    for i in range(0, total, batch_size):
        batch_texts = texts[i:i + batch_size]
        batch_keys = keys[i:i + batch_size]
        print(f"  Embedding {i+1}–{min(i+batch_size, total)}/{total}...", end=" ", flush=True)
        try:
            batch_vecs = provider.embed_batch(batch_texts, batch_size=batch_size)
            vectors_list.extend(zip(batch_keys, batch_vecs))
            print("✓")
        except Exception as e:
            print(f"\n❌ Failed at batch {i//batch_size + 1}: {e}", file=sys.stderr)
            return 1

    # Merge with existing vectors and hashes
    new_vectors = dict(existing_vectors)
    new_hashes = dict(existing_hashes)
    for (key, vec), h in zip(vectors_list, hashes):
        new_vectors[key] = vec
        new_hashes[key] = h

    # 索引原本只增不減。標題改名、記憶檔刪掉、卡片的關鍵論點從五條變三條——
    # 舊向量就永遠留著，照樣參與排序，然後顯示空白。實測 2,262 個 wiki 鍵
    # 裡有 80 個指向已經不存在的段落。
    #
    # 但「這次沒產生」不等於「不存在了」：增量執行本來就只看有變動的東西。
    # 安全的規則是：只清掉「這次有檢查過來源、而這次沒有產生」的鍵。沒被
    # 看過的來源，它的鍵一個都不動。
    # 清理沒有對應內容的索引鍵。這一段我寫錯過三次，每次都是同一個混淆的
    # 不同面向：
    #
    #   一  用 queued_keys 推出「檢查過的來源」。增量模式下，文件只要有一塊
    #       變了就算檢查過，於是它其他沒變的塊全被刪——改過的卡片會在論點鍵
    #       與卡片鍵之間永遠振盪，每晚重新付費嵌入。
    #   二  改成只在完整重建時清理。但每一條排程都帶 --incremental，於是清理
    #       從此不會執行。我把破壞性 bug 修成了一個關掉的功能。
    #   三  規則終於對了，卻放在「沒東西要嵌入就提早結束」的後面——而那正是
    #       平常的狀態。連跑三次，一次都沒清到。
    #
    # 真正的分辨是：**列舉 ≠ 排進佇列**。兩種模式都會走過每一個來源，
    # --incremental 只是跳過「把沒變的排進佇列」。所以要問「這次走到了哪些
    # 鍵」，而那個問題兩種模式的答案一樣好——這條規則因此不需要看模式，
    # 那正是它對的跡象。stale_keys 在上面列舉處算好，這裡只執行。
    #
    # 限定在「這次走過的文件」之內，順帶關掉另一個風險：卡片鍵只來自
    # --index-file，拿一份不完整的索引跑完整重建，沒走過的文件一根寒毛都不會動。
    #
    # 已知殘留：整頁被刪除時它一個段落都不會被列舉，於是它的鍵留著。
    # 這是安全的方向——寧可留著也不要誤刪。
    for key in stale_keys:
        new_vectors.pop(key, None)
        new_hashes.pop(key, None)

    # Save
    output = {
        "meta": {
            "provider": provider.__class__.__name__.replace("Provider", "").lower(),
            "model": getattr(provider, "model", ""),
            "dims": len(vectors_list[0][1]) if vectors_list else existing.get("meta", {}).get("dims", 0),
            "total": len(new_vectors),
            "point_vectors": sum(1 for k in new_vectors if "#kp" in k),
            "built_at": datetime.now(timezone.utc).isoformat(),
        },
        "vectors": new_vectors,
        "text_hashes": new_hashes,
    }
    save_vector_index(output, vector_path)

    # 另存召回用的索引，二進位格式。
    # 存 JSON 的話 1,868 個 3072 維向量要 76MB，光 json.loads 就要 5 秒——
    # 而召回是每句話都要跑的。float32 打包後只有 23MB，載入約 0.1 秒。
    # 順便先做單位化，查詢時只要點積，不必每次算平方根。
    semantic_vectors = {
        k: v for k, v in output.get("vectors", {}).items()
        if (k.startswith("wiki/topics/") or k.startswith("memory/"))
        and not k.startswith("memory/cards/")
    }
    if semantic_vectors:
        semantic_path = Path(os.getenv("XKB_SEMANTIC_INDEX", str(SEMANTIC_FILE)))
        write_semantic_index(semantic_vectors, semantic_path)
        print(f"✅ Saved {len(semantic_vectors)} semantic vectors → {semantic_path.with_suffix('.bin')}")

    card_vectors = {k: v for k, v in output.get("vectors", {}).items()
                    if k not in semantic_vectors}
    if card_vectors:
        cards_path = Path(os.getenv("XKB_CARDS_INDEX", str(CARDS_FILE)))
        write_semantic_index(card_vectors, cards_path)
        print(f"✅ Saved {len(card_vectors)} card vectors → {cards_path.with_suffix('.bin')}")

    print(f"\n✅ Saved {len(new_vectors)} vectors → {vector_path}")
    print(f"   Provider : {output['meta']['provider']} / {output['meta']['model']}")
    print(f"   Dims     : {output['meta']['dims']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
