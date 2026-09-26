#!/usr/bin/env python3
"""One recall contract for CLI and MCP: the Knowledge Service's recall core.

An explicit XKB_MEMORY_SERVICE_URL selects HTTP. Otherwise run the same Store
locally. A failed remote request never falls back to a different local library.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, build_opener, HTTPRedirectHandler

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
from runtime_config import runtime_env


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # Do not forward a service token to another endpoint.
        return None


def validate_packet(packet: object) -> dict:
    if not isinstance(packet, dict) or packet.get("schema") != "xkb-knowledge-service.v1":
        raise ValueError("response is not an XKB Knowledge Service packet")
    if (not isinstance(packet.get("records"), list)
            or not all(isinstance(r, dict) for r in packet["records"])
            or not isinstance(packet.get("context"), str)
            or type(packet.get("count")) is not int
            or packet["count"] != len(packet["records"])
            or not isinstance(packet.get("retrieval_mode"), str)):
        raise ValueError("incomplete XKB recall packet")
    return packet


def recall(query: str, limit: int = 10) -> dict:
    if not isinstance(query, str) or not query.strip():
        raise ValueError("message must be a non-empty string")
    if type(limit) is not int or not 1 <= limit <= 50:
        raise ValueError("limit must be an integer between 1 and 50")
    namespace = os.getenv("XKB_NAMESPACE", "private")
    if not namespace.strip():
        raise ValueError("XKB_NAMESPACE must not be empty")
    url = os.getenv("XKB_MEMORY_SERVICE_URL", "").rstrip("/")
    if url:
        parsed = urlsplit(url)
        if (parsed.scheme not in {"http", "https"} or not parsed.hostname
                or parsed.username or parsed.password or parsed.query or parsed.fragment):
            raise ValueError("XKB_MEMORY_SERVICE_URL must be an HTTP(S) base URL without credentials, query or fragment")
        headers = {"Content-Type": "application/json"}
        token = os.getenv("XKB_SERVICE_TOKEN", "")
        if token:
            headers["Authorization"] = "Bearer " + token
        request = Request(url + "/v1/recall", method="POST", headers=headers,
                          data=json.dumps({"query": query, "limit": limit,
                                           "namespace": namespace}).encode("utf-8"))
        try:
            with build_opener(NoRedirect).open(request, timeout=40) as response:
                packet = validate_packet(json.load(response))
        except HTTPError as exc:
            raise RuntimeError(f"XKB service returned HTTP {exc.code}; check endpoint, token and namespace") from None
        except (URLError, TimeoutError, OSError):
            raise RuntimeError("XKB service unreachable or timed out; no local fallback was attempted") from None
        connection = {"transport": "http", "endpoint": url,
                      "namespace": packet.get("namespace", namespace)}
    else:
        # Imported only after main has loaded the runtime environment. Import-
        # time paths and provider settings therefore use the same configuration.
        import xkb_paths
        from xkb_memory_service import Store
        packet = validate_packet(Store(xkb_paths.SERVICE_DB).knowledge_recall(query, limit, namespace))
        connection = {"transport": "local", "core": "Store.knowledge_recall",
                      "namespace": namespace, "data_dir": str(xkb_paths.DATA_DIR),
                      "index_file": str(xkb_paths.INDEX_FILE),
                      "wiki_dir": str(xkb_paths.WIKI_DIR),
                      "database": str(xkb_paths.SERVICE_DB)}
    return {**packet, "connection": connection}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("message")
    parser.add_argument("--limit", type=int, default=10)
    args = parser.parse_args()
    try:
        os.environ.update(runtime_env())
        packet = recall(args.message, args.limit)
    except Exception as exc:
        print(json.dumps({"status": "failed", "error": str(exc)}, ensure_ascii=True))
        return 1
    print(json.dumps(packet, ensure_ascii=True))
    return 0


if __name__ == "__main__":
    # Load settings before the telemetry module resolves data paths, too.
    try:
        os.environ.update(runtime_env())
    except Exception as exc:
        print(json.dumps({"status": "failed", "error": str(exc)}, ensure_ascii=True))
        raise SystemExit(1)
    import xkb_usage
    xkb_usage.record(__file__)
    raise SystemExit(main())
