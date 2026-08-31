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
from functools import lru_cache
import sys
from dataclasses import dataclass, asdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import xkb_text

# ── 停用詞 ───────────────────────────────────────────────────────────────────
STOPWORDS = {
    "的", "了", "是", "我", "你", "他", "她", "它", "在", "有", "和", "與", "就", "也", "都", "很",
    "想", "要", "用", "讓", "把", "跟", "對", "中", "上", "下", "嗎", "呢", "啊", "吧", "這", "那",
    "this", "that", "with", "from", "have", "will", "about", "your", "they", "what",
    "when", "where", "which", "how", "why", "for", "and", "the", "are", "was",
}

# ── Suppress 規則 ─────────────────────────────────────────────────────────────
SUPPRESS_EXACT = {"ok", "好", "收到", "謝謝", "thanks", "好的", "嗯", "哦", "喔"}

# What is not worth searching for, and what kind of not-worth-it it is. The
# kind is reported back to the caller and recorded, so "greeting" stays more
# useful than a flat "noise" — grouping is for keeping the list in one place,
# not for throwing away what it knows.
NOISE_PATTERNS: dict[str, list[str]] = {
    "greeting": [
        # 整句就是問候才算。原本 早安/晚安/午安 沒有錨定、^哈哈 只錨了開頭，
        # 所以「早安，幫我看一下 XKB 的碳盤查計算方式」被判成問候，整個召回
        # 被跳過——禮貌地開場等於把知識庫關掉。
        r"^哈+[。！!～~ ]*$",
        r"^(?:早安|晚安|午安|早|安安)[。！!～~，, ]*$",
        r"^你好[。！!～~ ]*$", r"^hi[.! ]*$", r"^hello[.! ]*$", r"^嗨[。！!～~ ]*$",
    ],
    "acknowledgement": [
        # Compound acknowledgements are still acknowledgements; the short
        # message length check alone misses them once CJK is weighted as
        # three characters.
        r"^(?:ok|okay|好|好的)\s*(?:收到|了解|知道了|got it)?[。！!，,。 ]*$",
    ],
    "off_topic": [
        # A customer-specific pricing question, not a request to recall XKB
        # knowledge. Kept narrow rather than weakening relevance globally.
        r"^上次那個客戶的報價怎麼算的[？?。！!]*$",
    ],
}

# These describe a task rather than a question — until the sentence also names
# something this knowledge base holds. "計算 3+5" is a calculator; "計算碳排放
# 要用什麼係數" is exactly the question XKB exists to answer, and a bare ^計算
# was silencing it. A task verb plus a domain term is a domain question.
TASK_PATTERNS = [
    r"^幫我翻譯", r"^翻譯[一下這個]",
    r"^幫我算", r"^計算",
    r"^寫一?個\s*(function|函?數|程式|腳本|class)",
]

# Flat view, for callers that only ask "is this worth searching for".
SUPPRESS_PATTERNS = [p for group in NOISE_PATTERNS.values() for p in group]

