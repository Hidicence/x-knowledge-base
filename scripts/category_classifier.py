"""LLM-assisted, taxonomy-aware card classification for XKB ingest."""
from __future__ import annotations

import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

import xkb_frontmatter
import xkb_paths
from _llm import call as _llm_call

RULES_PATH = xkb_paths.SKILL_DIR / "config" / "category-rules.json"
# 新分類寫進資料區，不寫進 skill 程式碼——分類是使用者的知識結構，
# 不是這個工具的一部分。路徑一律走 xkb_paths，不要自己再推一次。
RUNTIME_TAXONOMY_PATH = xkb_paths.XKB_DATA_DIR / "category-taxonomy.json"
# 被提議過但還沒達到門檻的新分類。提案留著，因為「這個名字被提了幾次」才是
# 該不該開一個分類的依據。
PROPOSALS_PATH = xkb_paths.XKB_DATA_DIR / "category-proposals.json"

# 新分類不是看到一筆就開。wiki topic 那一層早就有這條規則（xkb_review.PROMOTE_AFTER
# 的註解：「一個名字被提議過幾次，才算『重複出現』而值得開一頁。五次是刻意保守的：
# 開一頁很便宜，但一頁只放一條就是把佇列的問題搬進 wiki 裡」），而分類這一層原本
# 沒有——register_category 單一筆高信心就立刻開。
#
# 刻意用同一個數字，並由 tests/test_new_categories_wait_for_a_quorum.py 釘住兩邊
# 一致。不直接 import xkb_review 是因為那是一支帶 main 的大模組，為一個常數把它
# 拉進攝取路徑不划算。
PROMOTE_AFTER = 5
DEFAULT_CATEGORIES = [
    "01-openclaw-workflows", "02-seo-geo", "03-video-prompts",
    "04-ai-tools-agents", "05-startup-business", "06-visual-ai-prompts",
    "99-general",
]


def _load_rules() -> dict[str, Any]:
    try:
        return json.loads(RULES_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"default_category": "99-general", "categories": DEFAULT_CATEGORIES, "rules": []}


def _slug(value: str) -> str:
    value = value.strip().lower()
    value = re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "-", value)
    return re.sub(r"-+", "-", value).strip("-")[:48]


def taxonomy() -> list[str]:
    """\u8a2d\u5b9a\u6a94\u88e1\u7684\u5206\u985e + \u57f7\u884c\u671f\u65b0\u589e\u7684\u5206\u985e\u3002"""
    data = _load_rules()
    cats = list(data.get("categories") or DEFAULT_CATEGORIES)
    try:
        runtime = json.loads(RUNTIME_TAXONOMY_PATH.read_text(encoding="utf-8"))
        cats += list(runtime.get("categories", []))
    except (OSError, json.JSONDecodeError):
        pass
    return list(dict.fromkeys(str(c).strip() for c in cats if str(c).strip()))


