#!/usr/bin/env python3
"""
用向量做吸收閘門 — 不需要對話模型

閘門要回答的問題是「這張卡對這個主題頁**多出**了什麼」。原本靠對話 LLM 判斷，
但 LLM 是最容易掛的東西（2026-07-29 當天四個模型全部 503），
而 embedding 是另一條路、當時仍然正常。

判準兩道，量的是不同的東西：

    主題契合度 = 與該頁段落的最高相似度。太低表示根本不是在講這個主題。
    重複度     = 與**已經收錄進該頁的卡片**的最高相似度。太高表示已經有人講過。

這兩個不能共用一個數字。第一版拿「與頁面很像」當重複，實測是錯的：
ai-video-workflows 這頁滿是 Seedance 內容，於是每張講 Seedance 的卡都跟它很像，
0.86 與 0.798 講的是同一類東西，差別只是深淺。
跟頁面像代表主題對，跟已收錄的卡片像才代表重複。

每個主題再設上限。1,202 張候選攤到 14 個主題是每頁 85 張，
那不是消化，是傾倒；visual-ai-private-arsenal 已經 680K 就是這樣來的。

Usage:
    python3 scripts/absorb_gate_semantic.py --review
    python3 scripts/absorb_gate_semantic.py --review --topic ai-video-workflows
    python3 scripts/absorb_gate_semantic.py --write-decisions   # 寫入人工決策清單
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import xkb_paths
import continuity_recall as cr
import sync_cards_to_wiki as sync

MIN_RELEVANCE = float(os.getenv("XKB_ABSORB_MIN_RELEVANCE", "0.55"))
# 卡片之間的相似度。0.92 以上才算「同一件事被講兩次」——
# 同主題的兩張卡本來就會有 0.7~0.8，那不是重複。
MAX_REDUNDANCY = float(os.getenv("XKB_ABSORB_MAX_REDUNDANCY", "0.92"))
PER_TOPIC_CAP = int(os.getenv("XKB_ABSORB_PER_TOPIC_CAP", "25"))


def topic_section_vectors(vectors: dict) -> dict[str, list[list[float]]]:
    """把 wiki 段落向量依主題分組。"""
    grouped: dict[str, list[list[float]]] = {}
    prefix = "wiki/topics/"
    for key, vec in vectors.items():
        if not key.startswith(prefix):
            continue
        stem = key[len(prefix):].split("#", 1)[0]
        grouped.setdefault(Path(stem).stem, []).append(vec)
    return grouped


def is_absorbable(topic: str) -> bool:
    """redirect 頁與沒有 frontmatter 的頁面不是吸收目標。

    gpt-image-2-private-taxonomy 與 visual-ai-workflow-node-catalog 都是
    指向 visual-ai-private-arsenal 的轉址頁；往裡面寫等於寫進一個沒人會讀的殼，
    而且 sync_cards_to_wiki 解析 frontmatter 時會直接拋錯中斷整輪。
    """
    path = xkb_paths.WIKI_TOPICS_DIR / f"{topic}.md"
    try:
        head = path.read_text(encoding="utf-8")[:200]
    except OSError:
        return False
    return head.startswith("---") and "Redirect:" not in head


def main() -> int:
    parser = argparse.ArgumentParser(description="Semantic absorb gate")
    parser.add_argument("--review", action="store_true", help="只顯示結果")
    parser.add_argument("--write-decisions", action="store_true",
                        help="把判斷寫進 review-decisions.json 的人工清單")
    parser.add_argument("--topic", help="只處理某個主題")
    parser.add_argument("--cap", type=int, default=PER_TOPIC_CAP, help="每個主題最多收幾張")
    args = parser.parse_args()

    vectors = cr._load_semantic_vectors()
    by_topic = topic_section_vectors(vectors)
    if not by_topic:
        print("找不到 wiki 段落向量——先跑 build_vector_index.py", file=sys.stderr)
        return 1

    items = sync.load_search_items()
    url_to_path = {i.get("source_url"): (i.get("relative_path") or i.get("path") or "")
                   for i in items if i.get("source_url")}
    existing_by_topic = sync.collect_topic_existing_urls()
    grouped = sync.iter_mapped_cards(
        items, sync.load_topic_map(), args.topic,
        existing_by_topic, sync.load_review_decisions(),
    )

    allow: dict[str, list[str]] = {}
    skip: dict[str, list[str]] = {}
    summary: list[tuple[str, int, int, int, int]] = []

    for topic, cards in sorted(grouped.items()):
        sections = by_topic.get(topic)
        if not sections:
            print(f"  {topic}: 沒有段落向量，略過")
            continue
        if not is_absorbable(topic):
            print(f"  {topic}: 是 redirect 或缺 frontmatter 的頁面，不吸收")
            continue

        # 已經收錄進這一頁的卡片——重複度要跟它們比，不是跟頁面文字比。
        # 頁面記的是網址，向量索引的鍵是檔案路徑，中間要換一次。
        absorbed_paths = [url_to_path[u] for u in existing_by_topic.get(topic, set())
                          if u in url_to_path]
        # 每張卡回的是一串論點向量。這裡保持「一張卡一組」，比對時兩邊都取
        # 最像的那一條——攤平成一大堆論點向量會改變粒度，而 MAX_REDUNDANCY
        # 是照卡片級向量調的（同主題兩張卡本來就有 0.7~0.8），拿短短的論點
        # 互比會輕易衝破 0.92，然後以「重複」永久寫進跳過清單。
        # 卡片級鍵存在時 rows 只有一條，行為與改動前完全相同。
        absorbed = [rows for rows in cr.lookup_card_vectors(absorbed_paths).values() if rows]

        scored: list[tuple[float, sync.Card]] = []
        off_topic = redundant = 0
        for card in cards:
            card_rows = cr.lookup_card_vectors([card.path or card.url])
            # 取第一條會是靜默的錯誤：卡片級鍵不在時（重建索引或改名之後
            # 很常見），rows 是這張卡的論點向量，第一條只是字典順序的第一
            # 個。用它去比 0.55 的硬門檻，第三點正好命中的卡片會被判離題，
            # 而且以 skip 永久寫進人工決策清單。
            rows = next((r for r in card_rows.values() if r), None)
            if rows is None:
                # 沒有向量就判斷不了。要明確擋掉——
                # 不放進任何清單的話，--no-llm 會把它當成未表態而直接放行。
                skip.setdefault(topic, []).append(card.url)
                continue
            relevance = max(cr._cosine(v, s) for v in rows for s in sections)
            if relevance < MIN_RELEVANCE:
                off_topic += 1
                skip.setdefault(topic, []).append(card.url)
                continue
            if absorbed and max(cr._cosine(v, a)
                                for v in rows
                                for other in absorbed
                                for a in other) > MAX_REDUNDANCY:
                redundant += 1
                skip.setdefault(topic, []).append(card.url)
                continue
            scored.append((relevance, card))

        # 同一區間內優先收最相關的
        scored.sort(key=lambda kv: kv[0], reverse=True)
        chosen = scored[: args.cap]
        # 超過上限的不寫進 skip。skip 是持久的人工決策清單，
        # check_manual_decision 把它當成「這張卡不要收」的永久判定——而 cap
        # 的意思只是「這一輪先收這麼多」。把批次大小記成對卡片的判決，
        # 等於第 21 張從此再也不會被考慮。
        overflow = len(scored) - len(chosen)
        if overflow:
            print(f"  {topic}：超過上限 {args.cap}，{overflow} 張留待下次（未列入 skip）")
        allow[topic] = [c.url for _, c in chosen]

        summary.append((topic, len(cards), off_topic, redundant, len(chosen)))

    print(f"{'主題':34}{'候選':>6}{'離題':>6}{'重複':>6}{'收錄':>6}")
    for topic, total, off, red, kept in summary:
        print(f"{topic:34}{total:>6}{off:>6}{red:>6}{kept:>6}")
    print(f"{'合計':34}{sum(s[1] for s in summary):>6}{sum(s[2] for s in summary):>6}"
          f"{sum(s[3] for s in summary):>6}{sum(s[4] for s in summary):>6}")

    # --write-decisions 原本宣告了卻沒有人讀，寫入只看 --review，
    # 於是「跑一下看看」就覆寫了決策檔。宣告了的旗標要真的管事。
    if args.review or not args.write_decisions:
        return 0

    path = sync.REVIEW_DECISIONS_PATH
    data = sync.load_review_file()
    topics = data.setdefault("topics", {})
    for topic in set(allow) | set(skip):
        entry = topics.setdefault(topic, {})
        entry["allow"] = sorted(set(entry.get("allow", [])) | set(allow.get(topic, [])))
        entry["skip"] = sorted((set(entry.get("skip", [])) | set(skip.get(topic, [])))
                               - set(entry["allow"]))
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n已寫入 {path}。接著跑：python3 scripts/sync_cards_to_wiki.py --apply --no-llm")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
