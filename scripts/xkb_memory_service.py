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

try:
    from continuity_recall import card_similarities
except ImportError:  # pragma: no cover - relevance filtering is best-effort
    card_similarities = None

try:
    from conversation_state_parser import SUPPRESS_EXACT as ACK_ONLY
except ImportError:  # pragma: no cover - intent gating is best-effort
    ACK_ONLY = {"ok", "好", "收到", "謝謝", "thanks", "好的", "嗯", "哦", "喔"}

# Greetings only. Deliberately narrower than the parser's suppress list, which
# also drops things like "^計算" — that would silence real questions.
GREETING_PATTERNS = (r"^哈+$", r"^哈哈", r"^早安", r"^晚安", r"^午安",
                     r"^你好$", r"^hi$", r"^hello$", r"^嗨$")

SCHEMA = "xkb-knowledge-service.v1"
TRACE_SCHEMA = "xkb-l1-trace.v1"
KNOWLEDGE_SCHEMA = "xkb-knowledge-record.v1"


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


def _vector_key(record_id: str) -> str:
    """Map a backend slug onto the key the vector index uses."""
    if not record_id or record_id.startswith(("http://", "https://", "semantic:")):
        return ""
    return record_id if record_id.endswith(".md") else f"{record_id}.md"


def safe_read(path: Path, limit: int = 200_000) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")[:limit]
    except OSError:
        return ""


# Knowledge layers that ACL filtering can be attributed to. "semantic" is not a
# layer but a channel: the backend returns hits without saying which layer they
# came from, so drops there cannot be attributed any more precisely than that.
ACL_LAYERS = ("card", "wiki", "semantic", "conversation")

READ_SCOPE = "memory:read"
WRITE_SCOPE = "memory:write"
DEFAULT_SCOPES = (READ_SCOPE, WRITE_SCOPE)

