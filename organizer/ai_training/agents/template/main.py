# -*- coding: utf-8 -*-
"""前沿实验室多文件压缩包提交起步模板入口（根目录 main.py）。

打包提交示例：
  python scripts/make_submission.py agents/template --out ../submissions/ai_training.zip
"""
from __future__ import annotations

import json
from pathlib import Path
import sys

from planner import StarterPlanner

_PKG_DIR = Path(__file__).resolve().parent


def make_bot(init: dict) -> StarterPlanner:
    params_path = _PKG_DIR / "params.json"
    params = json.loads(params_path.read_text(encoding="utf-8"))
    return StarterPlanner(init, params)


def main() -> None:
    line = sys.stdin.readline()
    if not line:
        return
    bot = make_bot(json.loads(line))
    sys.stdout.write("{}\n")
    sys.stdout.flush()
    for raw in sys.stdin:
        if not raw.strip():
            continue
        msg = json.loads(raw)
        if msg.get("type") == "end":
            sys.stdout.write("{}\n")
            sys.stdout.flush()
            break
        try:
            act = bot.step(msg)
            if not isinstance(act, dict):
                act = {}
        except Exception:
            act = {}
        sys.stdout.write(json.dumps(act, separators=(",", ":")) + "\n")
        sys.stdout.flush()


if __name__ == "__main__":
    main()
