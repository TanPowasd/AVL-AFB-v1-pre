# -*- coding: utf-8 -*-
"""Thin delegation wrapper for backwards compatibility with kaggriculture launcher.

Redirects to top-level organizer/launch_contestant.py.
"""
from __future__ import annotations

import sys
from pathlib import Path

ORGANIZER_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ORGANIZER_ROOT))
from launch_contestant import main

if __name__ == "__main__":
    main()