# Minimum true cosine similarity for a semantic hit to be worth injecting.
#
# The backend's own score is an RRF *rank* score: it says "this ranked first",
# not "this is relevant", so rank 1 sits near 0.88 even when the knowledge base
# holds nothing on the subject. Measured on this corpus, "今天天氣如何" scored
# 0.863 while "碳盤查的計算方式" scored 0.862 — the score cannot tell them apart,
# so filtering has to use the real query/document cosine instead.
MIN_SEMANTIC_RELEVANCE = float(os.getenv("XKB_SERVICE_MIN_RELEVANCE", "0.55"))


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
        self._last_search_stats: dict[str, Any] = filter_stats()
        self._last_irrelevant = 0

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
        except Exception:
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
                "retrieval": "xbrain_hybrid",
            })
        # The semantic backend does not report which knowledge layer a hit came
        # from, so ACL drops here can only be attributed to the channel.
        self._last_search_stats = filter_stats(semantic=filtered)
        records, self._last_irrelevant = self._drop_irrelevant(query, records)
        return records

    def _drop_irrelevant(self, query: str, records: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], int]:
        """Replace rank scores with true similarity and drop what is not relevant.

        Injecting ten results into every turn regardless of relevance is what
        makes an unrelated question cost as much as a real one. Rank order
        cannot decide this — only the actual query/document cosine can.

        When similarity cannot be computed (no index, no embedding provider)
        the records pass through unchanged: losing recall because the index is
        missing would be a worse failure than showing a few weak results.
        """
        if not records or card_similarities is None:
            return records, 0
        # The backend returns slugs ("01-topic/12345"); the vector index is
        # keyed by the card's relative path, which carries the .md suffix.
        keys = {str(item.get("id") or ""): _vector_key(str(item.get("id") or "")) for item in records}
        try:
            scores = card_similarities(query, [key for key in keys.values() if key])
        except Exception:
            return records, 0
        if not scores:
            return records, 0
        kept = []
        for item in records:
            similarity = scores.get(keys.get(str(item.get("id") or "")) or "")
            if similarity is None:
                kept.append(item)
                continue
            if similarity < MIN_SEMANTIC_RELEVANCE:
                continue
            # Report the measured relevance, keeping the rank score it replaces
            # so a caller can still see what the backend ordered on.
            item["rank_score"] = item.get("score")
            item["score"] = round(similarity, 4)
            kept.append(item)
        kept.sort(key=lambda item: item.get("score", 0.0), reverse=True)
        return kept, len(records) - len(kept)

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
        semantic_backend["used"] = bool(semantic_records)
        if semantic_records:
            semantic_backend["status"] = "used"
        elif semantic_backend["attempted"]:
            semantic_backend["status"] = "empty_or_failed"
        if semantic_records:
            records = semantic_records
            retrieval_mode = "xbrain_hybrid"
            filtered_counts = dict(self._last_search_stats)
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
                score = sum(blob.count(term) for term in terms)
                if score:
                    hits.append((score, {"schema": KNOWLEDGE_SCHEMA, "record_type": "knowledge_card", "id": str(item.get("id") or Path(str(item.get("path", ""))).stem), "title": item.get("title", ""), "summary": item.get("summary", ""), "source_url": item.get("source_url", ""), "source_type": item.get("source_type", "unknown"), "memory_layer": "external_knowledge", "visibility": metadata.get("sensitivity", metadata.get("visibility", "private")), "namespace": metadata.get("namespace", "private"), "score": score, "retrieval": "keyword"}))
            for path in sorted(self.wiki_topics_dir.glob("*.md")):
                metadata = self._frontmatter(path)
                if not self._allowed(metadata, namespace):
                    filtered_wiki += 1
                    continue
                content = safe_read(path, 100_000)
                blob = f"{path.stem} {content}".lower()
                score = sum(blob.count(term) for term in terms)
                if score:
                    hits.append((score, {"schema": KNOWLEDGE_SCHEMA, "record_type": "wiki_topic", "id": path.stem, "title": path.stem, "summary": content[:500], "source_url": "", "source_type": "wiki", "memory_layer": "knowledge_product", "visibility": metadata.get("sensitivity", metadata.get("visibility", "private")), "namespace": metadata.get("namespace", "private"), "score": score, "retrieval": "keyword"}))
            hits.sort(key=lambda pair: pair[0], reverse=True)
            records = [item for _, item in hits[: max(1, min(limit, 50))]]
            retrieval_mode = "keyword_fallback"
            self._last_search_stats = filter_stats(card=filtered_cards, wiki=filtered_wiki)
            filtered_counts = dict(self._last_search_stats)
        context = "\n\n".join(f"[{item['record_type']}] {item.get('title', item['id'])}\n{item.get('summary', '')}" for item in records)
        return {
            "schema": SCHEMA, "query": query, "namespace": namespace,
            "request_namespace": namespace, "acl_policy": self._acl_policy(namespace),
            "records": records, "count": len(records), "context": context,
            "retrieval_mode": retrieval_mode, "semantic_backend": semantic_backend,
            "filtered_counts": filtered_counts,
            "dropped_as_irrelevant": self._last_irrelevant,
            "warnings": [],
        }

    def relations(self, card_id: str) -> dict[str, Any]:
        card = self.card(card_id)
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

    def recover_stale_jobs(self, older_than_seconds: int = 3600, dry_run: bool = True, confirm: bool = False) -> dict[str, Any]:
        """Requeue only this service's interrupted L1 worker jobs.

        Recovery is deliberately opt-in: inspection is the default, the TTL
        has a hard one-minute floor, and mutation requires ``confirm=true``.
        The recovery decision is appended to job metadata for auditability;
        this method never starts a worker or touches another worker's jobs.
        """
        try:
            ttl = int(older_than_seconds)
        except (TypeError, ValueError):
            raise ValueError("older_than_seconds must be an integer")
        if ttl < 60:
            raise ValueError("older_than_seconds must be at least 60")
        if ttl > 30 * 24 * 3600:
            raise ValueError("older_than_seconds must be at most 2592000")
        cutoff = datetime.now(timezone.utc).timestamp() - ttl
        worker_name = "xkb_l1_to_candidate"

        def stale(row: sqlite3.Row) -> bool:
            raw = row["updated_at"] or row["started_at"]
            try:
                return datetime.fromisoformat(raw.replace("Z", "+00:00")).timestamp() <= cutoff
            except (AttributeError, TypeError, ValueError):
                return False

        audit_at = now()
        with self.lock, self.connect() as db:
            rows = db.execute(
                "SELECT * FROM jobs WHERE stage='distill' AND worker=? AND status='running' ORDER BY updated_at",
                (worker_name,),
            ).fetchall()
            selected = [row for row in rows if stale(row)]
            ids = [row["job_id"] for row in selected]
            recovered = []
            if not dry_run:
                if not confirm:
                    raise ValueError("confirm=true is required when dry_run=false")
                for row in selected:
                    metadata = json.loads(row["metadata_json"] or "{}")
                    metadata["stale_recovery"] = {
                        "recovered_at": audit_at,
                        "older_than_seconds": ttl,
                        "from_status": "running",
                        "to_status": "queued",
                        "reason": "worker_interruption_recovery",
                    }
                    db.execute(
                        "UPDATE jobs SET status='queued',finished_at=NULL,retryable=1,error=NULL,metadata_json=?,updated_at=? WHERE job_id=? AND stage='distill' AND worker=? AND status='running'",
                        (json.dumps(metadata, ensure_ascii=False), audit_at, row["job_id"], worker_name),
                    )
                    recovered.append(row["job_id"])
        return {
            "schema": SCHEMA,
            "worker": worker_name,
            "stage": "distill",
            "dry_run": dry_run,
            "confirmed": bool(confirm and not dry_run),
            "older_than_seconds": ttl,
            "selected": ids,
            "recovered": recovered,
            "count": len(recovered),
            "audit": {"at": audit_at, "reason": "worker_interruption_recovery"} if recovered else None,
            "worker_started": False,
        }

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
            # Phase A+B boundary: persist an L1-backed distillation request, but
            # do not call an LLM or promote anything into stable memory here.
            candidate_id = f"candidate:{digest({'trace_id': trace_id})[:24]}"
            candidate_key = f"conversation:{trace_id}"
            timestamp = now()
            db.execute(
                "INSERT OR IGNORE INTO candidates(candidate_id,candidate_key,candidate_value,source_trace_ids_json,episode_ids_json,confidence,status,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)",
                (candidate_id, candidate_key, answer[:2000], json.dumps([trace_id]), json.dumps([text(body.get("episode_id") or body.get("episodeId")) or f"episode:{turn_id}"]), 0.0, "pending", timestamp, timestamp),
            )
            job_id = f"distill:{trace_id}"
            db.execute(
                "INSERT OR IGNORE INTO jobs(job_id,stage,worker,status,input_ref,output_ref,retryable,metadata_json,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
                (job_id, "distill", "xkb_l1_to_candidate", "queued", trace_id, candidate_id, 1, json.dumps({"source_trace_ids": [trace_id], "mode": "candidate_only"}, ensure_ascii=False), timestamp, timestamp),
            )
        return {"schema": SCHEMA, "turn_id": turn_id, "trace_id": trace_id, "status": status, "stored": True, "deduplicated": False, "retrieval": retrieval, "candidate_id": candidate_id, "distillation_job_id": job_id}

    def run_l1_to_candidate(self, job_ids: list[str] | None = None, limit: int = 50, dry_run: bool = False) -> dict[str, Any]:
        """Safely trigger the local rule-based distillation worker.

        This is intentionally the only service control path for this worker:
        it accepts existing service-owned jobs, records ``running`` before
        execution, and never performs promotion. The worker is imported lazily
        to avoid a module cycle (its CLI imports Store from this module).
        """
        worker_name = "xkb_l1_to_candidate"
        stage = "distill"
        with self.lock, self.connect() as db:
            if job_ids:
                placeholders = ",".join("?" for _ in job_ids)
                rows = db.execute(f"SELECT job_id FROM jobs WHERE stage=? AND worker=? AND status IN ('queued','pending') AND job_id IN ({placeholders})", (stage, worker_name, *job_ids)).fetchall()
            else:
                rows = db.execute("SELECT job_id FROM jobs WHERE stage=? AND worker=? AND status IN ('queued','pending') ORDER BY created_at LIMIT ?", (stage, worker_name, max(1, min(limit, 200)))).fetchall()
            selected = [row["job_id"] for row in rows]
            if not dry_run:
                timestamp = now()
                for selected_id in selected:
                    db.execute("UPDATE jobs SET status='running',started_at=COALESCE(started_at,?),updated_at=? WHERE job_id=? AND status IN ('queued','pending')", (timestamp, timestamp, selected_id))
        if dry_run:
            return {"schema": SCHEMA, "worker": worker_name, "stage": stage, "dry_run": True, "selected": selected, "results": []}
        try:
            import xkb_l1_to_candidate as worker
        except ImportError as exc:
            return {"schema": SCHEMA, "worker": worker_name, "stage": stage, "selected": selected, "results": [worker_error(self, job_id, exc) for job_id in selected]}
        results = []
        for selected_id in selected:
            try:
                results.append(worker.process_job(self, selected_id))
            except Exception as exc:
                results.append(worker.fail_job(self, selected_id, exc))
        return {"schema": SCHEMA, "worker": worker_name, "stage": stage, "selected": selected, "processed": len(results), "results": results, "promotion_performed": False}

    def query_candidates(self, status: str = "", limit: int = 50) -> dict[str, Any]:
        clauses, values = [], []
        if status:
            clauses.append("status=?"); values.append(status)
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        limit = max(1, min(limit, 200))
        with self.lock, self.connect() as db:
            rows = db.execute(f"SELECT * FROM candidates{where} ORDER BY updated_at DESC LIMIT ?", (*values, limit)).fetchall()
        candidates = [{
            "candidate_id": row["candidate_id"],
            "candidate_key": row["candidate_key"],
            "candidate_value": row["candidate_value"],
            "source_trace_ids": json.loads(row["source_trace_ids_json"] or "[]"),
            "episode_ids": json.loads(row["episode_ids_json"] or "[]"),
            "confidence": row["confidence"],
            "status": row["status"],
            "reject_reasons": json.loads(row["reject_reasons_json"] or "[]"),
            "analysis": json.loads(row["analysis_json"] or "{}"),
            "expires_at": row["expires_at"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        } for row in rows]
        return {"schema": SCHEMA, "read_only": True, "count": len(candidates), "candidates": candidates}

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
        scored = []
        for row in rows:
            haystack = f"{row['query']} {row['answer']}".lower()
            score = sum(1 for term in terms if term in haystack)
            if score:
                scored.append((score, row))
        scored.sort(key=lambda item: (item[0], item[1]["completed_at"] or ""), reverse=True)
        memories = [{"trace_id": row["trace_id"], "session_id": row["session_id"], "episode_id": row["episode_id"], "query": row["query"], "answer": row["answer"], "score": score, "memory_layer": "L1", "visibility": namespace} for score, row in scored[: max(1, min(limit, 50))]]
        context = "\n\n".join(f"[歷史證據] Q: {item['query']}\nA: {item['answer']}" for item in memories)
        return {"schema": SCHEMA, "query": query, "memories": memories, "context": context, "count": len(memories)}

    def artifact(self, trace_id: str) -> dict[str, Any] | None:
        with self.lock, self.connect() as db:
            row = db.execute("SELECT payload_json, retrieval_json, trace_id FROM turns WHERE trace_id=?", (trace_id,)).fetchone()
        if not row:
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
        """
        stripped = query.strip().lower()
        if len(stripped) <= 4 and stripped in ACK_ONLY:
            return "acknowledgement"
        for pattern in GREETING_PATTERNS:
            if re.search(pattern, query, re.IGNORECASE):
                return "greeting"
        return ""

    def knowledge_recall(self, query: str, limit: int = 10, namespace: str = "private") -> dict[str, Any]:
        if not isinstance(query, str) or not query.strip():
            raise ValueError("query is required")
        limit = bounded_int(limit, name="limit", default=10, minimum=1, maximum=50)
        if not isinstance(namespace, str) or not namespace.strip():
            raise ValueError("namespace is required")
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
            {**item, "record_type": "conversation_trace", "source_type": "conversation"}
            for item in conversation["memories"]
        ]
        records.sort(key=lambda item: item.get("score", 0), reverse=True)
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
                item = self.store.catalog.relations(card_id)
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
                    limit=bounded_int((params.get("limit") or ["50"])[0], name="limit", default=50, minimum=1, maximum=200),
                ))
                return
            if parsed.path.startswith("/v1/artifacts/"):
                item = self.store.artifact(unquote(parsed.path.rsplit("/", 1)[-1]))
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
            if parsed.path == "/v1/pipeline/jobs/run":
                self.authorize(principal, WRITE_SCOPE)
                requested_worker = text(body.get("worker")) or "xkb_l1_to_candidate"
                if requested_worker != "xkb_l1_to_candidate":
                    raise ValueError("only xkb_l1_to_candidate is triggerable")
                raw_ids = body.get("job_ids") or body.get("jobIds")
                if raw_ids is not None and (not isinstance(raw_ids, list) or not all(isinstance(item, str) and item for item in raw_ids)):
                    raise ValueError("job_ids must be a list of non-empty strings")
                self.send_json(200, self.store.run_l1_to_candidate(raw_ids, bounded_int(body.get("limit"), name="limit", default=50, minimum=1, maximum=200), bool(body.get("dry_run")))); return
            if parsed.path == "/v1/pipeline/jobs/recover-stale":
                self.authorize(principal, WRITE_SCOPE)
                self.send_json(200, self.store.recover_stale_jobs(
                    body.get("older_than_seconds", 3600),
                    bool(body.get("dry_run", True)),
                    bool(body.get("confirm", False)),
                )); return
            if parsed.path == "/v1/candidates/query":
                self.authorize(principal, READ_SCOPE)
                params = parse_qs(parsed.query)
                self.send_json(200, self.store.query_candidates(
                    status=(params.get("status") or [""])[0],
                    limit=int((params.get("limit") or ["50"])[0]),
                )); return
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
