# -*- coding: utf-8 -*-
"""Style 1: Precision Agronomy & Storage/Price-Timing Arbitrage.

Crop-dominant precision agronomy strategist (~65-80% crop revenue, 160-260+ plantings)
built around four distinct engines:
1. Multi-Cycle Horizon DP (TileCycleDP): backward dynamic program over days [day..29]
   optimizing multi-wave crop rotations across all 100 tiles (early MELON cliff capture
   + rapid CARROT/WHEAT bootstrap -> fertilized ongoing STRAWBERRY/TOMATO waves ->
   late PET_CAFE CARROT & WHEAT liquidity sprint).
2. Exact Water-Window & Organic-Manure Fertilizer Engine: skips non-lethal off-window
   waters to conserve Fibonacci labor, aligns 3-day fertilizer windows onto multi-tick
   production streaks (TOMATO ages 7/10, STRAWBERRY ages 9/13), and runs a strictly
   capped organic-manure support pen (<= 14 dairy/wool units) to supply zero-cost
   fertilizer and prevent opponents from monopolizing uncontested dairy/wool markets.
3. Exact-Curve Storage & Price-Timing Arbitrageur: evaluates exact multi-day cumulative
   market curves, holds appreciating hinge/shop crops in the 100-unit shed when cash
   allows, front-runs quadratic MELON cliffs with same-day rush drops, and liquidates
   100% by Day 29 step 718.
4. Angular Sector-Sweep 2-Opt Router: partitions tile tasks by polar sector around
   the central shed, runs 2-opt tour optimization per worker, and sizes the Fibonacci
   daily crew by marginal covered value minus wage.
"""
from __future__ import annotations

from collections import Counter, deque
import math

BOARD_SIZE = 10
TURNS_PER_DAY = 24
LAST_DAY = 29
SHED_TILES = ((4, 4), (5, 4), (4, 5), (5, 5))
SHED_SET = frozenset(SHED_TILES)
SHED_CAPACITY = 100
MARKET_I0 = 10000
HINGE_GAIN = 8.0
ORGANIC_PEN_CAP = 14  # Strictly capped organic-manure support pen; farm stays 80%+ crops!

CROPS = {
    "WHEAT": {"seed": 10, "first": 2, "max_day": 4, "interval": 0, "max_yield": 6, "ongoing": False},
    "CARROT": {"seed": 20, "first": 2, "max_day": 3, "interval": 0, "max_yield": 4, "ongoing": False},
    "TOMATO": {"seed": 50, "first": 8, "max_day": 8, "interval": 1, "max_yield": 4, "ongoing": True},
    "STRAWBERRY": {"seed": 100, "first": 10, "max_day": 10, "interval": 2, "max_yield": 4, "ongoing": True},
    "MELON": {"seed": 80, "first": 10, "max_day": 12, "interval": 0, "max_yield": 6, "ongoing": False},
}
ANIMALS = {
    "GOOSE": {"cost": 300, "struct": "COOP", "first": 4, "interval": 1, "max_held": 4, "product": "EGG"},
    "COW": {"cost": 400, "struct": "PASTURE", "first": 8, "interval": 2, "max_held": 6, "product": "MILK"},
    "SHEEP": {"cost": 500, "struct": "PASTURE", "first": 6, "interval": 3, "max_held": 6, "product": "WOOL"},
}
CROP_LIST = ("WHEAT", "CARROT", "TOMATO", "STRAWBERRY", "MELON")
SUPPORT_ANIMALS = ("COW", "SHEEP")
PRODUCTS = ("WHEAT", "CARROT", "TOMATO", "STRAWBERRY", "MELON", "EGG", "MILK", "WOOL", "FERTILIZER")

MARKET_PARAMS = {
    "WHEAT": (25, 400, "sqrt", 0.8, "log", 0.2),
    "CARROT": (35, 450, "hinge", 1.0, "sqrt", 0.7),
    "TOMATO": (60, 200, "hinge", 0.4, "sqrt", 0.6),
    "STRAWBERRY": (120, 100, "sqrt", 0.7, "linear", 1.6),
    "MELON": (250, 300, "log", 0.2, "sq", 3.6),
    "EGG": (50, 332, "hinge", 0.4, "log", 0.2),
    "MILK": (160, 122, "sqrt", 0.6, "linear", 1.6),
    "WOOL": (200, 105, "log", 0.2, "sq", 3.2),
    "FERTILIZER": (100, 200, "linear", 0.4, "linear", 0.4),
}

SHOPS = {
    "BAKERY": ("EGG", "WHEAT"),
    "PIZZA_SHOP": ("MILK", "TOMATO", "WHEAT"),
    "BRUNCH_SPOT": ("EGG", "WHEAT", "STRAWBERRY"),
    "YARN_STORE": ("WOOL",),
    "ICE_CREAM_SHOP": ("STRAWBERRY", "MILK", "WHEAT"),
    "PET_CAFE": ("CARROT",),
    "SMOOTHIE_SHOP": ("STRAWBERRY", "MILK"),
    "FARMERS_MARKET": ("WHEAT", "CARROT", "TOMATO", "STRAWBERRY"),
}

LAND_ORDER = ("NE", "SW", "SE")
LAND_PRICES = (1000, 2000, 4000)

WORKER_OPS = frozenset((
    "NORTH", "SOUTH", "EAST", "WEST", "PASS",
    "PICKUP", "PLANT", "WATER", "HARVEST", "FERTILIZE",
    "BUILD_COOP", "BUILD_PASTURE", "DIG", "PLACE",
    "FEED", "COLLECT_FERTILIZER", "CARE", "DROP",
))
MARKET_OPS = frozenset(("BUY_SEED", "BUY_PRODUCT", "BUY_ANIMAL", "SELL", "HIRE", "BUY_LAND"))


def _shape(func: str, x: float, t_val: float) -> float:
    if x <= 0.0:
        return 0.0
    if func == "linear":
        return x
    if func == "sq":
        return x * x
    if func == "sqrt":
        return math.sqrt(x)
    if func == "log":
        return math.log1p(x)
    if func == "hinge":
        u = x / t_val
        return u + (HINGE_GAIN * (u - 1.0) * (u - 1.0) if u > 1.0 else 0.0)
    return x


def _build_price_tables():
    lo = MARKET_I0 - 5000
    hi = MARKET_I0 + 5000
    tables = {}
    for item, (base, t_val, bf, bt, af, at) in MARKET_PARAMS.items():
        amp_b = bt * base / _shape(bf, t_val, t_val)
        amp_a = at * base / _shape(af, t_val, t_val)
        prices = []
        prefix = [0]
        acc = 0
        floor_inv = hi
        found_floor = False
        for inv in range(lo, hi + 1):
            if inv < MARKET_I0:
                val = base + amp_b * _shape(bf, MARKET_I0 - inv, t_val)
            else:
                val = base - amp_a * _shape(af, inv - MARKET_I0, t_val)
            px = max(1, int(round(val)))
            if not found_floor and inv >= MARKET_I0 and px == 1:
                floor_inv = inv
                found_floor = True
            prices.append(px)
            acc += px
            prefix.append(acc)
        tables[item] = (lo, hi, tuple(prices), tuple(prefix), floor_inv, base, t_val, bf, amp_b, af, amp_a)
    return tables


_TABLES = _build_price_tables()


def exact_price(item: str, inv: int) -> int:
    lo, hi, prices, _, _, base, t_val, bf, amp_b, af, amp_a = _TABLES[item]
    if lo <= inv <= hi:
        return prices[inv - lo]
    if inv < MARKET_I0:
        val = base + amp_b * _shape(bf, MARKET_I0 - inv, t_val)
    else:
        val = base - amp_a * _shape(af, inv - MARKET_I0, t_val)
    return max(1, int(round(val)))


