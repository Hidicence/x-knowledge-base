#!/usr/bin/env python3
"""Canonical entry point for the XKB Knowledge Service.

The implementation currently lives in ``xkb_memory_service.py`` so existing
local integrations remain compatible. The public service contract is broader:
each connected Agent turn captures evidence and performs semantic retrieval
across the complete XKB knowledge plane.
"""
from __future__ import annotations

from xkb_memory_service import main


if __name__ == "__main__":
    raise SystemExit(main())
