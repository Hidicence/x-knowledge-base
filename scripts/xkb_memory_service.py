#!/usr/bin/env python3
"""Local-first XKB Knowledge Service.

The service has two per-turn responsibilities: capture the conversation as
observable L1 evidence, and retrieve semantically relevant knowledge from the
whole XKB plane. Existing XKB files/indexes remain read-side sources of truth;
no endpoint promotes into MEMORY.md, wiki topics, cards, or production indexes.
"""
from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import re
import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager
from collections.abc import Iterator
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

SCRIPT_DIR = Path(__file__).resolve().parent
sys_path = str(SCRIPT_DIR)
import sys
if sys_path not in sys.path:
    sys.path.insert(0, sys_path)
import xkb_paths

try:
    from xbrain_recall import xbrain_query
except ImportError:  # pragma: no cover - semantic backend is optional
    xbrain_query = None

import xkb_eviction
import xkb_failures
import xkb_jev
from xkb_frontmatter import FRONTMATTER
import xkb_relevance
import xkb_text
import xkb_delivery
from xkb_evidence import bounded_excerpt, fields, record_id, render_context, identity_key, IDENTITY_PREFIX
from xkb_recall import recall_options, judge_quality, recall_quality, conversation_messages, conversation_fingerprint, contextual_query, retrieval_queries

# The list of what is not worth searching for lives with the parser, so the
# router and this service cannot disagree about it. They used to: the copy
# here was a subset that never gained the compound acknowledgement pattern,
# and "ok 收到" retrieved ten records into a conversation that asked nothing.
from conversation_state_parser import HARNESS_MARKERS
from conversation_state_parser import noise_kind as _noise_kind

SCHEMA = "xkb-knowledge-service.v1"
TRACE_SCHEMA = "xkb-l1-trace.v1"
KNOWLEDGE_SCHEMA = "xkb-knowledge-record.v1"


def query_terms(query: str) -> list[str]:
    """把查詢切成比對用的詞。用專案共用的那一個斷詞器。

    這裡我繞了一圈：原本 Store.recall 用 query.split()、關鍵字退路用一個
    CJK 正則，於是同一個「查詢詞」有兩種切法。我的第一版修法是自己再寫
    一個——**用第三個定義去修兩個定義的問題**——而且它根本沒作用：
    Python 3 的 \\w 本來就認得 CJK，所以 [\\w\\u4e00-\\u9fff]+ 對中文
    等於沒加東西，沒有標點的中文查詢照樣只切出一個詞。

    xkb_text.tokenize 是這個專案四個召回模組共用的那一個，中文切 n-gram：
    同一句話它給 13 個詞。這件事直接決定對話軌跡的分數是不是一個區間——
    只切出一個詞時，每一筆命中都剛好等於折扣上限 0.65，那不是區間，
    是一個常數，難怪怎麼調錨點都對不齊。
    """
    return xkb_text.tokenize(query or "")


def _normalise(value: str) -> str:
    """Collapse a statement to what it says, for grouping repeated observations.

    Wording drifts between sessions; the claim is what has to match, so
    whitespace and case are removed before hashing.
    """
    return re.sub(r"\s+", " ", (value or "").strip().lower())


def _unverified_warning(records: list[dict[str, Any]]) -> str | None:
    """整批都沒被驗證過時說出來。

    這代表卡片索引讀不到，而不是「知識庫裡沒有東西」。沉默是這個專案最貴的
    失敗模式：排序上再怎麼安排，一個看起來正常、內容卻少了一整層的回覆，
    使用者沒有辦法分辨。
    """
    knowledge_records = [r for r in records
                         if r.get("record_type") != "conversation_trace"]
    if not knowledge_records:
        return None
    # 只有「本來查得到卻查不到」才是故障。整批都是書籤時 id 是網址，
    # 依設計就沒有向量鍵，那是正常情況——用「全部 unverified」當判準會在
    # 完全健康的系統上報索引壞掉。這個判斷由 xkb_relevance 做完帶過來。
    if any(r.get("index_unreadable") for r in knowledge_records):
        return ("知識層這一批沒有任何一筆能跟問題比對——卡片向量索引可能讀不到。"
                "結果仍然回傳，但排序不可信。")
    return None


def _recall_warnings(knowledge: dict[str, Any], filtered_counts: dict[str, Any]) -> list[str]:
    """Say why a result set is thin, distinguishing the three reasons.

    "The backend is broken", "the backend worked but nothing was relevant" and
    "results existed but ACL removed them" produce the same empty list, and
    conflating them is how XKB failures previously stayed invisible for weeks.
    """
    warnings = []
    dropped = knowledge.get("dropped_as_irrelevant", 0)
    if "backends" in knowledge:
        for layer, state in knowledge["backends"].items():
            if state.get("status") in {"unavailable", "error", "timeout", "invalid_response", "unknown"}:
                warnings.append(f"{layer} retrieval: {state['status']}")
    elif knowledge.get("retrieval_mode") != "xbrain_hybrid" and not dropped:
        warnings.append("semantic_backend_unavailable_or_empty; keyword fallback used")
    if dropped:
        warnings.append(f"{dropped} semantic results dropped below the relevance floor")
    if filtered_counts.get("total"):
        warnings.append("records_filtered_by_acl")
    return warnings


def safe_read(path: Path, limit: int = 200_000) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")[:limit]
    except OSError:
        return ""


# Knowledge layers that ACL filtering can be attributed to. "semantic" is not a
# layer but a channel: the backend returns hits without saying which layer they
# came from, so drops there cannot be attributed any more precisely than that.
ACL_LAYERS = ("card", "wiki", "semantic", "conversation")

# 對話用關鍵字比對,卡片用語意相似度。把對話的命中比例乘上這個折扣,
# 讓兩者落在同一個尺度上——全部命中約 0.65,跟中等相關的卡片相當。
KEYWORD_EVIDENCE_DISCOUNT = float(os.getenv("XKB_KEYWORD_DISCOUNT", "0.65"))

READ_SCOPE = "memory:read"
WRITE_SCOPE = "memory:write"
DEFAULT_SCOPES = (READ_SCOPE, WRITE_SCOPE)



class Unauthorized(Exception):
    """Request could not be attributed to a principal, or lacks the scope."""

    def __init__(self, message: str, status: int = 401):
        super().__init__(message)
        self.status = status


class AuthPolicy:
    """Maps a bearer token to the namespace and scopes it is allowed to use.

    Identity has to come from the credential rather than the request body. A
    caller that names its own namespace can name *any* namespace, which makes
    the ACL decorative — that is acceptable only while the service is bound to
    loopback and used by one person.

    With no tokens configured the service stays anonymous, preserving the
    existing single-user setup; the moment a token is configured, anonymous
    access is refused unless it is explicitly re-enabled. Tokens are read from
    headers only, never the query string, because URLs end up in logs.
    """

    def __init__(self, config: dict[str, Any] | None = None):
        config = config or {}
        raw_tokens = config.get("tokens") or {}
        if not isinstance(raw_tokens, dict):
            raise ValueError("auth config: 'tokens' must be an object")
        self.tokens: dict[str, dict[str, Any]] = {}
        for token, entry in raw_tokens.items():
            if not isinstance(token, str) or len(token) < 16:
                raise ValueError("auth config: each token must be a string of at least 16 characters")
            entry = entry if isinstance(entry, dict) else {}
            namespace = str(entry.get("namespace") or "").strip()
            if not namespace:
                raise ValueError("auth config: every token must pin a namespace")
            scopes = entry.get("scopes") or list(DEFAULT_SCOPES)
            self.tokens[token] = {
                "namespace": namespace,
                "scopes": [str(scope) for scope in scopes],
                "label": str(entry.get("label") or "token"),
            }
        # Anonymous stays on only while no token exists; configuring one is the
        # signal that this service is no longer a single-trust-domain box.
        self.allow_anonymous = bool(config.get("allow_anonymous", not self.tokens))

    @classmethod
    def load(cls, path: Path) -> "AuthPolicy":
        try:
            return cls(json.loads(path.read_text(encoding="utf-8")))
        except FileNotFoundError:
            return cls({})
        except (json.JSONDecodeError, OSError) as exc:
            # Fail closed: an unreadable policy must not silently downgrade to
            # "anyone may read everything".
            raise ValueError(f"auth config at {path} could not be read: {exc}") from exc

    @property
    def enabled(self) -> bool:
        return bool(self.tokens)

    def principal(self, token: str | None) -> dict[str, Any]:
        if token:
            for known, entry in self.tokens.items():
                if hmac.compare_digest(token, known):
                    return {"kind": "token", "namespace": entry["namespace"],
                            "scopes": entry["scopes"], "label": entry["label"]}
            raise Unauthorized("invalid service token")
        if self.allow_anonymous:
            # No pinned namespace: the caller may still name one, which is the
            # historical behaviour and only safe on a loopback-only service.
            return {"kind": "anonymous", "namespace": None,
                    "scopes": list(DEFAULT_SCOPES), "label": "anonymous"}
        raise Unauthorized("service token required")

    @staticmethod
    def namespace_for(principal: dict[str, Any], requested: str | None) -> str:
        """A pinned namespace always wins; mismatches are refused, not silently retargeted."""
        pinned = principal.get("namespace")
        requested = (requested or "").strip()
        if pinned:
            if requested and requested != pinned:
                raise Unauthorized("namespace is not permitted for this token", status=403)
            return pinned
        return requested or "private"

    @staticmethod
    def require(principal: dict[str, Any], scope: str) -> None:
        scopes = principal.get("scopes") or []
        if "*" not in scopes and scope not in scopes:
            raise Unauthorized(f"token lacks required scope: {scope}", status=403)


ANONYMOUS_AUTH = AuthPolicy({})


def _keyword_unit_score(blob: str, terms: list[str]) -> float:
    """把出現次數換算到 0–1，好跟餘弦相似度放在一起排序。

    原本直接用 sum(blob.count(term))，動輒十幾二十；而對話軌跡刻意被放在
    0–0.65 這個可與餘弦比較的尺度上。knowledge_recall 把兩者排在一起截斷，
    所以只要語意後端不在——正是共享對話記憶最有用的時候——每一筆軌跡都會
    排在每一個關鍵字命中之下，然後被切掉。

    上界固定，不用這一批的最大值：用批內最大值的話，一批爛結果裡最好的那個
    會被算成滿分。
    """
    if not terms:
        return 0.0
    hits = sum(blob.count(term) for term in terms)
    matched = sum(1 for term in terms if term in blob)
    # 命中詞數的比例是主要訊號，出現次數只當作小幅加成。
    coverage = matched / len(terms)
    density = min(1.0, hits / (len(terms) * 3))
    return round(min(1.0, 0.8 * coverage + 0.2 * density), 4)


def filter_stats(*, card: int = 0, wiki: int = 0, semantic: int = 0, conversation: int = 0) -> dict[str, Any]:
    """Report ACL drops per knowledge layer, not just as one opaque number.

    Without the breakdown, "recall returned nothing" and "recall returned
    nothing *because the wiki layer was filtered out*" look identical — the
    exact ambiguity that let earlier XKB failures stay silent for weeks.

    The flat ``semantic``/``keyword`` channel keys are kept alongside so
    existing callers keep working.
    """
    by_layer = {"card": card, "wiki": wiki, "semantic": semantic, "conversation": conversation}
    return {
        "total": sum(by_layer.values()),
        "by_layer": by_layer,
        "semantic": semantic,
        "keyword": card + wiki,
    }


# jev 判斷的門檻。2026-09-24 用 12 個 Pan 真的問過的問題、走完整召回路徑量到的
# 分布：確實回答問題的落在 0.17~0.92、邊緣但有關的 0.11~0.13、純雜訊 0.01~0.07。
# 0.07 到 0.11 之間是乾淨的空隙，所以取 0.10——砍掉全部雜訊，留下邊緣的那些。
#
# 這個數字是資料定的，不是挑的。改它之前先重跑那份量測。
JUDGE_FLOOR = 0.10


