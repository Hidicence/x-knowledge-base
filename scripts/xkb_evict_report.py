#!/usr/bin/env python3
"""哪些知識反覆被端上來卻從來沒被用上——也就是現在正被降權的那些。

規則與門檻在 `xkb_eviction.is_demoted`，SQL 在 `Store.demoted_ids`；這支只是把
同一個判斷印成人看得懂的樣子，不自己定義任何門檻。

**降權不是刪除、也不是排除。** 被列在這裡的知識還在索引裡、還會被撈出來、還
會被量測，只是在沒通過相關度地板的那一層被排到最後，先被 limit 切掉。它哪天
真的通過一次地板就自動從這張表上消失（見 xkb_eviction.is_demoted 的說明）。

所以這張表唯一的用途是**看機制有沒有在動、有沒有冤枉誰**：清單太長代表門檻
太鬆，清單裡出現你認得的好知識代表召回把它配給了錯的問題——那是召回要修的，
不是退場要修的。

同一張表也拿來驗證一件事：2026-09-12 量到「從來沒被用上」的那些最高相似度全部
落在 0.48~0.55，而地板是 0.55。如果哪天這個分布變了（有東西以 0.7 的相似度被
撈出來十次卻從來沒被注入），那是上游壞了，不是這筆知識沒用。

用法：
    python3 scripts/xkb_evict_report.py
    python3 scripts/xkb_evict_report.py --json
    python3 scripts/xkb_evict_report.py --after 8
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import xkb_console
import xkb_eviction as ev
import xkb_frontmatter
import xkb_paths
import xkb_relevance

xkb_console.use_utf8()


def describe(record_id: str) -> tuple[str, str]:
    """record_id 的標題與**真正的**分類。

    record_id 的前綴是檔案放在哪個 bookmarks 資料夾（`02-seo-geo/2032...`），而那
    不是這張卡的分類——1,754 筆書籤裡有 650 筆的 frontmatter `category` 跟所在目錄
    不一致，而召回、wiki 路由、健檢讀的全都是 frontmatter。目錄只是位址。

    2026-09-12 我自己就被這個前綴騙過：看到 `02-seo-geo/` 就推論那張卡「被歸錯類
    所以被 SEO 問題撈出來」。它其實是日文的 AI 生圖 prompt 合集，frontmatter 寫
    04-ai-tools-agents，而它被撈出來是內容嵌入弱相關，跟資料夾毫無關係。報告印出
    前綴卻不印真正的分類，就是在邀請這個推論。
    """
    stem = record_id.rsplit("/", 1)[-1]
    card = xkb_paths.CARDS_DIR / f"{stem}.md"
    if not card.is_file():
        return "", ""
    try:
        text = card.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return "", ""
    title = ""
    for line in text.splitlines():
        if line.startswith("# "):
            title = line[2:].strip()
            break
    return title, (xkb_frontmatter.get(text, "category") or "")


def load_rows(after: int = ev.DEMOTE_AFTER_CONSIDERED) -> list[dict]:
    """使用統計全表（有被撈出過的），交給 is_demoted 判斷——門檻不在這裡。"""
    db_path = xkb_paths.SERVICE_DB
    if not db_path.exists():
        return []
    db = None
    try:
        db = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        db.row_factory = sqlite3.Row
        rows = db.execute(
            "SELECT record_id, considered_count, injected_count, best_similarity,"
            "       last_considered_at, last_injected_at"
            "  FROM knowledge_usage ORDER BY considered_count DESC"
        ).fetchall()
    except sqlite3.Error as err:
        print(f"讀不到 knowledge_usage：{err}", file=sys.stderr)
        return []
    finally:
        if db is not None:
            try:
                db.close()
            except Exception:  # noqa: BLE001
                pass
    return [dict(r) for r in rows]


def assess(rows: list[dict], after: int = ev.DEMOTE_AFTER_CONSIDERED) -> list[dict]:
    out = []
    for row in rows:
        considered = int(row["considered_count"] or 0)
        injected = int(row["injected_count"] or 0)
        out.append({
            "record_id": row["record_id"],
            "considered": considered,
            "injected": injected,
            "hit_rate": round(injected / considered, 3) if considered else 0.0,
            "best_similarity": round(float(row["best_similarity"] or 0), 3),
            "demoted": ev.is_demoted(considered, injected, after=after),
            "last_injected_at": row["last_injected_at"],
        })
    return out


def suspicious(results: list[dict], floor: float) -> list[dict]:
    """被降權、但最高相似度其實過得了地板的——那是上游的問題，不是它沒用。

    從來沒被注入卻曾經算出高相似度，意思是某次它夠相關卻沒被採用。真的發生
    的話要去查召回，不要靜靜把它壓下去。
    """
    return [r for r in results if r["demoted"] and r["best_similarity"] >= floor]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--after", type=int, default=ev.DEMOTE_AFTER_CONSIDERED,
                        help="被撈出幾次以上才算（預設照 xkb_eviction 的門檻）")
    parser.add_argument("--limit", type=int, default=20)
    args = parser.parse_args()

    rows = load_rows(args.after)
    if not rows:
        print("knowledge_usage 是空的——召回還沒跑過，沒有東西可以判斷。")
        return 0

    floor = xkb_relevance.min_similarity()
    results = assess(rows, args.after)
    demoted = [r for r in results if r["demoted"]]
    odd = suspicious(results, floor)

    if args.json:
        print(json.dumps({"after": args.after, "floor": floor,
                          "tracked": len(results), "demoted": len(demoted),
                          "suspicious": odd, "records": demoted},
                         ensure_ascii=False, indent=2))
        return 0

    print(f"追蹤 {len(results)} 筆知識，其中被撈出 ≥{args.after} 次"
          f"、從未通過地板（{floor}）的有 {len(demoted)} 筆")
    print("  它們被降權：還在索引裡、還會被量測，只是排在所有相關命中之後。")
    print()
    if demoted:
        print(f"    {'撈出':>5} {'最高相似度':>10}  分類 / 標題")
        for r in sorted(demoted, key=lambda x: -x["considered"])[:args.limit]:
            title, category = describe(r["record_id"])
            label = f"[{category or '?'}] {title}" if title else r["record_id"]
            print(f"    {r['considered']:>5} {r['best_similarity']:>10.3f}  {label}")
            # 前綴是檔案位址，不是分類——兩者常常不一樣，見 describe()。
            print(f"    {'':>5} {'':>10}  存放於 {r['record_id']}")
        print()
    if odd:
        print(f"  ⚠ {len(odd)} 筆曾經算出 ≥{floor} 的相似度卻從來沒被注入——")
        print("    那是召回該查的事，不是這些知識沒用。")
        for r in odd[:args.limit]:
            print(f"      {r['best_similarity']:.3f}  {r['record_id']}")
        print()
    print("  降權會自動解除：通過地板一次就從這張表上消失，不需要人介入。")
    return 0


import xkb_usage  # noqa: E402  — 量測誰在跑，見 scripts/xkb_usage.py

if __name__ == "__main__":
    xkb_usage.record(__file__)
    raise SystemExit(main())