def sell_revenue(item: str, inv: int, qty: int) -> tuple[int, int]:
    if qty <= 0:
        return 0, inv
    lo, hi, _, prefix, floor_inv, *_ = _TABLES[item]
    if inv >= floor_inv:
        return qty, inv
    move = min(qty, floor_inv - inv)
    if lo <= inv and inv + move <= hi + 1:
        rev = prefix[inv + move - lo] - prefix[inv - lo] + (qty - move)
        return rev, inv + move
    rev = 0
    cur = inv
    for _ in range(qty):
        px = exact_price(item, cur)
        rev += px
        if px > 1:
            cur += 1
    return rev, cur


def buy_cost(item: str, inv: int, qty: int) -> tuple[int, int]:
    if qty <= 0:
        return 0, inv
    lo, hi, _, prefix, *_ = _TABLES[item]
    if inv - qty >= lo and inv <= hi + 1:
        return prefix[inv - lo] - prefix[inv - qty - lo], inv - qty
    cost = 0
    cur = inv
    for _ in range(qty):
        cur -= 1
        cost += exact_price(item, cur)
    return cost, cur


def fib_total(n_hands: int) -> int:
    a, b, tot = 1, 1, 0
    for _ in range(max(0, n_hands)):
        tot += a
        a, b = b, a + b
    return tot


def mdist(a: tuple[int, int], b: tuple[int, int]) -> int:
    return abs(a[0] - b[0]) + abs(a[1] - b[1])


def shed_dist(p: tuple[int, int]) -> int:
    return min(mdist(p, s) for s in SHED_TILES)


def nearest_shed(p: tuple[int, int]) -> tuple[int, int]:
    return min(SHED_TILES, key=lambda s: (mdist(p, s), s))


def path_moves(a: tuple[int, int], b: tuple[int, int]) -> list[list[str]]:
    dx = b[0] - a[0]
    dy = b[1] - a[1]
    out = []
    if dx > 0:
        out.extend([["EAST"]] * dx)
    elif dx < 0:
        out.extend([["WEST"]] * (-dx))
    if dy > 0:
        out.extend([["SOUTH"]] * dy)
    elif dy < 0:
        out.extend([["NORTH"]] * (-dy))
    return out


def quadrant_of(x: int, y: int) -> str:
    return ("N" if y < 5 else "S") + ("W" if x < 5 else "E")


def polar_angle(p: tuple[int, int]) -> float:
    return math.atan2(p[1] - 4.5, p[0] - 4.5)


