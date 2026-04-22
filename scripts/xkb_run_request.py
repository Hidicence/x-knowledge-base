#!/usr/bin/env python3
"""
XKB Request Runner
==================
Consumes a request artifact and produces a result artifact.

Delegates all inference to _llm.call() — model routing (MiniMax / OpenClaw / Gemini)
is handled there. No adapter selection needed here.

Usage:
    python3 xkb_run_request.py --request runtime/memory-distill/2026-04-19/requests/chunk-0001.json
    python3 xkb_run_request.py --request ... --model MiniMax-M2.7
    python3 xkb_run_request.py --request ... --timeout 120
"""

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL_DIR / "scripts"))

from _llm import call as llm_call


def load_config() -> dict:
    cfg = SKILL_DIR / "config" / "llm.json"
    try:
        return json.loads(cfg.read_text(encoding="utf-8"))
    except Exception:
        return {"model": "MiniMax-M2.7"}


def main():
    parser = argparse.ArgumentParser(description="XKB Request Runner")
    parser.add_argument("--request", required=True, help="Path to request artifact JSON")
    parser.add_argument("--model", help="Model override (default: from request or config)")
    parser.add_argument("--timeout", type=int, default=120, help="Timeout in seconds (default: 120)")
    args = parser.parse_args()

    request_path = Path(args.request)
    if not request_path.exists():
        print(f"ERROR: request file not found: {request_path}", file=sys.stderr)
        sys.exit(1)

    request = json.loads(request_path.read_text(encoding="utf-8"))

    runtime_dir = request.get("meta", {}).get("runtime_dir")
    if not runtime_dir:
        print("ERROR: request.meta.runtime_dir is required", file=sys.stderr)
        sys.exit(1)
    runtime_dir = Path(runtime_dir)
    results_dir = runtime_dir / "results"
    results_dir.mkdir(parents=True, exist_ok=True)

    chunk_index = request.get("chunk_index", 0)
    out_path = results_dir / f"chunk-{chunk_index:04d}.json"

    model = args.model or request.get("meta", {}).get("model") or load_config().get("model", "MiniMax-M2.7")
    system = request.get("input", {}).get("system", "")
    user = request.get("input", {}).get("user", "")

    print(f"[runner] model={model} timeout={args.timeout}s request={request_path}", file=sys.stderr)
    start = time.time()

    ok = False
    raw_text = ""
    error_info = None

    try:
        raw_text = llm_call(system, user, model=model, timeout=args.timeout)
        ok = True
    except Exception as e:
        error_info = {"type": type(e).__name__, "message": str(e)[:500]}
        print(f"[runner] ERROR: {error_info}", file=sys.stderr)

    elapsed_ms = int((time.time() - start) * 1000)

    output: dict = {
        "version": "xkb.result.v1",
        "ok": ok,
        "backend": "llm-call",
        "model": model,
        "source_date": request.get("source_date"),
        "chunk_index": chunk_index,
        "finished_at": datetime.now(timezone.utc).isoformat(),
        "timing_ms": elapsed_ms,
    }

    if ok:
        output["raw_text"] = raw_text
        # Parse insights from raw_text
        try:
            cleaned = raw_text.strip()
            cleaned = cleaned.removeprefix("```json").removeprefix("```").rstrip("` \n")
            parsed = json.loads(cleaned)
            output["output"] = {"insights": parsed.get("insights", [])}
            print(f"[runner] insights_count={len(output['output']['insights'])}", file=sys.stderr)
        except Exception as parse_err:
            output["output"] = {"insights": [], "_parse_error": str(parse_err), "_raw_text": raw_text[:300]}
            print(f"[runner] WARNING: JSON parse failed: {parse_err}", file=sys.stderr)
    else:
        output["error"] = error_info
        output["output"] = {"insights": []}

    out_path.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[runner] done in {elapsed_ms}ms ok={ok} out={out_path}", file=sys.stderr)


if __name__ == "__main__":
    main()