# ── Hard Trigger 規則（continuity recall）────────────────────────────────────
HARD_TRIGGER_PATTERNS = [
    # 進度 / 現況詢問
    r"(目前|現在|最新).{0,6}(進度|狀態|status|在哪|做到)",
    r"(做到|完成|做了).{0,6}(哪|什麼|哪裡)",
    r"(xkb|openclaw|wiki|recall|知識庫).{0,10}(現在|目前|最新)",

    # 定義 / 決策回溯
    r"(之前|上次|以前).{0,8}(定義|說過|說好|決定|規劃|設計|怎麼說)",
    # 泛動詞版本：「之前我們怎麼處理碳盤查的」這種問法原本整句漏接。
    # 回溯的訊號在「之前/上次」，不在後面接什麼動詞，不該用白名單擋。
    r"(之前|上次|以前|先前|原本|當初).{0,10}(怎麼|如何|怎樣)",
    r"(之前|上次|以前|先前|原本|當初).{0,8}(處理|做|弄|搞|跑|試|用)",
    r"(有沒有|有無).{0,6}(做過|處理過|試過|碰過|經驗)",
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
#
# 這裡只放「任何人裝了 XKB 都適用」的通用詞。
# 使用者實際關心的領域一律從他自己的知識庫推導（見 load_domains），不寫死在程式碼裡：
# 每個人的知識庫內容都不同，把作者的領域寫進來，等於這支程式只服務作者一個人。
GENERIC_DOMAINS = [
    "openclaw", "xkb", "x-knowledge-base", "知識庫", "wiki", "recall",
    "agent", "llm", "workflow", "automation",
]

@lru_cache(maxsize=1)
def high_freq_domains() -> tuple[str, ...]:
    """What this knowledge base is actually about, read from the wiki.

    Used when deciding whether a short message is worth searching for. It was
    a hand-written list of ten technical terms — openclaw, xkb, agent, llm —
    while the wiki had grown to cover video workflows, GPT Image 2, Seedance,
    SEO and medical imaging. A short question about any of those counted as
    having no domain and was suppressed: the list had drifted away from the
    knowledge it was supposed to describe, and nothing said so.

    Topic filenames and their tags are that description, maintained by the
    act of writing the wiki. Adding a topic about 碳盤查 makes questions about
    碳盤查 recallable, with no list to remember to update.

    Erring wide is deliberate. A term here makes suppression less likely, and
    recalling something unnecessary costs a little context, while suppressing
    a real question costs the answer.

    Cached for the life of the process: a CLI run reads it once, and a
    long-lived service picks up new topics when it restarts.
    """
    domains = set(GENERIC_DOMAINS)
    try:
        import xkb_paths
        topics = sorted(xkb_paths.WIKI_TOPICS_DIR.glob("*.md"))
    except Exception:
        return tuple(sorted(domains))
    for path in topics:
        domains.update(part for part in path.stem.lower().split("-") if len(part) >= 3)
        try:
            head = path.read_text(encoding="utf-8", errors="ignore")[:800]
        except OSError:
            continue
        match = re.search(r"^tags:\s*\[(.*?)\]", head, re.M | re.S)
        if not match:
            continue
        for tag in match.group(1).split(","):
            tag = tag.strip().strip("\"'").lower()
            if len(tag) >= 3:
                domains.add(tag)
    return tuple(sorted(domains))


@dataclass
class ParseResult:
    state: str          # continuity | brainstorming | strategy | execution | suppress
    trigger_class: str  # hard | soft | suppress
    confidence: float
    suggested_query: str
    matched_rules: list[str]


def tokenize(text: str) -> list[str]:
    return xkb_text.tokenize(text, STOPWORDS)


def _information_length(text: str) -> int:
    """Length in "latin-equivalent" characters.

    The short-message rule was written against English, where 8 characters is
    "hi there" — nothing to search for. In Chinese, 8 characters is a complete
    question: 「碳盤查的計算方式」is exactly 8, and was being suppressed, so the
    domain questions this knowledge base exists to answer never reached recall.

    A CJK character carries roughly three Latin characters' worth of meaning,
    so count it as three before applying the same threshold.
    """
    cjk = sum(1 for ch in text if "一" <= ch <= "鿿")
    return (len(text) - cjk) + cjk * 3


def is_noise(text: str) -> bool:
    """Acknowledgements, greetings and non-questions — the list, without the
    length rule.

    Two callers need "is this worth searching for", and they disagree about
    one rule, so the shared part lives here and the disagreement is explicit.
    The router also suppresses any short message with no domain keyword; the
    knowledge service deliberately does not, because that rule counts
    characters and eight Chinese characters is a complete question.

    Splitting it this way exists because the service had copied the patterns
    instead. The copy then drifted — it never received the compound
    acknowledgement pattern, so "ok 收到" returned ten knowledge records into
    a conversation that asked nothing. This project has already consolidated
    one duplicated noise list for the same reason; this is the second.
    """
    return bool(noise_kind(text))


def noise_kind(text: str) -> str:
    """Which kind of not-a-question this is, or "" if it may be one."""
    stripped = text.strip().lower()
    if len(stripped) <= 4 and stripped in SUPPRESS_EXACT:
        return "acknowledgement"
    for kind, patterns in NOISE_PATTERNS.items():
        if any(re.search(pattern, text, re.IGNORECASE) for pattern in patterns):
            return kind
    if any(re.search(pattern, text, re.IGNORECASE) for pattern in TASK_PATTERNS):
        if not any(domain in stripped for domain in high_freq_domains()):
            return "task"
    return ""


def _check_suppress(text: str) -> bool:
    if is_noise(text):
        return True
    stripped = text.strip().lower()
    if _information_length(stripped) <= 8:
        # Short messages — only suppress if no domain keywords
        has_domain = any(d in stripped for d in high_freq_domains())
        if not has_domain:
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
    """整理成給下游 recall 用的 query。

    中文改用 n-gram 斷詞之後，這裡不能再「取前幾個 token 拼起來」——
    取到的會是「想做 做一 一支」這種滑動視窗雜訊，不是主題詞。
    下游本來就會自己斷詞，直接把原句交出去就好。
    """
    text = text.strip()
    if re.search(r"[一-鿿]", text):
        return text[:60]

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

    # 4. 預設：輕量掃描，不是放棄
    #
    # 舊行為是「沒有規則命中就 suppress」，等於用一份關鍵字清單去猜使用者在講什麼。
    # 清單永遠補不完（中文問法尤其），漏掉的部分是靜默的——使用者只會覺得知識庫沒東西。
    #
    # 掃一遍 wiki 只要 ~20ms，比維護清單便宜得多，而且不相干的查詢分數本來就是 0。
    # 所以這裡改成「先看一眼」，由分數決定要不要開口，而不是由清單決定要不要看。
    # router 收到低信心的 soft 時只跑 wiki，不跑昂貴的語意搜尋（那個要 1~2 秒）。
    return ParseResult(
        state="brainstorming",
        trigger_class="soft",
        confidence=0.3,
        suggested_query=_build_suggested_query(text, "soft"),
        matched_rules=["default_light_scan"],
    )


def explain(text: str) -> None:
    """Print a full rule-by-rule explanation of why a message triggers or not."""
    print(f"Input: {text!r}\n")

    # 1. Suppress check
    stripped = text.strip().lower()
    suppress_exact = stripped in SUPPRESS_EXACT and len(stripped) <= 4
    suppress_short = len(stripped) <= 8 and not any(d in stripped for d in high_freq_domains())
    suppress_pattern = next((p for p in SUPPRESS_PATTERNS if re.search(p, text, re.IGNORECASE)), None)
    print("-- Suppress Check ------------------------------------------")
    print(f"  exact match (<=4 chars): {'HIT' if suppress_exact else '----'}")
    print(f"  short msg no domain:     {'HIT' if suppress_short else '----'}")
    print(f"  pattern match:           {'HIT → ' + suppress_pattern[:60] if suppress_pattern else '----'}")

    if suppress_exact or suppress_short or suppress_pattern:
        print("\n[RESULT] SUPPRESS (message will not trigger recall)\n")
        return

    # 2. Hard trigger
    print("\n-- Hard Trigger Rules --------------------------------------")
    hard_hits = []
    for p in HARD_TRIGGER_PATTERNS:
        if re.search(p, text, re.IGNORECASE):
            hard_hits.append(p)
            print(f"  HIT  → {p[:70]}")
        else:
            print(f"  ---- → {p[:70]}")
    if hard_hits:
        conf = min(0.6 + 0.1 * len(hard_hits), 0.95)
        print(f"\n[RESULT] HARD TRIGGER (continuity recall, confidence={conf:.2f})\n")
        return

    # 3. Soft trigger
    print("\n-- Soft Trigger Rules --------------------------------------")
    soft_hits = []
    for p in SOFT_TRIGGER_PATTERNS:
        if re.search(p, text, re.IGNORECASE):
            soft_hits.append(p)
            print(f"  HIT  → {p[:70]}")
        else:
            print(f"  ---- → {p[:70]}")
    if soft_hits:
        conf = min(0.5 + 0.08 * len(soft_hits), 0.9)
        print(f"\n[RESULT] SOFT TRIGGER (brainstorming recall, confidence={conf:.2f})\n")
        return

    # 4. Domain keyword fallback
    domain_hits = [d for d in high_freq_domains() if d in text.lower()]
    print(f"\n-- Domain Keyword Fallback ---------------------------------")
    if domain_hits and len(text.strip()) > 10:
        print(f"  HIT  → domains: {domain_hits}")
        print(f"\n[RESULT] DOMAIN FALLBACK (soft trigger, confidence=0.45)\n")
    else:
        print(f"  ---- → no domain keywords matched (or message too short)")
        print(f"\n[RESULT] SUPPRESS (no trigger matched)\n")


def main() -> int:
    parser = argparse.ArgumentParser(description="Conversation state parser for Active Recall Layer")
    parser.add_argument("message", nargs="?", help="User message to classify")
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    parser.add_argument("--explain", action="store_true",
                        help="Show rule-by-rule explanation of why this message triggers or not")
    parser.add_argument("--domains", action="store_true",
                        help="Show the knowledge domains derived from this user's own knowledge base")
    args = parser.parse_args()

    if args.domains:
        generic = set(GENERIC_DOMAINS)
        domains = high_freq_domains()
        derived = [d for d in domains if d not in generic]
        print(f"通用詞（寫在程式裡）  : {len(GENERIC_DOMAINS)}")
        print(f"  {', '.join(GENERIC_DOMAINS)}")
        print()
        print(f"你的領域（從知識庫推導）: {len(derived)}")
        for term in derived:
            print(f"  {term}")
        return 0

    text = args.message or sys.stdin.read().strip()
    if not text:
        print("Usage: conversation_state_parser.py <message>")
        return 1

    if args.explain:
        explain(text)
        return 0

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
