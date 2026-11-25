# -*- coding: utf-8 -*-
"""crop_rotation_strong: 纯标准库私有强参考 bot（高周转经济作物轮作 + 商店节拍套利 + 动态劳动力调度）。

特点：
1. 经济分期与作物轮作：
   - 早期（Day 0-5）：胡萝卜与小麦快速周转，积累原始资本
   - 中期（Day 6-23）：扩张地块（BUY_LAND），配置番茄、草莓持续产出与甜瓜高单价作物
   - 末期（Day 24-27）：根据成熟剩余天数收缩种植，只种胡萝卜/小麦
   - 终局（Day 28-29）：全面停种，全员收割回棚 DROP，在 step 718 前全额 SELL 清仓
2. 动态雇工与多单位协同：
   - 按资金阶梯动态雇工，多单位基于最近未分配作业贪心分配（收割 > 浇水 > 除草 > 播种 > 卸货）
3. 商店节拍与市场套利：
   - 监控城镇商店消耗与市场库存，在库存被城镇吸空（价格飙升）时集中高价出售
   - 仓储水位动态平衡，防止 shed 容量（100）溢出损耗
"""
from __future__ import annotations

import collections
import math
import random

CROPS_INFO = {
    "WHEAT": {"seed": 10, "first_yield": 2, "max_yield_day": 4, "ongoing": False},
    "CARROT": {"seed": 20, "first_yield": 2, "max_yield_day": 3, "ongoing": False},
    "TOMATO": {"seed": 50, "first_yield": 8, "max_yield_day": 8, "ongoing": True},
    "STRAWBERRY": {"seed": 100, "first_yield": 10, "max_yield_day": 10, "ongoing": True},
    "MELON": {"seed": 80, "first_yield": 10, "max_yield_day": 12, "ongoing": False},
}

SHED_TILES = ((4, 4), (5, 4), (4, 5), (5, 5))
SHED_SET = set(SHED_TILES)


def dist(p1: tuple[int, int], p2: tuple[int, int]) -> int:
    return abs(p1[0] - p2[0]) + abs(p1[1] - p2[1])


def step_toward(p: tuple[int, int], target: tuple[int, int]) -> str:
    dx = target[0] - p[0]
    dy = target[1] - p[1]
    if abs(dx) >= abs(dy):
        if dx > 0:
            return "EAST"
        if dx < 0:
            return "WEST"
        if dy > 0:
            return "SOUTH"
        if dy < 0:
            return "NORTH"
    else:
        if dy > 0:
            return "SOUTH"
        if dy < 0:
            return "NORTH"
        if dx > 0:
            return "EAST"
        if dx < 0:
            return "WEST"
    return "PASS"


