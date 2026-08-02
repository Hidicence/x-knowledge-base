#!/usr/bin/env python3
"""把審核通過的候選記憶變成知識卡——閉環的最後一哩。

在這支之前，對話會走到「候選」就停住：
    對話 → L1 軌跡 → 候選（pending / rejected） → ？

候選池、證據門檻、系統雜訊過濾都做好了，但沒有任何東西能核准候選，
也沒有東西把核准的候選變成知識。整條路蓋到最後一哩沒接上。

三件事是刻意這樣設計的：

**核准一定是人做的。** 沒有 `--approve` 就沒有東西會被升級。
schema 把 `automatic_promotion` 鎖成 false 是有原因的——對話裡隨口講的話
如果能自己變成知識，這個知識庫遲早會變成你自己的回音。

**升級要有跨 episode 的證據。** 同一場對話裡講兩次不算數，
預設要兩個不同的 episode 才夠格被核准（`--force` 可以覆寫，但會記錄下來）。

**產生的卡片標記為 self-derived。** 這樣召回時就會套用既有的回音室降權
（`recall_for_conversation` 的 `SELF_DERIVED_PENALTY`）。
從自己的對話長出來的知識，權重本來就該低於外部來源的證據。

用法：
    python3 scripts/xkb_promote_candidate.py --list
    python3 scripts/xkb_promote_candidate.py --show <candidate_id>
    python3 scripts/xkb_promote_candidate.py --approve <candidate_id>
    python3 scripts/xkb_promote_candidate.py --reject <candidate_id> --reason "…"
    python3 scripts/xkb_promote_candidate.py --apply            # 核准的 → 知識卡
    python3 scripts/xkb_promote_candidate.py --apply --dry-run
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

import xkb_paths
from xkb_memory_service import Store, now, text

# 要幾個不同的 episode 才夠格被核准。
# 沿用 candidate pool analyzer 的門檻，兩邊必須一致——
# 一邊說夠格、另一邊不讓核准，只會讓人不知道該信哪個。
MIN_EPISODES = 2
MIN_EVIDENCE = 2


def gate(candidate: dict[str, Any]) -> tuple[bool, str]:
    """回傳 (夠不夠格被核准, 原因)。"""
    episodes = len(set(candidate.get("episode_ids") or []))
    evidence = len(candidate.get("source_trace_ids") or [])
    if candidate.get("status") == "rejected":
        return False, "已被判定為系統雜訊"
    if episodes < MIN_EPISODES:
        return False, f"只有 {episodes} 個 episode，需要 {MIN_EPISODES}（同一場對話講兩次不算）"
    if evidence < MIN_EVIDENCE:
        return False, f"只有 {evidence} 筆證據，需要 {MIN_EVIDENCE}"
    return True, f"{episodes} 個 episode、{evidence} 筆證據"


def slugify(value: str) -> str:
    value = re.sub(r"\s+", "-", value.strip().lower())
    value = re.sub(r"[^\w一-鿿-]", "", value)
    return value.strip("-")[:60] or "candidate"


def render_card(candidate: dict[str, Any]) -> str:
    """產生知識卡。只填得出來的段落才填，不編造。

    一則候選記憶不足以生出完整的九段式卡片。硬把九段填滿只會產生
    看起來很完整、其實是編的內容——那比缺幾段糟得多。
    """
    value = str(candidate.get("candidate_value") or "").strip()
    episodes = sorted(set(candidate.get("episode_ids") or []))
    traces = sorted(set(candidate.get("source_trace_ids") or []))
    analysis = candidate.get("analysis") or {}
    created = now()

    lines = [
        "---",
        f"title: {value[:80]}",
        "category: 99-general",
        "source_type: conversation",
        # 這一行是關鍵：召回時會據此降權，避免自己的話被當成外部證據。
        "provenance: self-derived",
        "claim_level: Inference",
        f"episodes: {json.dumps(episodes, ensure_ascii=False)}",
        f"candidate_id: {candidate.get('candidate_id')}",
        f"created: {created}",
        "---",
        "",
        f"# {value[:120]}",
        "",
        "## 1. 核心問題與結論",
        "",
        value,
        "",
        "## 2. Claim 等級",
        "",
        "**Inference** — 從對話蒸餾而來（self-derived），未經外部來源驗證。",
        f"在 {len(episodes)} 個不同的 episode 中重複出現，因此通過證據門檻；",
        "但重複出現只代表穩定，不代表正確。",
        "",
        "## 6. 與現有知識的關係",
        "",
        f"- 來自對話，非外部來源；召回時權重低於有出處的證據卡。",
    ]
    if analysis.get("noise_markers"):
        lines.append(f"- 分析時偵測到的雜訊標記：{', '.join(analysis['noise_markers'])}")
    lines += [
        "",
        "## 9. 原始來源",
        "",
        f"- 候選 ID：`{candidate.get('candidate_id')}`",
    ]
    for episode in episodes:
        lines.append(f"- Episode：`{episode}`")
    for trace in traces[:10]:
        lines.append(f"- L1 軌跡：`{trace}`")
    lines.append("")
    return "\n".join(lines)


def load(store: Store, candidate_id: str | None = None, status: str = "") -> list[dict[str, Any]]:
    result = store.query_candidates(status=status, limit=200)
    items = result.get("candidates", [])
    if candidate_id:
        items = [c for c in items if c.get("candidate_id") == candidate_id]
    return items


def set_status(store: Store, candidate_id: str, status: str, reasons: list[str]) -> bool:
    with store.lock, store.connect() as db:
        cur = db.execute(
            "UPDATE candidates SET status=?, reject_reasons_json=?, updated_at=? WHERE candidate_id=?",
            (status, json.dumps(reasons, ensure_ascii=False), now(), candidate_id),
        )
        return cur.rowcount > 0


def cmd_list(store: Store, status: str) -> int:
    items = load(store, status=status)
    if not items:
        print("沒有候選記憶。")
        return 0
    print(f"{'狀態':<10}{'episode':>8}{'證據':>6}  {'夠格核准':<10} 候選")
    for c in items:
        ok, why = gate(c)
        print(f"{c['status']:<10}{len(set(c.get('episode_ids') or [])):>8}"
              f"{len(c.get('source_trace_ids') or []):>6}  {'是' if ok else '否':<10} "
              f"{str(c.get('candidate_value') or '')[:44]}")
        if not ok:
            print(f"{'':<26}   └ {why}")
    print(f"\n共 {len(items)} 筆。核准：--approve <candidate_id>")
    return 0


def cmd_show(store: Store, candidate_id: str) -> int:
    items = load(store, candidate_id=candidate_id)
    if not items:
        print(f"找不到候選：{candidate_id}", file=sys.stderr)
        return 1
    c = items[0]
    ok, why = gate(c)
    print(json.dumps({
        "candidate_id": c["candidate_id"],
        "status": c["status"],
        "value": c.get("candidate_value"),
        "episodes": sorted(set(c.get("episode_ids") or [])),
        "evidence": c.get("source_trace_ids"),
        "eligible_for_approval": ok,
        "gate": why,
        "analysis": c.get("analysis"),
    }, ensure_ascii=False, indent=2))
    return 0


def cmd_approve(store: Store, candidate_id: str, force: bool) -> int:
    items = load(store, candidate_id=candidate_id)
    if not items:
        print(f"找不到候選：{candidate_id}", file=sys.stderr)
        return 1
    c = items[0]
    ok, why = gate(c)
    if not ok and not force:
        print(f"不夠格核准：{why}", file=sys.stderr)
        print("確定要核准請加 --force（會記錄下來）", file=sys.stderr)
        return 2
    note = [f"approved: {why}"] + ([f"forced by operator at {now()}"] if not ok else [])
    if not set_status(store, candidate_id, "approved", note):
        print("更新失敗", file=sys.stderr)
        return 1
    print(json.dumps({"candidate_id": candidate_id, "status": "approved",
                      "gate": why, "forced": not ok}, ensure_ascii=False))
    return 0


def cmd_reject(store: Store, candidate_id: str, reason: str) -> int:
    if not set_status(store, candidate_id, "rejected", [reason or "rejected by operator"]):
        print(f"找不到候選：{candidate_id}", file=sys.stderr)
        return 1
    print(json.dumps({"candidate_id": candidate_id, "status": "rejected"}, ensure_ascii=False))
    return 0


def cmd_apply(store: Store, dry_run: bool) -> int:
    items = load(store, status="approved")
    if not items:
        print("沒有已核准的候選。先用 --approve 核准。")
        return 0
    cards_dir = xkb_paths.CARDS_DIR
    written = []
    for c in items:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d")
        path = cards_dir / f"conversation-{stamp}-{slugify(str(c.get('candidate_value') or ''))}.md"
        if dry_run:
            print(f"  會寫入 {path.name}")
            written.append(str(path))
            continue
        cards_dir.mkdir(parents=True, exist_ok=True)
        path.write_text(render_card(c), encoding="utf-8")
        set_status(store, c["candidate_id"], "promoted", [f"promoted to {path.name} at {now()}"])
        written.append(str(path))
        print(f"  已寫入 {path.name}")
    print(json.dumps({"promoted": len(written), "dry_run": dry_run}, ensure_ascii=False))
    if written and not dry_run:
        print("\n記得重建索引，新卡片才會被召回：")
        print("  bash scripts/build_search_index.sh && python3 scripts/build_vector_index.py --incremental")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--list", action="store_true", help="列出候選與是否夠格核准")
    mode.add_argument("--show", metavar="ID", help="顯示單一候選的完整內容")
    mode.add_argument("--approve", metavar="ID", help="核准（人工動作，不會自動發生）")
    mode.add_argument("--reject", metavar="ID", help="退回")
    mode.add_argument("--apply", action="store_true", help="把已核准的候選寫成知識卡")
    parser.add_argument("--status", default="", help="--list 時過濾狀態")
    parser.add_argument("--reason", default="", help="--reject 時的原因")
    parser.add_argument("--force", action="store_true", help="核准未達證據門檻的候選（會記錄）")
    parser.add_argument("--dry-run", action="store_true", help="--apply 時只顯示會寫什麼")
    parser.add_argument("--db", type=Path, default=None)
    args = parser.parse_args(argv)

    store = Store(args.db or Path.home() / ".xkb-runtime" / "knowledge.sqlite")
    if args.list:
        return cmd_list(store, args.status)
    if args.show:
        return cmd_show(store, args.show)
    if args.approve:
        return cmd_approve(store, args.approve, args.force)
    if args.reject:
        return cmd_reject(store, args.reject, args.reason)
    return cmd_apply(store, args.dry_run)


if __name__ == "__main__":
    raise SystemExit(main())
