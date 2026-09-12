#!/usr/bin/env python3
"""把分類在正式清單外的知識重新分類。預設只報告，--apply 才寫。

2026-09-12 實測 1,706 筆索引裡有 68 筆（4%）的 category 不在 taxonomy() 裡：

    ai-tools 25、tech 13、inbox 11、general 7、空 4、02-ai-tools 2，
    以及 design / developer-tools / test / 05-knowledge-management 等零星值

分類不影響召回（沒有過濾也沒有加權），但它決定 wiki 路由：sync_cards_to_wiki
的 make_card 看到空的 category 就回 None，那張卡從此不會進任何 topic。清單外的
值則會被當成一個沒有 topic 映射的分類，效果跟落在角落一樣。

**為什麼不是順手對齊資料夾。** 書籤原始檔的資料夾有 650 筆跟自己的 frontmatter
不一致，但那跟分類品質無關：索引是從 memory/cards/（平的）建的，資料夾只是書籤
的進料位址，沒有任何程式把它當分類讀（見 tests/test_the_folder_is_not_the_category）。
搬那 650 個檔會改掉 relative_path，而那是向量索引與 knowledge_usage 的鍵——會
把使用統計變成孤兒，換不到任何東西。

**新分類走候診，不是禁止。** 分類器看到真的沒有現成分類可用的內容時會提議一個新
名字，但一票不開：同一個名字累積到 category_classifier.PROMOTE_AFTER 才真的開（跟
wiki topic 那一層同一個數字）。沒達門檻的卡片先歸到既有分類，身上留著
`proposed_category`；門檻到了 gather_promoted() 會把它們搬過去。報告會列出候診中的
提案與票數，所以「差幾票」看得見。

**壞掉的卡片不在這支的守備範圍。** 有幾筆的 frontmatter 寫著 `<category or empty>`、
標題是 `<clean title>`——那是 LLM 的模板原樣漏進檔案，是內容壞了，不是分類錯了。
幫它分類只會讓一張壞卡片看起來正常。這支把它們單獨列出來，不動它們。

用法：
    python3 scripts/xkb_recategorize.py                 # 只報告
    python3 scripts/xkb_recategorize.py --apply         # 寫入並重建索引
    python3 scripts/xkb_recategorize.py --limit 10 --apply
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import category_classifier
import xkb_console
import xkb_frontmatter
import xkb_index
import xkb_paths

xkb_console.use_utf8()

# LLM 的 prompt 模板漏進檔案留下的痕跡，以及抓取失敗留下的空殼。
# 這些是內容壞了，不是分類錯了——分類它們等於把壞卡片粉刷一遍。
BROKEN_MARKERS = (
    re.compile(r"<category or empty>"),
    re.compile(r"<clean title>"),
    # 其他沒列舉到的佔位符。要求裡面有空格——`<clean title>` 這類模板都有，而
    # `<div>` / `<pre>` / `<code>` 這些正常標記沒有。不要求空格的話，內容裡夾
    # 一個 HTML 標記的好卡片會被當成壞卡片跳過。
    re.compile(r"<[a-z]+ [a-z ]{2,30}>"),
    re.compile(r"擷取失敗|無法擷取內容"),
)


def load_items() -> list[dict]:
    try:
        raw = json.loads(xkb_paths.INDEX_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as err:
        print(f"讀不到索引 {xkb_paths.INDEX_FILE}：{err}", file=sys.stderr)
        return []
    items = raw if isinstance(raw, list) else raw.get("items", [])
    return [i for i in items if isinstance(i, dict)]


def off_taxonomy(items: list[dict]) -> list[dict]:
    """分類不在 taxonomy() 裡的。

    判準取自 category_classifier.taxonomy()，不是自己抄一份清單——它會把
    runtime 註冊的新分類算進來，而那是使用者的知識結構，不是這支的常數。
    """
    valid = set(category_classifier.taxonomy())
    return [i for i in items if (i.get("category") or "").strip() not in valid]


def broken_reason(text: str) -> str | None:
    """這筆是不是「內容壞了」而不是「分類錯了」。"""
    head = text[:1500]
    for pattern in BROKEN_MARKERS:
        m = pattern.search(head)
        if m:
            return m.group(0)
    return None


def decide(item: dict, text: str) -> tuple[str, str, str]:
    """這筆該歸到哪一類、理由，以及（如果有）它提議的新分類名。

    交給既有的 category_classifier——它是 LLM 判斷、帶關鍵字 fallback、而且認得
    同一份 taxonomy。這支不自己做判斷：多一份判斷就多一份會跟它不一致的東西。

    **新分類走候診，不是禁止。** 規則是「同一個名字被提議到門檻才開」
    （category_classifier.PROMOTE_AFTER，跟 wiki topic 那一層同一個數字），所以這裡
    不傳 allow_new=False。我一度那樣寫，那會把提案整個丟掉——跟「一票就開」是相反
    方向的同一種錯。沒達門檻時 classifier 回既有分類外加 proposed_category，由這支
    寫到卡片上，門檻到了才撈得回來。

    回傳的理由裡一定說明 LLM 有沒有真的跑。classify_content 在 LLM 失敗時會安靜地
    退回關鍵字，只把 `llm: False` 放在結果裡——不把它印出來的話，整批都是關鍵字猜的
    也會被當成「分類器判斷」。
    """
    current = (item.get("category") or "").strip()
    record_id = str(item.get("relative_path") or item.get("path") or "")
    result = category_classifier.classify_content(
        text, source_type=str(item.get("source_type") or ""),
        current_category=current, record_id=record_id)
    category = str(result.get("category") or "").strip()
    confidence = str(result.get("confidence") or "?")
    proposed = str(result.get("proposed_category") or "")
    how = "LLM" if result.get("llm") else "關鍵字 fallback"
    why = f"{how}·信心 {confidence}"
    if proposed:
        why += (f"·提議新分類 {proposed}"
                f"（第 {result.get('proposal_count')}/{category_classifier.PROMOTE_AFTER} 票）")
    if category not in set(category_classifier.taxonomy()):
        return "99-general", f"{why}·回了清單外的 {category!r}，退回預設", proposed
    return category, why, proposed


def apply_to(files: list[Path], category: str, proposed: str = "") -> list[Path]:
    """把分類寫進這一組的每一個檔。

    要寫整組，不是只寫索引那一列指向的檔：索引會依 source_url 去重，同一份知識
    在磁碟上可能有卡片與書籤各一份。2026-09-10 品質排除只標了贏的那一列，重建
    之後去重挑了沒被標的那一份，結果「已標記」等於沒發生。
    """
    written = []
    for path in files:
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if not xkb_frontmatter.has_frontmatter(text):
            continue
        updated = xkb_frontmatter.set_field(text, "category", category)
        if proposed:
            # 把提議的名字留在卡片上。門檻到了之後 gather_promoted() 靠它撈回來——
            # 少了這一步，提案達標也沒有任何卡片會跟著搬過去。
            updated = xkb_frontmatter.set_field(updated, "proposed_category", proposed)
        if updated != text:
            path.write_text(updated, encoding="utf-8")
            written.append(path)
    return written


def gather_promoted(items: list[dict]) -> list[tuple[str, list[Path]]]:
    """提案達標、分類已經開了之後，把在候診的卡片搬過去。

    這是整條規則的最後一段，也是最容易漏的一段：提案累積、門檻、開分類都做了，
    但沒有人回頭處理那些先被歸到既有分類、身上帶著 proposed_category 的卡片。
    漏掉它的話，門檻到了也只是多一個空分類。

    wiki topic 那邊是同一件事——導向 general 的條目保留 proposed_topic，就是為了
    日後撈得回來。
    """
    valid = set(category_classifier.taxonomy())
    out = []
    for item in items:
        files = xkb_index.files_for_item(item)
        if not files:
            continue
        try:
            text = files[0].read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        proposed = (xkb_frontmatter.get(text, "proposed_category") or "").strip()
        if proposed and proposed in valid and (item.get("category") or "") != proposed:
            out.append((proposed, files))
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true",
                        help="真的寫入並重建索引（預設只報告）")
    parser.add_argument("--limit", type=int, default=0, help="只處理前 N 筆")
    args = parser.parse_args()

    items = load_items()
    if not items:
        return 1
    targets = off_taxonomy(items)
    print(f"索引 {len(items)} 筆，分類在正式清單外的 {len(targets)} 筆")
    print(f"  正式清單：{', '.join(category_classifier.taxonomy())}")
    print()
    if not targets:
        print("  沒有要處理的。")
        return 0
    if args.limit:
        targets = targets[: args.limit]

    broken, planned, unreadable = [], [], []
    for item in targets:
        files = xkb_index.files_for_item(item)
        if not files:
            unreadable.append(item)
            continue
        try:
            text = files[0].read_text(encoding="utf-8", errors="replace")
        except OSError:
            unreadable.append(item)
            continue
        reason = broken_reason(text)
        if reason:
            broken.append((item, reason))
            continue
        category, why, proposed = decide(item, text)
        planned.append((item, files, category, why, proposed))

    if planned:
        print(f"  要重新分類 {len(planned)} 筆：")
        for item, files, category, why, _proposed in planned:
            print(f"    {(item.get('category') or '空'):>22} -> {category:<22}"
                  f" {(item.get('title') or '')[:32]}  （{len(files)} 個檔·{why}）")
        print()

    waiting = category_classifier.proposals()
    if waiting:
        print(f"  候診中的新分類提案（{category_classifier.PROMOTE_AFTER} 票才開）：")
        for slug, entry in sorted(waiting.items(),
                                  key=lambda kv: -int(kv[1].get("count") or 0)):
            count = int(entry.get("count") or 0)
            state = "已達門檻" if count >= category_classifier.PROMOTE_AFTER else "等票"
            print(f"    {slug:<26} {count:>2}/{category_classifier.PROMOTE_AFTER}"
                  f"  {state}  {str(entry.get('reason') or '')[:40]}")
        print("    沒達門檻的卡片先歸到既有分類，身上留著提議的名字——門檻到了會被撈回來。")
        print()

    promoted = gather_promoted(items)
    if promoted:
        print(f"  提案已達門檻、要把 {len(promoted)} 筆候診的卡片搬過去：")
        for slug, files in promoted:
            print(f"    -> {slug:<26} {len(files)} 個檔")
        print()
    if broken:
        print(f"  ⚠ {len(broken)} 筆是內容壞了，不是分類錯了——沒有動它們：")
        for item, reason in broken:
            print(f"    {reason!r}  {(item.get('title') or '')[:40]}")
        print("    這些要修的是卡片本身（重新產生或排除），分類只是症狀。")
        print()
    if unreadable:
        print(f"  ⚠ {len(unreadable)} 筆在磁碟上找不到檔案，沒有動：")
        for item in unreadable[:10]:
            print(f"    {(item.get('relative_path') or item.get('path') or '?')}")
        print()

    if not args.apply:
        print("  這是預覽。加 --apply 才會寫入並重建索引。")
        return 0

    touched = 0
    for _item, files, category, _why, proposed in planned:
        touched += len(apply_to(files, category, proposed))
    for slug, files in promoted:
        # 搬過去之後清掉提議欄位——它的用途已經結束，留著會讓下一輪再搬一次。
        touched += len(apply_to(files, slug))
        for path in files:
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            if xkb_frontmatter.get(text, "proposed_category"):
                path.write_text(
                    xkb_frontmatter.set_field(text, "proposed_category", ""),
                    encoding="utf-8")
    print(f"  已寫入 {touched} 個檔。重建索引……")
    # 索引是衍生物，只有這一個寫入者；改了 frontmatter 一定要整建，
    # 增量重建不會重讀沒動到 mtime 判斷的那些列。
    ok = xkb_index.rebuild(incremental=False)
    print("  索引重建完成。" if ok else "  索引重建失敗——分類已寫入，索引還是舊的。")
    return 0 if ok else 1


import xkb_usage  # noqa: E402  — 量測誰在跑，見 scripts/xkb_usage.py

if __name__ == "__main__":
    xkb_usage.record(__file__)
    raise SystemExit(main())
