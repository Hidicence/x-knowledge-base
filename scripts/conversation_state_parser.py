#!/usr/bin/env python3
"""
Conversation State Parser — Active Recall Layer Phase 1

輸入：使用者訊息字串
輸出：state + trigger_class + confidence + suggested_query

Rule-based，不需要 LLM。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, asdict

# ── 停用詞 ───────────────────────────────────────────────────────────────────
STOPWORDS = {
    "的", "了", "是", "我", "你", "他", "她", "它", "在", "有", "和", "與", "就", "也", "都", "很",
    "想", "要", "用", "讓", "把", "跟", "對", "中", "上", "下", "嗎", "呢", "啊", "吧", "這", "那",
    "this", "that", "with", "from", "have", "will", "about", "your", "they", "what",
    "when", "where", "which", "how", "why", "for", "and", "the", "are", "was",
}

# ── Suppress 規則 ─────────────────────────────────────────────────────────────
SUPPRESS_EXACT = {"ok", "好", "收到", "謝謝", "thanks", "好的", "嗯", "哦", "喔"}

SUPPRESS_PATTERNS = [
    r"^哈+$", r"^哈哈", r"早安", r"晚安", r"午安",
    r"^幫我翻譯", r"^翻譯[一下這個]",
    r"^幫我算", r"^計算",
    r"^寫一?個\s*(function|函?數|程式|腳本|class)",
    r"^你好$", r"^hi$", r"^hello$",
]

# ── Hard Trigger 規則（continuity recall）────────────────────────────────────
HARD_TRIGGER_PATTERNS = [
    # 進度 / 現況詢問
    r"(目前|現在|最新).{0,6}(進度|狀態|status|在哪|做到)",
    r"(做到|完成|做了).{0,6}(哪|什麼|哪裡)",
    r"(xkb|openclaw|wiki|recall|知識庫).{0,10}(現在|目前|最新)",

    # 定義 / 決策回溯
    r"(之前|上次|以前).{0,8}(定義|說過|說好|決定|規劃|設計|怎麼說)",
    r"(不是|我們).{0,4}(說好|說過|定義|決定)",
    r"(原本|先前|之前).{0,6}(方向|架構|定位|設計)",
    r"(我們的|你的|已有的).{0,6}(定義|方向|決策|spec|prd|roadmap)",

    # Roadmap / 計畫接續
    r"(roadmap|計畫|plan|spec|prd).{0,10}(在哪|怎麼|接下來|下一步|下一個)",
    r"(接下來|下一步|下一個).{0,10}(xkb|openclaw|wiki|recall|知識庫)",
    r"(xkb|openclaw).{0,10}(下一步|接下來|下一個)",

    # 「你記得嗎」類
    r"你記得.{0,10}(嗎|之前|上次)",
    r"我們.{0,4}(不是|有沒有|之前).{0,6}(討論|決定|說好|做過)",
]

# ── Soft Trigger 規則（associative recall）───────────────────────────────────
SOFT_TRIGGER_PATTERNS = [
    # 做法 / workflow
    r"怎麼(做|設計|規劃|跑|實作|實現|優化|改)",
    r"(如何|怎樣).{0,4}(做|設計|規劃|實作|實現|優化)",
    r"(workflow|sop|流程|架構|framework|設計模式)",
    r"有沒有.{0,6}(做法|方法|參考|案例|範本|template)",
    r"值得.{0,4}(抄|參考|借鑑|學)",

    # 案例 / 靈感
    r"(案例|範例|參考|靈感|inspiration|example)",
    r"(比較|對比|比一比|vs\.?|對照)",
    r"有.{0,4}(類似|相關|相似).{0,4}(嗎|的嗎|資料|文章|書籤)",

    # 策略 / 決策
    r"(值不值得|要不要|應不應該|可不可以)",
    r"(策略|方向|優先|先做|下一步).{0,6}(建議|看法|怎麼說)",
    r"(選哪|用哪|哪個比較|哪條路)",

    # 高頻知識域 + 問法
    r"(openclaw|xkb|知識庫|wiki|recall).{0,10}(怎麼|如何|有沒有|值不值得|方向|案例)",
    r"(ai.?seo|geo|aeo|seo).{0,10}(值不值得|怎麼做|案例|方向|有沒有)",
    r"(agent|llm|ai).{0,8}(架構|設計|workflow|案例|做法|記憶)",
    r"(content|內容|影片|video).{0,8}(workflow|系統|設計|案例|做法)",
    r"(startup|saas|gtm|產品).{0,8}(策略|方向|案例|做法|設計)",
    r"(automation|自動化|github).{0,8}(工具|做法|案例|設計)",
]

# ── 高頻知識域關鍵詞（輔助提升信心）────────────────────────────────────────
HIGH_FREQ_DOMAINS = [
    "openclaw", "xkb", "x-knowledge-base", "知識庫", "wiki", "recall",
    "agent", "llm", "ai seo", "geo", "aeo", "workflow", "content system",
    "automation", "startup", "saas", "gtm", "ai 影片", "vibe coding",
]


@dataclass
class ParseResult:
    state: str          # continuity | brainstorming | strategy | execution | suppress
    trigger_class: str  # hard | soft | suppress
    confidence: float
    suggested_query: str
    matched_rules: list[str]


def tokenize(text: str) -> list[str]:
    tokens = re.findall(r"[A-Za-z0-9_\-]{2,}|[\u4e00-\u9fff]{1,}", text.lower())
    return [t for t in tokens if t not in STOPWORDS]


def _check_suppress(text: str) -> bool:
    stripped = text.strip().lower()
    if len(stripped) <= 4 and stripped in SUPPRESS_EXACT:
        return True
    if len(stripped) <= 8:
        # Short messages — only suppress if no domain keywords
        has_domain = any(d in stripped for d in HIGH_FREQ_DOMAINS)
        if not has_domain:
            return True
    for p in SUPPRESS_PATTERNS:
        if re.search(p, text, re.IGNORECASE):
            return True
    return False


def _check_hard_trigger(text: str) -> tuple[bool, list[str]]:
    matched = []
    for p in HARD_TRIGGER_PATTERNS:
        if re.search(p, text, re.IGNORECASE):
            matched.append(p[:40])
    return bool(matched), matched


def _check_soft_trigger(text: str) -> tuple[bool, list[str]]:
    matched = []
    for p in SOFT_TRIGGER_PATTERNS:
        if re.search(p, text, re.IGNORECASE):
            matched.append(p[:40])
    return bool(matched), matched


def _build_suggested_query(text: str, trigger_class: str) -> str:
    """簡單抽主題詞 + 意圖詞，生成短 query。"""
    tokens = tokenize(text)

    # 保留有意義的詞（過濾太短或純數字）
    meaningful = [t for t in tokens if len(t) >= 2 and not t.isdigit()]

    # 意圖詞優先
    intent_words = []
    intent_map = {
        "hard": ["進度", "定義", "決策", "roadmap", "status", "之前", "下一步"],
        "soft": ["案例", "做法", "workflow", "策略", "設計", "架構", "如何"],
    }
    for iw in intent_map.get(trigger_class, []):
        if iw in text.lower() and iw not in intent_words:
            intent_words.append(iw)
            if len(intent_words) >= 2:
                break

    # 主題詞（取前 4 個有意義的詞）
    topic_words = [t for t in meaningful if t not in intent_words][:4]

    combined = topic_words + intent_words
    return " ".join(combined[:6]) if combined else text[:30]


def parse(text: str) -> ParseResult:
    # 1. Suppress check
    if _check_suppress(text):
        return ParseResult(
            state="suppress",
            trigger_class="suppress",
            confidence=0.95,
            suggested_query="",
            matched_rules=["suppress"],
        )

    # 2. Hard trigger check
    hard_hit, hard_rules = _check_hard_trigger(text)
    if hard_hit:
        query = _build_suggested_query(text, "hard")
        confidence = min(0.6 + 0.1 * len(hard_rules), 0.95)
        return ParseResult(
            state="continuity",
            trigger_class="hard",
            confidence=confidence,
            suggested_query=query,
            matched_rules=hard_rules[:3],
        )

    # 3. Soft trigger check
    soft_hit, soft_rules = _check_soft_trigger(text)
    if soft_hit:
        query = _build_suggested_query(text, "soft")
        confidence = min(0.5 + 0.08 * len(soft_rules), 0.9)
        return ParseResult(
            state="brainstorming",
            trigger_class="soft",
            confidence=confidence,
            suggested_query=query,
            matched_rules=soft_rules[:3],
        )

    # 4. Domain keyword check (fallback soft trigger)
    text_lower = text.lower()
    domain_hits = [d for d in HIGH_FREQ_DOMAINS if d in text_lower]
    if domain_hits and len(text.strip()) > 10:
        query = _build_suggested_query(text, "soft")
        return ParseResult(
            state="brainstorming",
            trigger_class="soft",
            confidence=0.45,
            suggested_query=query,
            matched_rules=[f"domain:{d}" for d in domain_hits[:2]],
        )

    # 5. Default: suppress
    return ParseResult(
        state="suppress",
        trigger_class="suppress",
        confidence=0.7,
        suggested_query="",
        matched_rules=["no_trigger_matched"],
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Conversation state parser for Active Recall Layer")
    parser.add_argument("message", nargs="?", help="User message to classify")
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    args = parser.parse_args()

    text = args.message or sys.stdin.read().strip()
    if not text:
        print("Usage: conversation_state_parser.py <message>")
        return 1

    result = parse(text)

    if args.json:
        print(json.dumps(asdict(result), ensure_ascii=False, indent=2))
    else:
        print(f"state         : {result.state}")
        print(f"trigger_class : {result.trigger_class}")
        print(f"confidence    : {result.confidence:.2f}")
        print(f"query         : {result.suggested_query}")
        print(f"matched       : {result.matched_rules}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
