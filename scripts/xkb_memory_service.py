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
import uuid
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

import xkb_failures
import xkb_relevance
import xkb_score

# The list of what is not worth searching for lives with the parser, so the
# router and this service cannot disagree about it. They used to: the copy
# here was a subset that never gained the compound acknowledgement pattern,
# and "ok 收到" retrieved ten records into a conversation that asked nothing.
from conversation_state_parser import noise_kind as _noise_kind

SCHEMA = "xkb-knowledge-service.v1"
TRACE_SCHEMA = "xkb-l1-trace.v1"
KNOWLEDGE_SCHEMA = "xkb-knowledge-record.v1"


def _normalise(value: str) -> str:
    """Collapse a statement to what it says, for grouping repeated observations.

    Wording drifts between sessions; the claim is what has to match, so
    whitespace and case are removed before hashing.
    """
    return re.sub(r"\s+", " ", (value or "").strip().lower())


def _recall_warnings(knowledge: dict[str, Any], filtered_counts: dict[str, Any]) -> list[str]:
    """Say why a result set is thin, distinguishing the three reasons.

    "The backend is broken", "the backend worked but nothing was relevant" and
    "results existed but ACL removed them" produce the same empty list, and
    conflating them is how XKB failures previously stayed invisible for weeks.
    """
    warnings = []
    dropped = knowledge.get("dropped_as_irrelevant", 0)
    if knowledge.get("retrieval_mode") != "xbrain_hybrid":
        if dropped:
            warnings.append(f"semantic results found but {dropped} dropped below the relevance floor")
        else:
            warnings.append("semantic_backend_unavailable_or_empty; keyword fallback used")
    elif dropped:
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
        if xbrain_query is None or not query.strip():
            return []
        try:
            hits = xbrain_query(query, limit=limit, no_expand=True, semantic=True)
        except Exception as err:
            # 每一台機器都是透過這個服務問 XKB。這裡回空的，對方收到的是
            # 「我們沒有這方面的知識」——跟一切正常時的回答一模一樣。
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
                "source_url": hit.get("source_url", ""),
                "source_type": hit.get("type") or "xkb",
                "memory_layer": "external_knowledge",
                "visibility": hit.get("sensitivity", hit.get("visibility", "private")),
                "namespace": hit.get("namespace", "private"),
                "score": hit.get("score", 0.0),
                # gbrain 的混合 RRF。source_type 這裡是資料種類（會回給
                # 用戶），跟分數的尺度是兩件事。
                "score_scale": "card",
                "retrieval": "xbrain_hybrid",
            })
        # The semantic backend does not report which knowledge layer a hit came
        # from, so ACL drops here can only be attributed to the channel.
        self._stats = filter_stats(semantic=filtered)
        records, self._irrelevant = self._drop_irrelevant(query, records)
        return records

    def _wiki_search(self, query: str, limit: int, namespace: str = "private") -> list[dict[str, Any]]:
        """Search distilled wiki topics and daily memory.

        These already carry true cosine similarity, so they bypass the rank
        score filter applied to the card backend — but not the ACL: a wiki
        page belonging to another namespace must stay invisible here exactly
        as it would through the card path.
        """
        try:
            from continuity_recall import recall_semantic
        except ImportError:
            return []
        try:
            hits = recall_semantic(query, top_k=max(2, limit // 2))
        except Exception as err:
            xkb_failures.note("service wiki search", err)
            return []
        if not hits:            # None means unavailable, [] means nothing relevant
            return []
        allowed = []
        for hit in hits:
            topic = Path(hit.source_file).stem
            path = self.wiki_topics_dir / f"{topic}.md"
            metadata = self._frontmatter(path) if path.exists() else {}
            if self._allowed(metadata, namespace):
                allowed.append(hit)
            else:
                stats = self._stats
                stats["by_layer"]["wiki"] = stats["by_layer"].get("wiki", 0) + 1
                stats["total"] = stats.get("total", 0) + 1
        hits = allowed
        return [{
            "schema": KNOWLEDGE_SCHEMA,
            "record_type": "wiki_topic" if hit.source_type == "wiki_semantic" else "memory_note",
            "id": hit.source_file,
            "title": hit.section or Path(hit.source_file).stem,
            "summary": hit.excerpt,
            "source_url": hit.url,
            "source_type": "wiki",
            "memory_layer": "knowledge_product",
            "visibility": "private",
            "namespace": "private",
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
            # Every candidate the backend surfaced, with the similarity it
            # actually achieved. This is the only honest "was it any use"
            # signal XKB has: a card retrieved many times that never once
            # clears the floor is not earning its place.
            floor = xkb_relevance.min_similarity()
            try:
                self.usage_sink([
                    (record_id, scores.get(key), (scores.get(key) or 0) >= floor)
                    for record_id, key in keys.items() if key and scores.get(key) is not None
                ])
            except Exception:
                pass  # usage accounting must never break recall
        return kept, dropped

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

    def search(self, query: str, limit: int = 10, namespace: str = "private") -> dict[str, Any]:
        if not isinstance(query, str) or not query.strip():
            raise ValueError("query is required")
        limit = bounded_int(limit, name="limit", default=10, minimum=1, maximum=50)
        if not isinstance(namespace, str) or not namespace.strip():
            raise ValueError("namespace is required")
        semantic_backend = {
            "name": "xbrain_hybrid",
            "available": xbrain_query is not None,
            "attempted": bool(query.strip() and xbrain_query is not None),
            "used": False,
            "status": "available" if xbrain_query is not None else "unavailable",
        }
        semantic_records = self._semantic_search(query, limit, namespace)
        # The card backend and the wiki index are two different stores. Cards
        # live in gbrain; wiki topics and daily memory live in XKB's own
        # semantic index and gbrain has never seen them — so searching only
        # gbrain made the entire wiki invisible to this service, including a
        # 700K arsenal page holding 320 named patterns. Recall returned card
        # fragments and never the distilled conclusions written above them.
        semantic_records += self._wiki_search(query, limit, namespace)
        semantic_backend["used"] = bool(semantic_records)
        if semantic_records:
            semantic_backend["status"] = "used"
        elif semantic_backend["attempted"]:
            semantic_backend["status"] = "empty_or_failed"
        if semantic_records:
            records = semantic_records
            retrieval_mode = "xbrain_hybrid"
            filtered_counts = dict(self._stats)
        else:
            terms = [term.lower() for term in re.findall(r"[\w\u4e00-\u9fff]+", query) if len(term) > 1]
            hits: list[tuple[int, dict[str, Any]]] = []
            filtered_cards = filtered_wiki = 0
            for item in self._index():
                metadata = self._item_metadata(item)
                if not self._allowed(metadata, namespace):
                    filtered_cards += 1
                    continue
                blob = json.dumps(item, ensure_ascii=False).lower()
                score = _keyword_unit_score(blob, terms)
                if score:
                    hits.append((score, {"schema": KNOWLEDGE_SCHEMA, "record_type": "knowledge_card", "id": str(item.get("id") or Path(str(item.get("path", ""))).stem), "title": item.get("title", ""), "summary": item.get("summary", ""), "source_url": item.get("source_url", ""), "source_type": item.get("source_type", "unknown"), "memory_layer": "external_knowledge", "score_scale": "card", "visibility": metadata.get("sensitivity", metadata.get("visibility", "private")), "namespace": metadata.get("namespace", "private"), "score": score, "retrieval": "keyword"}))
            for path in sorted(self.wiki_topics_dir.glob("*.md")):
                metadata = self._frontmatter(path)
                if not self._allowed(metadata, namespace):
                    filtered_wiki += 1
                    continue
                content = safe_read(path, 100_000)
                blob = f"{path.stem} {content}".lower()
                score = _keyword_unit_score(blob, terms)
                if score:
                    hits.append((score, {"schema": KNOWLEDGE_SCHEMA, "record_type": "wiki_topic", "id": path.stem, "title": path.stem, "summary": content[:500], "source_url": "", "source_type": "wiki", "memory_layer": "knowledge_product", "score_scale": "wiki_semantic", "visibility": metadata.get("sensitivity", metadata.get("visibility", "private")), "namespace": metadata.get("namespace", "private"), "score": score, "retrieval": "keyword"}))
            hits.sort(key=lambda pair: pair[0], reverse=True)
            records = [item for _, item in hits[: max(1, min(limit, 50))]]
            retrieval_mode = "keyword_fallback"
            self._stats = filter_stats(card=filtered_cards, wiki=filtered_wiki)
            filtered_counts = dict(self._stats)
        context = "\n\n".join(f"[{item['record_type']}] {item.get('title', item['id'])}\n{item.get('summary', '')}" for item in records)
        return {
            "schema": SCHEMA, "query": query, "namespace": namespace,
            "request_namespace": namespace, "acl_policy": self._acl_policy(namespace),
            "records": records, "count": len(records), "context": context,
            "retrieval_mode": retrieval_mode, "semantic_backend": semantic_backend,
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
                """
            )
            columns = {row["name"] for row in db.execute("PRAGMA table_info(turns)").fetchall()}
            if "retrieval_json" not in columns:
                db.execute("ALTER TABLE turns ADD COLUMN retrieval_json TEXT")
            candidate_columns = {row["name"] for row in db.execute("PRAGMA table_info(candidates)").fetchall()}
            if "analysis_json" not in candidate_columns:
                db.execute("ALTER TABLE candidates ADD COLUMN analysis_json TEXT NOT NULL DEFAULT '{}'")

    def connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path, timeout=10, check_same_thread=False)
        db.row_factory = sqlite3.Row
        return db

    def record_usage(self, observations: list[tuple[str, float, bool]]) -> None:
        """Accumulate how each record actually performed when retrieved.

        XKB has no task-success signal, so Memmy's reward model cannot be
        copied honestly — a card about camera moves is not "successful" or
        "failed". What can be measured is whether a record, once surfaced,
        was ever relevant enough to be worth injecting. A record retrieved
        many times that has never cleared the floor is dead weight, and that
        is a real observation rather than an invented score.
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
                return {"schema": SCHEMA, "turn_id": turn_id, "session_id": existing["session_id"], "episode_id": existing["episode_id"], "resumed": True, "source_memory_ids": [item.get("id") for item in retrieval.get("records", [])], "retrieval": retrieval}
            retrieval_limit = bounded_int(body.get("retrieval_limit"), name="retrieval_limit", default=10, minimum=1, maximum=50)
            retrieval = self.knowledge_recall(query, retrieval_limit, session_namespace)
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
        terms = [term.lower() for term in query.split() if len(term) > 1]
        with self.lock, self.connect() as db:
            rows = db.execute(
                "SELECT turns.* FROM turns JOIN sessions ON sessions.session_id=turns.session_id "
                "WHERE turns.status!='cancelled' AND turns.answer IS NOT NULL AND sessions.namespace=? "
                "ORDER BY turns.completed_at DESC LIMIT 500",
                (namespace,),
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

    def knowledge_recall(self, query: str, limit: int = 10, namespace: str = "private") -> dict[str, Any]:
        if not isinstance(query, str) or not query.strip():
            raise ValueError("query is required")
        limit = bounded_int(limit, name="limit", default=10, minimum=1, maximum=50)
        if not isinstance(namespace, str) or not namespace.strip():
            raise ValueError("namespace is required")
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
            return {
                "schema": SCHEMA, "query": query, "namespace": namespace,
                "request_namespace": namespace, "acl_policy": self.catalog._acl_policy(namespace),
                "records": [], "count": 0, "unfiltered_count": 0,
                "filtered_counts": filter_stats(), "context": "",
                "retrieval_mode": "skipped", "skip_reason": skipped,
                "semantic_retrieval_attempted": False,
                "semantic_backend": {"status": "not_attempted"},
                "dropped_as_irrelevant": 0, "warnings": [],
            }
        knowledge = self.catalog.search(query, limit, namespace)
        conversation = self.recall(query, limit, namespace)
        records = knowledge["records"] + [
            {**item, "record_type": "conversation_trace", "source_type": "conversation",
             "score_scale": "conversation"}
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
        records = xkb_score.rank(records)
        # Conversation recall filters by namespace in SQL, so nothing is
        # dropped after the fact and its layer count is structurally zero.
        filtered_counts = dict(knowledge.get("filtered_counts", filter_stats()))
        # Copy by_layer too: a shallow dict() would alias the catalog's cached
        # stats and let this response mutate the next one.
        filtered_counts["by_layer"] = {"conversation": 0, **filtered_counts.get("by_layer", {})}
        context = "\n\n".join(
            f"[{item.get('record_type', 'knowledge')}] {item.get('title') or item.get('query') or item.get('id')}\n{item.get('summary') or item.get('answer') or ''}"
            for item in records[: max(1, min(limit, 50))]
        )
        return {
            "schema": SCHEMA,
            "query": query,
            "namespace": namespace,
            "request_namespace": namespace,
            "acl_policy": knowledge.get("acl_policy", {"request_namespace": namespace}),
            "records": records[:limit],
            "count": min(len(records), limit),
            "unfiltered_count": len(records),
            "filtered_counts": filtered_counts,
            "dropped_as_irrelevant": knowledge.get("dropped_as_irrelevant", 0),
            "context": context,
            "retrieval_mode": knowledge.get("retrieval_mode", "keyword_fallback"),
            "semantic_retrieval_attempted": knowledge.get("semantic_backend", {}).get("attempted", False),
            "semantic_backend": knowledge.get("semantic_backend", {"status": "unknown"}),
            "warnings": _recall_warnings(knowledge, filtered_counts),
        }


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
                self.send_json(200, self.store.knowledge_recall(text(body.get("query")), bounded_int(body.get("limit"), name="limit", default=10, minimum=1, maximum=50), self.namespace(principal, text(body.get("namespace"))))); return
            if parsed.path == "/v1/context":
                self.authorize(principal, READ_SCOPE)
                self.send_json(200, self.store.knowledge_recall(text(body.get("query")), bounded_int(body.get("limit"), name="limit", default=10, minimum=1, maximum=50), self.namespace(principal, text(body.get("namespace"))))); return
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


if __name__ == "__main__":
    raise SystemExit(main())
