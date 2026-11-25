# -*- coding: utf-8 -*-
# 公开对手，走 loader.load_trusted，不受提交沙箱限制：
# RSIdemo 公开陪练 elite+CFR；仅提供运行策略，不包含研究赛果与日志。
# 来源：kaggriculture/RSIdemo/best_agent（冻结源码，未改动）。
import os
import sys

CH = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                  "..", "..", "engine", "champion"))
if CH not in sys.path:
    sys.path.insert(0, CH)
from main import agent as _champ  # noqa: E402


def act(observation):
    return _champ(observation, None)
