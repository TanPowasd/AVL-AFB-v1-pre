# -*- coding: utf-8 -*-
"""前沿实验室多文件包示例子模块：演示模块拆分与包内只读参数加载。"""
from __future__ import annotations


class StarterPlanner:
    def __init__(self, init: dict, params: dict) -> None:
        self.seat = int(init.get("seat", 0))
        self.rules = init.get("rules") or {}
        self.techs = init.get("techs") or {}
        self.params_b = float(params.get("params_b", 20.0))
        self.tokens_b = float(params.get("tokens_b", 400.0))
        self.release_price = float(params.get("release_price", 10.0))
        self.research_order = list(params.get("research_order") or ["cooling", "infer_opt_1"])
        self.started = False
        self.released = False
        self.done_tech: set[str] = set()
        self.research_set: list[str] = []

    def step(self, obs: dict) -> dict:
        act: dict = {}
        for ev in obs.get("ev", []):
            ty = ev.get("type")
            if ty == "tech":
                self.done_tech.add(ev["tech"])
                self.research_set = []
            elif ty == "done" and ev.get("run") == "main" and not self.released:
                self.released = True
                act["release"] = [{"run": "main", "price": self.release_price}]
            elif ty == "slow_node" and ev.get("run"):
                act.setdefault("run_ctl", []).append({"name": ev["run"], "op": "diagnose"})
            elif ty in ("spike", "diverged") and ev.get("run"):
                act.setdefault("run_ctl", []).append({"name": ev["run"], "op": "rollback"})

        if not self.started:
            self.started = True
            act["run"] = [{
                "name": "main",
                "params_b": self.params_b,
                "tokens_b": self.tokens_b,
                "sched": "cosine",
                "ckpt_every": 12,
            }]

        if obs["t"] % 24 == 0:
            avail = []
            for k in self.research_order:
                if k in self.done_tech or k not in self.techs:
                    continue
                pre = self.techs[k].get("pre")
                if pre and pre not in self.done_tech:
                    continue
                avail.append(k)
            pick = avail[:2]
            if pick and pick != self.research_set:
                self.research_set = pick
                if len(pick) == 1:
                    act["research"] = [{"tech": pick[0], "share": 1.0}]
                else:
                    act["research"] = [{"tech": pick[0], "share": 0.6}, {"tech": pick[1], "share": 0.4}]

        return act