class MarketCurveForecast:
    """Exact multi-day cumulative market book (zero phantom integer-step penalties)."""

    def __init__(self, obs: dict, cur_day: int):
        self.day = cur_day
        self.base_inv = {k: int(v) for k, v in obs["market"]["inventory"].items()}
        shops = list(obs.get("town", {}).get("unlocked_shops") or [])
        self.n_shops = len(shops)
        self.open_demand = Counter()
        for sh in shops:
            prods = SHOPS.get(sh, ())
            per_day = 12 if len(prods) == 1 else 6
            for it in prods:
                self.open_demand[it] += per_day
        self.expected_shop = Counter()
        for prods in SHOPS.values():
            per_day = 12 if len(prods) == 1 else 6
            for it in prods:
                self.expected_shop[it] += per_day / 8.0

        self.town_daily = {}
        for it in PRODUCTS:
            arr = [0] * 30
            for d in range(cur_day, 30):
                fut = min(8 - self.n_shops, max(0, d // 3 - cur_day // 3))
                frac = 0.50 if d == cur_day else 1.0
                td = self.open_demand[it] + self.expected_shop[it] * fut + (0.0 if it == "FERTILIZER" else 1.0)
                arr[d] = int(round(td * frac))
            self.town_daily[it] = arr

        self.own_supply = [Counter() for _ in range(30)]
        self.own_buy = [Counter() for _ in range(30)]
        self.rival_supply = [Counter() for _ in range(30)]
        self.daily_px = [dict() for _ in range(30)]
        self.recompute_all()

    def set_rival_board(self, rival_farm: dict):
        """Account for rival's visible crops & animals so our supply curve stays accurate."""
        self.rival_supply = [Counter() for _ in range(30)]
        tiles = rival_farm.get("tiles") or []
        for row in tiles:
            for tl in row:
                if not isinstance(tl, dict):
                    continue
                if tl.get("kind") == "PLANT":
                    crop = tl.get("crop")
                    if crop not in CROPS:
                        continue
                    cd = CROPS[crop]
                    pday = int(tl.get("planted_day", self.day))
                    held = int(tl.get("yield_units", 0))
                    is_f = int(tl.get("fertilized_until_day", -1)) >= self.day
                    if not cd["ongoing"]:
                        if pday + cd["first"] > LAST_DAY:
                            continue
                        hday = min(LAST_DAY, max(self.day, pday + cd["max_day"]))
                        est_y = cd["max_yield"] if (crop == "MELON" or is_f) else max(held, cd["max_yield"] - 1)
                        self.rival_supply[min(LAST_DAY, hday + 1)][crop] += est_y
                    else:
                        if held > 0 and self.day - pday >= cd["first"]:
                            self.rival_supply[min(LAST_DAY, self.day + 1)][crop] += held
                        per_t = 2 if is_f else 1
                        for k in range(cd["max_yield"]):
                            yday = pday + cd["first"] + k * cd["interval"]
                            if self.day < yday <= LAST_DAY:
                                self.rival_supply[min(LAST_DAY, yday + 1)][crop] += per_t
                elif "animal" in tl:
                    an = tl["animal"]
                    if an not in ANIMALS:
                        continue
                    ad = ANIMALS[an]
                    pday = int(tl.get("placed_day", self.day))
                    prod = ad["product"]
                    for d in range(self.day, LAST_DAY):
                        self.rival_supply[min(LAST_DAY, d + 1)]["FERTILIZER"] += 1
                        dsf = (d + 1) - pday - ad["first"]
                        if dsf >= 0 and dsf % ad["interval"] == 0:
                            self.rival_supply[min(LAST_DAY, d + 1)][prod] += min(ad["max_held"], 1 + max(1, ad["interval"] - 1))
        self.recompute_all()

    def add_own_flow(self, out_flows: dict[int, Counter], buy_flows: dict[int, Counter]):
        touched = set()
        for d, c in out_flows.items():
            if 0 <= d < 30:
                self.own_supply[d].update(c)
                touched.update(c.keys())
        for d, c in buy_flows.items():
            if 0 <= d < 30:
                self.own_buy[d].update(c)
                touched.update(c.keys())
        for it in touched:
            self._recompute_item(it)

    def _recompute_item(self, it: str):
        inv = self.base_inv[it]
        td_arr = self.town_daily[it]
        for d in range(self.day, 30):
            inv -= td_arr[d] + self.own_buy[d][it]
            sup = self.own_supply[d][it] + self.rival_supply[d][it]
            if sup > 0:
                rev, inv = sell_revenue(it, inv, sup)
                self.daily_px[d][it] = rev / sup
            else:
                self.daily_px[d][it] = float(exact_price(it, inv))

    def recompute_all(self):
        for it in PRODUCTS:
            self._recompute_item(it)

    def exact_cycle_net_cash(
        self,
        out_flows: dict[int, Counter],
        buy_flows: dict[int, Counter],
        disc_rate: float,
    ) -> float:
        """Exact discounted marginal portfolio cash delta (includes exact own cannibalization)."""
        touched = set()
        for c in out_flows.values():
            touched.update(c.keys())
        for c in buy_flows.values():
            touched.update(c.keys())

        delta_cash = 0.0
        for it in touched:
            inv0 = self.base_inv[it]
            inv1 = self.base_inv[it]
            td_arr = self.town_daily[it]
            for d in range(self.day, 30):
                td = td_arr[d]
                base_b = self.own_buy[d][it]
                add_b = buy_flows.get(d, Counter()).get(it, 0)
                dfac = (1.0 - disc_rate) ** max(0, d - self.day)

                inv0 -= td
                inv1 -= td
                c0, inv0 = buy_cost(it, inv0, base_b) if base_b > 0 else (0, inv0)
                c1, inv1 = buy_cost(it, inv1, base_b + add_b) if (base_b + add_b) > 0 else (0, inv1)
                delta_cash -= dfac * (c1 - c0)

                base_o = self.own_supply[d][it]
                base_r = self.rival_supply[d][it]
                add_o = out_flows.get(d, Counter()).get(it, 0)

                tot0 = base_o + base_r
                if tot0 > 0:
                    r0, inv0 = sell_revenue(it, inv0, tot0)
                    my0 = r0 * (base_o / tot0)
                else:
                    my0 = 0.0

                tot1 = base_o + add_o + base_r
                if tot1 > 0:
                    r1, inv1 = sell_revenue(it, inv1, tot1)
                    my1 = r1 * ((base_o + add_o) / tot1)
                else:
                    my1 = 0.0

                delta_cash += dfac * (my1 - my0)
        return delta_cash


def simulate_crop_cycle(
    crop: str,
    start_day: int,
    tile: dict | None,
    fert_mode: int,
    harvest_day: int,
) -> dict | None:
    """Exact day-by-day crop lifecycle simulation with water-window skipping."""
    cd = CROPS[crop]
    fresh = tile is None
    pday = start_day if fresh else int(tile["planted_day"])
    if pday + cd["first"] > LAST_DAY:
        return None

    ongoing = cd["ongoing"]
    interval = cd["interval"]
    first = cd["first"]
    max_day = cd["max_day"]
    max_yield = cd["max_yield"]
    win_start = (max_day + 1) // 2

    held = (0 if ongoing else 1) if fresh else int(tile.get("yield_units", 0))
    dry = 1 if fresh else int(tile.get("consecutive_unwatered", 0))
    until = -1 if fresh else int(tile.get("fertilized_until_day", -1))
    expiry = -1 if fresh else int(tile.get("max_lifespan_step", -1))

    if ongoing:
        last_prod_day = pday + first + (max_yield - 1) * interval
        end_day = min(LAST_DAY, max(start_day, last_prod_day))
    else:
        end_day = min(LAST_DAY, harvest_day)
        if end_day < start_day:
            return None

    schedule = {}
    out_flows = {}
    buy_flows = {}
    total_ops = 0
    total_visits = 0
    produced_any = False

    for d in range(start_day, end_day + 1):
        if expiry >= 0 and d * TURNS_PER_DAY >= expiry and d > start_day:
            break
        age = d - pday
        already_watered = (not fresh) and (d == start_day) and bool(tile.get("watered_today"))
        ops = []
        if fresh and d == start_day:
            ops.append(("PLANT", crop))

        want_fert = False
        if fert_mode > 0:
            if ongoing:
                next_diff = (d + 1) - pday - first
                prod_tick = next_diff >= 0 and next_diff % interval == 0 and (next_diff // interval) < max_yield
                if prod_tick and until < d:
                    idx_tick = next_diff // interval
                    if fert_mode == 1 or (fert_mode == 2 and idx_tick < 2):
                        want_fert = True
            else:
                if age == win_start and until < d:
                    want_fert = True

        if ongoing:
            next_diff = (d + 1) - pday - first
            prod_tick = next_diff >= 0 and next_diff % interval == 0 and (next_diff // interval) < max_yield
            useful_water = prod_tick and (want_fert or until >= d)
            need_water = (not already_watered) and (dry >= 1 or useful_water or want_fert)
            if d >= last_prod_day:
                need_water = False
                want_fert = False
        else:
            in_win = win_start <= age <= max_day
            useful_water = in_win and held < max_yield
            need_water = (not already_watered) and (dry >= 1 or useful_water or want_fert)
            if d == end_day and held > 0 and not useful_water:
                need_water = False
                want_fert = False

        if want_fert:
            ops.append(("FERTILIZE",))
            buy_flows.setdefault(d, Counter())["FERTILIZER"] += 1
            until = max(until, d + 2)
            if not already_watered:
                need_water = True

        watered = already_watered or need_water
        if need_water:
            ops.append(("WATER",))
            if not ongoing and win_start <= age <= max_day:
                bonus = 2 if until >= d else 1
                held = min(max_yield, held + bonus)

        ripe = age >= first and held > 0
        do_harvest = False
        if ripe:
            if not ongoing:
                do_harvest = (d == end_day)
            else:
                next_diff = (d + 1) - pday - first
                next_prod = next_diff >= 0 and next_diff % interval == 0 and (next_diff // interval) < max_yield
                inc = 2 if (watered and until >= d) else 1
                do_harvest = (d == end_day) or (held >= max_yield) or (next_prod and held + inc > max_yield)

        if do_harvest:
            ops.append(("HARVEST",))
            deliv = d if (crop == "MELON" or d == LAST_DAY) else min(LAST_DAY, d + 1)
            out_flows.setdefault(deliv, Counter())[crop] += held
            produced_any = True
            held = 0

        if ops:
            schedule[d] = tuple(ops)
            total_ops += len(ops)
            total_visits += 1

        if do_harvest and not ongoing:
            end_day = d
            break

        dry = 0 if watered else dry + 1
        if dry >= 2:
            end_day = d
            break

        if ongoing:
            next_diff = (d + 1) - pday - first
            if next_diff >= 0 and next_diff % interval == 0:
                pc = next_diff // interval + 1
                if pc <= max_yield:
                    inc = 2 if (watered and until >= d) else 1
                    held = min(max_yield, held + inc)
                    if pc == max_yield:
                        expiry = (d + 2) * TURNS_PER_DAY

    if not produced_any:
        return None
    return {
        "crop": crop,
        "seed_cost": cd["seed"] if fresh else 0,
        "schedule": schedule,
        "out_flows": out_flows,
        "buy_flows": buy_flows,
        "total_ops": total_ops,
        "total_visits": total_visits,
        "end_day": end_day,
        "next_free": end_day + 1,
        "ongoing": ongoing,
    }


def simulate_support_animal(
    animal: str,
    start_day: int,
    tile: dict | None,
    forecast: MarketCurveForecast,
    labor_unit: float,
) -> dict | None:
    """Compact organic-manure support pen lifecycle (COW/SHEEP providing free dung & dairy/wool hedge)."""
    ad = ANIMALS[animal]
    fresh = (tile is None) or ("animal" not in tile)
    pday = start_day if fresh else int(tile.get("placed_day", start_day))
    if pday + ad["first"] > LAST_DAY:
        return None
    prod = ad["product"]
    iv = ad["interval"]
    first = ad["first"]
    mh = ad["max_held"]
    struct = ad["struct"]

    hungry = 0 if fresh else int(tile.get("consecutive_unfed", 0))
    bonus = 0 if fresh else int(tile.get("pending_care_bonus", 0))
    held = 0 if fresh else int(tile.get("yield_units", 0))
    fav = False if fresh else bool(tile.get("fertilizer_available", False))

    pdays = [d for d in range(start_day, LAST_DAY) if (d + 1 - pday - first) >= 0 and (d + 1 - pday - first) % iv == 0]
    pset = set(pdays)
    last_p = pdays[-1] if pdays else -1

    sched, out_flows, buy_flows = {}, {}, {}
    tot_ops = 0
    tot_visits = 0
    any_out = False

    for d in range(start_day, LAST_DAY + 1):
        ops = []
        deliv = LAST_DAY if d == LAST_DAY else min(LAST_DAY, d + 1)
        if fresh and d == start_day:
            if not (isinstance(tile, dict) and tile.get("kind") == struct and "animal" not in tile):
                if isinstance(tile, dict):
                    ops.append(("DIG",))
                ops.append(("BUILD_" + struct,))
            ops.append(("PLACE", animal))

        fed = (not fresh) and (d == start_day) and bool(tile.get("fed_today"))
        cared = (not fresh) and (d == start_day) and bool(tile.get("cared_today"))
        is_p = d in pset
        has_fut = last_p > d

        want_care = (d < LAST_DAY - 1) and has_fut and not (d < pday + first - 1 and bonus >= mh - 1)
        need_feed = False
        if d < LAST_DAY:
            if want_care or (is_p and bonus > 0) or (hungry >= 1 and last_p >= d):
                need_feed = True
            if fresh and d == start_day and not want_care and not is_p:
                need_feed = False

        if need_feed and not fed:
            ops.append(("FEED",))
            buy_flows.setdefault(d, Counter())["WHEAT"] += 1
            fed = True
        if want_care and fed and not cared:
            ops.append(("CARE",))
            cared = True

        if held > 0:
            ng = (1 + (bonus if fed else 0)) if is_p else 0
            if d == LAST_DAY or (d == LAST_DAY - 1 and held >= 3) or held >= mh or (is_p and held + ng > mh):
                ops.append(("HARVEST",))
                out_flows.setdefault(deliv, Counter())[prod] += held
                any_out = True
                held = 0

        if fav:
            # Always collect organic manure for our crop fertilization!
            ops.append(("COLLECT_FERTILIZER",))
            out_flows.setdefault(deliv, Counter())["FERTILIZER"] += 1
            any_out = True
            fav = False

        if ops:
            sched[d] = tuple(ops)
            tot_ops += len(ops)
            tot_visits += 1
        if d == LAST_DAY:
            break
        hungry = 0 if fed else hungry + 1
        if hungry >= 2:
            break
        if is_p:
            held = min(mh, held + 1 + (bonus if fed else 0))
            bonus = 0
        if cared and fed:
            bonus += 1
        fav = True

    if not any_out:
        return None
    return {
        "animal": animal, "cost": ad["cost"] if fresh else 0,
        "schedule": sched, "out_flows": out_flows, "buy_flows": buy_flows,
        "total_ops": tot_ops, "total_visits": tot_visits,
    }


def compute_tail_dp(forecast: MarketCurveForecast, cur_day: int, avg_dist: float, labor_unit: float) -> list[float]:
    """Backward DP V[d] = optimal future net value of an empty tile from day d..29."""
    V = [0.0] * 32
    visit_pen = labor_unit * (1.15 + 0.32 * avg_dist)
    for d in range(LAST_DAY - 1, cur_day - 1, -1):
        best = V[d + 1]
        for crop in CROP_LIST:
            cd = CROPS[crop]
            if d + cd["first"] > LAST_DAY:
                continue
            fert_modes = (0, 1) if cd["ongoing"] else (0,)
            if crop in ("CARROT", "WHEAT") and forecast.daily_px[min(LAST_DAY, d + cd["max_day"])][crop] > 85:
                fert_modes = (0, 1)
            hdays = (min(LAST_DAY, d + cd["first"] + 3 * cd["interval"]),) if cd["ongoing"] else (
                min(LAST_DAY, d + cd["max_day"]),
            )
            for fm in fert_modes:
                for hd in hdays:
                    cyc = simulate_crop_cycle(crop, d, None, fm, hd)
                    if cyc is None:
                        continue
                    rev = sum(forecast.daily_px[dd][it] * q for dd, cnt in cyc["out_flows"].items() for it, q in cnt.items())
                    fcost = sum(forecast.daily_px[dd][it] * q for dd, cnt in cyc["buy_flows"].items() for it, q in cnt.items())
                    work = labor_unit * cyc["total_ops"] + visit_pen * cyc["total_visits"]
                    dig_pen = labor_unit * 2.0 if cyc["ongoing"] and cyc["next_free"] <= LAST_DAY - 2 else 0.0
                    val = rev - cyc["seed_cost"] - fcost - work - dig_pen + V[min(31, cyc["next_free"])]
                    if val > best:
                        best = val
        V[d] = best
    return V


def ops_inputs(ops: tuple) -> Counter:
    c = Counter()
    for op in ops:
        if op[0] == "FERTILIZE":
            c["FERTILIZER"] += 1
        elif op[0] == "FEED":
            c["WHEAT"] += 1
        elif op[0] == "PLACE" and len(op) >= 2 and op[1] in ANIMALS:
            c[op[1]] += 1
    return c


def two_opt_tour(start: tuple[int, int], stops: list[int], jobs: list[dict], return_shed: bool) -> list[int]:
    n = len(stops)
    if n <= 2:
        return stops

    def tour_cost(seq: list[int]) -> int:
        c = mdist(start, jobs[seq[0]]["pos"])
        for i in range(n - 1):
            c += mdist(jobs[seq[i]]["pos"], jobs[seq[i + 1]]["pos"])
        if return_shed:
            c += shed_dist(jobs[seq[-1]]["pos"])
        return c

    best = list(stops)
    best_c = tour_cost(best)
    improved = True
    iters = 0
    while improved and iters < 24:
        improved = False
        iters += 1
        for i in range(n - 1):
            for j in range(i + 1, n):
                cand = best[:i] + best[i:j + 1][::-1] + best[j + 1:]
                cc = tour_cost(cand)
                if cc < best_c:
                    best = cand
                    best_c = cc
                    improved = True
    return best


def route_steps(start: tuple[int, int], seq: list[int], jobs: list[dict], force_return: bool) -> tuple[int, Counter, int]:
    if not seq:
        return 0, Counter(), 0
    ins = Counter()
    outs = 0
    rush = 0.0
    steps = 0
    pos = start
    for idx in seq:
        j = jobs[idx]
        ins.update(j["inputs"])
        outs += j["out_units"]
        rush += j["rush_val"]
        steps += mdist(pos, j["pos"]) + len(j["ops"])
        pos = j["pos"]
    steps += sum(1 for v in ins.values() if v > 0)
    if outs > 0 and (force_return or rush >= 500.0):
        steps += shed_dist(pos) + 1
    return steps, ins, outs


def build_sector_routes(
    jobs: list[dict],
    starts: list[tuple[int, int]],
    capacity: int,
    force_return: bool,
) -> tuple[list[list[int]], set[int], float]:
    """Angular sector-partitioned routing with 2-opt and overflow-repair stealing."""
    n_workers = len(starts)
    if not jobs or n_workers == 0:
        return [[] for _ in range(n_workers)], set(), 0.0

    ordered = sorted(
        range(len(jobs)),
        key=lambda i: (polar_angle(jobs[i]["pos"]), shed_dist(jobs[i]["pos"]), jobs[i]["pos"]),
    )
    loads = [len(jobs[i]["ops"]) + 2 for i in ordered]
    target_per_worker = max(1.0, sum(loads) / n_workers)
    worker_order = sorted(range(n_workers), key=lambda w: (polar_angle(starts[w]), w))
    wedges = [[] for _ in range(n_workers)]
    cur_bucket = 0
    acc = 0.0
    for idx, ld in zip(ordered, loads):
        if cur_bucket < n_workers - 1 and acc >= target_per_worker and wedges[worker_order[cur_bucket]]:
            cur_bucket += 1
            acc = 0.0
        wedges[worker_order[cur_bucket]].append(idx)
        acc += ld

    routes = [[] for _ in range(n_workers)]
    overflow = []
    for w in range(n_workers):
        if not wedges[w]:
            continue
        rem = list(wedges[w])
        seq = []
        pos = starts[w]
        while rem:
            nxt = min(rem, key=lambda idx: (mdist(pos, jobs[idx]["pos"]), -jobs[idx]["risk"], -jobs[idx]["weight"]))
            rem.remove(nxt)
            seq.append(nxt)
            pos = jobs[nxt]["pos"]
        seq = two_opt_tour(starts[w], seq, jobs, force_return)
        while seq:
            st, _, _ = route_steps(starts[w], seq, jobs, force_return)
            if st <= capacity:
                break
            drop_i = min(
                range(len(seq)),
                key=lambda k: (1 if jobs[seq[k]]["risk"] else 0, jobs[seq[k]]["weight"] / max(1, len(jobs[seq[k]]["ops"]))),
            )
            overflow.append(seq.pop(drop_i))
        routes[w] = seq

    overflow.sort(key=lambda idx: (jobs[idx]["risk"], jobs[idx]["weight"]), reverse=True)
    for idx in overflow:
        best_ins = None
        for w in range(n_workers):
            base_st, _, _ = route_steps(starts[w], routes[w], jobs, force_return)
            for pos_k in range(len(routes[w]) + 1):
                cand = routes[w][:pos_k] + [idx] + routes[w][pos_k:]
                st, _, _ = route_steps(starts[w], cand, jobs, force_return)
                if st <= capacity:
                    delta = st - base_st + 0.02 * st
                    if best_ins is None or delta < best_ins[0]:
                        best_ins = (delta, w, cand)
        if best_ins is not None:
            routes[best_ins[1]] = best_ins[2]

    covered = set()
    score = 0.0
    for w in range(n_workers):
        for idx in routes[w]:
            covered.add(idx)
            score += jobs[idx]["weight"]
    return routes, covered, score


class AgronomyArbitrageAgent:
    """Style 1: Crop-Dominant Precision Agronomy & Storage/Price-Timing Arbitrage."""

    def __init__(self):
        self.reset()

    def reset(self):
        self.day = -1
        self.player = -1
        self.last_step = -1
        self.queues: list[deque] = []
        self.market_queue: deque = deque()
        self.start_hour = 1
        self.forecast: MarketCurveForecast | None = None
        self.hold_targets: dict[str, int] = {}
        self.manure_keep = 0

    def _spawn_positions(self, farm: dict, count: int) -> list[tuple[int, int]]:
        starts = [tuple(farm["farmer"])]
        starts.extend(tuple(h) for h in (farm.get("hands") or []))
        occ = Counter(starts)
        while len(starts) < count:
            pos = min(SHED_TILES, key=lambda s: (occ[s], SHED_TILES.index(s)))
            starts.append(pos)
            occ[pos] += 1
        return starts

    def _plan_day(self, obs: dict):
        day = int(obs["day"])
        player = int(obs["player"])
        farm = obs["farms"][player]
        rival = obs["farms"][1 - player]
        priv = obs["private"]
        tiles = farm["tiles"]
        shed = dict(priv.get("shed") or {})
        money = float(farm["money"])
        unlocked = list(farm.get("unlocked_quadrants") or ["NW"])

        forecast = MarketCurveForecast(obs, day)
        forecast.set_rival_board(rival)

        n_existing_animals = sum(
            1 for row in tiles for tl in row
            if isinstance(tl, dict) and "animal" in tl
        )
        n_active = sum(
            1 for row in tiles for tl in row
            if isinstance(tl, dict) and (tl.get("kind") == "PLANT" or "animal" in tl)
        )
        labor_unit = 3.2 + 0.03 * n_active
        disc_rate = max(0.0, 0.048 - 0.0022 * day)

        # 1. Existing crops & compact organic-manure support pen
        jobs = []
        empty_slots = []
        for y in range(BOARD_SIZE):
            for x in range(BOARD_SIZE):
                tl = tiles[y][x]
                pos = (x, y)
                if tl == "LOCKED":
                    continue
                if isinstance(tl, dict) and tl.get("kind") == "PLANT":
                    crop = tl["crop"]
                    cd = CROPS[crop]
                    pday = int(tl["planted_day"])
                    held = int(tl.get("yield_units", 0))
                    last_prod_day = pday + cd["first"] + (cd["max_yield"] - 1) * cd["interval"]
                    if cd["ongoing"] and held == 0 and (day >= last_prod_day or int(tl.get("max_lifespan_step", -1)) >= 0):
                        empty_slots.append((pos, tl))
                        continue
                    fert_modes = (0, 1, 2) if cd["ongoing"] else (0, 1)
                    if cd["ongoing"]:
                        hdays = (min(LAST_DAY, last_prod_day),)
                    else:
                        hdays = tuple(range(max(day, pday + cd["first"]), min(LAST_DAY, pday + cd["max_day"]) + 1))
                        if not hdays and held > 0:
                            hdays = (day,)
                    best_cyc = None
                    best_val = -1e18
                    d_pen = labor_unit * (1.05 + 0.30 * shed_dist(pos))
                    for fm in fert_modes:
                        for hd in hdays:
                            cyc = simulate_crop_cycle(crop, day, tl, fm, hd)
                            if cyc is None:
                                continue
                            val = forecast.exact_cycle_net_cash(cyc["out_flows"], cyc["buy_flows"], disc_rate)
                            val -= labor_unit * cyc["total_ops"] + d_pen * cyc["total_visits"]
                            if val > best_val:
                                best_val = val
                                best_cyc = cyc
                    if best_cyc is not None:
                        forecast.add_own_flow(best_cyc["out_flows"], best_cyc["buy_flows"])
                        ops = best_cyc["schedule"].get(day)
                        if ops:
                            ins = ops_inputs(ops)
                            out_today = sum(
                                cnt.get(crop, 0)
                                for dd, cnt in best_cyc["out_flows"].items()
                                if dd <= min(LAST_DAY, day + 1)
                            ) if any(op[0] == "HARVEST" for op in ops) else 0
                            risk = bool(tl.get("consecutive_unwatered", 0) >= 1 and any(op[0] == "WATER" for op in ops))
                            imm = out_today * forecast.daily_px[min(LAST_DAY, day)][crop]
                            rush = imm if crop == "MELON" else 0.0
                            wt = imm + (0.78 * max(0.0, best_val) if risk else 0.14 * max(0.0, best_val)) + 14.0 + 0.3 * rush
                            jobs.append({
                                "pos": pos, "ops": ops, "inputs": ins,
                                "out_units": out_today, "rush_val": rush,
                                "weight": max(10.0, wt), "risk": risk,
                                "seed_crop": None, "new_animal": None,
                            })
                    continue

                if isinstance(tl, dict) and "animal" in tl:
                    an = tl["animal"]
                    sup = simulate_support_animal(an, day, tl, forecast, labor_unit)
                    if sup is not None:
                        forecast.add_own_flow(sup["out_flows"], sup["buy_flows"])
                        ops = sup["schedule"].get(day)
                        if ops:
                            ins = ops_inputs(ops)
                            deliv = LAST_DAY if day == LAST_DAY else min(LAST_DAY, day + 1)
                            out_u = sum(sup["out_flows"].get(deliv, Counter()).values()) if any(
                                op[0] in ("HARVEST", "COLLECT_FERTILIZER") for op in ops
                            ) else 0
                            imm = sum(q * forecast.daily_px[deliv][it] for it, q in sup["out_flows"].get(deliv, Counter()).items())
                            risk = bool(tl.get("consecutive_unfed", 0) >= 1 and any(op[0] == "FEED" for op in ops))
                            wt = imm + (260.0 if risk else 55.0)
                            jobs.append({
                                "pos": pos, "ops": ops, "inputs": ins,
                                "out_units": out_u, "rush_val": 0.0,
                                "weight": max(15.0, wt), "risk": risk,
                                "seed_crop": None, "new_animal": None,
                            })
                    continue

                empty_slots.append((pos, tl))

        # 2. Land expansion (unlock all 4 quadrants fast for 85-tile precision agronomy!)
        shed_liquid_val = sum(
            exact_price(it, forecast.base_inv[it]) * int(cnt)
            for it, cnt in shed.items() if it in PRODUCTS and cnt > 0
        )
        available_wealth = money + shed_liquid_val
        land_order = False
        new_spend = 0.0
        n_extra = len(unlocked) - 1
        if n_extra < len(LAND_ORDER) and day <= 21:
            lcost = LAND_PRICES[n_extra]
            want_land = (
                (n_extra == 0 and day <= 2 and available_wealth >= lcost + 1400)
                or (len(empty_slots) <= 10 and available_wealth >= lcost + 700)
            )
            if want_land:
                q_name = LAND_ORDER[n_extra]
                for y in range(BOARD_SIZE):
                    for x in range(BOARD_SIZE):
                        if tiles[y][x] == "LOCKED" and quadrant_of(x, y) == q_name:
                            empty_slots.append(((x, y), None))
                land_order = True
                new_spend += lcost

        empty_slots.sort(key=lambda e: (shed_dist(e[0]), 0 if e[1] is None else 1, e[0][1], e[0][0]))

        # 3. Multi-Cycle Horizon DP (80%+ crops) + Capped Organic-Manure Pen (<= 14 animals)
        if day < LAST_DAY and empty_slots:
            avg_d = sum(shed_dist(e[0]) for e in empty_slots[:25]) / max(1, min(25, len(empty_slots)))
            tail_v = compute_tail_dp(forecast, day, avg_d, labor_unit)
            pen_count = n_existing_animals

            for idx_slot, (pos, tl) in enumerate(empty_slots):
                if len(jobs) >= 96:
                    break
                if idx_slot > 0 and idx_slot % 24 == 0:
                    tail_v = compute_tail_dp(forecast, day, avg_d, labor_unit)

                sd_p = shed_dist(pos)
                d_pen = labor_unit * (1.05 + 0.30 * sd_p)
                need_dig = tl is not None
                dig_cost = labor_unit * 1.5 if need_dig else 0.0
                wage_guard = fib_total(min(15, max(5, (len(jobs) + 5) // 5)))

                best_choice = None

                # Evaluate all 5 crops via Multi-Cycle Horizon DP
                for crop in CROP_LIST:
                    cd = CROPS[crop]
                    scost = cd["seed"]
                    if day + cd["first"] > LAST_DAY:
                        continue
                    if available_wealth - new_spend < scost + max(130.0, wage_guard):
                        continue

                    fert_modes = (0, 1) if cd["ongoing"] else (0,)
                    if crop in ("CARROT", "WHEAT") and forecast.daily_px[min(LAST_DAY, day + cd["max_day"])][crop] > 80:
                        fert_modes = (0, 1)

                    if cd["ongoing"]:
                        hdays = (min(LAST_DAY, day + cd["first"] + 3 * cd["interval"]),)
                    else:
                        cands_h = {min(LAST_DAY, day + cd["max_day"])}
                        if crop == "MELON" and day + cd["first"] <= LAST_DAY:
                            cands_h.add(day + cd["first"])
                        hdays = tuple(sorted(cands_h))

                    for fm in fert_modes:
                        for hd in hdays:
                            cyc = simulate_crop_cycle(crop, day, None, fm, hd)
                            if cyc is None:
                                continue
                            cash_delta = forecast.exact_cycle_net_cash(cyc["out_flows"], cyc["buy_flows"], disc_rate)
                            work = labor_unit * cyc["total_ops"] + d_pen * cyc["total_visits"] + dig_cost
                            nf = min(31, cyc["next_free"])
                            tail_disc = (1.0 - disc_rate) ** max(0, nf - day)
                            dig_after = labor_unit * 2.0 if cyc["ongoing"] and nf <= LAST_DAY - 2 else 0.0
                            net_val = cash_delta - scost - work - dig_after + 0.55 * tail_disc * tail_v[nf]
                            score = net_val / (1.0 + 0.0007 * scost) if day <= 5 else net_val
                            if (cash_delta - scost - work) > 25.0 and (best_choice is None or score > best_choice[0]):
                                best_choice = (score, net_val, "CROP", cyc, scost)

                # Compact organic-manure support pen (strictly capped at ORGANIC_PEN_CAP <= 14!)
                # Keep working capital safe: require extra cash cushion so Day 1-2 feed never starves
                if day <= 16 and pen_count < ORGANIC_PEN_CAP and sd_p <= 4:
                    for an in SUPPORT_ANIMALS:
                        ad = ANIMALS[an]
                        if day + ad["first"] > LAST_DAY:
                            continue
                        already_owned = max(0, int(shed.get(an, 0)) - sum(
                            1 for j in jobs if j["new_animal"] == an
                        ))
                        acost = 0 if already_owned > 0 else ad["cost"]
                        if available_wealth - new_spend < acost + max(420.0, wage_guard + (pen_count + 1) * 45.0):
                            continue
                        sup = simulate_support_animal(an, day, tl, forecast, labor_unit)
                        if sup is None:
                            continue
                        cash_delta = forecast.exact_cycle_net_cash(sup["out_flows"], sup["buy_flows"], disc_rate)
                        work = labor_unit * sup["total_ops"] + d_pen * sup["total_visits"]
                        net_val = cash_delta - acost - work
                        score = (net_val * 0.90) / (1.0 + 0.0007 * acost) if day <= 5 else net_val * 0.92
                        if net_val > 250.0 and (best_choice is None or score > best_choice[0]):
                            best_choice = (score, net_val, "ANIMAL", sup, acost)

                if best_choice is None:
                    continue
                _, net_val, kind, plan_obj, cash_c = best_choice
                ops = list(plan_obj["schedule"].get(day) or ())
                if not ops:
                    continue
                if kind == "CROP" and need_dig:
                    ops.insert(0, ("DIG",))
                ops_t = tuple(ops)
                ins = ops_inputs(ops_t)
                jobs.append({
                    "pos": pos, "ops": ops_t, "inputs": ins,
                    "out_units": 0, "rush_val": 0.0,
                    "weight": max(16.0, 0.24 * net_val), "risk": False,
                    "seed_crop": plan_obj["crop"] if kind == "CROP" else None,
                    "new_animal": plan_obj["animal"] if kind == "ANIMAL" else None,
                })
                new_spend += cash_c
                if kind == "ANIMAL":
                    pen_count += 1
                forecast.add_own_flow(plan_obj["out_flows"], plan_obj["buy_flows"])

        self.forecast = forecast
        self._compute_hold_targets(day, forecast, len(unlocked))
        fert_px_now = exact_price("FERTILIZER", forecast.base_inv["FERTILIZER"])
        n_ongoing = sum(
            1 for row in tiles for tl in row
            if isinstance(tl, dict) and tl.get("kind") == "PLANT" and tl.get("crop") in ("STRAWBERRY", "TOMATO")
        )
        # Only hoard free organic manure once early high-margin fert sales drop and cash is healthy!
        self.manure_keep = (
            min(22, n_ongoing // 2)
            if (day < LAST_DAY - 1 and money >= 1500.0 and fert_px_now <= 55 and n_ongoing >= 6)
            else 0
        )

        # 4. Optimal Fibonacci crew sizing via Angular Sector-Sweep 2-Opt Router
        is_final = (day == LAST_DAY)
        route_limit = 23 if is_final else 24
        best_plan = None
        max_crew = min(16, max(4, len(jobs) + 1))

        for crew in range(1, max_crew + 1):
            wages = fib_total(crew - 1)
            if crew > 2 and wages > available_wealth - 35:
                continue
            starts = self._spawn_positions(farm, crew)
            start_h = 1 if crew == 1 else 2
            for _ in range(3):
                routes, covered, val_score = build_sector_routes(
                    jobs, starts, route_limit - start_h, is_final
                )
                orders, expense, sale_rev = self._build_opening_orders(
                    obs, jobs, covered, crew, land_order, day, forecast
                )
                needed_h = max(1 if crew > 1 else 0, (len(orders) + 9) // 10)
                if needed_h <= start_h:
                    break
                start_h = needed_h
            if expense > money + 0.98 * sale_rev - 2.0:
                continue
            net = val_score - wages
            if best_plan is None or net > best_plan[0]:
                best_plan = (net, crew, starts, routes, covered, start_h, orders)
            if len(covered) == len(jobs):
                break

        if best_plan is None:
            maint_jobs = [j for j in jobs if j["seed_crop"] is None and j["new_animal"] is None]
            for crew in range(1, 14):
                starts = self._spawn_positions(farm, crew)
                routes, covered, val_score = build_sector_routes(
                    maint_jobs, starts, route_limit - 2, is_final
                )
                orders, expense, sale_rev = self._build_opening_orders(
                    obs, maint_jobs, covered, crew, False, day, forecast
                )
                if expense > money + 0.98 * sale_rev:
                    continue
                net = val_score - fib_total(crew - 1)
                if best_plan is None or net > best_plan[0]:
                    best_plan = (net, crew, starts, routes, covered, 2, orders)
                if len(covered) == len(maint_jobs):
                    break
            jobs = maint_jobs

        if best_plan is None:
            starts = self._spawn_positions(farm, 1)
            routes, covered, _ = build_sector_routes(jobs, starts, route_limit - 1, is_final)
            orders, _, _ = self._build_opening_orders(obs, jobs, covered, 1, False, day, forecast)
            best_plan = (0.0, 1, starts, routes, covered, 1, orders)

        _, crew, starts, routes, covered, self.start_hour, orders = best_plan
        self.market_queue = deque(orders)

        self.queues = []
        for w, seq in enumerate(routes):
            q = []
            ins = Counter()
            for idx in seq:
                ins.update(jobs[idx]["inputs"])
            for it in ("WHEAT", "FERTILIZER", "COW", "SHEEP", "GOOSE"):
                if ins[it] > 0:
                    q.append(["PICKUP", it, ins[it]])
            pos = starts[w]
            carried = 0
            rush_tot = 0.0
            for idx in seq:
                j = jobs[idx]
                q.extend(path_moves(pos, j["pos"]))
                q.extend(list(op) for op in j["ops"])
                pos = j["pos"]
                carried += j["out_units"]
                rush_tot += j["rush_val"]
            if carried > 0 and (is_final or rush_tot >= 500.0 or carried >= 12):
                sh = nearest_shed(pos)
                ret = path_moves(pos, sh) + [["DROP"]]
                if self.start_hour + len(q) + len(ret) <= route_limit:
                    q.extend(ret)
            self.queues.append(deque(q))

        self.day = day
        self.player = player

    def _compute_hold_targets(self, day: int, forecast: MarketCurveForecast, n_quads: int):
        """Arbitrage hold limits: hold uncontested appreciating hinge/shop crops when funded."""
        self.hold_targets = {}
        if day >= 27 or n_quads < 3:
            for it in PRODUCTS:
                self.hold_targets[it] = 0
            return
        horizon = min(LAST_DAY, day + 4)
        for it in PRODUCTS:
            if it == "MELON":
                self.hold_targets[it] = 0
                continue
            rival_soon = sum(forecast.rival_supply[dd][it] for dd in range(day, min(30, day + 4)))
            if rival_soon >= 12:
                # Never hold while rival is about to dump the same good!
                self.hold_targets[it] = 0
                continue
            p_now = exact_price(it, forecast.base_inv[it])
            p_fut = forecast.daily_px[horizon][it]
            if it in ("TOMATO", "CARROT") and forecast.open_demand[it] >= 6 and p_fut >= p_now * 1.10:
                self.hold_targets[it] = 36
            elif it in ("STRAWBERRY", "MILK", "WOOL") and forecast.open_demand[it] >= 6 and p_fut >= p_now * 1.07:
                self.hold_targets[it] = 24
            else:
                self.hold_targets[it] = 0

    def _build_opening_orders(
        self,
        obs: dict,
        jobs: list[dict],
        covered: set[int],
        crew: int,
        land_order: bool,
        day: int,
        forecast: MarketCurveForecast,
    ) -> tuple[list[list], float, float]:
        farm = obs["farms"][obs["player"]]
        priv = obs["private"]
        shed = dict(priv.get("shed") or {})
        seeds = dict(priv.get("seeds") or {})
        money = float(farm["money"])

        req_ins = Counter()
        seed_need = Counter()
        expected_harvest = 0
        for idx in covered:
            req_ins.update(jobs[idx]["inputs"])
            if jobs[idx]["seed_crop"]:
                seed_need[jobs[idx]["seed_crop"]] += 1
            expected_harvest += jobs[idx]["out_units"]

        orders = []
        expense = 0.0
        revenue = 0.0
        inv = dict(obs["market"]["inventory"])

        hires_needed = max(0, crew - 1 - len(farm.get("hands") or []))
        hires_today = int(farm.get("hires_today", 0))
        wage_cost = fib_total(hires_today + hires_needed) - fib_total(hires_today)
        land_cost = LAND_PRICES[len(farm["unlocked_quadrants"]) - 1] if (land_order and len(farm["unlocked_quadrants"]) < 4) else 0
        anim_buys = {an: max(0, req_ins[an] - int(shed.get(an, 0))) for an in ANIMALS}
        anim_cost = sum(ANIMALS[an]["cost"] * q for an, q in anim_buys.items())
        prod_buys = {it: max(0, req_ins[it] - int(shed.get(it, 0))) for it in ("WHEAT", "FERTILIZER")}
        prod_cost = sum(buy_cost(it, inv[it], q)[0] for it, q in prod_buys.items() if q > 0)
        seed_cost = sum(CROPS[c]["seed"] * max(0, seed_need[c] - int(seeds.get(c, 0))) for c in CROP_LIST)
        total_need = wage_cost + land_cost + anim_cost + prod_cost + seed_cost + (750.0 if len(farm["unlocked_quadrants"]) < 4 else 250.0)

        sell_priority = ("MELON", "WOOL", "STRAWBERRY", "MILK", "EGG", "WHEAT", "CARROT", "TOMATO", "FERTILIZER")
        held_pool = {}
        shed_after = dict(shed)

        for it in sell_priority:
            avail = int(shed.get(it, 0))
            if it == "FERTILIZER":
                avail = max(0, avail - req_ins["FERTILIZER"])
            elif it == "WHEAT":
                avail = max(0, avail - req_ins["WHEAT"])
            if avail <= 0:
                continue
            keep = self.manure_keep if it == "FERTILIZER" else self.hold_targets.get(it, 0)
            sell_n = max(0, avail - keep)
            if avail - sell_n > 0:
                held_pool[it] = avail - sell_n
            if sell_n > 0:
                orders.append(["SELL", it, sell_n])
                rev, inv[it] = sell_revenue(it, inv[it], sell_n)
                revenue += rev
                shed_after[it] = shed_after.get(it, 0) - sell_n

        incoming_buys = sum(anim_buys.values()) + sum(prod_buys.values())
        shed_used_after = sum(max(0, int(v)) for v in shed_after.values()) + incoming_buys
        room_needed = max(0, shed_used_after + min(expected_harvest, 70) - (SHED_CAPACITY - 6))
        cash_shortfall = max(0.0, total_need - (money + revenue))

        if (cash_shortfall > 0 or room_needed > 0) and held_pool:
            h_day = min(LAST_DAY, day + 4)
            ranked = sorted(
                held_pool.keys(),
                key=lambda it: forecast.daily_px[h_day][it] / max(1.0, exact_price(it, inv[it])),
            )
            for it in ranked:
                if cash_shortfall <= 0 and room_needed <= 0:
                    break
                qty = held_pool[it]
                px = max(1, exact_price(it, inv[it]))
                need_q = max(room_needed, int(math.ceil(cash_shortfall / px)) if cash_shortfall > 0 else 0)
                take = min(qty, max(1, need_q))
                orders.append(["SELL", it, take])
                rev, inv[it] = sell_revenue(it, inv[it], take)
                revenue += rev
                cash_shortfall -= rev
                room_needed -= take

        for _ in range(hires_needed):
            orders.append(["HIRE"])
        expense += wage_cost

        if land_order and land_cost > 0:
            orders.append(["BUY_LAND"])
            expense += land_cost

        for an, q in anim_buys.items():
            if q > 0:
                orders.append(["BUY_ANIMAL", an, q])
                expense += ANIMALS[an]["cost"] * q

        for it in ("WHEAT", "FERTILIZER"):
            q = prod_buys[it]
            if q > 0:
                orders.append(["BUY_PRODUCT", it, q])
                c_p, inv[it] = buy_cost(it, inv[it], q)
                expense += c_p

        for c in CROP_LIST:
            q = max(0, seed_need[c] - int(seeds.get(c, 0)))
            if q > 0:
                orders.append(["BUY_SEED", c, q])
                expense += CROPS[c]["seed"] * q

        return orders, expense, revenue

    def _sanitize(self, farmer_act, hand_acts, market_orders, n_hands: int) -> dict:
        def ok_unit(a):
            if not isinstance(a, list) or not a or a[0] not in WORKER_OPS:
                return ["PASS"]
            op = a[0]
            if op == "PLANT":
                return [op, str(a[1])] if len(a) >= 2 and a[1] in CROPS else ["PASS"]
            if op in ("PICKUP", "PLACE"):
                if len(a) < 2 or not isinstance(a[1], str):
                    return ["PASS"]
                if len(a) >= 3:
                    return [op, a[1], max(1, int(a[2]))]
                return [op, a[1]]
            return [op]

        f_clean = ok_unit(farmer_act)
        h_clean = [ok_unit(hand_acts[i]) if i < len(hand_acts) else ["PASS"] for i in range(n_hands)]
        m_clean = []
        for m in market_orders:
            if len(m_clean) >= 10:
                break
            if not isinstance(m, list) or not m or m[0] not in MARKET_OPS:
                continue
            op = m[0]
            if op in ("HIRE", "BUY_LAND"):
                m_clean.append([op])
            elif len(m) >= 3 and isinstance(m[1], str):
                n = int(m[2])
                if n > 0:
                    m_clean.append([op, m[1], n])
        return {"farmer": f_clean, "hands": h_clean, "market": m_clean}

    def act(self, obs: dict) -> dict:
        step = int(obs.get("step", 0))
        day = int(obs["day"])
        hour = int(obs["hour"])
        player = int(obs["player"])
        if step == 0 or step <= self.last_step or day != self.day or player != self.player:
            if step == 0 or step <= self.last_step:
                self.reset()
            self._plan_day(obs)
        self.last_step = step

        farm = obs["farms"][player]
        priv = obs["private"]
        hands = farm.get("hands") or []
        n_hands = len(hands)
        positions = [tuple(farm["farmer"])] + [tuple(h) for h in hands]
        invs = priv.get("inventories") or [{}]
        shed = Counter({k: int(v) for k, v in (priv.get("shed") or {}).items() if int(v) > 0})
        seeds = Counter({k: int(v) for k, v in (priv.get("seeds") or {}).items() if int(v) > 0})

        shed_used = sum(shed.values())
        unit_acts = []
        plant_used = Counter()

        for idx, pos in enumerate(positions):
            uinv = invs[idx] if idx < len(invs) else {}
            carry_total = sum(int(v) for v in uinv.values() if int(v) > 0)

            if day == LAST_DAY and carry_total > 0 and (22 - hour) <= shed_dist(pos) + 1:
                if pos in SHED_SET:
                    if shed_used + carry_total <= SHED_CAPACITY:
                        unit_acts.append(["DROP"])
                        shed_used += carry_total
                    else:
                        room = max(0, SHED_CAPACITY - shed_used)
                        if room > 0:
                            best_it = max(
                                (k for k, v in uinv.items() if int(v) > 0),
                                key=lambda k: exact_price(k, int(obs["market"]["inventory"].get(k, MARKET_I0))) if k in PRODUCTS else 0,
                            )
                            put = min(room, int(uinv[best_it]))
                            unit_acts.append(["PLACE", best_it, put])
                            shed_used += put
                        else:
                            unit_acts.append(["PASS"])
                else:
                    mv = path_moves(pos, nearest_shed(pos))
                    unit_acts.append(mv[0] if mv else ["PASS"])
                continue

            if hour < self.start_hour:
                unit_acts.append(["PASS"])
                continue

            if idx < len(self.queues) and self.queues[idx]:
                nxt = self.queues[idx][0]
                if nxt[0] == "PLANT":
                    crop = nxt[1]
                    if plant_used[crop] + 1 > seeds[crop]:
                        unit_acts.append(["PASS"])
                        continue
                    plant_used[crop] += 1
                    unit_acts.append(self.queues[idx].popleft())
                elif nxt[0] == "DROP":
                    if shed_used + carry_total <= SHED_CAPACITY:
                        unit_acts.append(self.queues[idx].popleft())
                        shed_used += carry_total
                    else:
                        room = max(0, SHED_CAPACITY - shed_used)
                        if room > 0 and carry_total > 0:
                            best_it = max(
                                (k for k, v in uinv.items() if int(v) > 0),
                                key=lambda k: exact_price(k, int(obs["market"]["inventory"].get(k, MARKET_I0))) if k in PRODUCTS else 0,
                            )
                            put = min(room, int(uinv[best_it]))
                            unit_acts.append(["PLACE", best_it, put])
                            shed_used += put
                        else:
                            unit_acts.append(["PASS"])
                else:
                    unit_acts.append(self.queues[idx].popleft())
            elif carry_total > 0 and (day == LAST_DAY or carry_total >= 12):
                if pos in SHED_SET:
                    if shed_used + carry_total <= SHED_CAPACITY:
                        unit_acts.append(["DROP"])
                        shed_used += carry_total
                    else:
                        room = max(0, SHED_CAPACITY - shed_used)
                        if room > 0:
                            best_it = max(
                                (k for k, v in uinv.items() if int(v) > 0),
                                key=lambda k: exact_price(k, int(obs["market"]["inventory"].get(k, MARKET_I0))) if k in PRODUCTS else 0,
                            )
                            put = min(room, int(uinv[best_it]))
                            unit_acts.append(["PLACE", best_it, put])
                            shed_used += put
                        else:
                            unit_acts.append(["PASS"])
                else:
                    mv = path_moves(pos, nearest_shed(pos))
                    unit_acts.append(mv[0] if mv else ["PASS"])
            else:
                unit_acts.append(["PASS"])

        orders = []
        while self.market_queue and len(orders) < 10:
            orders.append(self.market_queue.popleft())

        if not self.market_queue and hour >= self.start_hour and len(orders) < 10:
            pickup_res = Counter()
            for q in self.queues:
                for op in q:
                    if op[0] == "PICKUP" and len(op) >= 3:
                        pickup_res[op[1]] += int(op[2])
            post_shed = Counter(shed)
            for idx, ua in enumerate(unit_acts):
                uinv = invs[idx] if idx < len(invs) else {}
                if ua[0] == "DROP":
                    for k, v in uinv.items():
                        post_shed[k] += int(v)
                elif ua[0] == "PLACE" and len(ua) >= 3 and positions[idx] in SHED_SET:
                    post_shed[ua[1]] += int(ua[2])
                elif ua[0] == "PICKUP" and len(ua) >= 3:
                    post_shed[ua[1]] = max(0, post_shed[ua[1]] - int(ua[2]))

            eod_carry = sum(int(v) for inv in invs for v in inv.values() if int(v) > 0)
            force_clear = (day == LAST_DAY) or (hour >= 21 and sum(post_shed.values()) + eod_carry > SHED_CAPACITY - 4)

            for it in ("MELON", "WOOL", "STRAWBERRY", "MILK", "EGG", "WHEAT", "CARROT", "TOMATO", "FERTILIZER"):
                if len(orders) >= 10:
                    break
                avail = post_shed[it]
                keep = pickup_res[it]
                if not force_clear:
                    keep = max(keep, self.hold_targets.get(it, 0))
                    if it == "FERTILIZER":
                        keep = max(keep, self.manure_keep)
                sell_n = max(0, avail - keep)
                if sell_n > 0:
                    orders.append(["SELL", it, sell_n])
                    post_shed[it] -= sell_n

            if hour >= 22 and day < LAST_DAY and len(orders) < 10:
                overflow_n = sum(post_shed.values()) + eod_carry - (SHED_CAPACITY - 2)
                if overflow_n > 0:
                    for it in ("WHEAT", "FERTILIZER", "CARROT", "TOMATO", "STRAWBERRY", "MELON", "MILK", "WOOL"):
                        if overflow_n <= 0 or len(orders) >= 10:
                            break
                        avail = max(0, post_shed[it] - pickup_res[it])
                        if avail > 0:
                            take = min(avail, overflow_n)
                            orders.append(["SELL", it, take])
                            post_shed[it] -= take
                            overflow_n -= take

        return self._sanitize(unit_acts[0], unit_acts[1:], orders, n_hands)


class Agent(AgronomyArbitrageAgent):
    pass


_INSTANCES: dict[int, AgronomyArbitrageAgent] = {}


def act(observation: dict) -> dict:
    p = int(observation["player"])
    ag = _INSTANCES.get(p)
    if ag is None or int(observation.get("step", 0)) == 0:
        ag = AgronomyArbitrageAgent()
        _INSTANCES[p] = ag
    return ag.act(observation)
