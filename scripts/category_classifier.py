"""LLM-assisted, taxonomy-aware card classification for XKB ingest."""
from __future__ import annotations

import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

import xkb_paths
from _llm import call as _llm_call

RULES_PATH = xkb_paths.SKILL_DIR / "config" / "category-rules.json"
# 新分類寫進資料區，不寫進 skill 程式碼——分類是使用者的知識結構，
# 不是這個工具的一部分。路徑一律走 xkb_paths，不要自己再推一次。
RUNTIME_TAXONOMY_PATH = xkb_paths.XKB_DATA_DIR / "category-taxonomy.json"
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
                     allow_new: bool = True) -> dict[str, Any]:
    """回傳分類結果；LLM 不可用時退回關鍵字，不會擋住攝取。

    新分類要三個條件同時成立才接受：模型明講 NEW_CATEGORY、slug 合法、
    信心是 high。成立時會**寫進執行期的分類檔**（連同時間與理由），
    下次就成為既有分類。

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
        if category in cats:
            chosen = category
        elif category == "NEW_CATEGORY" and allow_new and new_category and confidence == "high":
            chosen = new_category
            result["new_category"] = new_category
            register_category(chosen, reason=str(result.get("reason", "")))
        else:
            chosen = _fallback(content)
            confidence = "low" if confidence not in {"high", "medium"} else confidence
        tags = result.get("tags", [])
        if not isinstance(tags, list):
            tags = []
        return {"category": chosen, "confidence": confidence,
                "reason": str(result.get("reason", ""))[:300],
                "tags": [str(t).strip() for t in tags[:5] if str(t).strip()],
                "llm": True, "new_category": chosen not in cats}
    except Exception as exc:
        return {"category": _fallback(content), "confidence": "low",
                "reason": f"LLM 分類失敗，使用關鍵字 fallback：{type(exc).__name__}",
                "tags": [], "llm": False, "new_category": False}


def apply_category(card_content: str, category: str) -> str:
    """Replace only the YAML frontmatter category field."""
    if not category:
        return card_content
    if re.search(r"^category:\s*.*$", card_content, re.MULTILINE):
        return re.sub(r"^category:\s*.*$", f"category: {category}", card_content,
                      count=1, flags=re.MULTILINE)
    # 只有在檔案真的以 frontmatter 開頭時才插入。原本是找「第一個 ---」，
    # 而卡片模板本身充滿分隔線，所以模型沒寫 YAML 區塊時，category 會被插進
    # 正文中間，卡片反而完全沒有分類欄位。
    if card_content.startswith("---\n"):
        return card_content.replace("---\n", f"---\ncategory: {category}\n", 1)
    return f"---\ncategory: {category}\n---\n\n{card_content}"
