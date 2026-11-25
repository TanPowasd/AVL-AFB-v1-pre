# -*- coding: utf-8 -*-
"""Kaggriculture 多文件压缩包提交起步模板入口（根目录 main.py）。

打包提交示例：
  python scripts/make_submission.py agents/template --out ../submissions/kaggriculture.zip
"""
from __future__ import annotations

import json
from pathlib import Path

from planner import SimplePlanner

_PKG_DIR = Path(__file__).resolve().parent


class Agent:
    def __init__(self) -> None:
        params_path = _PKG_DIR / "params.json"
        params = json.loads(params_path.read_text(encoding="utf-8"))
        self.planner = SimplePlanner(params)

    def act(self, observation: dict) -> dict:
        return self.planner.step(observation)
