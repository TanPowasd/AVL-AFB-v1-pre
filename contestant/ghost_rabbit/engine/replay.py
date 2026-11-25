# -*- coding: utf-8 -*-
"""《天翼幽灵兔》（ghost_rabbit）多局回放包封装工具（供 viz/player.html 加载）。"""
from __future__ import annotations

import time
from typing import Any


def build_replay_bundle(
    *,
    title: str,
    submission: str,
    matches: list[dict[str, Any]],
    summary: dict[str, Any],
) -> dict[str, Any]:
    return {
        "format": "ghost_rabbit_replay_bundle/1",
        "problem": "ghost_rabbit",
        "title": title,
        "submission": submission,
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "summary": summary,
        "matches": matches,
    }
