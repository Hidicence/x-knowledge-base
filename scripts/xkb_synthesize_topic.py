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

消化不出結論的條列會原封不動留在頁面上，不會被結論取代掉——模型給不出
結論不代表那幾條筆記沒有價值，下一次消化會再試一次。

--apply 併回去的，就是你剛剛看過的那一份審閱稿。它不會重跑模型——重跑會
產出不一樣的結論，那樣審閱的稿子跟寫進 wiki 的稿子就不是同一份，審閱也就
沒有意義。頁面在你審閱之後又變動了的話，它會停下來說明，而不是默默重產。

用法：
    python3 scripts/xkb_synthesize_topic.py --list
    python3 scripts/xkb_synthesize_topic.py --topic ai-video-workflows
    python3 scripts/xkb_synthesize_topic.py --topic ai-video-workflows --apply
"""
from __future__ import annotations

import argparse
import hashlib
import pathlib
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

# 每批最多產出幾條結論。
#
# 原本這裡還有一個 MIN_COMPRESSION 門檻，低於它就拒絕併回。那個指標拿掉了：
# 它會因為丟掉內容而變好看，所以它獎勵的正是要防的行為。現在筆記全部保留，
# 不需要用比例去猜有沒有真的消化。
PER_CHUNK = 8


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


CONCLUSIONS_HEADING = "## 結論（消化自累積筆記）"
UNDIGESTED_HEADING = "## 尚未消化"
# 消化過的原始筆記搬到這裡，不刪除。
#
# 2026-09-11 改的：原本 --apply 是用結論「取代」筆記，所以消化是不可逆的，而
# 壓縮比會因為丟掉內容而變好看（實測有一次 16.2x，其實是整批不見了）。一個會
# 因為弄壞事情而變好看的指標，不該留在會被自動執行的路徑上。
#
# 現在筆記全部留著。代價是頁面變長，而那個代價很小：語意召回撈的是段落，不是
# 整頁。換來的是「消化丟掉東西」在結構上不可能發生——不必用任何數字去追它，
# 也讓這件事終於可以安全地排程。
DIGESTED_HEADING = "## 已消化的筆記（原文保留）"
SOURCES_HEADING = "## 出處"
GENERATED_HEADINGS = (CONCLUSIONS_HEADING, UNDIGESTED_HEADING,
                      DIGESTED_HEADING, SOURCES_HEADING)


def split_generated(text: str) -> tuple[str, str, list[str], list[str]]:
    """拆成 (人寫的部分, 既有結論, 尚未消化的條列, 出處)。

    消化寫出來的區塊要認得出來，否則再跑一次就會把結論當成素材，再壓一次
    結論——原本那些具體細節就是這樣消失的。而「尚未消化」相反：它們本來
    就在等下一次，所以要重新交出去。
    """
    positions = [text.index(h) for h in GENERATED_HEADINGS if h in text]
    if not positions:
        return text, "", [], []

    human, tail = text[: min(positions)], text[min(positions) :]
    blocks: dict[str, list[str]] = {}
    # 治理在消化之後還會往檔尾追加知識（### 標題加一段文字），那些行落在
    # 我們的區塊後面，卻不屬於任何一個。原本它們被歸進最後一個區塊、只留
    # 「- 」開頭的行，於是既看不到、也在下次 --apply 時被整段刪掉。
    foreign: list[str] = []
    current: str | None = None
    for line in tail.splitlines():
        if line.strip() in GENERATED_HEADINGS:
            current = line.strip()
            blocks[current] = []
        elif line.startswith("#") and not line.strip().startswith("####"):
            # 出現了不是我們寫的標題：從這裡開始都不是我們的東西。
            current = None
            foreign.append(line)
        elif current is not None:
            blocks[current].append(line)
        else:
            foreign.append(line)

    def bullets_of(heading: str) -> list[str]:
        return [l.strip() for l in blocks.get(heading, []) if l.strip().startswith("- ")]

    conclusions = "\n".join(blocks.get(CONCLUSIONS_HEADING, [])).strip("\n")
    # 別人寫的東西併回人寫的那一半，這樣它會被保留，而且下次會被當成素材。
    extra = "\n".join(foreign).strip("\n")
    if extra:
        human = human.rstrip() + "\n\n" + extra + "\n"
    return human, conclusions, bullets_of(UNDIGESTED_HEADING), bullets_of(SOURCES_HEADING)


def prior_digested(text: str) -> list[str]:
    """上一輪已經消化、原文保留在頁面上的筆記。

    undigested() 刻意不回傳這些（否則每次 --apply 都會把同一批重新消化一次），
    所以併回去的時候要另外讀一次，不然它們會在這一輪被寫掉——那就又變成「消化
    會丟東西」，只是晚一輪發生。
    """
    marker = DIGESTED_HEADING + "\n"
    if marker not in text:
        return []
    section = text.split(marker, 1)[1].split("\n## ", 1)[0]
    return [l.strip() for l in section.splitlines() if l.strip().startswith("- ")]


def undigested(text: str) -> tuple[str, list[str], list[str], str]:
    """(前言, 待消化的條列, 出處, 既有結論) —— 給每個想知道「還剩多少」的人。

    「還沒消化的有幾條」在三個地方各自被算過一次，其中兩種算法會把結論也
    算進去。這是唯一的定義。
    """
    human, conclusions, waiting, prior_links = split_generated(text)
    prose, bullets, links = split_page(human)
    for line in prior_links:
        if line not in links:
            links.append(line)
    # 已消化的筆記不在這裡面——它們留在頁面上是為了可追溯，不是為了再消化一次。
    # 再交出去的話，每次 --apply 都會把同一批筆記重新消化，而結論會一層層疊加。
    return prose, bullets + waiting, links, conclusions


def take_bullets(markdown: str, limit: int) -> list[str]:
    """只留前 limit 條。

    「最多 8 條」寫在提示詞裡只是請求，模型可以無視——實測 117 條進去、
    86 條出來，等於沒有消化。上限必須由程式執行，不能靠模型自律。
    """
    kept: list[str] = []
    for line in markdown.splitlines():
        if line.strip().startswith(("- ", "* ")):
            if len(kept) >= limit:
                break
            kept.append(line.rstrip())
        elif kept and line.strip():
            kept[-1] += " " + line.strip()      # 條列的續行
    return kept


# 條列結尾的出處標記，例如 *(self-derived · memory/2026-09-10.md)* 或一個網址。
_PROVENANCE_RE = re.compile(r"\*\(([^)]{4,200})\)\*\s*$")
_URL_RE = re.compile(r"https?://\S+")


def provenance_of(bullet: str) -> str:
    """一條筆記的出處字串；沒有就回空字串。

    用來對帳「這條筆記有沒有被某條結論代表」。比對的是出處而不是內容，因為
    結論本來就會改寫措辭——改寫之後比字串只會得到「全都沒消化」。
    """
    match = _PROVENANCE_RE.search((bullet or "").rstrip())
    if match:
        return match.group(1).strip()
    url = _URL_RE.search(bullet or "")
    return url.group(0).rstrip(").,") if url else ""


def _is_covered(bullet: str, conclusions: str) -> bool:
    source = provenance_of(bullet)
    if not source:
        # 本來就沒有出處的筆記無法對帳。保守起見當成沒消化——留著比丟掉安全，
        # 而且它會在頁面上顯示出來，讓「這條沒有出處」這件事被看見。
        return False
    return source in conclusions


def synthesise(topic: str, prose: str, bullets: list[str],
               per_chunk: int) -> tuple[str, list[str]]:
    """回傳 (結論, 沒能消化的原始條列)。

    第二個值是「這一輪沒能消化的」：整批沒產出，或出處沒有被任何結論引用到。
    它們留在「尚未消化」區，下一次再試。

    --apply 不會刪掉任何筆記（見 DIGESTED_HEADING），所以這個值的意義是
    「下次要不要再試」，不再是「會不會消失」。
    """
    from _llm import call as llm_call

    system = (
        "You turn accumulated research notes into a short, decisive knowledge page. "
        "Write in Traditional Chinese (Taiwan). No emoji."
    )
    out: list[str] = []
    lost: list[str] = []
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
            f"4. 用 Markdown 條列，**最多 {per_chunk} 條**。超過的部分會被直接截掉，\n"
            "   所以請自己挑最重要的，不要把每條筆記都改寫一遍\n"
            "5. 沒有把握的地方標註『（待查證）』，不要編\n"
            "6. **每一條結論的結尾，要把它合併到的那幾條筆記的出處標記原樣抄上**\n"
            "   （每條筆記結尾那個 *(…)* 的部分，多條就並排寫）。\n"
            "   出處沒有被抄到的筆記會被當成沒有消化，原樣留在頁面上。\n"
            "只輸出條列本身，不要開場白。"
        )
        # 一次重試。空回應多半是暫時的；連兩次都空，才算這一批真的消化不出來。
        kept: list[str] = []
        for attempt in range(2):
            raw = llm_call(system, user)
            cleaned = re.sub(r"<think>.*?</think>", "", raw, flags=re.DOTALL).strip()
            kept = take_bullets(cleaned, per_chunk) if cleaned else []
            if kept:
                break
            if attempt == 0:
                print(f"    第 {start // CHUNK + 1} 批沒有產出結論，重試一次", flush=True)
        if kept:
            out.append("\n".join(kept))
            # 沒有被任何一條結論引用到出處的筆記，算沒有消化，原樣留著。
            #
            # 提示詞本身就在要求丟東西（「最多 N 條，不要把每條筆記都改寫一遍」），
            # 而 lost 原本只接住「整批都沒產出」。30 條進去、8 條出來的時候，另外
            # 22 條是無聲消失的——壓縮比好看正是因為內容被丟掉。
            #
            # 現在改成由出處對帳。模型不配合抄出處的話，結果是「什麼都沒消化」
            # 而不是「安靜丟掉一批」——失敗的方向是安全的那一邊。
            covered = "\n".join(kept)
            lost.extend(b for b in batch if not _is_covered(b, covered))
        else:
            lost.extend(batch)
        print(f"    已消化 {min(start + CHUNK, len(bullets))}/{len(bullets)} 條", flush=True)
    return "\n\n".join(out), lost


DIGEST_LINE = re.compile(r"^<!-- source-digest: ([0-9a-f]{16}) -->$", re.M)
LOST_LINE = re.compile(r"^<!-- lost-bullets: (\d+) -->$", re.M)


def digest(bullets: list[str]) -> str:
    """指紋，用來確認審閱稿跟現在的頁面是同一批條列。"""
    joined = "\n".join(bullets).encode("utf-8")
    return hashlib.sha256(joined).hexdigest()[:16]


def read_draft(review: pathlib.Path, bullets: list[str]) -> str | None:
    """讀回審閱稿的結論；指紋對不上就回 None。

    對不上代表頁面在你審閱之後又長了新東西，那份稿子已經不是這一頁的
    消化結果了。
    """
    if not review.exists():
        return None
    text = review.read_text(encoding="utf-8", errors="replace")
    found = DIGEST_LINE.search(text)
    if not found or found.group(1) != digest(bullets):
        return None
    body = text.split("\n## 結論\n", 1)
    if len(body) != 2:
        return None
    # 到下一個標題為止。原本寫死找「## 出處」，於是後來插進來的
    # 「## 尚未消化」整段都被當成結論讀了回來。
    return body[1].split("\n## ", 1)[0].strip("\n")


def draft_lost(review: pathlib.Path) -> list[str]:
    """審閱稿裡那些消化不出結論的原始條列。

    存的是條列本身而不是數量，因為它們要原封不動跟著併回頁面——沒有消化
    出結論不代表可以丟掉。
    """
    if not review.exists():
        return []
    text = review.read_text(encoding="utf-8", errors="replace")
    if UNDIGESTED_HEADING + "\n" not in text:
        return []
    section = text.split(UNDIGESTED_HEADING + "\n", 1)[1].split("\n## ", 1)[0]
    return [line.strip() for line in section.splitlines() if line.strip().startswith("- ")]


def render(topic: str, synthesis: str, links: list[str], bullets_text: list[str],
           lost: list[str] | None = None) -> str:
    stamp = datetime.now(timezone.utc).isoformat()
    return "\n".join([
        f"# {topic} — 消化後",
        "",
        f"> 由 {len(bullets_text)} 條累積筆記消化而成，{stamp}。",
        "> 這是審閱稿：確認無誤後再用 --apply 併回主題頁。",
        f"<!-- source-digest: {digest(bullets_text)} -->",
        f"<!-- lost-bullets: {len(lost or [])} -->",
        "",
        "## 結論",
        "",
        synthesis,
        "",
        *((
            UNDIGESTED_HEADING,
            "",
            "> 這幾條模型給不出結論（已重試一次）。它們會原封不動留在主題頁上，",
            "> 下次消化會再試一次——沒有消化出結論，不代表可以丟掉。",
            "",
            *lost,
            "",
        ) if lost else ()),
        "## 出處",
        "",
        *(links or ["（無）"]),
        "",
    ])


def cmd_list() -> int:
    print(f"{'主題':34}{'敘述條列':>8}{'純連結':>8}{'值得消化':>10}")
    for path in sorted(xkb_paths.WIKI_TOPICS_DIR.glob("*.md")):
        _, bullets, links, _ = undigested(path.read_text(encoding="utf-8", errors="replace"))
        worth = "是" if len(bullets) >= 10 else ""
        print(f"{path.stem[:32]:34}{len(bullets):>8}{len(links):>8}{worth:>10}")
    return 0


def cmd_topic(topic: str, apply: bool, regenerate: bool = False) -> int:
    path = xkb_paths.WIKI_TOPICS_DIR / f"{topic}.md"
    if not path.exists():
        print(f"找不到主題頁：{path}", file=sys.stderr)
        return 1
    text = path.read_text(encoding="utf-8", errors="replace")
    prose, bullets, links, existing = undigested(text)
    per_chunk = PER_CHUNK
    if not bullets:
        print("這一頁沒有累積的條列可以消化。")
        return 0
    print(f"  {topic}：{len(bullets)} 條敘述、{len(links)} 條連結")

    review = REVIEW_DIR / f"{topic}-synthesis.md"
    synthesis = None if regenerate else read_draft(review, bullets)
    lost: list[str] = draft_lost(review) if synthesis else []
    if synthesis:
        print(f"  沿用既有審閱稿：{review.name}")
    else:
        if apply and not review.exists() and not regenerate:
            # 沒有審閱稿就 --apply，等於產生完直接寫進 wiki——而兩段式流程
            # 存在的理由就是中間要有人看過。這比舊稿對不上更值得擋。
            print("  這一頁還沒有審閱稿。先不加 --apply 跑一次，看過再併回；", file=sys.stderr)
            print("  確定不需要審閱的話，加 --regenerate 明確表示。", file=sys.stderr)
            return 5
        if apply and review.exists() and not regenerate:
            print("  審閱稿是針對舊版頁面產生的，頁面之後又有變動。", file=sys.stderr)
            print("  請先重跑一次消化、看過新稿，再 --apply；"
                  "或加 --regenerate 直接重產。", file=sys.stderr)
            return 3
        synthesis, lost = synthesise(topic, prose, bullets, per_chunk)
    if not synthesis:
        print("模型沒有產出內容，未寫入任何檔案。", file=sys.stderr)
        return 2

    # 壓縮比這個指標拿掉了。
    #
    # 它是「有沒有真的消化」的直接指標，但它同時是丟掉內容的誘因：把 30 條筆記
    # 換成 8 條結論、另外 22 條不見，壓縮比會很好看。實測 16.2x 那次就是整批不
    # 見了。一個會因為弄壞事情而變好看的指標，不該留在會被自動執行的路徑上。
    #
    # 取而代之的保證是結構性的：--apply 不再取代筆記。結論是附加上去的，原始
    # 條列搬到「已消化的筆記」區留著。所以「消化丟掉東西」在結構上不可能發生，
    # 不需要用一個數字去追它。
    produced = len([b for b in synthesis.splitlines() if b.strip().startswith("- ")])
    print(f"  產出 {produced} 條結論（{len(bullets)} 條筆記全部保留）")

    if lost:
        print(f"  其中 {len(lost)} 條沒有產出結論（已重試一次），"
              f"留在「尚未消化」區，下次再試。")

    REVIEW_DIR.mkdir(parents=True, exist_ok=True)
    review.write_text(render(topic, synthesis, links, bullets, lost),
                      encoding="utf-8")
    print(f"  審閱稿：{review}")

    if not apply:
        print("  確認後再加 --apply 併回主題頁。")
        return 0

    # 備份還是留著。現在消化不會刪掉筆記，但它仍然會重排整頁，而「重排一頁
    # 我手寫的東西」值得有一份退路。
    backup = path.with_suffix(f".md.before-synthesis-{datetime.now().strftime('%Y%m%d-%H%M')}")
    shutil.copy2(path, backup)

    # 這次併回去的是「結論 + 原始筆記」，不是「結論取代原始筆記」。
    #
    # 原本是取代，所以任何沒被寫進結論的筆記就此消失——而提示詞本身就在要求
    # 模型挑重點不要全改寫。結果是消化會安靜丟掉內容，而壓縮比會因此好看。
    #
    # 筆記搬到 DIGESTED_HEADING 之下：頁面上看得到、召回查得到、下一次不會被
    # 當成素材再消化一次。唯一的代價是頁面變長。
    digested = [b for b in bullets if b not in lost]
    for line in prior_digested(text):
        if line not in digested:
            digested.append(line)

    conclusions = f"{existing}\n\n{synthesis}" if existing else synthesis
    merged = prose.rstrip() + "\n\n" + CONCLUSIONS_HEADING + "\n\n" + conclusions
    if lost:
        merged += ("\n\n" + UNDIGESTED_HEADING + "\n\n"
                   + "\n".join(lost))
    if digested:
        merged += ("\n\n" + DIGESTED_HEADING + "\n\n"
                   + "\n".join(digested))
    merged += "\n\n" + SOURCES_HEADING + "\n\n" + "\n".join(links) + "\n"
    path.write_text(merged, encoding="utf-8")
    kept = len(digested)
    print(f"  已併回 {path.name}：{produced} 條結論，"
          f"{kept} 條筆記原文保留（備份：{backup.name}）")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--list", action="store_true", help="列出哪些主題頁累積了未消化的條列")
    mode.add_argument("--topic", help="消化這個主題頁")
    parser.add_argument("--apply", action="store_true", help="把結論併回主題頁（預設只產生審閱稿）")
    parser.add_argument("--regenerate", action="store_true",
                        help="就算已有審閱稿也重新消化一次")
    args = parser.parse_args(argv)
    if args.list:
        return cmd_list()
    return cmd_topic(args.topic, args.apply, args.regenerate)


import xkb_usage  # noqa: E402  — 量測誰在跑，見 scripts/xkb_usage.py

if __name__ == "__main__":
    xkb_usage.record(__file__)
    raise SystemExit(main())