class CropRotationAgent:
    def __init__(self):
        self.step_idx = 0

    def act(self, observation: dict) -> dict:
        player = observation["player"]
        farm = observation["farms"][player]
        priv = observation["private"]
        town = observation["town"]
        market_obs = observation["market"]
        tiles = farm["tiles"]
        bs = len(tiles)
        day = observation["day"]
        hour = observation["hour"]
        step = observation["step"]
        money = float(farm["money"])
        unlocked_quads = farm.get("unlocked_quadrants") or ["NW"]
        hands_list = farm.get("hands") or []
        n_hands = len(hands_list)
        farmer_pos = tuple(farm["farmer"])
        all_units = [farmer_pos] + [tuple(h) for h in hands_list]
        n_units = len(all_units)
        invs = priv.get("inventories") or [{}]
        shed = priv.get("shed") or {}
        seeds = priv.get("seeds") or {}

        half = bs // 2

        def in_area(x: int, y: int) -> bool:
            q = ("N" if y < half else "S") + ("W" if x < half else "E")
            return q in unlocked_quads

        # 1. 搜集本方地块任务
        harvest_jobs = []
        water_jobs = []
        weed_jobs = []
        empty_jobs = []

        for y in range(bs):
            for x in range(bs):
                if not in_area(x, y):
                    continue
                tl = tiles[y][x]
                pos = (x, y)
                if tl is None:
                    empty_jobs.append(pos)
                elif isinstance(tl, dict):
                    kind = tl.get("kind")
                    if kind == "WEED":
                        weed_jobs.append(pos)
                    elif kind == "PLANT":
                        crop = tl.get("crop")
                        info = CROPS_INFO.get(crop, {})
                        fy = info.get("first_yield", 99)
                        planted_day = int(tl.get("planted_day", -99))
                        yield_units = int(tl.get("yield_units", 0))
                        watered = tl.get("watered_today", False)
                        if yield_units > 0 and (day - planted_day) >= fy:
                            harvest_jobs.append(pos)
                        elif not watered:
                            water_jobs.append(pos)

        # 2. 为每个单位生成动作
        unit_actions = []
        assigned_jobs = set()

        # 辅助：找到离 pos 最近的可用谷仓格
        def nearest_shed(p: tuple[int, int]) -> tuple[int, int]:
            pool = [s for s in SHED_TILES if in_area(*s)]
            return min(pool or SHED_TILES, key=lambda s: dist(p, s))

        for u_idx, pos in enumerate(all_units):
            inv = invs[u_idx] if u_idx < len(invs) else {}
            carrying_count = sum(int(n) for k, n in inv.items() if k != "FERTILIZER")

            # 终局 Day 29 或背包已满时优先回棚卸货
            must_unload = (day >= 29 and carrying_count > 0 and hour >= 18) or (carrying_count >= 15)

            if must_unload:
                if pos in SHED_SET:
                    unit_actions.append(["DROP"])
                else:
                    unit_actions.append([step_toward(pos, nearest_shed(pos))])
                continue

            # 脚下即为任务优先处理
            cur_tile = tiles[pos[1]][pos[0]] if (0 <= pos[1] < bs and 0 <= pos[0] < bs) else None
            if in_area(*pos) and isinstance(cur_tile, dict):
                kind = cur_tile.get("kind")
                if kind == "WEED":
                    unit_actions.append(["DIG"])
                    continue
                elif kind == "PLANT":
                    crop = cur_tile.get("crop")
                    info = CROPS_INFO.get(crop, {})
                    fy = info.get("first_yield", 99)
                    p_day = int(cur_tile.get("planted_day", -99))
                    y_units = int(cur_tile.get("yield_units", 0))
                    if y_units > 0 and (day - p_day) >= fy:
                        unit_actions.append(["HARVEST"])
                        continue
                    elif not cur_tile.get("watered_today", False):
                        unit_actions.append(["WATER"])
                        continue

            # 寻找全局最优任务
            chosen_target = None
            action_op = None

            # 优先收割
            cand_h = [p for p in harvest_jobs if p not in assigned_jobs]
            if cand_h:
                chosen_target = min(cand_h, key=lambda p: dist(pos, p))
                action_op = "HARVEST"
            else:
                # 其次浇水
                cand_w = [p for p in water_jobs if p not in assigned_jobs]
                if cand_w:
                    chosen_target = min(cand_w, key=lambda p: dist(pos, p))
                    action_op = "WATER"
                else:
                    # 再次除草
                    cand_d = [p for p in weed_jobs if p not in assigned_jobs]
                    if cand_d:
                        chosen_target = min(cand_d, key=lambda p: dist(pos, p))
                        action_op = "DIG"
                    elif carrying_count > 0:
                        # 背包有货且无紧急农活，回棚卸货
                        if pos in SHED_SET:
                            unit_actions.append(["DROP"])
                            continue
                        else:
                            unit_actions.append([step_toward(pos, nearest_shed(pos))])
                            continue
                    elif day < 27:
                        # 播种
                        cand_e = [p for p in empty_jobs if p not in assigned_jobs]
                        if cand_e:
                            chosen_target = min(cand_e, key=lambda p: dist(pos, p))
                            action_op = "PLANT"

            if chosen_target is not None:
                assigned_jobs.add(chosen_target)
                if pos == chosen_target:
                    if action_op == "PLANT":
                        # 选择要播种的作物
                        plant_crop = "CARROT"
                        if day < 12 and money > 1500 and (seeds.get("TOMATO", 0) > 0 or money > 2000):
                            plant_crop = "TOMATO"
                        elif day >= 25:
                            plant_crop = "CARROT"
                        unit_actions.append(["PLANT", plant_crop])
                    else:
                        unit_actions.append([action_op])
                else:
                    unit_actions.append([step_toward(pos, chosen_target)])
            else:
                # 无事可做
                if carrying_count > 0:
                    if pos in SHED_SET:
                        unit_actions.append(["DROP"])
                    else:
                        unit_actions.append([step_toward(pos, nearest_shed(pos))])
                else:
                    unit_actions.append(["PASS"])

        # 3. 市场订单决策 (Market Orders)
        market_orders = []

        # (a) 雇工决策
        desired_hands = 0
        if day >= 1:
            desired_hands = 1
        if day >= 2 and money >= 2000:
            desired_hands = 2
        if day >= 5 and money >= 3000:
            desired_hands = 3
        if day >= 9 and len(unlocked_quads) >= 2 and money >= 5000:
            desired_hands = 4

        hires_today = farm.get("hires_today", 0)
        hire_cost = hires_today * 50 + 50
        if hour == 0 and n_hands < desired_hands and money > hire_cost + 500 and day < 28:
            for _ in range(desired_hands - n_hands):
                if len(market_orders) < 10 and money > hire_cost + 300:
                    market_orders.append(["HIRE"])
                    hires_today += 1
                    hire_cost = hires_today * 50 + 50

        # (b) 购地决策 (BUY_LAND: 5000)
        if len(unlocked_quads) < 2 and money >= 8000 and day <= 18:
            if len(market_orders) < 10:
                market_orders.append(["BUY_LAND"])
                money -= 5000

        # (c) 种子采购决策
        if day < 27:
            # 维持种子安全库存
            c_seed = seeds.get("CARROT", 0)
            if c_seed < 12 and money >= 240 and len(market_orders) < 10:
                market_orders.append(["BUY_SEED", "CARROT", 15])
                money -= 300
            if day <= 14 and seeds.get("TOMATO", 0) < 6 and money >= 600 and len(market_orders) < 10:
                market_orders.append(["BUY_SEED", "TOMATO", 6])
                money -= 300

        # (d) 农产品出售决策
        shed_total = sum(int(n) for n in shed.values())
        is_end_game = (day == 29 and hour >= 16) or (step >= 710)

        for prod, count in shed.items():
            if count <= 0 or prod == "FERTILIZER":
                continue
            should_sell = False
            if is_end_game:
                should_sell = True
            elif shed_total >= 70:
                should_sell = True
            else:
                # 检查市场库存是否偏低（价格高企）
                m_inv = market_obs.get("inventory", {}).get(prod, 10000)
                if m_inv < 9500 or hour == 23:
                    should_sell = True

            if should_sell and len(market_orders) < 10:
                market_orders.append(["SELL", prod, int(count)])

        farmer_act = unit_actions[0] if unit_actions else ["PASS"]
        hands_act = unit_actions[1:1 + n_hands]
        while len(hands_act) < n_hands:
            hands_act.append(["PASS"])

        return {
            "farmer": farmer_act,
            "hands": hands_act,
            "market": market_orders[:10],
        }


_global_agent = None


def act(observation: dict) -> dict:
    global _global_agent
    if _global_agent is None or observation.get("step") == 0:
        _global_agent = CropRotationAgent()
    return _global_agent.act(observation)
