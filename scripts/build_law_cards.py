#!/usr/bin/env python3
"""Thin wrapper so `python scripts/build_law_cards.py` works."""
from __future__ import annotations

import runpy
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

runpy.run_path(str(_ROOT / "build_law_cards.py"), run_name="__main__")