def judgeable_text(item: dict[str, Any]) -> str:
    """一筆記錄可以拿去判斷的文字。

    **每一種記錄的內容在不同欄位。** 卡片與 wiki 在 title/summary，對話軌跡在
    query/answer。只讀 title/summary 的話，對話軌跡會拿不到文字——而拿不到文字
    在下游就等於「判斷為不相關」，於是「你之前說過什麼」那一整層會被無條件丟掉。

    那一層混著金礦和垃圾：問「食品展的客戶通常怎麼找」時，注入的三筆軌跡裡有
    一筆是完整的正確答案（展前拿名單、展中面對面、展後跟催），另外兩筆是
    harness 通知留下的殘骸。所以它需要被判斷，不是被略過、也不是被全丟。
    """
    if not any(item.get(k) for k in ("title", "query", "section", "summary", "answer", "excerpt")):
        return ""
    title, body, _ = fields(item)
    return f"{title} {body}".strip()


def judge_relevance(query: str, records: list[dict[str, Any]], *,
                    floor: float = JUDGE_FLOOR
                    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """讓 jev 決定哪些記錄真的回答了這個問題。回 (保留的, 說明)。

    **判斷放在合併點，不是塞在某一條腿裡。** 三條腿加對話軌跡只在這裡同時存在；
    放在語意腿裡的話，對話軌跡與關鍵字命中會整批繞過判斷——2026-09-24 的模擬就
    是這樣：碳盤查那一題注入的四筆軌跡全是 harness 殘骸，而它們從來沒被任何一層
    判斷過。

    **判斷不出來就全部保留。** jev 回 None 代表沒跑（憑證沒設、端點不通、逾時），
    那時候的正確行為是退回今天的樣子，而不是清空召回。這個專案為「靜默關閉知識庫」
    付過 12 週。

    說明會回給呼叫端放進回應裡，所以「被砍掉幾筆、為什麼」看得見——不是靜靜消失。
    """
    if not records:
        return records, {"status": "no_records"}
    candidates = [(str(id(item)), judgeable_text(item)) for item in records]
    candidates = [(key, text) for key, text in candidates if text]
    if not candidates:
        return records, {"status": "no_text"}
    verdicts = xkb_jev.relevance(query, candidates)
    if verdicts is None:
        return records, {"status": "unavailable", "kept": len(records)}
    kept = []
    for item in records:
        score = verdicts.get(str(id(item)))
        if score is None:
            # 沒有拿到這一筆的判斷，保留它。缺答案不是否定的答案。
            item["judge"] = None
            kept.append(item)
            continue
        # Keep the verdict's precision: rounding .0999 to .100 would make
        # usage accounting call a filtered-out item relevant at the .10 floor.
        item["judge"] = float(score)
        if score >= floor:
            kept.append(item)
    judged = sum(verdicts.get(str(id(item))) is not None for item in records)
    return kept, {"status": "judged" if judged == len(records) else "partial", "floor": floor,
                  "judged": judged, "unjudged": len(records) - judged,
                  "considered": len(records), "kept": len(kept),
                  "dropped": len(records) - len(kept)}


def tag_demoted(records: list[dict[str, Any]], sink: Any, *, relevant_ids: set[str] | None = None) -> None:
    """Apply historical demotion after merging; a current relevant verdict revives.

    Scores from retrieval legs are not judge verdicts. Unknown verdicts do not
    add rejections, but they do not erase earlier explicit rejections either.
    Missing statistics must never break recall.
    """
    for item in records:
        item.pop("demoted", None)
    if sink is None or not records:
        return
    try:
        demoted = sink()
    except Exception:
        return
    cleared = relevant_ids or set()
    for item in records:
        key = identity_key(item)
        if key in demoted and key not in cleared:
            item["demoted"] = True


class KnowledgeCatalog:
    """Read-only facade over the existing XKB data plane.

    The service owns the API boundary; existing markdown/JSON stores remain the
    source of truth during this migration. No ingest or promotion is performed
    here yet.
    """

    def __init__(self):
        self.index_file = xkb_paths.INDEX_FILE
        self.cards_dir = xkb_paths.CARDS_DIR
        self.wiki_dir = xkb_paths.WIKI_DIR
        self.wiki_topics_dir = xkb_paths.WIKI_TOPICS_DIR
        self.status_files = [xkb_paths.XKB_DATA_DIR / "status-after-x-sync-20260726.json"]
        # 每個請求自己一份。原本是實例屬性，而 ThreadingHTTPServer 的每一條
        # 執行緒共用同一個 catalog：兩個同時進來的 recall 會互相覆蓋，沒有
        # 語意後端時 wiki 的 ACL 計數還會在同一個 dict 上一直累加，於是
        # records_filtered_by_acl 從此每一次回應都出現。
        self._local = threading.local()
        # Set by Store so usage can be persisted; the catalog itself stays
        # read-only over the knowledge plane.
        self.usage_sink: Any = None
        # 影子比對的出口。由 Store 接上，跟 usage_sink 同一個形狀。
        self.shadow_sink: Any = None

    def _index(self) -> list[dict[str, Any]]:
        try:
            data = json.loads(safe_read(self.index_file, 20_000_000))
            return data.get("items", data if isinstance(data, list) else [])
        except (json.JSONDecodeError, OSError):
            return []

    def _card_path(self, card_id: str) -> Path | None:
        if not re.fullmatch(r"[A-Za-z0-9_.-]+", card_id):
            return None
        path = self.cards_dir / f"{card_id}.md"
        return path if path.exists() else None

    def _wiki_path(self, topic_id: str) -> Path | None:
        if not re.fullmatch(r"[A-Za-z0-9_.-]+", topic_id):
            return None
        path = self.wiki_topics_dir / f"{topic_id}.md"
        return path if path.exists() else None

    @staticmethod
    def _frontmatter(path: Path) -> dict[str, Any]:
        """Read only simple YAML scalar/list metadata; malformed metadata fails closed."""
        content = safe_read(path, 20_000)
        if not content.startswith("---"):
            return {}
        result: dict[str, Any] = {}
        for line in content.splitlines()[1:]:
            if line.strip() == "---":
                break
            match = re.match(r"^([A-Za-z_][A-Za-z0-9_-]*):\s*(.*?)\s*$", line)
            if not match:
                continue
            key, value = match.groups()
            value = value.strip().strip('"').strip("'")
            if value.startswith("[") and value.endswith("]"):
                value = [part.strip().strip('"').strip("'") for part in value[1:-1].split(",") if part.strip()]
            result[key] = value
        return result

    @property
    def _stats(self) -> dict[str, Any]:
        """這一條執行緒、這一個請求的過濾統計。"""
        if not hasattr(self._local, "stats"):
            self._local.stats = filter_stats()
        return self._local.stats

    @_stats.setter
    def _stats(self, value: dict[str, Any]) -> None:
        self._local.stats = value

    @property
    def _irrelevant(self) -> int:
        return getattr(self._local, "irrelevant", 0)

    @_irrelevant.setter
    def _irrelevant(self, value: int) -> None:
        self._local.irrelevant = value

    def _reset_request_stats(self) -> None:
        """每次召回開始時清空。沒有這一步，讀不到的路徑會沿用上一個請求的數字。"""
        self._local.stats = filter_stats()
        self._local.irrelevant = 0
        self._local.backends = {}

    @staticmethod
    def _allowed(metadata: dict[str, Any], namespace: str) -> bool:
        """Public is global; every other record needs an explicit matching ACL namespace.

        Legacy records without namespace remain readable only in the historical
        private default, but are never exposed to another namespace.
        """
        sensitivity = str(metadata.get("sensitivity") or metadata.get("visibility") or "private").lower()
        record_namespace = metadata.get("namespace")
        if sensitivity == "public":
            return True
        if isinstance(record_namespace, str) and record_namespace.strip():
            return record_namespace.strip() == namespace
        return namespace == "private"

    def _item_metadata(self, item: dict[str, Any]) -> dict[str, Any]:
        metadata = dict(item)
        card_id = str(item.get("id") or Path(str(item.get("path", ""))).stem)
        card_path = self.cards_dir / f"{card_id}.md"
        if card_path.exists():
            frontmatter = self._frontmatter(card_path)
            for key in ("namespace", "sensitivity", "visibility"):
                if key in frontmatter:
                    metadata[key] = frontmatter[key]
        return metadata

    def card(self, card_id: str, namespace: str = "private") -> dict[str, Any] | None:
        path = self._card_path(card_id)
        item = next(
            (
                item for item in self._index()
                if str(item.get("id", "")) == card_id
                or Path(str(item.get("path", ""))).stem == card_id
                or Path(str(item.get("relative_path", ""))).stem == card_id
            ),
            {},
        )
        if not path:
            # The search index points at raw bookmark sources while cards live
            # in the separate generated-card directory. Resolve by ID rather
            # than exposing a workspace path to callers.
            candidate = self.cards_dir / f"{card_id}.md"
            path = candidate if candidate.exists() else None
        if not path and item:
            candidate = self.cards_dir / f"{item.get('id', '')}.md"
            path = candidate if candidate.exists() else None
        if not path:
            return None
        metadata = self._item_metadata(item)
        if not self._allowed(metadata | self._frontmatter(path), namespace):
            return None
        return {
            "schema": KNOWLEDGE_SCHEMA,
            "id": card_id,
            "record_type": "knowledge_card",
            "source_type": item.get("source_type", "unknown"),
            "source_url": item.get("source_url", ""),
            "memory_layer": "external_knowledge",
            "visibility": metadata.get("sensitivity", metadata.get("visibility", "private")),
            "namespace": metadata.get("namespace", "private"),
            "metadata": metadata,
            "content": safe_read(path),
            "path": str(path),
        }

    def wiki_topic(self, topic_id: str, namespace: str = "private") -> dict[str, Any] | None:
        path = self._wiki_path(topic_id)
        if not path:
            return None
        metadata = self._frontmatter(path)
        if not self._allowed(metadata, namespace):
            return None
        return {
            "schema": KNOWLEDGE_SCHEMA,
            "id": topic_id,
            "record_type": "wiki_topic",
            "memory_layer": "knowledge_product",
            "visibility": metadata.get("sensitivity", metadata.get("visibility", "private")),
            "namespace": metadata.get("namespace", "private"),
            "metadata": metadata,
            "content": safe_read(path),
            "path": str(path),
        }

    def source(self, source_id: str, namespace: str = "private") -> dict[str, Any] | None:
        matches = [item for item in self._index() if (str(item.get("id", "")) == source_id or source_id in str(item.get("source_url", ""))) and self._allowed(self._item_metadata(item), namespace)]
        if not matches:
            return None
        return {"schema": KNOWLEDGE_SCHEMA, "record_type": "source", "source_id": source_id, "items": matches[:20]}

    def evidence(self, evidence_id: str, namespace: str = "private") -> dict[str, Any] | None:
        card = self.card(evidence_id, namespace)
        if card:
            return {"schema": KNOWLEDGE_SCHEMA, "record_type": "evidence", "evidence_id": evidence_id, "card": card}
        return None

    def _semantic_search(self, query: str, limit: int, namespace: str = "private") -> list[dict[str, Any]]:
        """Use the existing XBrain/Gemini hybrid index when available.

        The service must not silently claim semantic retrieval when the vector
        backend is unavailable, so the caller receives an explicit mode.
        """
        state = {"backend": "xbrain_hybrid", "available": False, "attempted": False,
                 "used": False, "status": "unavailable"}
        if not hasattr(self._local, "backends"):
            self._local.backends = {}
        self._local.backends["cards"] = state
        if xbrain_query is None or not query.strip():
            return []
        try:
            hits = xbrain_query(query, limit=limit, no_expand=True, semantic=True, diagnostics=state)
            if hits:
                state.update(available=True, attempted=True, used=True, status="used")
        except Exception as err:
            # 每一台機器都是透過這個服務問 XKB。這裡回空的，對方收到的是
            # 「我們沒有這方面的知識」——跟一切正常時的回答一模一樣。
            state.update(attempted=True, status="error")
            xkb_failures.note("service semantic search", err)
            return []
        records = []
        filtered = 0
        for hit in hits[:limit]:
            metadata = {"namespace": hit.get("namespace"), "sensitivity": hit.get("sensitivity", hit.get("visibility"))}
            if not self._allowed(metadata, namespace):
                filtered += 1
                continue
            records.append({
                "schema": KNOWLEDGE_SCHEMA,
                "record_type": "knowledge_chunk",
                "id": hit.get("slug") or hit.get("source_url") or f"semantic:{len(records)}",
                "title": hit.get("title", ""),
                "summary": hit.get("chunk_text", ""),
                **({"excerpt_truncated": True} if hit.get("excerpt_truncated") else {}),
                "source_url": hit.get("source_url", ""),
                "source_type": hit.get("type") or "xkb",
                "memory_layer": "external_knowledge",
                "visibility": hit.get("sensitivity", hit.get("visibility", "private")),
                "namespace": hit.get("namespace", "private"),
                "score": hit.get("score", 0.0),
                # gbrain 的混合 RRF。source_type 這裡是資料種類（會回給
                # 用戶），跟分數的尺度是兩件事。
                "score_scale": hit.get("score_scale", "card"),
                "retrieval": "xbrain_hybrid",
            })
        # The semantic backend does not report which knowledge layer a hit came
        # from, so ACL drops here can only be attributed to the channel.
        self._stats = filter_stats(semantic=filtered)
        # GBrain owns this candidate order. The legacy JSON vector index has
        # different coverage and embeddings; using it here demotes newly
        # published cards solely because they are absent from that side index.
        # Keep its filter only for legacy adapters without the explicit contract.
        hybrid = [r for r in records if r["score_scale"] == "card_hybrid"]
        legacy = [r for r in records if r["score_scale"] != "card_hybrid"]
        if legacy:
            legacy, self._irrelevant = self._drop_irrelevant(query, legacy)
        records = hybrid + legacy
        state.update(candidate_count=len(records), filtered_count=filtered)
        state["used"] = bool(records)
        if state["status"] == "used" and not records:
            state["status"] = "filtered"
        return records

    def _wiki_search(self, query: str, limit: int, namespace: str = "private") -> list[dict[str, Any]]:
        """Search distilled wiki topics and daily memory.

        These already carry true cosine similarity, so they bypass the rank
        score filter applied to the card backend — but not the ACL: a wiki
        page belonging to another namespace must stay invisible here exactly
        as it would through the card path.
        """
        state = {"backend": "wiki_semantic", "available": False, "attempted": False,
                 "used": False, "status": "unavailable"}
        if not hasattr(self._local, "backends"):
            self._local.backends = {}
        self._local.backends["wiki"] = state
        try:
            from continuity_recall import recall_semantic
        except ImportError:
            return []
        try:
            state["attempted"] = True
            # Shared recall delegates relevance to Jev after candidate merging.
            # The legacy cosine floor must not discard conversational evidence
            # before that judge sees it. Count/ACL budgets still apply.
            candidate_options = {"min_similarity": 0.0, "sections_per_document": 2,
                                 "excerpt_limit": 2000} if os.getenv("XKB_JEV_DECIDE", "1") != "0" else {}
            hits = recall_semantic(query, top_k=max(1, limit), **candidate_options)
        except Exception as err:
            state["status"] = "error"
            xkb_failures.note("service wiki search", err)
            return []
        if hits is None:
            return []
        state.update(available=True, used=bool(hits), status="used" if hits else "empty")
        if not hits:
            return []
        allowed = []
        metadata_by_hit = {}
        for hit in hits:
            topic = Path(hit.source_file).stem
            path = self.wiki_topics_dir / f"{topic}.md"
            metadata = self._frontmatter(path) if path.exists() else {}
            if self._allowed(metadata, namespace):
                allowed.append(hit)
                metadata_by_hit[id(hit)] = metadata
            else:
                stats = self._stats
                stats["by_layer"]["wiki"] = stats["by_layer"].get("wiki", 0) + 1
                stats["total"] = stats.get("total", 0) + 1
        state.update(candidate_count=len(allowed), filtered_count=len(hits) - len(allowed))
        state["used"] = bool(allowed)
        if hits and not allowed:
            state["status"] = "filtered"
        hits = allowed
        return [{
            "schema": KNOWLEDGE_SCHEMA,
            "record_type": "wiki_topic" if hit.source_type == "wiki_semantic" else "memory_note",
            "id": hit.source_file,
            "title": hit.section or Path(hit.source_file).stem,
            "source_file": hit.source_file, "section": hit.section,
            "summary": hit.excerpt,
            "source_url": hit.url,
            "source_type": "wiki",
            "memory_layer": "knowledge_product",
            "visibility": metadata_by_hit[id(hit)].get("sensitivity", metadata_by_hit[id(hit)].get("visibility", "private")),
            "namespace": metadata_by_hit[id(hit)].get("namespace", "private"),
            "score": hit.score,
            # 這裡是餘弦，不是關鍵字分數——wiki 的錨點是照關鍵字尺度量的，
            # 套上去會把每一筆 wiki 命中都算錯。
            "score_scale": "wiki_semantic",
            "retrieval": "wiki_semantic",
        } for hit in hits]

    def _drop_irrelevant(self, query: str, records: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], int]:
        """Replace rank scores with true similarity and drop what is not relevant.

        Injecting ten results into every turn regardless of relevance is what
        makes an unrelated question cost as much as a real one. Rank order
        cannot decide this — only the actual query/document cosine can.

        When similarity cannot be computed (no index, no embedding provider)
        the records pass through unchanged: losing recall because the index is
        missing would be a worse failure than showing a few weak results.
        """
        if not records:
            return records, 0
        kept, dropped, scores = xkb_relevance.filter_irrelevant(
            query, records, key_of=lambda item: str(item.get("id") or ""))
        keys = {str(item.get("id") or ""): xkb_relevance.vector_key(str(item.get("id") or ""))
                for item in records}
        if self.usage_sink is not None:
            # Legacy cosine observations remain comparable with historical
            # reports. They do not measure final delivery or drive demotion.
            floor = xkb_relevance.min_similarity()
            try:
                self.usage_sink([
                    (record_id, scores.get(key), (scores.get(key) or 0) >= floor)
                    for record_id, key in keys.items() if key and scores.get(key) is not None
                ])
            except Exception:
                pass  # usage accounting must never break recall
        self._shadow_compare(query, records, scores, keys, kept)
        return kept, dropped

    def _shadow_compare(self, query: str, records: list[dict[str, Any]],
                        scores: dict[str, float], keys: dict[str, str],
                        kept: list[dict[str, Any]]) -> None:
        """在背景問 jev 同一批候選，把它的判斷跟餘弦的決定一起存起來。

        **影子模式：什麼都不改變。** 餘弦照常決定要丟掉誰，jev 的答案只是被記下來。
        門檻要從真實分布校準，而不是我挑一個數字——這個專案在手調門檻上犯過的錯
        有紀錄在案（xkb_score 的錨點繞了六輪）。

        **丟到背景執行緒，因為它不該讓任何人多等。** jev 實測 1.85 秒；掛在召回
        路徑上等於每一次對話都多等快兩秒，而影子模式下沒有任何東西依賴它的答案。
        等到真的要讓 jev 決定的時候它才會變成同步的——那時候我們已經知道這 1.85 秒
        換到了什麼。

        整段包在 try 裡：比對失敗不可以影響召回，這是純觀測。
        """
        if self.shadow_sink is None or not records:
            return
        # 這個檔其餘的旗標都走 os.getenv（見 XKB_SERVICE_LOG），跟著它。
        if os.getenv("XKB_JEV_SHADOW", "1") == "0":
            return
        # jev 已經在合併點做決定時，影子比對就是第二次呼叫同一個模型，而它要比對的
        # 「餘弦的決定」也已經不是最後的決定了。預設關掉；要重新量餘弦與 jev 的差異
        # 時再開（XKB_JEV_SHADOW=1 搭配 XKB_JEV_DECIDE=0）。
        if os.getenv("XKB_JEV_DECIDE", "1") != "0":
            return
        if not xkb_jev.available():
            return
        floor = xkb_relevance.min_similarity()
        kept_ids = {str(item.get("id") or "") for item in kept}
        # 候選在主執行緒先抓成不可變的資料。背景執行緒去讀 records 的話，
        # 讀到的可能是下一次請求改過的內容。
        candidates: list[tuple[str, str]] = []
        cosines: dict[str, float | None] = {}
        for item in records:
            record_id = str(item.get("id") or "")
            if not record_id:
                continue
            text = " ".join(str(item.get(field) or "")
                            for field in ("title", "summary")).strip()
            if not text:
                continue
            candidates.append((record_id, text))
            key = keys.get(record_id) or ""
            cosines[record_id] = scores.get(key) if key else None
        if not candidates:
            return

        def run() -> None:
            try:
                verdicts = xkb_jev.relevance(query, candidates)
                if verdicts is None:
                    # jev 沒跑成。不要寫一整批 jev=NULL 的列——那會在分析時
                    # 長得像「jev 覺得每一筆都不相關」。
                    return
                self.shadow_sink(
                    query,
                    [(record_id, cosines.get(record_id), verdicts.get(record_id),
                      record_id in kept_ids) for record_id, _text in candidates],
                    floor)
            except Exception:  # noqa: BLE001
                pass  # 純觀測，不可以影響召回

        threading.Thread(target=run, daemon=True,
                         name="jev-shadow").start()

    @staticmethod
    def _acl_policy(namespace: str) -> dict[str, Any]:
        """Stable, machine-readable explanation of the fail-closed ACL rule."""
        return {
            "name": "namespace_match_public_global",
            "request_namespace": namespace,
            "public_global": True,
            "legacy_private_default": True,
            "decision": "allow_public_or_matching_namespace",
        }

    def search(self, query: str, limit: int = 10, namespace: str = "private", *, options=None) -> dict[str, Any]:
        if not isinstance(query, str) or not query.strip():
            raise ValueError("query is required")
        limit = bounded_int(limit, name="limit", default=10, minimum=1, maximum=50)
        if not isinstance(namespace, str) or not namespace.strip():
            raise ValueError("namespace is required")
        self._reset_request_stats()
        opts = recall_options(options)
        card_limit = min(limit, opts["max_cards"]) if opts["cards"] else 0
        wiki_limit = min(limit, opts["max_wiki"]) if opts["wiki"] else 0
        # Preserve the existing semantic Wiki budget without reducing keyword
        # quotas. Options cap candidates; they do not expand default payloads.
        wiki_semantic_limit = min(wiki_limit, max(2, limit // 2))
        def search_layer(spec):
            layer, budget, search = spec
            self._reset_request_stats()
            self._local.backends[layer] = {"backend": "xbrain_hybrid" if layer == "cards" else "wiki_semantic",
                "available": False, "attempted": False, "used": False, "status": "disabled"}
            rows = []
            started = time.monotonic()
            if budget and opts["semantic"]:
                # Unknown is useful for adapters that return rows but omit diagnostics.
                self._local.backends[layer]["status"] = "unknown"
                rows = search(query, budget, namespace)
                if rows:
                    self._local.backends[layer].update(available=True, attempted=True, used=True, status="used")
            state = dict(self._local.backends[layer], elapsed_ms=round((time.monotonic()-started)*1000))
            return layer, rows, state, self._stats, self._irrelevant
        specs = [("cards", card_limit, self._semantic_search),
                 ("wiki", wiki_semantic_limit, self._wiki_search)]
        if opts["semantic"] and card_limit and wiki_semantic_limit:
            from concurrent.futures import ThreadPoolExecutor
            with ThreadPoolExecutor(max_workers=2) as pool:
                layers = list(pool.map(search_layer, specs))
        else:
            layers = [search_layer(spec) for spec in specs]
        # Worker diagnostics are thread-local. Merge their returned snapshots,
        # never whichever stats happen to remain on the caller's thread.
        semantic_records = [record for _, rows, _, _, _ in layers for record in rows]
        self._local.backends = {layer: state for layer, _, state, _, _ in layers}
        self._stats = filter_stats(**{name: sum(stats['by_layer'][name] for _, _, _, stats, _ in layers)
                                    for name in ('card', 'wiki', 'semantic', 'conversation')})
        self._irrelevant = sum(count for _, _, _, _, count in layers)
        backends = self._local.backends
        used = [v["backend"] for v in backends.values() if v["used"]]
        semantic_backend = {"name": "+".join(used) or "none",
            "available": any(v["available"] for v in backends.values()),
            "attempted": any(v["attempted"] for v in backends.values()),
            "used": bool(used), "status": "used" if used else "empty" if any(v["status"] == "empty" for v in backends.values()) else "unavailable"}
        records = list(semantic_records)
        retrieval_mode = ("xbrain_hybrid" if backends["cards"]["used"] else "wiki_semantic") if records else (
            ("keyword_fallback" if opts["semantic"] else "keyword") if card_limit or wiki_limit else "conversation_only")
        # One successful source must not prevent another source from falling
        # back. In particular, broad wiki candidates cannot hide exact cards
        # while the card semantic backend is unavailable or empty.
        keyword_cards = card_limit if not backends["cards"]["used"] else 0
        keyword_wiki = wiki_limit if not backends["wiki"]["used"] else 0
        if keyword_cards or keyword_wiki:
            terms = [term for term in query_terms(query) if term not in xkb_text.STOPWORDS]
            hits: list[tuple[float, dict[str, Any], bool]] = []
            filtered_cards = filtered_wiki = 0
            for item in self._index() if keyword_cards else []:
                metadata = self._item_metadata(item)
                if not self._allowed(metadata, namespace):
                    filtered_cards += 1
                    continue
                blob = " ".join(str(item.get(key) or "") for key in
                                ("title", "summary", "tags", "keywords")).lower()
                score = _keyword_unit_score(blob, terms)
                if score:
                    hits.append((score, {"schema": KNOWLEDGE_SCHEMA, "record_type": "knowledge_card", "id": str(item.get("id") or Path(str(item.get("path", ""))).stem), "title": item.get("title", ""), "summary": item.get("summary", ""), "source_url": item.get("source_url", ""), "source_type": item.get("source_type", "unknown"), "memory_layer": "external_knowledge", "score_scale": "card_keyword", "visibility": metadata.get("sensitivity", metadata.get("visibility", "private")), "namespace": metadata.get("namespace", "private"), "score": score, "retrieval": "keyword"}, all(term in blob for term in terms)))
            for path in sorted(self.wiki_topics_dir.glob("*.md")) if keyword_wiki else []:
                metadata = self._frontmatter(path)
                if not self._allowed(metadata, namespace):
                    filtered_wiki += 1
                    continue
                content = FRONTMATTER.sub("", safe_read(path, 100_000), count=1)
                blob = (content + " " + " ".join(str(metadata.get(key) or "") for key in
                        ("title", "tags", "keywords"))).lower()
                score = _keyword_unit_score(blob, terms)
                if score:
                    hits.append((score, {"schema": KNOWLEDGE_SCHEMA, "record_type": "wiki_topic", "id": path.stem, "title": path.stem, "summary": bounded_excerpt(content, 1600), "source_url": "", "source_type": "wiki", "memory_layer": "knowledge_product", "score_scale": "wiki_keyword", "visibility": metadata.get("sensitivity", metadata.get("visibility", "private")), "namespace": metadata.get("namespace", "private"), "score": score, "retrieval": "keyword"}, all(term in blob for term in terms)))
            # Prefer complete content matches when available. Retain partial
            # matches when none exist, including natural-language CJK queries
            # whose overlapping n-grams rarely all appear in one document.
            if any(complete for _, _, complete in hits):
                hits = [hit for hit in hits if hit[2]]
            hits.sort(key=lambda pair: pair[0], reverse=True)
            keyword_records = []
            counts = {"cards": 0, "wiki": 0}
            for _, item, _ in hits:
                layer = "wiki" if item["record_type"] == "wiki_topic" else "cards"
                if counts[layer] < (wiki_limit if layer == "wiki" else card_limit):
                    keyword_records.append(item)
                    counts[layer] += 1
            records.extend(keyword_records[:limit])
            for layer, budget in (("cards", keyword_cards), ("wiki", keyword_wiki)):
                if budget:
                    backends[layer]["fallback"] = {"backend": "keyword", "status": "used" if counts[layer] else "empty"}
            previous = self._stats["by_layer"]
            self._stats = filter_stats(card=filtered_cards + previous.get("card", 0),
                                       wiki=filtered_wiki + previous.get("wiki", 0),
                                       semantic=previous.get("semantic", 0))
        filtered_counts = dict(self._stats)
        context = render_context(records)
        return {
            "schema": SCHEMA, "query": query, "namespace": namespace,
            "request_namespace": namespace, "acl_policy": self._acl_policy(namespace),
            "records": records, "count": len(records), "context": context,
            "retrieval_mode": retrieval_mode, "semantic_backend": semantic_backend, "backends": backends,
            "filtered_counts": filtered_counts,
            "dropped_as_irrelevant": self._irrelevant,
            "warnings": [],
        }

    def relations(self, card_id: str, namespace: str = "private") -> dict[str, Any]:
        """一張卡的關聯資訊。要帶 namespace，跟其他讀取端點一樣。

        原本用預設的 private 去查卡片，所以被釘在別的 namespace 的 token
        也分得出「這張卡存在但沒有關聯區」與「查無此卡」——那就是一個
        存在性探測。"""
        card = self.card(card_id, namespace)
        content = card.get("content", "") if card else ""
        return {"schema": KNOWLEDGE_SCHEMA, "card_id": card_id, "relations": [], "status": "derived_from_card_content", "note": "No canonical relation store is currently exposed." if card else "card_not_found", "content_has_relation_section": bool(re.search(r"relation|關係|補充|衝突|延伸", content, re.I))}

    def pipeline_status(self) -> dict[str, Any]:
        statuses = []
        for path in self.status_files:
            if path.exists():
                try:
                    statuses.append({"path": str(path), "status": json.loads(safe_read(path))})
                except json.JSONDecodeError:
                    statuses.append({"path": str(path), "status": "invalid_json"})
        return {"schema": SCHEMA, "read_only": True, "jobs": statuses, "control_plane": "observed_status_only"}

    def pipeline_snapshot(self, days: int = 7) -> dict[str, Any]:
        """Return one read-only view of the existing XKB pipeline.

        This deliberately describes ownership and observed filesystem/status
        evidence; it does not infer that a worker is currently running and it
        never starts or retries one.
        """
        stages = [
            ("ingest", "run_bookmark_worker.py", "抓取與來源匯入"),
            ("card_generation", "run_scan_worker.py", "生成 knowledge cards"),
            ("index", "build_vector_index.py", "建立搜尋／向量索引"),
            ("distill", "distill_memory_to_wiki.py", "整理候選知識"),
            ("promotion", "sync_cards_to_wiki.py", "通過 absorb gate"),
            ("publish", "sync_cards_to_wiki.py", "寫入 wiki 成品層"),
        ]
        workers = []
        for name, script, description in stages:
            path = SCRIPT_DIR / script
            workers.append({
                "stage": name,
                "script": script,
                "description": description,
                "script_exists": path.exists(),
                "observability": "filesystem_and_status_files",
                "control": "read_only",
            })
        try:
            sys.path.insert(0, str(SCRIPT_DIR))
            import status_knowledge_pipeline as pipeline
            items = pipeline.load_search_index()
            decisions = pipeline.load_review_decisions()
            topic_files = list(pipeline.TOPICS_DIR.glob("*.md")) if pipeline.TOPICS_DIR.exists() else []
            summary = {
                "bookmarks": pipeline.status_bookmarks(items, days),
                "cards": pipeline.status_cards(),
                "wiki_topics": {"total": len(pipeline.status_wiki_topics())},
                "absorb": pipeline.status_absorb(decisions),
                "staging": pipeline.status_staging(),
                "gap_topics": len(pipeline.status_gaps(items, topic_files)),
            }
        except Exception as exc:  # status facade must remain available
            summary = {"error": f"status_summary_unavailable: {exc}"}
        return {
            "schema": SCHEMA,
            "control_plane": "observed_status_only",
            "read_only": True,
            "generated_at": now(),
            "lookback_days": days,
            "stages": workers,
            "summary": summary,
            "status_files": self.pipeline_status()["jobs"],
            "next_control_plane_step": "persist worker job events before enabling retry or promotion",
        }


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def worker_error(store: "Store", job_id: str, exc: Exception) -> dict[str, Any]:
    error = f"{type(exc).__name__}: {exc}"
    with store.lock, store.connect() as db:
        db.execute("UPDATE jobs SET status='failed',finished_at=?,error=?,retryable=1,updated_at=? WHERE job_id=? AND status='running'", (now(), error, now(), job_id))
    return {"job_id": job_id, "status": "failed", "error": error}


def text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _as_int(raw: Any, fallback: int) -> int:
    """把 query string 的值轉成整數，轉不動就用預設。

    bounded_int 刻意不接受字串（它擋的是 bool 與 float 這種會靜靜通過的型別），
    所以 HTTP 這一層要先轉。
    """
    if raw is None:
        return fallback
    value = raw[0] if isinstance(raw, list) else raw
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return fallback


def bounded_int(value: Any, *, name: str, default: int, minimum: int, maximum: int) -> int:
    """Validate API numeric knobs without accepting bools, floats, or strings."""
    if value is None:
        result = default
    elif isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{name} must be an integer")
    else:
        result = value
    if result < minimum or result > maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}")
    return result


def digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def redact(value: Any) -> Any:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return [redact(item) for item in value]
    if isinstance(value, dict):
        return {
            key: "[REDACTED]" if any(token in key.lower() for token in ("token", "secret", "password", "cookie", "api_key", "authorization")) else redact(item)
            for key, item in value.items()
        }
    return value


class Store:
    def __init__(self, path: Path):
        self.path = path
        self.catalog = KnowledgeCatalog()
        self.catalog.usage_sink = self.record_usage
        self.catalog.shadow_sink = self.record_shadow
        self.lock = threading.RLock()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript(
                """
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS sessions (
                  session_id TEXT PRIMARY KEY,
                  session_key TEXT NOT NULL,
                  source TEXT NOT NULL,
                  agent_id TEXT NOT NULL,
                  namespace TEXT NOT NULL,
                  workspace_path TEXT,
                  created_at TEXT NOT NULL,
                  updated_at TEXT NOT NULL,
                  closed_at TEXT
                );
                CREATE UNIQUE INDEX IF NOT EXISTS sessions_identity
                  ON sessions(namespace, source, session_key);
                CREATE TABLE IF NOT EXISTS turns (
                  turn_id TEXT PRIMARY KEY,
                  session_id TEXT NOT NULL,
                  episode_id TEXT,
                  query TEXT NOT NULL,
                  answer TEXT,
                  status TEXT NOT NULL,
                  trace_id TEXT UNIQUE,
                  payload_json TEXT,
                  retrieval_json TEXT,
                  started_at TEXT NOT NULL,
                  completed_at TEXT,
                  FOREIGN KEY(session_id) REFERENCES sessions(session_id)
                );
                CREATE INDEX IF NOT EXISTS turns_query ON turns(query);
                CREATE INDEX IF NOT EXISTS turns_session ON turns(session_id);
                CREATE TABLE IF NOT EXISTS jobs (
                  job_id TEXT PRIMARY KEY,
                  stage TEXT NOT NULL,
                  worker TEXT NOT NULL,
                  status TEXT NOT NULL,
                  started_at TEXT,
                  finished_at TEXT,
                  input_ref TEXT,
                  output_ref TEXT,
                  error TEXT,
                  retryable INTEGER NOT NULL DEFAULT 0,
                  metadata_json TEXT,
                  created_at TEXT NOT NULL,
                  updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS jobs_stage_status ON jobs(stage, status);
                CREATE INDEX IF NOT EXISTS jobs_updated ON jobs(updated_at);
                CREATE TABLE IF NOT EXISTS candidates (
                  candidate_id TEXT PRIMARY KEY,
                  candidate_key TEXT NOT NULL,
                  candidate_value TEXT NOT NULL,
                  source_trace_ids_json TEXT NOT NULL,
                  episode_ids_json TEXT NOT NULL,
                  confidence REAL NOT NULL DEFAULT 0.0,
                  status TEXT NOT NULL DEFAULT 'pending',
                  reject_reasons_json TEXT NOT NULL DEFAULT '[]',
                  analysis_json TEXT NOT NULL DEFAULT '{}',
                  expires_at TEXT,
                  created_at TEXT NOT NULL,
                  updated_at TEXT NOT NULL
                );
                CREATE UNIQUE INDEX IF NOT EXISTS candidates_key
                  ON candidates(candidate_key);
                CREATE INDEX IF NOT EXISTS candidates_status
                  ON candidates(status, updated_at);
                CREATE TABLE IF NOT EXISTS knowledge_usage (
                  record_id TEXT PRIMARY KEY,
                  considered_count INTEGER NOT NULL DEFAULT 0,
                  injected_count INTEGER NOT NULL DEFAULT 0,
                  best_similarity REAL NOT NULL DEFAULT 0,
                  first_seen_at TEXT NOT NULL,
                  last_considered_at TEXT NOT NULL,
                  last_injected_at TEXT
                );
                CREATE INDEX IF NOT EXISTS knowledge_usage_cold
                  ON knowledge_usage(injected_count, considered_count);
                -- Merged recall counts have different semantics from legacy
                -- cosine passes. Historical counts are never backfilled here.
                CREATE TABLE IF NOT EXISTS recall_usage (
                  namespace TEXT NOT NULL,
                  record_id TEXT NOT NULL,
                  considered_count INTEGER NOT NULL DEFAULT 0,
                  judged_count INTEGER NOT NULL DEFAULT 0,
                  relevant_count INTEGER NOT NULL DEFAULT 0,
                  returned_count INTEGER NOT NULL DEFAULT 0,
                  last_considered_at TEXT NOT NULL,
                  PRIMARY KEY(namespace, record_id)
                );
                -- Optional shadow mode: kept is the cosine decision, not Jev's.
                CREATE TABLE IF NOT EXISTS relevance_shadow (
                  id INTEGER PRIMARY KEY AUTOINCREMENT,
                  at TEXT NOT NULL,
                  query TEXT NOT NULL,
                  record_id TEXT NOT NULL,
                  cosine REAL,
                  jev REAL,
                  kept INTEGER NOT NULL,
                  floor REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS relevance_shadow_at
                  ON relevance_shadow(at);
                """
            )
            columns = {row["name"] for row in db.execute("PRAGMA table_info(turns)").fetchall()}
            if "retrieval_json" not in columns:
                db.execute("ALTER TABLE turns ADD COLUMN retrieval_json TEXT")
            candidate_columns = {row["name"] for row in db.execute("PRAGMA table_info(candidates)").fetchall()}
            if "analysis_json" not in candidate_columns:
                db.execute("ALTER TABLE candidates ADD COLUMN analysis_json TEXT NOT NULL DEFAULT '{}'")

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(self.path, timeout=10, check_same_thread=False)
        db.row_factory = sqlite3.Row
        try:
            # sqlite's transaction context commits/rolls back but does not close
            # the handle. Close it deterministically for repeated HTTP requests
            # and Windows callers that need to release the database file.
            with db:
                yield db
        finally:
            db.close()

    def record_recall_usage(self, namespace: str, candidates: list[dict], returned: list[dict],
                            judge: dict) -> None:
        """Count unique merged candidates, explicit verdicts and returned evidence.

        Legacy knowledge_usage records cosine passes, not delivery. Keep it
        intact; new decisions start with independent, namespace-scoped counters.
        Unknown verdicts never count as rejections. Returned means in the API
        packet, not proof that an agent used it in its answer.
        """
        groups: dict[str, list[dict]] = {}
        for item in candidates:
            key = identity_key(item)
            if key:
                groups.setdefault(key, []).append(item)
        delivered = {identity_key(item) for item in returned}
        with self.lock, self.connect() as db:
            for key, items in groups.items():
                scores = [i.get("judge") for i in items]
                known = [s for s in scores if type(s) in (int, float)] if judge_quality(judge)["has_verdicts"] else []
                relevant = any(s >= judge.get("floor", JUDGE_FLOOR) for s in known)
                judged = bool(known) and (relevant or len(known) == len(scores))
                db.execute("""INSERT INTO recall_usage(namespace, record_id, considered_count,
                    judged_count, relevant_count, returned_count, last_considered_at)
                    VALUES(?,?,1,?,?,?,?) ON CONFLICT(namespace,record_id) DO UPDATE SET
                    considered_count=considered_count+1, judged_count=judged_count+excluded.judged_count,
                    relevant_count=relevant_count+excluded.relevant_count,
                    returned_count=returned_count+excluded.returned_count,
                    last_considered_at=excluded.last_considered_at""",
                    (namespace, key, int(judged), int(relevant), int(key in delivered), now()))

    def record_usage(self, observations: list[tuple[str, float, bool]]) -> None:
        """Retain legacy cosine-pass observations for historical reports.

        The old injected_count name does not mean returned or used in an answer.
        Current demotion uses record_recall_usage instead.
        """
        if not observations:
            return
        timestamp = now()
        with self.lock, self.connect() as db:
            for record_id, similarity, injected in observations:
                db.execute(
                    """
                    INSERT INTO knowledge_usage(record_id, considered_count, injected_count,
                                                best_similarity, first_seen_at, last_considered_at, last_injected_at)
                    VALUES(?,1,?,?,?,?,?)
                    ON CONFLICT(record_id) DO UPDATE SET
                      considered_count = considered_count + 1,
                      injected_count = injected_count + excluded.injected_count,
                      best_similarity = MAX(best_similarity, excluded.best_similarity),
                      last_considered_at = excluded.last_considered_at,
                      last_injected_at = COALESCE(excluded.last_injected_at, last_injected_at)
                    """,
                    (record_id, 1 if injected else 0, float(similarity or 0.0),
                     timestamp, timestamp, timestamp if injected else None),
                )

    def record_shadow(self, query: str, rows: list[tuple[str, float | None, float | None, bool]],
                      floor: float) -> None:
        """存一次餘弦與 jev 的對照。純觀測，不影響任何決定。

        存 query 是因為門檻要從真實分布校準，而「同一個問題下兩邊怎麼排」才是
        有意義的單位——只存分數的話，看不出 jev 是在哪一類問題上跟餘弦分岔。
        """
        if not rows:
            return
        timestamp = now()
        try:
            with self.lock, self.connect() as db:
                db.executemany(
                    "INSERT INTO relevance_shadow(at, query, record_id, cosine, jev, kept, floor)"
                    " VALUES(?,?,?,?,?,?,?)",
                    [(timestamp, query[:500], record_id, cosine, jev, 1 if kept else 0, floor)
                     for record_id, cosine, jev, kept in rows])
        except sqlite3.Error as err:
            xkb_failures.note("relevance shadow", err)

    def demoted_ids(self, after: int = xkb_eviction.DEMOTE_AFTER_CONSIDERED, *, namespace: str = "private") -> set[str]:
        """IDs repeatedly judged irrelevant in this namespace, never relevant."""
        try:
            with self.lock, self.connect() as db:
                rows = db.execute(
                    "SELECT record_id, judged_count, relevant_count"
                    "  FROM recall_usage WHERE namespace = ? AND relevant_count = 0 AND judged_count >= ? AND record_id LIKE ?",
                    (namespace, max(1, int(after)), IDENTITY_PREFIX + "%"),
                ).fetchall()
        except sqlite3.Error:
            return set()
        return {r["record_id"] for r in rows
                if xkb_eviction.is_demoted(r["judged_count"], r["relevant_count"],
                                           after=after)}

    def cold_knowledge(self, min_considered: int = 5, limit: int = 100) -> dict[str, Any]:
        """Records repeatedly retrieved that never once cleared the relevance floor.

        Reported only. Nothing is archived or deleted automatically: XKB's
        value is provenance, and silently dropping evidence would trade that
        away for tidiness.
        """
        min_considered = bounded_int(min_considered, name="min_considered", default=5, minimum=1, maximum=1000)
        limit = bounded_int(limit, name="limit", default=100, minimum=1, maximum=1000)
        with self.lock, self.connect() as db:
            rows = db.execute(
                """
                SELECT * FROM knowledge_usage
                WHERE injected_count = 0 AND considered_count >= ?
                ORDER BY considered_count DESC, best_similarity ASC LIMIT ?
                """,
                (min_considered, limit),
            ).fetchall()
            totals = db.execute(
                "SELECT COUNT(*) AS tracked, SUM(injected_count > 0) AS ever_useful FROM knowledge_usage"
            ).fetchone()
        return {
            "schema": SCHEMA,
            "read_only": True,
            "automatic_retirement": False,
            "measurement_basis": "legacy_cosine_passes; not returned evidence or current demotion",
            "relevance_floor": xkb_relevance.min_similarity(),
            "min_considered": min_considered,
            "tracked_records": totals["tracked"] or 0,
            "ever_useful": totals["ever_useful"] or 0,
            "count": len(rows),
            "records": [dict(row) for row in rows],
        }

    def record_job_event(self, body: dict[str, Any]) -> dict[str, Any]:
        """Persist an observed worker event; never executes the worker."""
        job_id = text(body.get("job_id") or body.get("jobId"))
        stage = text(body.get("stage"))
        worker = text(body.get("worker") or body.get("script"))
        status = text(body.get("status"))
        allowed = {"queued", "running", "succeeded", "failed", "cancelled"}
        if not job_id or not stage or not worker or status not in allowed:
            raise ValueError("job_id, stage, worker and valid status are required")
        timestamp = now()
        finished = timestamp if status in {"succeeded", "failed", "cancelled"} else None
        with self.lock, self.connect() as db:
            existing = db.execute("SELECT * FROM jobs WHERE job_id=?", (job_id,)).fetchone()
            if existing and existing["status"] in {"succeeded", "failed", "cancelled"} and status != existing["status"]:
                raise ValueError("terminal job status cannot transition")
            if existing:
                db.execute(
                    "UPDATE jobs SET stage=?,worker=?,status=?,started_at=COALESCE(started_at,?),finished_at=COALESCE(?,finished_at),input_ref=?,output_ref=?,error=?,retryable=?,metadata_json=?,updated_at=? WHERE job_id=?",
                    (stage, worker, status, body.get("started_at") or timestamp, finished, text(body.get("input_ref")), text(body.get("output_ref")), text(body.get("error")), int(bool(body.get("retryable"))), json.dumps(redact(body.get("metadata", {})), ensure_ascii=False), timestamp, job_id),
                )
            else:
                db.execute(
                    "INSERT INTO jobs(job_id,stage,worker,status,started_at,finished_at,input_ref,output_ref,error,retryable,metadata_json,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (job_id, stage, worker, status, body.get("started_at") or timestamp, finished, text(body.get("input_ref")), text(body.get("output_ref")), text(body.get("error")), int(bool(body.get("retryable"))), json.dumps(redact(body.get("metadata", {})), ensure_ascii=False), timestamp, timestamp),
                )
        return {"schema": SCHEMA, "job_id": job_id, "status": status, "stored": True, "observed_only": True}

    def list_jobs(self, stage: str = "", status: str = "", limit: int = 50) -> dict[str, Any]:
        clauses, values = [], []
        if stage:
            clauses.append("stage=?"); values.append(stage)
        if status:
            clauses.append("status=?"); values.append(status)
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        limit = bounded_int(limit, name="limit", default=50, minimum=1, maximum=200)
        with self.lock, self.connect() as db:
            rows = db.execute(f"SELECT * FROM jobs{where} ORDER BY updated_at DESC LIMIT ?", (*values, limit)).fetchall()
        jobs = []
        for row in rows:
            jobs.append({
                "job_id": row["job_id"], "stage": row["stage"], "worker": row["worker"], "status": row["status"],
                "started_at": row["started_at"], "finished_at": row["finished_at"], "input_ref": row["input_ref"],
                "output_ref": row["output_ref"], "error": row["error"], "retryable": bool(row["retryable"]),
                "metadata": json.loads(row["metadata_json"] or "{}"), "updated_at": row["updated_at"],
            })
        return {"schema": SCHEMA, "read_only": True, "control_plane": "observed_status_only", "count": len(jobs), "jobs": jobs}

    def open_session(self, body: dict[str, Any]) -> dict[str, Any]:
        source = text(body.get("source")) or "unknown"
        agent_id = text(body.get("agent_id") or body.get("agentId")) or source
        namespace_value = body.get("namespace")
        if namespace_value is not None and not text(namespace_value):
            raise ValueError("namespace is required")
        namespace = text(namespace_value) or "private"
        session_key = text(body.get("session_key") or body.get("sessionKey") or body.get("session_id") or body.get("sessionId"))
        if not session_key:
            raise ValueError("session_key is required")
        with self.lock, self.connect() as db:
            row = db.execute("SELECT * FROM sessions WHERE namespace=? AND source=? AND session_key=?", (namespace, source, session_key)).fetchone()
            if row:
                db.execute("UPDATE sessions SET updated_at=? WHERE session_id=?", (now(), row["session_id"]))
                return {"schema": SCHEMA, "session_id": row["session_id"], "resumed": True, "source": source, "namespace": namespace}
            session_id = text(body.get("stable_session_id")) or f"sess:{uuid.uuid4()}"
            timestamp = now()
            db.execute(
                "INSERT INTO sessions(session_id,session_key,source,agent_id,namespace,workspace_path,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)",
                (session_id, session_key, source, agent_id, namespace, text(body.get("workspace_path") or body.get("workspacePath")) or None, timestamp, timestamp),
            )
            return {"schema": SCHEMA, "session_id": session_id, "resumed": False, "source": source, "namespace": namespace}

    def start_turn(self, body: dict[str, Any]) -> dict[str, Any]:
        session_id = text(body.get("session_id") or body.get("sessionId"))
        query = text(body.get("query"))
        if not session_id or not query:
            raise ValueError("session_id and query are required")
        turn_id = text(body.get("turn_id") or body.get("turnId")) or f"turn:{uuid.uuid4()}"
        with self.lock, self.connect() as db:
            session = db.execute("SELECT namespace FROM sessions WHERE session_id=?", (session_id,)).fetchone()
            if session is None:
                raise ValueError("unknown session_id")
            session_namespace = session["namespace"]
            namespace_value = body.get("namespace")
            if namespace_value is not None and not text(namespace_value):
                raise ValueError("namespace is required")
            if namespace_value is not None and text(namespace_value) != session_namespace:
                raise ValueError("namespace conflicts with session")
            existing = db.execute("SELECT * FROM turns WHERE turn_id=?", (turn_id,)).fetchone()
            if existing:
                # A turn id is an idempotency key, not a writable lookup key:
                # retries must describe the exact same turn.  In particular,
                # do not return another session's retrieval packet merely
                # because an attacker (or a stale client) guessed the id.
                if existing["session_id"] != session_id:
                    raise ValueError("turn_id conflicts with existing session_id")
                if existing["query"] != query:
                    raise ValueError("turn_id conflicts with existing query")
                if session_namespace != (db.execute("SELECT namespace FROM sessions WHERE session_id=?", (existing["session_id"],)).fetchone() or {"namespace": None})["namespace"]:
                    raise ValueError("turn_id conflicts with existing session")
                retrieval = json.loads(existing["retrieval_json"] or "{}")
                if "conversation" in body and retrieval.get("conversation_fingerprint") != conversation_fingerprint(conversation_messages(body["conversation"])):
                    raise ValueError("turn_id conflicts with existing conversation context")
                return {"schema": SCHEMA, "turn_id": turn_id, "session_id": existing["session_id"], "episode_id": existing["episode_id"], "resumed": True, "source_memory_ids": [item.get("id") for item in retrieval.get("records", [])], "retrieval": retrieval}
            retrieval_limit = bounded_int(body.get("retrieval_limit"), name="retrieval_limit", default=10, minimum=1, maximum=50)
            if "conversation" in body:
                recent = conversation_messages(body["conversation"])
            else:
                previous = db.execute(
                    "SELECT query,answer FROM turns WHERE session_id=? AND status='succeeded' "
                    "ORDER BY started_at DESC, rowid DESC LIMIT 2", (session_id,)).fetchall()
                recent = conversation_messages([
                    {"role": role, "content": row[column]} for row in reversed(previous)
                    for role, column in (("user", "query"), ("assistant", "answer")) if row[column]])
            retrieval = self.knowledge_recall(query, retrieval_limit, session_namespace, conversation=recent)
            db.execute(
                "INSERT INTO turns(turn_id,session_id,query,status,retrieval_json,started_at) VALUES(?,?,?,?,?,?)",
                (turn_id, session_id, query, "started", json.dumps(retrieval, ensure_ascii=False), now()),
            )
            return {"schema": SCHEMA, "turn_id": turn_id, "session_id": session_id, "episode_id": f"episode:{turn_id}", "resumed": False, "source_memory_ids": [item.get("id") for item in retrieval.get("records", [])], "retrieval": retrieval}

    def complete_turn(self, turn_id: str, body: dict[str, Any]) -> dict[str, Any]:
        query = text(body.get("query"))
        answer = text(body.get("answer"))
        status = text(body.get("status")) or "succeeded"
        if status not in {"succeeded", "cancelled"}:
            raise ValueError("status must be succeeded or cancelled")
        if status == "cancelled":
            with self.lock, self.connect() as db:
                db.execute("DELETE FROM turns WHERE turn_id=? AND status='started'", (turn_id,))
            return {"schema": SCHEMA, "turn_id": turn_id, "status": "cancelled", "stored": False}
        if not query or not answer:
            raise ValueError("query and answer are required unless status is cancelled")
        stable = {"turn_id": turn_id, "query": query, "answer": answer, "status": status, "content": redact(body.get("content"))}
        trace_id = f"trace:{digest(stable)[:24]}"
        payload = redact(body)
        with self.lock, self.connect() as db:
            existing = db.execute("SELECT * FROM turns WHERE turn_id=?", (turn_id,)).fetchone()
            if not existing:
                raise ValueError("unknown turn_id")
            if body.get("session_id") is not None and text(body.get("session_id")) != existing["session_id"]:
                raise ValueError("session_id conflicts with turn")
            if body.get("namespace") is not None:
                session = db.execute("SELECT namespace FROM sessions WHERE session_id=?", (existing["session_id"],)).fetchone()
                if not text(body.get("namespace")):
                    raise ValueError("namespace is required")
                if session is None or text(body.get("namespace")) != session["namespace"]:
                    raise ValueError("namespace conflicts with session")
            if existing["trace_id"]:
                if existing["trace_id"] != trace_id:
                    raise ValueError("turn completion conflicts with existing payload")
                return {"schema": SCHEMA, "turn_id": turn_id, "trace_id": trace_id, "status": existing["status"], "stored": False, "deduplicated": True}
            db.execute(
                "UPDATE turns SET episode_id=?,query=?,answer=?,status=?,trace_id=?,payload_json=?,completed_at=? WHERE turn_id=?",
                (text(body.get("episode_id") or body.get("episodeId")) or f"episode:{turn_id}", query, answer, status, trace_id, json.dumps(payload, ensure_ascii=False), now(), turn_id),
            )
            retrieval = json.loads(existing["retrieval_json"] or "{}")
            # A turn used to also become a "candidate" here — the answer
            # truncated to 2,000 characters, confidence hard-coded to zero —
            # plus a job to analyse it. Promotion required the same claim in
            # two distinct episodes, and an earlier fix keyed candidates on the
            # answer text so repetition could accumulate. Free-form answers are
            # never byte-identical, so the condition still could not occur: 154
            # candidates in four weeks, none ever eligible, and the analysis
            # queue ran 142 jobs deep before anyone noticed nothing consumed it.
            #
            # Conversations do become knowledge, through distill_memory_to_wiki:
            # an LLM extracts durable claims from the day's notes, and claims do
            # recur even when the sentences around them do not. That path put
            # 913 entries into the wiki. This one stored transcripts.
            #
            # Turns are still captured in full — they are recalled semantically
            # and are the shared conversation memory across machines. What is
            # gone is the pretence that a transcript was a candidate fact.
        return {"schema": SCHEMA, "turn_id": turn_id, "trace_id": trace_id, "status": status, "stored": True, "deduplicated": False, "retrieval": retrieval}

    def recall(self, query: str, limit: int = 5, namespace: str = "private") -> dict[str, Any]:
        if not isinstance(query, str) or not query.strip():
            raise ValueError("query is required")
        limit = bounded_int(limit, name="limit", default=5, minimum=1, maximum=50)
        if not isinstance(namespace, str) or not namespace.strip():
            raise ValueError("namespace is required")
        # 原本這裡是 query.split()，而上面的關鍵字退路用的是 CJK 正則。
        # 同一個「查詢詞」兩種切法：中文查詢在這裡只會切出一個詞，於是
        # 每一筆命中它的軌跡都剛好拿到折扣上限 0.65——那個「區間」其實
        # 只有一個值，難怪怎麼調錨點都不對。
        terms = query_terms(query)
        # harness 產生的「對話」要排除在視窗之外，而不是排除在評分之外。
        #
        # 這個查詢只看最近 500 筆。2026-09-24 的資料庫裡有 137 筆 turn 的 query 其實
        # 是背景任務通知（在 hook 端擋掉之前累積的），也就是**視窗的 27% 被佔掉**，
        # 真正的對話歷史被擠出去。在 Python 端過濾救不了這件事：它們已經佔掉名額了。
        #
        # 刻意不刪那些列：它們的 answer 是真實的回答內容，而這個專案的價值有一部分
        # 是 provenance。代價要講清楚——那些答案從此不會被對話召回撈到。真的有知識
        # 價值的內容，它的家是卡片或 wiki，不是一個問句是系統通知的對話軌跡。
        #
        # 標記清單取自 conversation_state_parser，不在這裡再寫一份 SQL 字面值。
        blanks = " " + chr(10) + chr(13) + chr(9)
        marker_clause = " ".join(
            "AND lower(ltrim(turns.query, ?)) NOT LIKE ?"
            for _ in HARNESS_MARKERS)
        marker_params = [value for marker in HARNESS_MARKERS
                         for value in (blanks, marker + "%")]
        with self.lock, self.connect() as db:
            rows = db.execute(
                "SELECT turns.* FROM turns JOIN sessions ON sessions.session_id=turns.session_id "
                "WHERE turns.status!='cancelled' AND turns.answer IS NOT NULL AND sessions.namespace=? "
                f"{marker_clause} "
                "ORDER BY turns.completed_at DESC LIMIT 500",
                (namespace, *marker_params),
            ).fetchall()
        # 命中詞數不是相關度。
        #
        # 原本直接把「命中幾個詞」當分數，所以只中一個詞的對話拿 1 分，
        # 而餘弦相似度最高只有 1——對話因此永遠排在卡片前面。匯入 45 筆
        # OpenClaw 對話之後，語意查詢的前八名全被對話佔滿，真正相關的
        # 知識卡一張都擠不進來。這是 xkb_score 當初診斷的同一個病：
        # 拿公分跟英吋比大小。
        #
        # 這裡不走 xkb_score，因為這條管線的卡片保留的是原始餘弦，
        # 只換算其中一邊會讓對話反過來永遠墊底。改成一開始就產生
        # 同一個尺度的數字：命中比例乘上折扣——關鍵字命中是比語意
        # 相似弱的證據，詞出現過不代表在講同一件事。
        # 全部命中約 0.65，落在中等相關的卡片附近；只中一半約 0.33，
        # 低於卡片的相關度門檻，等於自動被濾掉。
        scored = []
        for row in rows:
            haystack = f"{row['query']} {row['answer']}".lower()
            matched = sum(1 for term in terms if term in haystack)
            if matched:
                scored.append((round(KEYWORD_EVIDENCE_DISCOUNT * matched / len(terms), 4), row))
        scored.sort(key=lambda item: (item[0], item[1]["completed_at"] or ""), reverse=True)
        memories = [{"trace_id": row["trace_id"], "session_id": row["session_id"], "episode_id": row["episode_id"], "query": row["query"], "answer": row["answer"], "score": score, "memory_layer": "L1", "visibility": namespace} for score, row in scored[: max(1, min(limit, 50))]]
        context = "\n\n".join(f"[歷史證據] Q: {item['query']}\nA: {item['answer']}" for item in memories)
        return {"schema": SCHEMA, "query": query, "memories": memories, "context": context, "count": len(memories)}

    def artifact(self, trace_id: str, namespace: str = "private") -> dict[str, Any] | None:
        """一次對話的完整內容。要過 namespace，跟其他讀取端點一樣。

        turn 的 namespace 在它的 session 上，所以這裡要 join。讀不到與不存在
        回同一個答案——否則 trace_id 就成了一個可以探測「這筆存不存在」的
        工具，而 trace_id 是 complete_turn 與 recall 主動交給客戶端的。
        """
        with self.lock, self.connect() as db:
            row = db.execute(
                """SELECT t.payload_json, t.retrieval_json, t.trace_id, s.namespace
                     FROM turns t
                     LEFT JOIN sessions s ON s.session_id = t.session_id
                    WHERE t.trace_id=?""",
                (trace_id,),
            ).fetchone()
        if not row:
            return None
        # _allowed 在 KnowledgeCatalog 上，Store 透過 self.catalog 用它——
        # 跟這個檔案裡其他地方（例如 _acl_policy）同一個模式。
        if not self.catalog._allowed({"namespace": row["namespace"]}, namespace):
            return None
        payload = json.loads(row["payload_json"])
        payload.update({
            "schema": TRACE_SCHEMA,
            "trace_id": trace_id,
            "memory_layer": "L1",
            "status": "observed",
            "retrieval": json.loads(row["retrieval_json"] or "{}"),
        })
        return payload

    @staticmethod
    def _skip_reason(query: str) -> str:
        """Return why retrieval should be skipped outright, or "" to proceed.

        Only pure acknowledgements and greetings are skipped — "好", "謝謝",
        "早安". Those cannot match knowledge, so searching costs an embedding
        call and injects context for nothing.

        The parser's other suppress rule (short messages without a known domain
        keyword) is deliberately *not* honoured here: it measures length in
        characters, and eight Chinese characters is a complete question.
        "碳盤查的計算方式" is exactly eight, so trusting that rule would silence
        precisely the domain questions this knowledge base exists to answer.
        Relevance is decided after retrieval, by similarity, not by length.

        The list itself comes from the parser rather than being copied here.
        The copy that used to live in this file had drifted: it never gained
        the compound acknowledgement pattern, so "ok 收到" retrieved ten
        records into a conversation that had asked nothing.
        """
        return _noise_kind(query)

    def knowledge_recall(self, query: str, limit: int = 10, namespace: str = "private", *, options=None, conversation=None) -> dict[str, Any]:
        started = time.monotonic()
        if not isinstance(query, str) or not query.strip():
            raise ValueError("query is required")
        limit = bounded_int(limit, name="limit", default=10, minimum=1, maximum=50)
        if not isinstance(namespace, str) or not namespace.strip():
            raise ValueError("namespace is required")
        opts = recall_options(options)
        recent = conversation_messages(conversation)
        context_receipt = {"conversation_fingerprint": conversation_fingerprint(recent),
                           "conversation_messages": len(recent)}
        queries = retrieval_queries(query, recent)
        # 每次召回從乾淨的統計開始。少了這一步，某些路徑（例如語意後端不在時
        # 提早回傳的那條）會沿用同一條執行緒上一個請求的數字，回應裡的
        # 「為什麼結果這麼少」就會是別人的答案。
        #
        # catalog 是可替換的（測試會換成自己的），所以不要求它一定有這個方法。
        reset = getattr(self.catalog, "_reset_request_stats", None)
        if callable(reset):
            reset()
        skipped = self._skip_reason(query)
        if skipped:
            packet = {
                "schema": SCHEMA, "query": query, "namespace": namespace, **context_receipt,
                "request_namespace": namespace, "acl_policy": self.catalog._acl_policy(namespace),
                "records": [], "count": 0, "unfiltered_count": 0,
                "filtered_counts": filter_stats(), "context": "",
                "retrieval_mode": "skipped", "skip_reason": skipped, "options": opts,
                "backends": {k: {"status": "not_attempted", "attempted": False, "used": False} for k in ("cards", "wiki", "conversation")},
                "semantic_retrieval_attempted": False,
                "semantic_backend": {"status": "not_attempted"},
                "dropped_as_irrelevant": 0, "warnings": [],
            }
            packet["quality"] = recall_quality(packet)
            packet["delivery"] = xkb_delivery.select([], {"status": "no_records"}, recent, query=query)
            return packet
        # Keep current-utterance candidates even when old dialogue is longer or
        # contains stronger keywords. Context resolves references and the final
        # sentence gives a new topic its own pre-judge candidate budget.
        search_options = {"options": opts} if options is not None else {}
        # Prepare the task independently while bounded source reads run. The
        # planner never sees retrieved text; selection cannot change this task.
        from concurrent.futures import ThreadPoolExecutor
        try:
            can_prepare = os.getenv('XKB_JEV_DECIDE', '1') != '0' and xkb_jev.available()
        except Exception as err:
            # A broken provider config must not erase locally available evidence.
            can_prepare = False
            xkb_failures.note('delivery preparation configuration', ValueError(type(err).__name__))
        with ThreadPoolExecutor(max_workers=len(queries) + int(can_prepare)) as pool:
            pending_task = pool.submit(xkb_delivery.prepare, query, recent) if can_prepare else None
            branches = list(pool.map(lambda text: self.catalog.search(text, limit, namespace, **search_options), queries))
            retrieved = time.monotonic()
            delivery_options = {'task': pending_task.result()} if pending_task else {}
        knowledge = {**branches[0], "records": [r for branch in branches for r in branch["records"]]}
        context_knowledge = branches[1] if len(branches) > 1 else None
        prepared = time.monotonic()
        conversation_state = {"backend": "conversation_keyword", "attempted": opts["conversations"],
                              "used": False, "status": "disabled"}
        conversation = {"memories": []}
        if opts["conversations"]:
            try:
                conversation = self.recall(query, limit, namespace)
                for search_query in queries[1:]:
                    conversation = {"memories": conversation["memories"] + self.recall(search_query, limit, namespace)["memories"]}
                conversation_state.update(used=bool(conversation["memories"]),
                                          status="used" if conversation["memories"] else "empty")
            except sqlite3.Error as err:
                conversation_state["status"] = "error"
                xkb_failures.note("conversation recall", err)
        backends = {**knowledge.get("backends", {}), "conversation": conversation_state}
        records = knowledge["records"] + [
            {**item, "record_type": "conversation_trace", "source_type": "conversation",
             "score_scale": "conversation", "namespace": namespace}
            for item in conversation["memories"]
        ]
        # 三層各有自己的尺度：卡片是 RRF 或餘弦、wiki 是真餘弦、對話是關鍵字
        # 比例（上限 0.65）。照原始 score 排等於拿公分比英吋——xkb_score 就是
        # 為這件事寫的，它的說明開頭就在講這個，連 conversation 的錨點與權重
        # 都定義好了，只是這個合併點從來沒呼叫它。
        #
        # 我在 xkb_relevance 補了五輪都補不完，就是因為補錯了層：不管把驗不出
        # 相似度的項目壓到哪一段，都會撞上另一層的尺度。壓高一點壓過 wiki 的
        # 真餘弦，壓低一點掉到對話軌跡之下——後者會讓索引壞掉長得像
        # 「知識庫裡沒東西」，而這個模組的說明明講那是不能發生的事。
        # 降權要標在這裡——xkb_score.rank 是唯一的跨層比較點，也是唯一一處
        # 三條腿的記錄同時存在。標在任何一條腿裡都會被其他腿繞過（見
        # tag_demoted 的說明）。
        for item in records:
            item["evidence_key"] = identity_key(item)
        candidates = list(records)
        merged = time.monotonic()
        # jev 判斷接在這裡，排序之前：排序只需要處理真的相關的那些。
        # 一個旗標就能退回餘弦——XKB_JEV_DECIDE=0。
        judge_note: dict[str, Any] = {"status": "off"}
        if os.getenv("XKB_JEV_DECIDE", "1") != "0":
            try:
                records, judge_note = judge_relevance(contextual_query(query, recent, for_judge=True), records)
            except Exception as err:  # noqa: BLE001
                xkb_failures.note("jev judge", err)
                judge_note = {"status": "error"}
        judged = time.monotonic()
        relevant_ids = {identity_key(item) for item in records
                        if type(item.get("judge")) in (int, float)
                        and item["judge"] >= judge_note.get("floor", JUDGE_FLOOR)} if judge_quality(judge_note)["has_verdicts"] else set()
        tag_demoted(records, lambda: self.demoted_ids(namespace=namespace), relevant_ids=relevant_ids)
        records = xkb_delivery.rank(records, judge_note)
        # Quotas apply after identity fusion, before the global response limit.
        counts = {"cards": 0, "wiki": 0}
        selected = []
        for item in records:
            kind = item.get("record_type")
            layer = "wiki" if kind in {"wiki_topic", "memory_note"} else "cards"
            if kind == "conversation_trace":
                selected.append(item)
            elif opts[layer] and counts[layer] < opts["max_" + layer]:
                selected.append(item)
                counts[layer] += 1
        records = selected
        try:
            self.record_recall_usage(namespace, candidates, records[:limit], judge_note)
        except Exception as err:
            xkb_failures.note("recall usage", err)
        # Conversation recall filters by namespace in SQL, so nothing is
        # dropped after the fact and its layer count is structurally zero.
        filtered_counts = dict(knowledge.get("filtered_counts", filter_stats()))
        # Copy by_layer too: a shallow dict() would alias the catalog's cached
        # stats and let this response mutate the next one.
        filtered_counts["by_layer"] = {"conversation": 0, **filtered_counts.get("by_layer", {})}
        # 警告要在清掉內部欄位之前算。index_unreadable 是內部事實：
        # 它必須活過 catalog.search 才能變成警告，但不在
        # KNOWLEDGE_SCHEMA 上，不該送到用戶端——兩輪前 _unverified
        # 就是這樣漏出去的。
        warnings = [w for w in (_recall_warnings({**knowledge, "backends": backends}, filtered_counts)
                                + [_unverified_warning(records)]) if w]
        quality_warning = judge_quality(judge_note)["warning"]
        if quality_warning:
            warnings.append(quality_warning)
        for extra in branches[1:]:
            warnings.extend("conversation context retrieval: " + warning for warning in
                            _recall_warnings(extra, extra.get("filtered_counts", filter_stats())) if warning)
        for item in records:
            item.pop("index_unreadable", None)
        context = render_context(records[:limit])
        packet = {
            "schema": SCHEMA, **context_receipt,
            "query": query,
            "namespace": namespace,
            "request_namespace": namespace,
            "acl_policy": knowledge.get("acl_policy", {"request_namespace": namespace}),
            "records": records[:limit],
            "count": min(len(records), limit),
            "unfiltered_count": len(records),
            "filtered_counts": filtered_counts,
            "dropped_as_irrelevant": knowledge.get("dropped_as_irrelevant", 0),
            # 被 jev 砍掉幾筆、門檻多少、有沒有真的跑成，都要出現在回應裡。
            # 這個專案吃過最大的虧就是「什麼都沒有」跟「壞了」長得一樣。
            "judge": judge_note,
            "context": context,
            "retrieval_mode": knowledge.get("retrieval_mode", "keyword_fallback"),
            "semantic_retrieval_attempted": knowledge.get("semantic_backend", {}).get("attempted", False),
            "semantic_backend": knowledge.get("semantic_backend", {"status": "unknown"}),
            "backends": backends, "options": opts,
            "context_retrieval": ({"query": queries[1], **{key: context_knowledge.get(key) for key in
                                   ("retrieval_mode", "semantic_backend", "backends", "filtered_counts")}
                                   }
                                  if context_knowledge is not None else None),
            "retrieval_branches": [{"query": text, **{key: branch.get(key) for key in
                                    ("retrieval_mode", "semantic_backend", "backends", "filtered_counts")}}
                                   for text, branch in zip(queries, branches)],
            "warnings": warnings,
        }
        delivery_started = time.monotonic()
        packet["delivery"] = xkb_delivery.select(packet["records"], judge_note, recent, query=query, **delivery_options)
        finished = time.monotonic()
        packet["timing_ms"] = {"retrieval": round((retrieved-started)*1000),
                               "task_wait": round((prepared-retrieved)*1000),
                               "conversation_merge": round((merged-prepared)*1000),
                               "relevance": round((judged-merged)*1000),
                               "fusion_and_usage": round((delivery_started-judged)*1000),
                               "delivery": round((finished-delivery_started)*1000),
                               "total": round((finished-started)*1000)}
        packet["quality"] = recall_quality(packet)
        return packet


class Handler(BaseHTTPRequestHandler):
    server_version = "XKBKnowledgeService/0.2"

    def log_message(self, fmt: str, *args: Any) -> None:
        if os.getenv("XKB_SERVICE_LOG") == "1":
            super().log_message(fmt, *args)

    @property
    def store(self) -> Store:
        return self.server.store  # type: ignore[attr-defined]

    def send_json(self, status: int, payload: dict[str, Any]) -> None:
        encoded = json.dumps(payload, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def body(self) -> dict[str, Any]:
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError as exc:
            raise ValueError("invalid Content-Length") from exc
        if length > 2_000_000:
            raise ValueError("request body too large")
        try:
            payload = json.loads(self.rfile.read(length) or b"{}")
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise ValueError("malformed JSON body") from exc
        if not isinstance(payload, dict):
            raise ValueError("JSON body must be an object")
        return payload

    @property
    def auth(self) -> AuthPolicy:
        # An unconfigured server falls back to the documented default (no
        # tokens means anonymous), so the handler is usable standalone.
        return getattr(self.server, "auth", None) or ANONYMOUS_AUTH

    def principal(self) -> dict[str, Any]:
        """Resolve who is calling, from headers only."""
        header = self.headers.get("Authorization") or ""
        token = header[len("Bearer "):].strip() if header.startswith("Bearer ") else None
        return self.auth.principal(token or self.headers.get("X-API-Key"))

    def namespace(self, principal: dict[str, Any], requested: str | None) -> str:
        return AuthPolicy.namespace_for(principal, requested)

    def authorize(self, principal: dict[str, Any], scope: str) -> None:
        AuthPolicy.require(principal, scope)

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        try:
            if parsed.path == "/v1/health":
                self.send_json(200, {"schema": SCHEMA, "ok": True, "service": "knowledge", "data_plane": "sources/evidence/cards/wiki/conversations", "control_plane": "observed_status_only", "write_mode": "l1_capture_only", "auth_required": self.server.auth.enabled})  # type: ignore[attr-defined]
                return
            principal = self.principal()
            self.authorize(principal, READ_SCOPE)
            if parsed.path.startswith("/v1/sources/"):
                params = parse_qs(parsed.query)
                item = self.store.catalog.source(unquote(parsed.path.rsplit("/", 1)[-1]), self.namespace(principal, (params.get("namespace") or [""])[0]))
                self.send_json(200 if item else 404, item or {"error": "not found"})
                return
            if parsed.path.startswith("/v1/evidence/"):
                params = parse_qs(parsed.query)
                item = self.store.catalog.evidence(unquote(parsed.path.rsplit("/", 1)[-1]), self.namespace(principal, (params.get("namespace") or [""])[0]))
                self.send_json(200 if item else 404, item or {"error": "not found"})
                return
            if parsed.path.startswith("/v1/cards/") and parsed.path.endswith("/relations"):
                card_id = unquote(parsed.path.split("/")[3])
                item = self.store.catalog.relations(
                    card_id, self.namespace(principal, (params.get("namespace") or [""])[0])
                )
                self.send_json(200, item)
                return
            if parsed.path.startswith("/v1/cards/"):
                params = parse_qs(parsed.query)
                item = self.store.catalog.card(unquote(parsed.path.rsplit("/", 1)[-1]), self.namespace(principal, (params.get("namespace") or [""])[0]))
                self.send_json(200 if item else 404, item or {"error": "not found"})
                return
            if parsed.path.startswith("/v1/wiki/topics/"):
                params = parse_qs(parsed.query)
                item = self.store.catalog.wiki_topic(unquote(parsed.path.rsplit("/", 1)[-1]), self.namespace(principal, (params.get("namespace") or [""])[0]))
                self.send_json(200 if item else 404, item or {"error": "not found"})
                return
            if parsed.path == "/v1/ingest/status":
                self.send_json(200, self.store.catalog.pipeline_status())
                return
            if parsed.path == "/v1/pipeline/snapshot":
                params = parse_qs(parsed.query)
                raw_days = (params.get("days") or ["7"])[0]
                try:
                    days = max(1, min(int(raw_days), 365))
                except ValueError:
                    raise ValueError("days must be an integer")
                self.send_json(200, self.store.catalog.pipeline_snapshot(days))
                return
            if parsed.path == "/v1/pipeline/jobs":
                params = parse_qs(parsed.query)
                self.send_json(200, self.store.list_jobs(
                    stage=(params.get("stage") or [""])[0],
                    status=(params.get("status") or [""])[0],
                    # query string 一定是字串，而 bounded_int 明確拒絕字串——
                    # 所以這個端點原本每一次呼叫都回 400，連沒帶參數的也是。
                    limit=bounded_int(_as_int(params.get("limit"), 50), name="limit",
                                      default=50, minimum=1, maximum=200),
                ))
                return
            if parsed.path == "/v1/knowledge/cold":
                params = parse_qs(parsed.query)
                self.send_json(200, self.store.cold_knowledge(
                    min_considered=int((params.get("min_considered") or ["5"])[0]),
                    limit=int((params.get("limit") or ["100"])[0]),
                ))
                return
            if parsed.path.startswith("/v1/artifacts/"):
                item = self.store.artifact(
                    unquote(parsed.path.rsplit("/", 1)[-1]),
                    self.namespace(principal, (params.get("namespace") or [""])[0]),
                )
                self.send_json(200 if item else 404, item or {"error": "not found"})
                return
            self.send_json(404, {"error": "not found"})
        except Unauthorized as exc:
            self.send_json(exc.status, {"error": str(exc)})
        except ValueError as exc:
            self.send_json(400, {"error": str(exc)})
        except Exception as exc:
            self.send_json(500, {"error": str(exc)})

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        try:
            body = self.body()
            principal = self.principal()
            # A pinned namespace replaces whatever the body claimed, so the
            # handlers below cannot be talked into another tenant's data.
            if principal.get("namespace"):
                body["namespace"] = self.namespace(principal, text(body.get("namespace")))
            if parsed.path == "/v1/sessions/open":
                self.authorize(principal, WRITE_SCOPE)
                self.send_json(200, self.store.open_session(body)); return
            if parsed.path == "/v1/turns/start":
                self.authorize(principal, WRITE_SCOPE)
                self.send_json(200, self.store.start_turn(body)); return
            if parsed.path.startswith("/v1/turns/") and parsed.path.endswith("/complete"):
                self.authorize(principal, WRITE_SCOPE)
                turn_id = unquote(parsed.path.split("/")[3])
                self.send_json(200, self.store.complete_turn(turn_id, body)); return
            if parsed.path == "/v1/recall":
                self.authorize(principal, READ_SCOPE)
                self.send_json(200, self.store.knowledge_recall(text(body.get("query")), bounded_int(body.get("limit"), name="limit", default=10, minimum=1, maximum=50), self.namespace(principal, text(body.get("namespace"))), options=body.get("options"), conversation=body.get("conversation"))); return
            if parsed.path == "/v1/context":
                self.authorize(principal, READ_SCOPE)
                self.send_json(200, self.store.knowledge_recall(text(body.get("query")), bounded_int(body.get("limit"), name="limit", default=10, minimum=1, maximum=50), self.namespace(principal, text(body.get("namespace"))), options=body.get("options"), conversation=body.get("conversation"))); return
            if parsed.path == "/v1/ingest/status":
                self.authorize(principal, READ_SCOPE)
                self.send_json(200, self.store.catalog.pipeline_status()); return
            if parsed.path == "/v1/pipeline/jobs/events":
                self.authorize(principal, WRITE_SCOPE)
                self.send_json(200, self.store.record_job_event(body)); return
            self.send_json(404, {"error": "not found"})
        except Unauthorized as exc:
            self.send_json(exc.status, {"error": str(exc)})
        except ValueError as exc:
            self.send_json(400, {"error": str(exc)})
        except Exception as exc:
            self.send_json(500, {"error": str(exc)})


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the local-first XKB Knowledge Service")
    parser.add_argument("--host", default=os.getenv("XKB_SERVICE_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.getenv("XKB_SERVICE_PORT", "18972")))
    parser.add_argument("--db", type=Path, default=Path(os.getenv("XKB_SERVICE_DB", str(Path.home() / ".xkb-runtime" / "knowledge.sqlite"))))
    parser.add_argument("--auth", type=Path, default=Path(os.getenv("XKB_SERVICE_AUTH", str(Path.home() / ".xkb-runtime" / "auth.json"))))
    args = parser.parse_args()
    if args.host not in {"127.0.0.1", "localhost", "::1"} and os.getenv("XKB_ALLOW_NON_LOOPBACK") != "1":
        parser.error("refusing non-loopback bind; set XKB_ALLOW_NON_LOOPBACK=1 only with explicit network controls")
    try:
        auth = AuthPolicy.load(args.auth)
    except ValueError as exc:
        parser.error(str(exc))
    if not auth.enabled and args.host not in {"127.0.0.1", "localhost", "::1"}:
        parser.error("refusing to serve a non-loopback bind without tokens: configure --auth first")
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    server.store = Store(args.db)  # type: ignore[attr-defined]
    server.auth = auth  # type: ignore[attr-defined]
    print(json.dumps({"ok": True, "schema": SCHEMA, "host": args.host, "port": args.port, "db": str(args.db),
                      "auth": "token" if auth.enabled else "anonymous", "auth_file": str(args.auth)}, ensure_ascii=False), flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


import xkb_usage  # noqa: E402  — 量測誰在跑，見 scripts/xkb_usage.py

if __name__ == "__main__":
    xkb_usage.record(__file__)
    raise SystemExit(main())
