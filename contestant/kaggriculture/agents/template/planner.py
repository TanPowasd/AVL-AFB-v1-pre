# -*- coding: utf-8 -*-
"""多文件包示例子模块：演示如何拆分策略逻辑并读取包内只读配置参数。"""
from __future__ import annotations


class SimplePlanner:
    def __init__(self, params: dict) -> None:
        self.default_crop = str(params.get("default_crop", "WHEAT"))
        self.min_seed_stock = int(params.get("min_seed_stock", 2))
        self.seed_buy_batch = int(params.get("seed_buy_batch", 2))

    def step(self, obs: dict) -> dict:
        me = obs["player"]
        farm = obs["farms"][me]
        priv = obs["private"]
        hands = farm["hands"]
        fx, fy = farm["farmer"]
        tile = farm["tiles"][fy][fx]
        seeds = priv.get("seeds", {})
        shed = priv.get("shed", {})

        farmer_act: list = ["PASS"]
        market_orders: list = []

        # 简单闭环示例：买种子 -> 在脚下种植/浇水/收获 -> 回谷仓交货并出售
        if seeds.get(self.default_crop, 0) < self.min_seed_stock and farm["money"] >= 50:
            market_orders.append(["BUY_SEED", self.default_crop, self.seed_buy_batch])

        if shed.get(self.default_crop, 0) > 0:
            market_orders.append(["SELL", self.default_crop, shed[self.default_crop]])

        carry = priv.get("inventories", [{}])[0] if priv.get("inventories") else {}
        if carry and (fx, fy) in ((4, 4), (5, 4), (4, 5), (5, 5)):
            farmer_act = ["DROP"]
        elif tile is None and seeds.get(self.default_crop, 0) > 0:
            farmer_act = ["PLANT", self.default_crop]
        elif isinstance(tile, dict) and tile.get("kind") == "PLANT":
            if tile.get("yield_units", 0) > 0 and obs["day"] - tile.get("planted_day", 0) >= 2:
                farmer_act = ["HARVEST"]
            elif not tile.get("watered_today"):
                farmer_act = ["WATER"]
        elif isinstance(tile, dict) and tile.get("kind") == "WEED":
            farmer_act = ["DIG"]

        return {
            "farmer": farmer_act,
            "hands": [["PASS"] for _ in hands],
            "market": market_orders[:10],
        }
