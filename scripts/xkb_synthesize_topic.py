#!/usr/bin/env python3
"""把主題頁裡累積的碎片寫成結論——真正的「消化」步驟。

為什麼需要這支：

    吸收閘門做的是「附加」，不是「消化」。它把卡片摘要與連結一條條接在
    頁尾，所以經過它的頁面有 48~75% 的條列只是連結：

        ai-video-workflows        75% 純連結
        video-prompt-patterns     68%
        openclaw-agent-workflows  49%

    而真正好讀的頁面（gpt-image2-seedance-workflow、learning-base）
    恰恰是它沒碰過、由人寫出來的那幾個。

    再吸收更多卡片，只會得到更多連結，不會得到更多知識。

這支做的是另一件事：把已經累積在頁面裡的條列，交給模型寫成「結論」，
並保留出處。輸出到審閱檔，**不直接覆寫**主題頁。

用法：
    python3 scripts/xkb_synthesize_topic.py --list
    python3 scripts/xkb_synthesize_topic.py --topic ai-video-workflows
    python3 scripts/xkb_synthesize_topic.py --topic ai-video-workflows --apply
"""
from __future__ import annotations

import argparse
import re
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import xkb_paths

REVIEW_DIR = xkb_paths.WIKI_DIR / "_synthesis"
LINK_ONLY = re.compile(r"^\s*-\s*\[[^\]]+\]\(https?://")
BULLET = re.compile(r"^\s*-\s+\S")
# 一次餵給模型的條列數。太多會讓它只能寫出泛泛的摘要，
# 反而失去「結論」該有的具體性。
CHUNK = 40


def split_page(text: str) -> tuple[str, list[str], list[str]]:
    """拆成 (前言/人寫的部分, 敘述型條列, 純連結)。

    純連結另外收著。它們是出處，不是內容——把它們丟給模型當素材，
    只會得到「有很多相關連結」這種沒有資訊的句子。
    """
    lines = text.splitlines()
    prose: list[str] = []
    bullets: list[str] = []
    links: list[str] = []
    for line in lines:
        if LINK_ONLY.match(line):
            links.append(line.strip())
        elif BULLET.match(line) and len(line.strip()) > 60:
            bullets.append(line.strip())
        else:
            prose.append(line)
    return "\n".join(prose), bullets, links


def synthesise(topic: str, prose: str, bullets: list[str]) -> str:
    from _llm import call as llm_call

    system = (
        "You turn accumulated research notes into a short, decisive knowledge page. "
        "Write in Traditional Chinese (Taiwan). No emoji."
    )
    out: list[str] = []
    for start in range(0, len(bullets), CHUNK):
        batch = bullets[start:start + CHUNK]
        user = (
            f"主題：{topic}\n\n"
            f"這一頁目前已經寫好的部分（作為脈絡，不要重複它）：\n{prose[:1500]}\n\n"
            f"以下是累積下來、尚未消化的筆記共 {len(batch)} 條：\n"
            + "\n".join(batch)
            + "\n\n"
            "請把它們寫成「結論」而不是摘要。要求：\n"
            "1. 合併重複的說法，只留下站得住腳的那一版\n"
            "2. 有衝突就明講衝突，不要假裝一致\n"
            "3. 每一條結論要具體到可以照著做，不要寫『可以提升效率』這種空話\n"
            "4. 用 Markdown 條列，最多 8 條\n"
            "5. 沒有把握的地方標註『（待查證）』，不要編\n"
            "只輸出條列本身，不要開場白。"
        )
        raw = llm_call(system, user)
        cleaned = re.sub(r"<think>.*?</think>", "", raw, flags=re.DOTALL).strip()
        if cleaned:
            out.append(cleaned)
        print(f"    已消化 {min(start + CHUNK, len(bullets))}/{len(bullets)} 條", flush=True)
    return "\n\n".join(out)


def render(topic: str, synthesis: str, links: list[str], bullets: int) -> str:
    stamp = datetime.now(timezone.utc).isoformat()
    return "\n".join([
        f"# {topic} — 消化後",
        "",
        f"> 由 {bullets} 條累積筆記消化而成，{stamp}。",
        "> 這是審閱稿：確認無誤後再用 --apply 併回主題頁。",
        "",
        "## 結論",
        "",
        synthesis,
        "",
        "## 出處",
        "",
        *(links or ["（無）"]),
        "",
    ])


def cmd_list() -> int:
    print(f"{'主題':34}{'敘述條列':>8}{'純連結':>8}{'值得消化':>10}")
    for path in sorted(xkb_paths.WIKI_TOPICS_DIR.glob("*.md")):
        _, bullets, links = split_page(path.read_text(encoding="utf-8", errors="replace"))
        worth = "是" if len(bullets) >= 10 else ""
        print(f"{path.stem[:32]:34}{len(bullets):>8}{len(links):>8}{worth:>10}")
    return 0


def cmd_topic(topic: str, apply: bool) -> int:
    path = xkb_paths.WIKI_TOPICS_DIR / f"{topic}.md"
    if not path.exists():
        print(f"找不到主題頁：{path}", file=sys.stderr)
        return 1
    text = path.read_text(encoding="utf-8", errors="replace")
    prose, bullets, links = split_page(text)
    if not bullets:
        print("這一頁沒有累積的條列可以消化。")
        return 0
    print(f"  {topic}：{len(bullets)} 條敘述、{len(links)} 條連結")
    synthesis = synthesise(topic, prose, bullets)
    if not synthesis:
        print("模型沒有產出內容，未寫入任何檔案。", file=sys.stderr)
        return 2

    REVIEW_DIR.mkdir(parents=True, exist_ok=True)
    review = REVIEW_DIR / f"{topic}-synthesis.md"
    review.write_text(render(topic, synthesis, links, len(bullets)), encoding="utf-8")
    print(f"  審閱稿：{review}")

    if not apply:
        print("  確認後再加 --apply 併回主題頁。")
        return 0

    # 併回去之前先備份。消化是不可逆的——原本那些條列會被結論取代，
    # 弄錯了沒有備份就回不來。
    backup = path.with_suffix(f".md.before-synthesis-{datetime.now().strftime('%Y%m%d-%H%M')}")
    shutil.copy2(path, backup)
    merged = prose.rstrip() + "\n\n## 結論（消化自累積筆記）\n\n" + synthesis + "\n\n## 出處\n\n" + "\n".join(links) + "\n"
    path.write_text(merged, encoding="utf-8")
    print(f"  已併回 {path.name}（備份：{backup.name}）")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--list", action="store_true", help="列出哪些主題頁累積了未消化的條列")
    mode.add_argument("--topic", help="消化這個主題頁")
    parser.add_argument("--apply", action="store_true", help="把結論併回主題頁（預設只產生審閱稿）")
    args = parser.parse_args(argv)
    return cmd_list() if args.list else cmd_topic(args.topic, args.apply)


if __name__ == "__main__":
    raise SystemExit(main())
