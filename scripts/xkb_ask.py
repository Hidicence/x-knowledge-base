#!/usr/bin/env python3
"""Ask XKB using the shared recall contract and generate an evidence-based answer."""
from __future__ import annotations
import argparse
import json
import os
import re
import sys
from pathlib import Path

import xkb_console
from _llm import call as _llm_backend
from xkb_recall import run_configured, recall_options
from xkb_evidence import fields, record_id, render_context

xkb_console.use_utf8()

def llm_call(prompt: str, *, system: str | None = None) -> str:
    return _llm_backend(system or "", prompt)


PROMPT_DIRECT = """\
你是一個在 AI、agent 架構、知識管理領域有長期積累的人。
直接用自己的理解回答，語氣像在跟認識的朋友討論，直接有觀點。
全程繁體中文，不出現簡體字，不加大標題，長度夠用就好。
"""


PROMPT_ENRICHMENT = """\
你是一個在 AI、agent 架構、知識管理領域有長期積累的人，同時有自己的研究積累。

回答方式：
先用自己的通識理解回答這個問題，語氣自然，像在跟朋友討論。
然後，如果下面提供的研究資料裡有你回答中沒有提到的新角度或具體案例，自然地補充進來。

補充時的關鍵要求：
- 引入研究資料的觀點時，要說明來源背景（「有個做了 100 天實驗的案例...」「某個工具的實測結果...」），不要無頭無尾直接說結論
- 說完別人的觀點後，加上你自己的詮釋——為什麼這件事重要、背後的邏輯是什麼
- 格式：「[來源背景]提到/發現 [觀點]。我覺得這背後的原因是...」
- 研究資料裡的東西，只有在「你通識回答裡沒有的」時候才用，重複的不要再說一遍
- 如果研究資料沒有真正新的東西，直接用通識回答就好，不要硬湊
- 有相關連結就在最後附上 2~3 條，沒有就不附
- 全程繁體中文，不提「知識庫」「卡片」「wiki」「筆記」，不加大標題
"""


CONTEXT_TMPL = """\
以下是與這個問題相關的研究資料，裡面可能有你通識回答中沒有的新角度或具體案例：

{context}

---

{query}
"""


_INTERNAL_LABELS = re.compile(
    r"(圖書館管理員|圖書館員|主動決策者|被動記錄者?|被動記錄|知識庫助理|主力維護者|偶爾的閱讀者)",
    re.UNICODE,
)


def _strip_internal_labels(text: str) -> str:
    """Remove internal analysis role-labels from card/wiki excerpts before sending to LLM."""
    text = _INTERNAL_LABELS.sub("", text)
    # Clean up empty bracket pairs left behind (regex avoids CJK char class issues)
    text = re.sub("\u300c\u300d", "", text)  # 「」
    text = re.sub("\u300e\u300f", "", text)  # 『』
    # Clean up orphaned connectors like "和「」" -> "和"
    text = re.sub(r"\s*\u548c\s*$", "", text, flags=re.MULTILINE)  # 和
    return text


_SIMPLIFIED_TO_TRAD = str.maketrans(
    "调这条记忆终样对现时间来实际问题后还没说应该因为就已经体验处理决定创建设计发现开始结束",
    "調這條記憶終樣對現時間來實際問題後還沒說應該因為就已經體驗處理決定創建設計發現開始結束",
)


def _fix_simplified(text: str) -> str:
    """Best-effort fix for common simplified Chinese characters that some LLM providers occasionally output."""
    return text.translate(_SIMPLIFIED_TO_TRAD)


def ask_shared(query: str, args) -> int:
    """Answer from the exact shared recall packet, including conversation traces."""
    try:
        packet = run_configured(query, args.limit, env_file=args.env_file, options=args.options)
    except Exception as exc:
        print(f"無法召回知識：{exc}", file=sys.stderr)
        return 2
    records = packet["records"]
    print(f"[召回] mode={packet['retrieval_mode']}, judge={packet.get('judge', {}).get('status', 'not_attempted')}, records={len(records)}", file=sys.stderr)
    for warning in packet.get("warnings", []):
        print(f"[XKB] {warning}", file=sys.stderr)
    refs = [{"id": record_id(r), "record_type": r.get("record_type", "knowledge"),
             "title": fields(r)[0], "source": fields(r)[2]} for r in records]
    context = render_context(records)
    if refs:
        context += "\n\n證據來源（只引用實際使用的內容）：\n" + "\n".join(
            f"- {r['title']}: {r['source'] or r['id']}" for r in refs)
    previous = os.environ.get("XKB_ENV_FILE")
    if args.env_file:
        os.environ["XKB_ENV_FILE"] = str(args.env_file)
    try:
        prompt = CONTEXT_TMPL.format(context=context, query=query) if records else query
        answer = _fix_simplified(_strip_internal_labels(llm_call(
            prompt, system=PROMPT_ENRICHMENT if records else PROMPT_DIRECT)))
    except RuntimeError as exc:
        print(f"無法產生回答：{exc}", file=sys.stderr)
        return 3
    finally:
        if args.env_file:
            if previous is None:
                os.environ.pop("XKB_ENV_FILE", None)
            else:
                os.environ["XKB_ENV_FILE"] = previous
    if args.json:
        print(json.dumps({"query": query, "answer": answer, "evidence_refs": refs,
            "wiki_refs": [{"slug": Path(r["id"]).stem, "title": r["title"]} for r in refs if r["record_type"] == "wiki_topic"],
            "card_refs": [{"title": r["title"], "url": r["source"]} for r in refs if r["record_type"] == "knowledge_card"],
            "recall": packet}, ensure_ascii=False, indent=2))
    else:
        if args.format == "full":
            print(f"# {query}\n")
        print(answer)
        if refs:
            print("\n召回證據：" + " | ".join(f"{r['title']} → {r['source'] or r['id']}" for r in refs))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("query")
    parser.add_argument("--format", choices=["full", "chat"], default="full")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--env-file")
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--no-wiki", action="store_true")
    parser.add_argument("--no-cards", action="store_true")
    parser.add_argument("--no-conversations", action="store_true")
    parser.add_argument("--no-gbrain", action="store_true", help="共用核心使用關鍵字檢索")
    parser.add_argument("--max-wiki", type=int)
    parser.add_argument("--max-cards", type=int)
    parser.add_argument("--legacy-search", action="store_true", help="已停用的舊搜尋名稱；使用共用核心")
    args = parser.parse_args()
    if args.legacy_search:
        print("--legacy-search 已由共用核心取代；分層與關鍵字選項仍可使用。", file=sys.stderr)
    options = {}
    for arg, key in ((args.no_wiki, "wiki"), (args.no_cards, "cards"),
                     (args.no_conversations, "conversations"), (args.no_gbrain, "semantic")):
        if arg:
            options[key] = False
    for key in ("max_wiki", "max_cards"):
        if getattr(args, key) is not None:
            options[key] = getattr(args, key)
    try:
        args.options = recall_options(options) if options else None
    except ValueError as exc:
        parser.error(str(exc))
    return ask_shared(args.query.strip(), args)


if __name__ == "__main__":
    import xkb_usage
    xkb_usage.record(__file__)
    raise SystemExit(main())
