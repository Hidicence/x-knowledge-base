#!/usr/bin/env python3
"""Canonical entry point for the XKB Knowledge Service.

The implementation currently lives in ``xkb_memory_service.py`` so existing
local integrations remain compatible. The public service contract is broader:
each connected Agent turn captures evidence and performs semantic retrieval
across the complete XKB knowledge plane.
"""
from __future__ import annotations

from xkb_memory_service import main


import xkb_usage  # noqa: E402  — 量測誰在跑，見 scripts/xkb_usage.py

if __name__ == "__main__":
    xkb_usage.record(__file__)
    raise SystemExit(main())
