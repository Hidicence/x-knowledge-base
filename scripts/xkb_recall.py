#!/usr/bin/env python3
"""One recall contract for CLI and MCP: the Knowledge Service's recall core.

An explicit XKB_MEMORY_SERVICE_URL selects HTTP. Otherwise run the same Store
locally. A failed remote request never falls back to a different local library.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
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


def recall_options(options: dict | None = None) -> dict:
    """One validated request contract for local, HTTP and CLI retrieval options."""
    defaults = {"cards": True, "wiki": True, "conversations": True, "semantic": True,
                "max_cards": 50, "max_wiki": 50}
    if options is None:
        return defaults
    if not isinstance(options, dict) or set(options) - set(defaults):
        raise ValueError("invalid recall options")
    values = {**defaults, **options}
    for key in ("cards", "wiki", "conversations", "semantic"):
        if type(values[key]) is not bool:
            raise ValueError(f"{key} must be a boolean")
    for key in ("max_cards", "max_wiki"):
        if type(values[key]) is not int or not 0 <= values[key] <= 50:
            raise ValueError(f"{key} must be an integer between 0 and 50")
    return values


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


def run_configured(message: str, limit: int = 10, *, env_file=None, script=None, options=None) -> dict:
    """Resolve runtime settings before importing path/provider modules in a worker."""
    if not isinstance(message, str) or not message.strip():
        raise ValueError("message must be a non-empty string")
    if type(limit) is not int or not 1 <= limit <= 50:
        raise ValueError("limit must be an integer between 1 and 50")
    entry = Path(script) if script else Path(__file__).resolve()
    if not entry.is_file():
        raise RuntimeError(f"recall entry point not found at {entry}")
    env = runtime_env(env_file)
    if env_file:
        env["XKB_ENV_FILE"] = str(env_file)
    env.update({"PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"})
    command = [sys.executable, str(entry), "--limit", str(limit)]
    if options is not None:
        command += ["--options-json", json.dumps(recall_options(options))]
    command += ["--", message]
    try:
        result = subprocess.run(
            command,
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=50, env=env)
    except subprocess.TimeoutExpired:
        raise RuntimeError("knowledge recall timed out after 50s") from None
    try:
        packet = json.loads(result.stdout)
    except ValueError:
        raise RuntimeError("knowledge recall returned an invalid response") from None
    if result.returncode != 0:
        reason = packet.get("error", "knowledge recall failed") if isinstance(packet, dict) else "knowledge recall failed"
        raise RuntimeError(reason)
    return validate_packet(packet)


def compatibility_aliases(packet: dict) -> dict:
    """Preserve the old presentation keys without another routing decision."""
    skipped = packet["retrieval_mode"] == "skipped"
    return {**packet, "status": "ok", "error": "",
            "results": packet["records"], "formatted_text": packet["context"],
            "trigger_class": "suppress" if skipped else "knowledge",
            "state": "suppress" if skipped else "recall",
            "delivery_mode": "none" if skipped else "inline"}


def recall(query: str, limit: int = 10, *, options=None) -> dict:
    if not isinstance(query, str) or not query.strip():
        raise ValueError("message must be a non-empty string")
    if type(limit) is not int or not 1 <= limit <= 50:
        raise ValueError("limit must be an integer between 1 and 50")
    selected = recall_options(options)
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
        body = {"query": query, "limit": limit, "namespace": namespace}
        if options is not None:
            body["options"] = selected
        request = Request(url + "/v1/recall", method="POST", headers=headers,
                          data=json.dumps(body).encode("utf-8"))
        try:
            with build_opener(NoRedirect).open(request, timeout=40) as response:
                packet = validate_packet(json.load(response))
                if options is not None and packet.get("options") != selected:
                    raise RuntimeError("XKB service did not acknowledge recall options; update the service")
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
        packet = validate_packet(Store(xkb_paths.SERVICE_DB).knowledge_recall(query, limit, namespace, options=selected))
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
    parser.add_argument("--options-json", help="Validated retrieval options object")
    args = parser.parse_args()
    try:
        os.environ.update(runtime_env())
        packet = recall(args.message, args.limit, options=json.loads(args.options_json) if args.options_json else None)
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