def proposals() -> dict[str, Any]:
    """還在候診的新分類提案：slug -> {count, first_at, last_at, reason, examples}。"""
    try:
        data = json.loads(PROPOSALS_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    items = data.get("proposals")
    return items if isinstance(items, dict) else {}


def propose_category(category: str, *, reason: str = "",
                     record_id: str = "") -> tuple[int, bool]:
    """記下一筆新分類提案，回傳（這個名字累積幾次, 是否已達門檻並開了）。

    達到門檻才呼叫 register_category。在那之前這個名字只是被記著——卡片會先歸到
    既有分類，但把提議的名字留在自己身上（呼叫端負責寫 proposed_category），所以
    門檻到了之後撈得回來。這是 wiki topic 那邊 general 候診室的同一個形狀。

    寫不進檔案時回 (0, False)：提案記不下來就不該放行，不然會變成「每次都差一票、
    永遠不會開」之外更糟的情況——悄悄開了一個沒人知道為什麼存在的分類。
    """
    category = _slug(category)
    if not category or category in taxonomy():
        return 0, False
    now = datetime.now(timezone.utc).isoformat()
    try:
        PROPOSALS_PATH.parent.mkdir(parents=True, exist_ok=True)
        try:
            data = json.loads(PROPOSALS_PATH.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            data = {"version": 1, "proposals": {}}
        items = data.setdefault("proposals", {})
        entry = items.setdefault(category, {"count": 0, "first_at": now,
                                            "reason": reason[:200], "examples": []})
        entry["count"] = int(entry.get("count") or 0) + 1
        entry["last_at"] = now
        if reason and not entry.get("reason"):
            entry["reason"] = reason[:200]
        if record_id and record_id not in entry.get("examples", []):
            entry.setdefault("examples", []).append(record_id)
            entry["examples"] = entry["examples"][:10]
        PROPOSALS_PATH.write_text(
            json.dumps(data, ensure_ascii=False, indent=2) + chr(10), encoding="utf-8")
    except OSError:
        return 0, False
    count = int(entry["count"])
    if count < PROMOTE_AFTER:
        return count, False
    return count, register_category(category, reason=entry.get("reason", ""))


def register_category(category: str, *, reason: str = "") -> bool:
    """\u628a\u65b0\u5206\u985e\u5beb\u9032\u8cc7\u6599\u5340\uff0c\u4e0d\u5beb\u9032 skill \u7a0b\u5f0f\u78bc\u3002

    \u9023\u540c\u6642\u9593\u8207\u7406\u7531\u4e00\u8d77\u8a18\uff0c\u56e0\u70ba\u65b0\u5206\u985e\u662f\u6a21\u578b\u63d0\u8b70\u7684\u2014\u2014\u4e4b\u5f8c\u8981\u56de\u982d\u6aa2\u8996
    \u300c\u9019\u500b\u5206\u985e\u7576\u521d\u70ba\u4ec0\u9ebc\u6703\u51fa\u73fe\u300d\u6642\uff0c\u53ea\u6709\u540d\u5b57\u662f\u4e0d\u5920\u7684\u3002
    """
    category = _slug(category)
    if not category or category in taxonomy():
        return False
    try:
        RUNTIME_TAXONOMY_PATH.parent.mkdir(parents=True, exist_ok=True)
        try:
            data = json.loads(RUNTIME_TAXONOMY_PATH.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            data = {"version": 1, "categories": []}
        data.setdefault("categories", []).append(category)
        data["categories"] = list(dict.fromkeys(data["categories"]))
        data.setdefault("added", {})[category] = {
            "at": datetime.now(timezone.utc).isoformat(),
            "reason": reason[:200],
        }
        RUNTIME_TAXONOMY_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return True
    except OSError:
        return False


def _fallback(text: str) -> str:
    data = _load_rules()
    haystack = text.lower()
    for rule in data.get("rules", []):
        if any(str(k).lower() in haystack for k in rule.get("keywords", [])):
            return str(rule.get("category"))
    return str(data.get("default_category", "99-general"))


def classify_content(content: str, *, source_type: str = "", current_category: str = "",
                     allow_new: bool = True, record_id: str = "") -> dict[str, Any]:
    """回傳分類結果；LLM 不可用時退回關鍵字，不會擋住攝取。

    新分類要三個條件同時成立才**算一票**：模型明講 NEW_CATEGORY、slug 合法、
    信心是 high。成立時不是立刻開一個分類，而是記一筆提案；同一個名字累積到
    PROMOTE_AFTER 才真的開。

    原本這裡是一票就開。那跟 wiki topic 那一層的規則不一致——那邊早就是「同一個
    名字被提議到門檻才算重複出現而值得開一頁」，不到門檻的先導向 general 但保留
    提議的名字。分類這一層沒有候診室，於是一筆高信心的零星內容就能長出一個永久
    分類；而另一個極端（完全不准開）會把提案整個丟掉，兩個都不是規則說的。

    還沒達門檻時回傳的 category 是既有分類（關鍵字 fallback），另外附
    `proposed_category` 與 `proposal_count`。呼叫端要把 proposed_category 寫到卡片
    上，門檻到了才撈得回來——這是 general 候診室保留 proposed_topic 的同一件事。

    這是唯一一處允許「自動長出新東西」的地方，而它擴充的是分類名稱，
    不是知識本身——卡片內容仍然只是被貼標籤，沒有任何東西被升級。
    """
    cats = taxonomy()
    prompt = f"""你是 XKB 分類器。請只輸出 JSON，不要 Markdown。

現有主分類（優先沿用，不要創造同義名稱）：
{json.dumps(cats, ensure_ascii=False)}

來源類型：{source_type or 'unknown'}
目前分類（可能是舊分類或錯誤值）：{current_category or '無'}
卡片內容：
{content[:7000]}

規則：
1. 選一個最適合的現有主分類。
2. 只有所有現有分類都明顯不適合時，才使用 NEW_CATEGORY，並提供簡短英文 slug。
3. 不要因為內容有多個主題就創新分類；細節放 tags。
4. 不確定時選 99-general。

格式：{{"category":"現有分類或 NEW_CATEGORY","new_category":"僅 NEW_CATEGORY 時填 slug，否則空字串","confidence":"high|medium|low","reason":"繁體中文一句話","tags":["最多5個英文或短 slug"]}}"""
    try:
        raw = _llm_call("You classify knowledge cards conservatively. Output valid JSON only.", prompt)
        match = re.search(r"\{.*\}", raw or "", re.DOTALL)
        result = json.loads(match.group(0) if match else raw)
        category = str(result.get("category", "")).strip()
        confidence = str(result.get("confidence", "low")).lower()
        new_category = _slug(str(result.get("new_category", "")))
        proposed = ""
        proposal_count = 0
        if category in cats:
            chosen = category
        elif category == "NEW_CATEGORY" and allow_new and new_category and confidence == "high":
            if new_category in cats:
                # 這個名字之前就提議過、而且已經達門檻開了。再走一次提案的話
                # propose_category 會回「不用了」（既有分類不再累積），而把那個回答
                # 當成「還在等」就會把卡片丟回 99-general——門檻機制反而讓第一批
                # 之後的同類卡片全部落在 general。
                chosen = new_category
                proposal_count = PROMOTE_AFTER
            else:
                proposal_count, opened = propose_category(
                    new_category, reason=str(result.get("reason", "")),
                    record_id=record_id)
                if opened:
                    chosen = new_category
                else:
                    # 還在候診：先歸到既有分類，但把提議的名字帶回去給呼叫端留在
                    # 卡片上。
                    chosen = _fallback(content)
                    proposed = new_category
        else:
            chosen = _fallback(content)
            confidence = "low" if confidence not in {"high", "medium"} else confidence
        tags = result.get("tags", [])
        if not isinstance(tags, list):
            tags = []
        return {"category": chosen, "confidence": confidence,
                "reason": str(result.get("reason", ""))[:300],
                "tags": [str(t).strip() for t in tags[:5] if str(t).strip()],
                "llm": True, "new_category": chosen not in cats,
                "proposed_category": proposed, "proposal_count": proposal_count}
    except Exception as exc:
        return {"category": _fallback(content), "confidence": "low",
                "reason": f"LLM 分類失敗，使用關鍵字 fallback：{type(exc).__name__}",
                "tags": [], "llm": False, "new_category": False,
                "proposed_category": "", "proposal_count": 0}


def apply_category(card_content: str, category: str) -> str:
    """Replace only the YAML frontmatter category field.

    寫入交給 xkb_frontmatter.set_field，不要自己再寫一次 regex。原本這裡是
    `^category:\s*.*$`，而 `\s` 會跨行：遇到空的 `category:` 欄位時，`\s*` 吃掉
    換行、`.*$` 吃掉下一行，於是下一個欄位被替換掉。實測

        ---
category:
title: 重要標題
...

    會變成 `category: 99-general` 接著直接是 source_url——title 整行消失，而
    sync_cards_to_wiki 的 make_card 沒有 title 就回 None，那張卡從此不進任何
    topic。索引裡有 4 筆 category 是空的，所以這不是假想的情況。

    這是這個專案第 N 次的 `\s` 跨行 bug（build_search_index.sh 的八個 regex 為
    同一個原因改成 `[ 	]*`）。set_field 已經處理過它，也處理過值裡含反斜線時
    re.sub 把它當跳脫序列的問題。
    """
    if not category:
        return card_content
    if xkb_frontmatter.has_frontmatter(card_content):
        return xkb_frontmatter.set_field(card_content, "category", category)
    # 只有在檔案真的以 frontmatter 開頭時才插入。原本是找「第一個 ---」，
    # 而卡片模板本身充滿分隔線，所以模型沒寫 YAML 區塊時，category 會被插進
    # 正文中間，卡片反而完全沒有分類欄位。
    if card_content.startswith("---\n"):
        return card_content.replace("---\n", f"---\ncategory: {category}\n", 1)
    return f"---\ncategory: {category}\n---\n\n{card_content}"
