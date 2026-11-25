# -*- coding: utf-8 -*-
"""Style 3: Rival-Inference, Town-Demand Pivot & Tactical Market Counter-Play.

Game-theoretic adaptive adversary built around four distinct engines:
1. Live Rival Board & Hidden-Shed Tracker (RivalTracker): diffs opponent public
   tiles and market inventory every turn to track opponent harvest events, infer
   hidden rival shed inventory, estimate rival fert/care habits, and build a live
   day-by-day rival supply matrix across all 9 commodities.
2. Zero-Sum Relative-Payoff Portfolio Solver (CounterPlayPlanner): evaluates all
   5 crops, 3 animals, and land unlocks by exact relative cash gap impact
   (delta_my_cash - gamma * delta_rival_cash), automatically pivoting into
   uncontested niches (livestock vs crop bots, crops vs ranch bots, exact unlocked
   town-shop hinge commodities) and front-running rival cliff harvests.
3. Tactical Lockstep Broker & Hinge Sniper: prioritizes slot-0 SELL orders when
   rival holds or is about to harvest cliff goods (MELON/WOOL/STRAWBERRY/MILK),
   triggers same-day rush drops on contested cliffs, and holds uncontested hinge
   goods (TOMATO/CARROT/EGG) into town-driven deficit surges.
4. Deadline-Priority Cheapest-Insertion + Cross-Worker Relocate Router: builds
   tight multi-worker tours with pickup-mask synergy and inter-worker compaction.
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
ANIMAL_LIST = ("GOOSE", "COW", "SHEEP")
PRODUCTS = ("WHEAT", "CARROT", "TOMATO", "STRAWBERRY", "MELON", "EGG", "MILK", "WOOL", "FERTILIZER")
CLIFF_ITEMS = frozenset(("MELON", "WOOL", "STRAWBERRY", "MILK"))
HINGE_ITEMS = frozenset(("TOMATO", "CARROT", "EGG"))

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
INPUT_BITS = {"WHEAT": 1, "FERTILIZER": 2, "GOOSE": 4, "COW": 8, "SHEEP": 16}


def _shape(fn: str, x: float, t_val: float) -> float:
    if x <= 0.0:
        return 0.0
    if fn == "linear":
        return x
    if fn == "sq":
        return x * x
    if fn == "sqrt":
        return math.sqrt(x)
    if fn == "log":
        return math.log1p(x)
    if fn == "hinge":
        u = x / t_val
        return u + (HINGE_GAIN * (u - 1.0) * (u - 1.0) if u > 1.0 else 0.0)
    return x


def _init_tables():
    lo, hi = MARKET_I0 - 5000, MARKET_I0 + 5000
    tbl = {}
    for item, (base, t_val, bf, bt, af, at) in MARKET_PARAMS.items():
        ab = bt * base / _shape(bf, t_val, t_val)
        aa = at * base / _shape(af, t_val, t_val)
        pxs = []
        pref = [0]
        acc = 0
        fl = hi
        found = False
        for inv in range(lo, hi + 1):
            val = (base + ab * _shape(bf, MARKET_I0 - inv, t_val)) if inv < MARKET_I0 else (
                base - aa * _shape(af, inv - MARKET_I0, t_val)
            )
            p = max(1, int(round(val)))
            if not found and inv >= MARKET_I0 and p == 1:
                fl = inv
                found = True
            pxs.append(p)
            acc += p
            pref.append(acc)
        tbl[item] = (lo, hi, tuple(pxs), tuple(pref), fl, base, t_val, bf, ab, af, aa)
    return tbl


_TBL = _init_tables()


def qprice(item: str, inv: int) -> int:
    lo, hi, pxs, _, _, base, t_val, bf, ab, af, aa = _TBL[item]
    if lo <= inv <= hi:
        return pxs[inv - lo]
    val = (base + ab * _shape(bf, MARKET_I0 - inv, t_val)) if inv < MARKET_I0 else (
        base - aa * _shape(af, inv - MARKET_I0, t_val)
    )
    return max(1, int(round(val)))


def sell_amt(item: str, inv: int, qty: int) -> tuple[int, int]:
    if qty <= 0:
        return 0, inv
    lo, hi, _, pref, fl, *_ = _TBL[item]
    if inv >= fl:
        return qty, inv
    mv = min(qty, fl - inv)
    if lo <= inv and inv + mv <= hi + 1:
        return pref[inv + mv - lo] - pref[inv - lo] + (qty - mv), inv + mv
    rev, cur = 0, inv
    for _ in range(qty):
        p = qprice(item, cur)
        rev += p
        if p > 1:
            cur += 1
    return rev, cur


def buy_amt(item: str, inv: int, qty: int) -> tuple[int, int]:
    if qty <= 0:
        return 0, inv
    lo, hi, _, pref, *_ = _TBL[item]
    if inv - qty >= lo and inv <= hi + 1:
        return pref[inv - lo] - pref[inv - qty - lo], inv - qty
    cost, cur = 0, inv
    for _ in range(qty):
        cur -= 1
        cost += qprice(item, cur)
    return cost, cur


def fib_cost(n: int) -> int:
    a, b, s = 1, 1, 0
    for _ in range(max(0, n)):
        s += a
        a, b = b, a + b
    return s


def mdist(a: tuple[int, int], b: tuple[int, int]) -> int:
    return abs(a[0] - b[0]) + abs(a[1] - b[1])


def shed_dist(p: tuple[int, int]) -> int:
    return min(mdist(p, s) for s in SHED_TILES)


def nearest_shed(p: tuple[int, int]) -> tuple[int, int]:
    return min(SHED_TILES, key=lambda s: (mdist(p, s), s))


def quad_of(x: int, y: int) -> str:
    return ("N" if y < 5 else "S") + ("W" if x < 5 else "E")


def moves_to(a: tuple[int, int], b: tuple[int, int]) -> list[list[str]]:
    dx, dy = b[0] - a[0], b[1] - a[1]
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


class RivalTracker:
    """Infers opponent's hidden shed, care/fert habits, posture, and day-by-day supply."""

    def __init__(self):
        self.prev_tiles = None
        self.prev_mkt_inv = None
        self.prev_own_delta = Counter()
        self.hidden_stock = Counter()
        self.observed_fert_plants = 0
        self.observed_plants = 0
        self.observed_care_animals = 0
        self.observed_animals = 0
        self.rival_wheat_buys = 0

    def update(self, obs: dict, own_orders_sent: list[list]):
        step = int(obs.get("step", 0))
        me = int(obs["player"])
        rival_farm = obs["farms"][1 - me]
        tiles = rival_farm["tiles"]
        mkt_inv = {k: int(v) for k, v in obs["market"]["inventory"].items()}

        if self.prev_tiles is not None and self.prev_mkt_inv is not None:
            # 1. Detect rival tile harvest disappearances -> add to rival hidden stock
            for y in range(BOARD_SIZE):
                for x in range(BOARD_SIZE):
                    old = self.prev_tiles[y][x]
                    cur = tiles[y][x]
                    if isinstance(old, dict):
                        if old.get("kind") == "PLANT":
                            crop = old["crop"]
                            oy = int(old.get("yield_units", 0))
                            if cur is None and oy > 0:
                                self.hidden_stock[crop] += oy
                            elif isinstance(cur, dict) and cur.get("kind") == "PLANT":
                                cy = int(cur.get("yield_units", 0))
                                if cy < oy and int(old.get("max_lifespan_step", -1)) < 0:
                                    self.hidden_stock[crop] += (oy - cy)
                        elif "animal" in old:
                            prod = ANIMALS[old["animal"]]["product"]
                            oy = int(old.get("yield_units", 0))
                            if isinstance(cur, dict) and "animal" in cur:
                                cy = int(cur.get("yield_units", 0))
                                if cy < oy:
                                    self.hidden_stock[prod] += (oy - cy)
                                if old.get("fertilizer_available") and not cur.get("fertilizer_available"):
                                    self.hidden_stock["FERTILIZER"] += 1

            # 2. Infer rival sales/buys from market inventory delta minus town & own orders
            prev_step = step - 1
            shops = obs.get("town", {}).get("unlocked_shops") or []
            town_delta = Counter()
            if prev_step >= 0 and prev_step % 4 == 0:
                for sh in shops:
                    prods = SHOPS.get(sh, ())
                    mult = 2 if len(prods) == 1 else 1
                    for it in prods:
                        town_delta[it] -= mult
            if prev_step >= 0 and prev_step % 24 == 0:
                for it in PRODUCTS:
                    if it != "FERTILIZER":
                        town_delta[it] -= 1

            for it in PRODUCTS:
                obs_delta = mkt_inv[it] - self.prev_mkt_inv[it]
                inferred_rival = obs_delta - town_delta[it] - self.prev_own_delta[it]
                if inferred_rival > 0:
                    self.hidden_stock[it] = max(0, self.hidden_stock[it] - inferred_rival)
                elif inferred_rival < 0 and it == "WHEAT":
                    self.rival_wheat_buys += (-inferred_rival)

        # 3. Update rival habit estimators once per day (late hour) or continuously
        hour = int(obs["hour"])
        if hour == 20:
            for row in tiles:
                for tl in row:
                    if not isinstance(tl, dict):
                        continue
                    if tl.get("kind") == "PLANT":
                        self.observed_plants += 1
                        if int(tl.get("fertilized_until_day", -1)) >= int(obs["day"]):
                            self.observed_fert_plants += 1
                    elif "animal" in tl:
                        self.observed_animals += 1
                        if tl.get("cared_today"):
                            self.observed_care_animals += 1

        # Snapshot shallow copy of tile essentials
        self.prev_tiles = [
            [dict(tl) if isinstance(tl, dict) else tl for tl in row]
            for row in tiles
        ]
        self.prev_mkt_inv = mkt_inv

    def record_own_orders(self, orders: list[list], shed: dict, money: float, mkt_inv: dict):
        """Approximate own market inventory effect of orders submitted this turn."""
        delta = Counter()
        rem_shed = Counter(shed)
        for m in orders[:10]:
            if m[0] == "SELL" and len(m) >= 3:
                q = min(int(m[2]), rem_shed[m[1]])
                if q > 0 and qprice(m[1], mkt_inv[m[1]]) > 1:
                    delta[m[1]] += q
                    rem_shed[m[1]] -= q
            elif m[0] == "BUY_PRODUCT" and len(m) >= 3:
                delta[m[1]] -= int(m[2])
        self.prev_own_delta = delta

    @property
    def fert_habit(self) -> float:
        return (self.observed_fert_plants + 1.5) / (self.observed_plants + 6.0)

    @property
    def care_habit(self) -> float:
        return (self.observed_care_animals + 2.0) / (self.observed_animals + 3.0)

    def project_rival_supply(self, rival_farm: dict, cur_day: int) -> tuple[list[Counter], dict]:
        """Project day-by-day rival supply + earliest cliff maturity & posture diagnostics."""
        supply = [Counter() for _ in range(30)]
        # Include currently held rival shed stock as imminent supply today/tomorrow
        for it, qty in self.hidden_stock.items():
            if qty > 0:
                supply[min(LAST_DAY, cur_day)][it] += qty

        fh = self.fert_habit
        ch = self.care_habit
        crop_tiles = 0
        animal_tiles = 0
        ripe_cliff_today = Counter()
        earliest_melon_day = 99

        for row in rival_farm.get("tiles") or []:
            for tl in row:
                if not isinstance(tl, dict):
                    continue
                if tl.get("kind") == "PLANT":
                    crop = tl["crop"]
                    if crop not in CROPS:
                        continue
                    crop_tiles += 1
                    cd = CROPS[crop]
                    pday = int(tl.get("planted_day", cur_day))
                    held = int(tl.get("yield_units", 0))
                    is_f = int(tl.get("fertilized_until_day", -1)) >= cur_day
                    if not cd["ongoing"]:
                        if pday + cd["first"] > LAST_DAY:
                            continue
                        hday = min(LAST_DAY, max(cur_day, pday + cd["max_day"] - (1 if crop == "MELON" else 0)))
                        if crop == "MELON":
                            earliest_melon_day = min(earliest_melon_day, max(cur_day, pday + cd["first"]))
                        if cur_day - pday >= cd["first"] and held > 0 and crop in CLIFF_ITEMS:
                            ripe_cliff_today[crop] += held
                        est_y = cd["max_yield"] if (crop == "MELON" or is_f or fh > 0.45) else max(held, cd["max_yield"] - 1)
                        supply[min(LAST_DAY, hday + 1)][crop] += est_y
                        # Assume rival replants similar fast crops once after harvest
                        if crop in ("CARROT", "WHEAT") and hday + cd["max_day"] <= LAST_DAY:
                            supply[min(LAST_DAY, hday + cd["max_day"] + 1)][crop] += max(2, est_y - 1)
                    else:
                        if held > 0 and cur_day - pday >= cd["first"]:
                            supply[min(LAST_DAY, cur_day + 1)][crop] += held
                            if crop in CLIFF_ITEMS:
                                ripe_cliff_today[crop] += held
                        per_tick = 2 if (is_f or fh > 0.35) else 1
                        for k in range(cd["max_yield"]):
                            yday = pday + cd["first"] + k * cd["interval"]
                            if cur_day < yday <= LAST_DAY:
                                supply[min(LAST_DAY, yday + 1)][crop] += per_tick
                elif "animal" in tl:
                    an = tl["animal"]
                    if an not in ANIMALS:
                        continue
                    animal_tiles += 1
                    ad = ANIMALS[an]
                    prod = ad["product"]
                    pday = int(tl.get("placed_day", cur_day))
                    held = int(tl.get("yield_units", 0))
                    if held > 0:
                        supply[min(LAST_DAY, cur_day + 1)][prod] += held
                        if prod in CLIFF_ITEMS:
                            ripe_cliff_today[prod] += held
                    bonus = int(tl.get("pending_care_bonus", 0))
                    for d in range(cur_day, LAST_DAY):
                        supply[min(LAST_DAY, d + 1)]["FERTILIZER"] += 1
                        dsf = (d + 1) - pday - ad["first"]
                        if dsf >= 0 and dsf % ad["interval"] == 0:
                            est_b = bonus if d == cur_day else int(round(ch * ad["interval"]))
                            supply[min(LAST_DAY, d + 1)][prod] += min(ad["max_held"], 1 + est_b)
                            bonus = 0

        info = {
            "crop_tiles": crop_tiles,
            "animal_tiles": animal_tiles,
            "ripe_cliff_today": ripe_cliff_today,
            "earliest_melon_day": earliest_melon_day,
            "hidden_stock": Counter(self.hidden_stock),
            "posture": (
                "RANCH_HEAVY" if animal_tiles >= crop_tiles + 5
                else "CROP_HEAVY" if crop_tiles >= animal_tiles + 8
                else "BALANCED"
            ),
        }
        return supply, info


class AdversarialMarketModel:
    """Computes both absolute cash AND relative cash-gap impact (delta_me - gamma * delta_rival)."""

    def __init__(self, obs: dict, cur_day: int, rival_supply: list[Counter], rel_weight: float = 0.72):
        self.day = cur_day
        self.rel_w = rel_weight
        self.base_inv = {k: int(v) for k, v in obs["market"]["inventory"].items()}
        shops = list(obs.get("town", {}).get("unlocked_shops") or [])
        self.n_shops = len(shops)
        self.shop_demand = Counter()
        for sh in shops:
            prods = SHOPS.get(sh, ())
            per_day = 12 if len(prods) == 1 else 6
            for it in prods:
                self.shop_demand[it] += per_day
        self.exp_shop = Counter()
        for prods in SHOPS.values():
            per_day = 12 if len(prods) == 1 else 6
            for it in prods:
                self.exp_shop[it] += per_day / 8.0

        self.own_sell = [Counter() for _ in range(30)]
        self.own_buy = [Counter() for _ in range(30)]
        self.rival_sell = [Counter(c) for c in rival_supply]
        self.daily_inv = [dict(self.base_inv) for _ in range(30)]
        self.daily_px = [dict() for _ in range(30)]
        self.recompute()

    def add_project(self, sells: dict[int, Counter], buys: dict[int, Counter]):
        for d, c in sells.items():
            if 0 <= d < 30:
                self.own_sell[d].update(c)
        for d, c in buys.items():
            if 0 <= d < 30:
                self.own_buy[d].update(c)
        self.recompute()

    def recompute(self):
        inv = dict(self.base_inv)
        for d in range(self.day, 30):
            fut_shops = min(8 - self.n_shops, max(0, d // 3 - self.day // 3))
            frac = 0.50 if d == self.day else 1.0
            for it in PRODUCTS:
                td = self.shop_demand[it] + self.exp_shop[it] * fut_shops + (0.0 if it == "FERTILIZER" else 1.0)
                inv[it] -= int(round(td * frac))
                inv[it] -= self.own_buy[d][it]
                self.daily_inv[d][it] = inv[it]
                tot_s = self.own_sell[d][it] + self.rival_sell[d][it]
                if tot_s > 0:
                    rev, after = sell_amt(it, inv[it], tot_s)
                    self.daily_px[d][it] = rev / tot_s
                    inv[it] = after
                else:
                    self.daily_px[d][it] = float(qprice(it, inv[it]))

    def eval_relative_project(
        self,
        sells: dict[int, Counter],
        buys: dict[int, Counter],
        upfront_cost: float,
        work_cost: float,
        disc_rate: float,
    ) -> float:
        """Exact relative-value evaluation: delta(my_cash) - rel_w * delta(rival_cash)."""
        touched = set()
        for c in sells.values():
            touched.update(c.keys())
        for c in buys.values():
            touched.update(c.keys())

        my_rev = 0.0
        my_buy_c = 0.0
        own_cannibal = 0.0
        rival_damage = 0.0

        for it in touched:
            inv_base = self.base_inv[it]
            inv_new = self.base_inv[it]
            for d in range(self.day, 30):
                fut_shops = min(8 - self.n_shops, max(0, d // 3 - self.day // 3))
                frac = 0.50 if d == self.day else 1.0
                td = self.shop_demand[it] + self.exp_shop[it] * fut_shops + (0.0 if it == "FERTILIZER" else 1.0)
                td_i = int(round(td * frac))
                inv_base -= td_i + self.own_buy[d][it]
                inv_new -= td_i + self.own_buy[d][it]

                dfac = (1.0 - disc_rate) ** max(0, d - self.day)
                add_b = buys.get(d, Counter()).get(it, 0)
                if add_b > 0:
                    bc, inv_new = buy_amt(it, inv_new, add_b)
                    my_buy_c += dfac * bc

                base_o = self.own_sell[d][it]
                base_r = self.rival_sell[d][it]
                add_o = sells.get(d, Counter()).get(it, 0)

                base_tot = base_o + base_r
                if base_tot > 0:
                    r0, after0 = sell_amt(it, inv_base, base_tot)
                    p0 = r0 / base_tot
                else:
                    p0 = float(qprice(it, inv_base))
                    after0 = inv_base

                new_tot = base_o + add_o + base_r
                if new_tot > 0:
                    r1, after1 = sell_amt(it, inv_new, new_tot)
                    p1 = r1 / new_tot
                else:
                    p1 = float(qprice(it, inv_new))
                    after1 = inv_new

                if add_o > 0:
                    my_rev += dfac * add_o * p1
                if base_o > 0:
                    own_cannibal += dfac * base_o * (p0 - p1)
                if base_r > 0:
                    # Positive when our extra supply depresses rival's revenue (good for us!)
                    rival_damage += dfac * base_r * (p0 - p1)

                inv_base = after0
                inv_new = after1

        return my_rev - my_buy_c - own_cannibal + self.rel_w * rival_damage - upfront_cost - work_cost


def build_crop_plan(crop: str, cur_day: int, tile: dict | None, fert_mode: int, harvest_day: int) -> dict | None:
    cd = CROPS[crop]
    fresh = tile is None
    pday = cur_day if fresh else int(tile["planted_day"])
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
        last_prod = pday + first + (max_yield - 1) * interval
        end_day = min(LAST_DAY, max(cur_day, last_prod))
    else:
        end_day = min(LAST_DAY, harvest_day)
        if end_day < cur_day:
            return None

    sched, sells, buys = {}, {}, {}
    tot_ops = 0
    tot_visits = 0
    any_out = False

    for d in range(cur_day, end_day + 1):
        if expiry >= 0 and d * TURNS_PER_DAY >= expiry and d > cur_day:
            break
        age = d - pday
        already_w = (not fresh) and (d == cur_day) and bool(tile.get("watered_today"))
        ops = []
        if fresh and d == cur_day:
            ops.append(("PLANT", crop))

        want_f = False
        if fert_mode > 0:
            if ongoing:
                nd = (d + 1) - pday - first
                if nd >= 0 and nd % interval == 0 and (nd // interval) < max_yield and until < d:
                    if fert_mode == 1 or (fert_mode == 2 and (nd // interval) < 2):
                        want_f = True
            elif age == win_start and until < d:
                want_f = True

        if ongoing:
            nd = (d + 1) - pday - first
            ptick = nd >= 0 and nd % interval == 0 and (nd // interval) < max_yield
            need_w = (not already_w) and (dry >= 1 or want_f or (ptick and until >= d))
            if d >= last_prod:
                need_w = False
                want_f = False
        else:
            in_w = win_start <= age <= max_day
            need_w = (not already_w) and (dry >= 1 or (in_w and held < max_yield) or want_f)
            if d == end_day and held > 0 and not (in_w and held < max_yield):
                need_w = False
                want_f = False

        if want_f:
            ops.append(("FERTILIZE",))
            buys.setdefault(d, Counter())["FERTILIZER"] += 1
            until = max(until, d + 2)
            if not already_w:
                need_w = True

        watered = already_w or need_w
        if need_w:
            ops.append(("WATER",))
            if not ongoing and win_start <= age <= max_day:
                held = min(max_yield, held + (2 if until >= d else 1))

        ripe = age >= first and held > 0
        do_h = False
        if ripe:
            if not ongoing:
                do_h = (d == end_day)
            else:
                nd = (d + 1) - pday - first
                nprod = nd >= 0 and nd % interval == 0 and (nd // interval) < max_yield
                inc = 2 if (watered and until >= d) else 1
                do_h = (d == end_day) or (held >= max_yield) or (nprod and held + inc > max_yield)

        if do_h:
            ops.append(("HARVEST",))
            # Same-day rush delivery for contested MELON/STRAWBERRY!
            deliv = d if (crop == "MELON" or d == LAST_DAY) else min(LAST_DAY, d + 1)
            sells.setdefault(deliv, Counter())[crop] += held
            any_out = True
            held = 0

        if ops:
            sched[d] = tuple(ops)
            tot_ops += len(ops)
            tot_visits += 1

        if do_h and not ongoing:
            break
        dry = 0 if watered else dry + 1
        if dry >= 2:
            break
        if ongoing:
            nd = (d + 1) - pday - first
            if nd >= 0 and nd % interval == 0:
                pc = nd // interval + 1
                if pc <= max_yield:
                    held = min(max_yield, held + (2 if (watered and until >= d) else 1))
                    if pc == max_yield:
                        expiry = (d + 2) * TURNS_PER_DAY

    if not any_out:
        return None
    return {
        "kind": "CROP", "label": crop, "cost": cd["seed"] if fresh else 0,
        "schedule": sched, "sells": sells, "buys": buys,
        "total_ops": tot_ops, "total_visits": tot_visits, "end_day": end_day,
    }


def build_animal_plan(
    animal: str, cur_day: int, tile: dict | None, model: AdversarialMarketModel,
    labor_unit: float, care_mode: int, rush_cliff: bool
) -> dict | None:
    ad = ANIMALS[animal]
    fresh = (tile is None) or ("animal" not in tile)
    pday = cur_day if fresh else int(tile.get("placed_day", cur_day))
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

    pdays = [d for d in range(cur_day, LAST_DAY) if (d + 1 - pday - first) >= 0 and (d + 1 - pday - first) % iv == 0]
    pset = set(pdays)
    last_p = pdays[-1] if pdays else -1

    sched, sells, buys = {}, {}, {}
    tot_ops = 0
    tot_visits = 0
    any_out = False

    for d in range(cur_day, LAST_DAY + 1):
        ops = []
        deliv = LAST_DAY if d == LAST_DAY else min(LAST_DAY, d + 1)
        if fresh and d == cur_day:
            if isinstance(tile, dict) and tile.get("kind") == struct and "animal" not in tile:
                pass
            else:
                if isinstance(tile, dict):
                    ops.append(("DIG",))
                ops.append(("BUILD_" + struct,))
            ops.append(("PLACE", animal))

        fed = (not fresh) and (d == cur_day) and bool(tile.get("fed_today"))
        cared = (not fresh) and (d == cur_day) and bool(tile.get("cared_today"))
        is_p = d in pset
        has_fut = last_p > d

        want_care = False
        if d < LAST_DAY - 1 and has_fut:
            nxt = next((pd for pd in pdays if pd > d), LAST_DAY)
            if not (d < pday + first - 1 and bonus >= mh - 1):
                if care_mode == 1:
                    want_care = True
                elif care_mode == 2:
                    if model.daily_px[min(LAST_DAY, nxt + 1)][prod] > model.daily_px[d]["WHEAT"] + 2.3 * labor_unit:
                        want_care = True

        need_feed = False
        if d < LAST_DAY:
            wpx = model.daily_px[d]["WHEAT"]
            rem_f = sum(max(0.0, model.daily_px[min(LAST_DAY, dd + 1)]["FERTILIZER"] - labor_unit)
                        for dd in range(d + 1, LAST_DAY + 1))
            if want_care:
                need_feed = True
            elif is_p and bonus > 0 and bonus * model.daily_px[deliv][prod] > wpx + labor_unit:
                need_feed = True
            elif hungry >= 1 and (last_p >= d or rem_f > wpx + labor_unit):
                need_feed = True
            if fresh and d == cur_day and not want_care and not is_p:
                need_feed = False

        if need_feed and not fed:
            ops.append(("FEED",))
            buys.setdefault(d, Counter())["WHEAT"] += 1
            fed = True
        if want_care and fed and not cared:
            ops.append(("CARE",))
            cared = True

        if held > 0:
            ng = (1 + (bonus if fed else 0)) if is_p else 0
            do_h = (
                d == LAST_DAY
                or (d == LAST_DAY - 1 and held >= 3)
                or held >= mh
                or (is_p and held + ng > mh)
                or (rush_cliff and prod in CLIFF_ITEMS and held >= max(3, mh - 2))
            )
            if do_h:
                ops.append(("HARVEST",))
                h_deliv = d if (rush_cliff and prod in ("WOOL", "MILK")) or d == LAST_DAY else deliv
                sells.setdefault(h_deliv, Counter())[prod] += held
                any_out = True
                held = 0

        if fav and model.daily_px[deliv]["FERTILIZER"] > labor_unit + 2.0:
            ops.append(("COLLECT_FERTILIZER",))
            sells.setdefault(deliv, Counter())["FERTILIZER"] += 1
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
        "kind": "ANIMAL", "label": animal, "cost": ad["cost"] if fresh else 0,
        "schedule": sched, "sells": sells, "buys": buys,
        "total_ops": tot_ops, "total_visits": tot_visits, "end_day": LAST_DAY,
    }


def extract_inputs(ops: tuple) -> tuple[Counter, int]:
    c = Counter()
    mask = 0
    for op in ops:
        if op[0] == "FEED":
            c["WHEAT"] += 1
            mask |= INPUT_BITS["WHEAT"]
        elif op[0] == "FERTILIZE":
            c["FERTILIZER"] += 1
            mask |= INPUT_BITS["FERTILIZER"]
        elif op[0] == "PLACE" and len(op) >= 2 and op[1] in ANIMALS:
            c[op[1]] += 1
            mask |= INPUT_BITS[op[1]]
    return c, mask


def eval_worker_seq(start: tuple[int, int], seq: list[int], jobs: list[dict], force_return: bool) -> tuple[int, int, int]:
    if not seq:
        return 0, 0, 0
    mask = 0
    outs = 0
    rush_val = 0.0
    steps = 0
    pos = start
    for idx in seq:
        j = jobs[idx]
        mask |= j["mask"]
        outs += j["out_units"]
        rush_val += j["rush_val"]
        steps += mdist(pos, j["pos"]) + len(j["ops"])
        pos = j["pos"]
    steps += mask.bit_count()
    need_ret = outs > 0 and (force_return or rush_val >= 450.0)
    if need_ret:
        steps += shed_dist(pos) + 1
    return steps, mask, outs


def route_deadline_insertion(
    jobs: list[dict],
    starts: list[tuple[int, int]],
    capacity: int,
    force_return: bool,
) -> tuple[list[list[int]], set[int], float]:
    """Deadline-priority cheapest-insertion router + cross-worker relocate local search."""
    nw = len(starts)
    if not jobs or nw == 0:
        return [[] for _ in range(nw)], set(), 0.0

    # Priority: survival risk -> cliff rush harvest -> value density
    order = sorted(
        range(len(jobs)),
        key=lambda i: (
            2 if jobs[i]["risk"] else (1 if jobs[i]["rush_val"] >= 200 else 0),
            jobs[i]["weight"] / max(1.0, len(jobs[i]["ops"]) + 0.65 * shed_dist(jobs[i]["pos"])),
            -shed_dist(jobs[i]["pos"]),
        ),
        reverse=True,
    )

    routes = [[] for _ in range(nw)]
    lengths = [0] * nw
    masks = [0] * nw
    covered = set()

    for idx in order:
        j = jobs[idx]
        best = None
        for w in range(nw):
            cur_len = lengths[w]
            # Fast optimistic lower bound
            if cur_len + len(j["ops"]) > capacity:
                continue
            r = routes[w]
            for pos_k in range(len(r) + 1):
                cand = r[:pos_k] + [idx] + r[pos_k:]
                st, cmask, _ = eval_worker_seq(starts[w], cand, jobs, force_return)
                if st > capacity:
                    continue
                # Reward sharing same input pickup mask (batch synergy!)
                mask_bonus = -0.35 if (masks[w] & j["mask"]) == j["mask"] and j["mask"] != 0 else 0.0
                cost = (st - cur_len) + 0.025 * st + mask_bonus + 0.001 * w
                if best is None or cost < best[0]:
                    best = (cost, w, cand, st, cmask)
        if best is not None:
            _, w, cand, st, cmask = best
            routes[w] = cand
            lengths[w] = st
            masks[w] = cmask
            covered.add(idx)

    # Single-pass cross-worker relocate to compact walking & pull in any skipped high-value jobs
    uncovered = [i for i in order if i not in covered]
    if uncovered:
        for idx in uncovered[:12]:
            # Try freeing room by moving one small job between workers
            for w_src in range(nw):
                if len(routes[w_src]) < 2:
                    continue
                for k_mv in range(len(routes[w_src])):
                    mv_idx = routes[w_src][k_mv]
                    r_src_new = routes[w_src][:k_mv] + routes[w_src][k_mv + 1:]
                    cand_src = r_src_new + [idx]
                    st_src, m_src, _ = eval_worker_seq(starts[w_src], cand_src, jobs, force_return)
                    if st_src > capacity:
                        continue
                    placed_mv = False
                    for w_dst in range(nw):
                        if w_dst == w_src:
                            continue
                        cand_dst = routes[w_dst] + [mv_idx]
                        st_dst, m_dst, _ = eval_worker_seq(starts[w_dst], cand_dst, jobs, force_return)
                        if st_dst <= capacity:
                            routes[w_src] = cand_src
                            lengths[w_src] = st_src
                            masks[w_src] = m_src
                            routes[w_dst] = cand_dst
                            lengths[w_dst] = st_dst
                            masks[w_dst] = m_dst
                            covered.add(idx)
                            placed_mv = True
                            break
                    if placed_mv:
                        break
                if idx in covered:
                    break

    score = sum(jobs[i]["weight"] for i in covered)
    return routes, covered, score


class TacticalCounterPlayAgent:
    """Style 3: Rival-Inference, Town-Demand Pivot & Tactical Market Counter-Play."""

    def __init__(self):
        self.reset()

    def reset(self):
        self.day = -1
        self.player = -1
        self.last_step = -1
        self.tracker = RivalTracker()
        self.queues: list[deque] = []
        self.market_queue: deque = deque()
        self.start_hour = 2
        self.rival_info: dict = {}
        self.hinge_holds: dict[str, int] = {}
        self.slot0_priority: list[str] = list(CLIFF_ITEMS)

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

        rival_supply, rival_info = self.tracker.project_rival_supply(rival, day)
        self.rival_info = rival_info
        model = AdversarialMarketModel(obs, day, rival_supply, rel_weight=0.72)

        active_cnt = sum(
            1 for row in tiles for tl in row
            if isinstance(tl, dict) and (tl.get("kind") == "PLANT" or "animal" in tl)
        )
        labor_unit = 3.5 + 0.036 * active_cnt
        disc_rate = max(0.0, 0.062 - 0.0032 * day)

        # Detect which cliff items are actively contested by rival right now
        contested_cliffs = set()
        for it in CLIFF_ITEMS:
            if (rival_info["ripe_cliff_today"][it] > 0
                    or rival_info["hidden_stock"][it] > 0
                    or sum(rival_supply[dd][it] for dd in range(day, min(30, day + 4))) >= 8):
                contested_cliffs.add(it)

        jobs = []
        open_tiles = []

        # 1. Existing crops & animals
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
                    last_p = pday + cd["first"] + (cd["max_yield"] - 1) * cd["interval"]
                    if cd["ongoing"] and held == 0 and (day >= last_p or int(tl.get("max_lifespan_step", -1)) >= 0):
                        open_tiles.append((pos, tl))
                        continue
                    fms = (0, 1, 2) if cd["ongoing"] else (0, 1)
                    if cd["ongoing"]:
                        hdays = (min(LAST_DAY, last_p),)
                    else:
                        hdays = tuple(range(max(day, pday + cd["first"]), min(LAST_DAY, pday + cd["max_day"]) + 1))
                        if not hdays and held > 0:
                            hdays = (day,)
                    best_p, best_v = None, -1e18
                    d_pen = labor_unit * (1.1 + 0.30 * shed_dist(pos))
                    for fm in fms:
                        for hd in hdays:
                            pl = build_crop_plan(crop, day, tl, fm, hd)
                            if pl is None:
                                continue
                            wk = labor_unit * pl["total_ops"] + d_pen * pl["total_visits"]
                            val = model.eval_relative_project(pl["sells"], pl["buys"], 0.0, wk, disc_rate)
                            # Extra front-run bonus if beating rival's earliest melon day!
                            if crop == "MELON" and hd <= rival_info["earliest_melon_day"]:
                                val += 90.0
                            if val > best_v:
                                best_v, best_p = val, pl
                    if best_p is not None:
                        model.add_project(best_p["sells"], best_p["buys"])
                        ops = best_p["schedule"].get(day)
                        if ops:
                            ins, mask = extract_inputs(ops)
                            out_u = sum(
                                cnt.get(crop, 0)
                                for dd, cnt in best_p["sells"].items()
                                if dd <= min(LAST_DAY, day + 1)
                            ) if any(op[0] == "HARVEST" for op in ops) else 0
                            imm = out_u * model.daily_px[min(LAST_DAY, day)][crop]
                            rush = imm if (crop in contested_cliffs or crop == "MELON") else 0.0
                            risk = bool(tl.get("consecutive_unwatered", 0) >= 1 and any(op[0] == "WATER" for op in ops))
                            wt = imm + (0.75 * max(0.0, best_v) if risk else 0.12 * max(0.0, best_v)) + 12.0 + 0.25 * rush
                            jobs.append({
                                "pos": pos, "ops": ops, "inputs": ins, "mask": mask,
                                "out_units": out_u, "rush_val": rush, "weight": max(8.0, wt),
                                "risk": risk, "seed_crop": None, "new_animal": None,
                            })
                    continue

                if isinstance(tl, dict) and "animal" in tl:
                    an = tl["animal"]
                    prod = ANIMALS[an]["product"]
                    rush_c = prod in contested_cliffs
                    d_pen = labor_unit * (1.1 + 0.30 * shed_dist(pos))
                    best_p, best_v = None, -1e18
                    for cm in (1, 2, 0):
                        pl = build_animal_plan(an, day, tl, model, labor_unit, cm, rush_c)
                        if pl is None:
                            continue
                        wk = labor_unit * pl["total_ops"] + d_pen * pl["total_visits"]
                        val = model.eval_relative_project(pl["sells"], pl["buys"], 0.0, wk, disc_rate)
                        if val > best_v:
                            best_v, best_p = val, pl
                    if best_p is not None:
                        model.add_project(best_p["sells"], best_p["buys"])
                        ops = best_p["schedule"].get(day)
                        if ops:
                            ins, mask = extract_inputs(ops)
                            delivs = [d for d in (day, min(LAST_DAY, day + 1)) if d in best_p["sells"]]
                            out_u = sum(sum(best_p["sells"][dd].values()) for dd in set(delivs)) if any(
                                op[0] in ("HARVEST", "COLLECT_FERTILIZER") for op in ops
                            ) else 0
                            imm = sum(
                                q * model.daily_px[dd][it]
                                for dd in set(delivs) for it, q in best_p["sells"][dd].items()
                            ) if out_u > 0 else 0.0
                            rush = imm if (prod in contested_cliffs and any(op[0] == "HARVEST" for op in ops)) else 0.0
                            risk = bool(tl.get("consecutive_unfed", 0) >= 1 and any(op[0] == "FEED" for op in ops))
                            wt = imm + (0.80 * max(0.0, best_v) if risk else 0.12 * max(0.0, best_v)) + 14.0 + 0.25 * rush
                            jobs.append({
                                "pos": pos, "ops": ops, "inputs": ins, "mask": mask,
                                "out_units": out_u, "rush_val": rush, "weight": max(8.0, wt),
                                "risk": risk, "seed_crop": None, "new_animal": None,
                            })
                    continue

                open_tiles.append((pos, tl))

        # 2. Adaptive Land Expansion
        shed_val = sum(qprice(it, model.base_inv[it]) * int(cnt) for it, cnt in shed.items() if it in PRODUCTS and cnt > 0)
        wealth = money + shed_val
        land_order = False
        new_spend = 0.0
        n_extra = len(unlocked) - 1
        if n_extra < len(LAND_ORDER) and day <= 19:
            lcost = LAND_PRICES[n_extra]
            if len(open_tiles) <= 6 and wealth >= lcost + 650:
                qn = LAND_ORDER[n_extra]
                for y in range(BOARD_SIZE):
                    for x in range(BOARD_SIZE):
                        if tiles[y][x] == "LOCKED" and quad_of(x, y) == qn:
                            open_tiles.append(((x, y), None))
                land_order = True
                new_spend += lcost

        open_tiles.sort(key=lambda e: (shed_dist(e[0]), 0 if e[1] is None else 1, e[0][1], e[0][0]))

        # 3. Relative-Payoff Counter-Positioning over ALL 5 Crops + 3 Animals
        # Compute per-product rival contention so we explicitly reward uncontested town-shop niches!
        rival_future_totals = Counter()
        for dd in range(day, 30):
            rival_future_totals.update(rival_supply[dd])

        if day < LAST_DAY and open_tiles:
            new_animals_today = 0
            for pos, tl in open_tiles:
                if len(jobs) >= 96:
                    break
                sd = shed_dist(pos)
                d_pen = labor_unit * (1.1 + 0.30 * sd)
                need_dig = (tl is not None)
                wage_guard = fib_cost(min(15, max(5, (len(jobs) + 5) // 5)))

                best_cand = None

                # Evaluate all 5 crops
                for crop in CROP_LIST:
                    cd = CROPS[crop]
                    cost = cd["seed"]
                    if day + cd["first"] > LAST_DAY:
                        continue
                    if wealth - new_spend < cost + max(160.0, wage_guard):
                        continue
                    fms = (0, 1) if cd["ongoing"] else (0,)
                    if crop in ("CARROT", "WHEAT") and model.daily_px[min(LAST_DAY, day + cd["max_day"])][crop] > 85:
                        fms = (0, 1)
                    if cd["ongoing"]:
                        hdays = (min(LAST_DAY, day + cd["first"] + 3 * cd["interval"]),)
                    else:
                        hset = {min(LAST_DAY, day + cd["max_day"])}
                        if crop == "MELON" and day + cd["first"] <= LAST_DAY:
                            # Test fast Day+10 melon to front-run rival!
                            hset.add(day + cd["first"])
                        hdays = tuple(sorted(hset))
                    for fm in fms:
                        for hd in hdays:
                            pl = build_crop_plan(crop, day, None, fm, hd)
                            if pl is None:
                                continue
                            wk = labor_unit * pl["total_ops"] + d_pen * pl["total_visits"] + (labor_unit * 1.5 if need_dig else 0.0)
                            val = model.eval_relative_project(pl["sells"], pl["buys"], float(cost), wk, disc_rate)
                            # Tile occupancy shadow price (favor quicker turnover when plenty of days remain)
                            dur = max(1, pl["end_day"] - day + 1)
                            if day <= 15:
                                val -= 5.5 * dur
                            # Counter-niche bonus: uncontested town-shop hinge or rival-wheat demand!
                            if crop in HINGE_ITEMS and model.shop_demand[crop] >= 6 and rival_future_totals[crop] <= 12:
                                val *= 1.15
                            if crop == "WHEAT" and rival_info["posture"] == "RANCH_HEAVY" and model.daily_px[min(LAST_DAY, day + 4)]["WHEAT"] >= 34:
                                val *= 1.18
                            score = val / (1.0 + 0.0003 * cost) if day <= 6 else val
                            if val > 35.0 and (best_cand is None or score > best_cand[0]):
                                best_cand = (score, val, pl, cost)

                # Evaluate all 3 animals (especially strong counter vs CROP_HEAVY rivals!)
                if day <= 20 and new_animals_today < 12:
                    for an in ANIMAL_LIST:
                        ad = ANIMALS[an]
                        cost = ad["cost"]
                        if day + ad["first"] > LAST_DAY:
                            continue
                        already_in_shed = max(0, int(shed.get(an, 0)) - sum(
                            1 for j in jobs if j["new_animal"] == an
                        ))
                        cash_c = 0 if already_in_shed > 0 else cost
                        if wealth - new_spend < cash_c + max(220.0, wage_guard):
                            continue
                        prod = ad["product"]
                        rush_c = prod in contested_cliffs
                        for cm in (1, 2):
                            pl = build_animal_plan(an, day, tl, model, labor_unit, cm, rush_c)
                            if pl is None:
                                continue
                            wk = labor_unit * pl["total_ops"] + d_pen * pl["total_visits"]
                            val = model.eval_relative_project(pl["sells"], pl["buys"], float(cash_c), wk, disc_rate)
                            # Counter-niche boost when rival neglects livestock or shop demands this product
                            if rival_future_totals[prod] <= 10 and (model.shop_demand[prod] >= 6 or rival_info["posture"] == "CROP_HEAVY"):
                                val *= 1.10
                            score = val / (1.0 + 0.0003 * cash_c) if day <= 6 else val
                            if val > 70.0 and (best_cand is None or score > best_cand[0]):
                                best_cand = (score, val, pl, cash_c)

                if best_cand is None:
                    continue
                _, val, pl, cash_c = best_cand
                ops = list(pl["schedule"].get(day) or ())
                if not ops:
                    continue
                if pl["kind"] == "CROP" and need_dig:
                    ops.insert(0, ("DIG",))
                ops_t = tuple(ops)
                ins, mask = extract_inputs(ops_t)
                jobs.append({
                    "pos": pos, "ops": ops_t, "inputs": ins, "mask": mask,
                    "out_units": 0, "rush_val": 0.0, "weight": max(20.0, 0.22 * val),
                    "risk": False,
                    "seed_crop": pl["label"] if pl["kind"] == "CROP" else None,
                    "new_animal": pl["label"] if pl["kind"] == "ANIMAL" else None,
                })
                new_spend += cash_c
                if pl["kind"] == "ANIMAL":
                    new_animals_today += 1
                model.add_project(pl["sells"], pl["buys"])

        # 4. Tactical Hinge-Hold & Slot-0 Cliff Priority Configuration
        self.hinge_holds = {}
        if day < 27:
            for it in HINGE_ITEMS:
                p_now = qprice(it, model.base_inv[it])
                p_fut = model.daily_px[min(LAST_DAY, day + 3)][it]
                # Hold ONLY if town is driving a hinge surge AND rival is NOT about to dump it!
                rival_soon = sum(rival_supply[dd][it] for dd in range(day, min(30, day + 3)))
                if model.shop_demand[it] >= 6 and p_fut >= p_now * 1.10 and rival_soon <= 10:
                    self.hinge_holds[it] = 28
                else:
                    self.hinge_holds[it] = 0

        # Prioritize contested cliffs in slot-0!
        self.slot0_priority = sorted(
            PRODUCTS,
            key=lambda it: (
                0 if it in contested_cliffs else (1 if it in CLIFF_ITEMS else 2),
                -qprice(it, model.base_inv[it]),
            ),
        )

        # 5. Crew sizing via Deadline-Priority Cheapest-Insertion Router
        is_final = (day == LAST_DAY)
        route_limit = 23 if is_final else 24
        best = None
        max_crew = min(16, max(4, len(jobs) + 1))

        for crew in range(1, max_crew + 1):
            wages = fib_cost(crew - 1)
            if crew > 2 and wages > wealth - 30:
                continue
            starts = self._spawn_positions(farm, crew)
            start_h = 2
            for _ in range(3):
                routes, covered, val_score = route_deadline_insertion(
                    jobs, starts, route_limit - start_h, is_final
                )
                orders, expense, rev = self._build_opening_orders(
                    obs, jobs, covered, crew, land_order, day, contested_cliffs
                )
                req_h = max(2 if crew > 1 else 1, (len(orders) + 9) // 10)
                if req_h <= start_h:
                    break
                start_h = req_h
            if expense > money + 0.98 * rev - 2.0:
                continue
            net = val_score - wages
            if best is None or net > best[0]:
                best = (net, crew, starts, routes, covered, start_h, orders)
            if len(covered) == len(jobs):
                break

        if best is None:
            maint_jobs = [j for j in jobs if j["seed_crop"] is None and j["new_animal"] is None]
            for crew in range(1, 14):
                starts = self._spawn_positions(farm, crew)
                routes, covered, val_score = route_deadline_insertion(
                    maint_jobs, starts, route_limit - 2, is_final
                )
                orders, expense, rev = self._build_opening_orders(
                    obs, maint_jobs, covered, crew, False, day, contested_cliffs
                )
                if expense > money + 0.98 * rev:
                    continue
                net = val_score - fib_cost(crew - 1)
                if best is None or net > best[0]:
                    best = (net, crew, starts, routes, covered, 2, orders)
                if len(covered) == len(maint_jobs):
                    break
            jobs = maint_jobs

        if best is None:
            starts = self._spawn_positions(farm, 1)
            routes, covered, _ = route_deadline_insertion(jobs, starts, route_limit - 2, is_final)
            orders, _, _ = self._build_opening_orders(obs, jobs, covered, 1, False, day, contested_cliffs)
            best = (0.0, 1, starts, routes, covered, 2, orders)

        _, crew, starts, routes, covered, self.start_hour, orders = best
        self.market_queue = deque(orders)

        self.queues = []
        for w, seq in enumerate(routes):
            q = []
            ins = Counter()
            for idx in seq:
                ins.update(jobs[idx]["inputs"])
            for it in ("WHEAT", "FERTILIZER", "GOOSE", "COW", "SHEEP"):
                if ins[it] > 0:
                    q.append(["PICKUP", it, ins[it]])
            pos = starts[w]
            outs = 0
            rush_tot = 0.0
            for idx in seq:
                j = jobs[idx]
                q.extend(moves_to(pos, j["pos"]))
                q.extend(list(op) for op in j["ops"])
                pos = j["pos"]
                outs += j["out_units"]
                rush_tot += j["rush_val"]
            if outs > 0 and (is_final or rush_tot >= 450.0 or outs >= 10):
                sh = nearest_shed(pos)
                ret = moves_to(pos, sh) + [["DROP"]]
                if self.start_hour + len(q) + len(ret) <= route_limit:
                    q.extend(ret)
            self.queues.append(deque(q))

        self.day = day
        self.player = player

    def _build_opening_orders(
        self,
        obs: dict,
        jobs: list[dict],
        covered: set[int],
        crew: int,
        land_order: bool,
        day: int,
        contested_cliffs: set[str],
    ) -> tuple[list[list], float, float]:
        farm = obs["farms"][obs["player"]]
        priv = obs["private"]
        shed = dict(priv.get("shed") or {})
        seeds = dict(priv.get("seeds") or {})
        inv = dict(obs["market"]["inventory"])

        req_ins = Counter()
        req_seeds = Counter()
        for idx in covered:
            req_ins.update(jobs[idx]["inputs"])
            if jobs[idx]["seed_crop"]:
                req_seeds[jobs[idx]["seed_crop"]] += 1

        orders = []
        expense = 0.0
        revenue = 0.0

        # Slot-0 Lockstep Front-Run: sell contested cliff goods FIRST before HIRE/BUY!
        for it in self.slot0_priority:
            avail = int(shed.get(it, 0))
            keep = req_ins[it] if it in ("WHEAT", "FERTILIZER") else self.hinge_holds.get(it, 0)
            if it in contested_cliffs:
                keep = 0
            qty = max(0, avail - keep)
            if qty > 0:
                orders.append(["SELL", it, qty])
                rev, inv[it] = sell_amt(it, inv[it], qty)
                revenue += rev

        hires_need = max(0, crew - 1 - len(farm.get("hands") or []))
        hires_done = int(farm.get("hires_today", 0))
        for _ in range(hires_need):
            orders.append(["HIRE"])
        expense += fib_cost(hires_done + hires_need) - fib_cost(hires_done)

        if land_order and len(farm["unlocked_quadrants"]) < 4:
            orders.append(["BUY_LAND"])
            expense += LAND_PRICES[len(farm["unlocked_quadrants"]) - 1]

        for an in ANIMAL_LIST:
            qty = max(0, req_ins[an] - int(shed.get(an, 0)))
            if qty > 0:
                orders.append(["BUY_ANIMAL", an, qty])
                expense += ANIMALS[an]["cost"] * qty

        for it in ("WHEAT", "FERTILIZER"):
            qty = max(0, req_ins[it] - int(shed.get(it, 0)))
            if qty > 0:
                orders.append(["BUY_PRODUCT", it, qty])
                c, inv[it] = buy_amt(it, inv[it], qty)
                expense += c

        for c in CROP_LIST:
            qty = max(0, req_seeds[c] - int(seeds.get(c, 0)))
            if qty > 0:
                orders.append(["BUY_SEED", c, qty])
                expense += CROPS[c]["seed"] * qty

        return orders, expense, revenue

    def _sanitize(self, farmer_act, hand_acts, market_orders, n_hands: int) -> dict:
        def ok_u(a):
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

        fc = ok_u(farmer_act)
        hc = [ok_u(hand_acts[i]) if i < len(hand_acts) else ["PASS"] for i in range(n_hands)]
        mc = []
        for m in market_orders:
            if len(mc) >= 10:
                break
            if not isinstance(m, list) or not m or m[0] not in MARKET_OPS:
                continue
            op = m[0]
            if op in ("HIRE", "BUY_LAND"):
                mc.append([op])
            elif len(m) >= 3 and isinstance(m[1], str):
                n = int(m[2])
                if n > 0:
                    mc.append([op, m[1], n])
        return {"farmer": fc, "hands": hc, "market": mc}

    def act(self, obs: dict) -> dict:
        step = int(obs.get("step", 0))
        day = int(obs["day"])
        hour = int(obs["hour"])
        player = int(obs["player"])
        if step == 0 or step <= self.last_step:
            self.reset()

        # Update rival tracker every step!
        self.tracker.update(obs, [])

        if day != self.day or player != self.player:
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
            carry = sum(int(v) for v in uinv.values() if int(v) > 0)

            if day == LAST_DAY and carry > 0 and (22 - hour) <= shed_dist(pos) + 1:
                if pos in SHED_SET:
                    if shed_used + carry <= SHED_CAPACITY:
                        unit_acts.append(["DROP"])
                        shed_used += carry
                    else:
                        room = max(0, SHED_CAPACITY - shed_used)
                        if room > 0:
                            best_it = max(
                                (k for k, v in uinv.items() if int(v) > 0),
                                key=lambda k: qprice(k, int(obs["market"]["inventory"].get(k, MARKET_I0))) if k in PRODUCTS else 0,
                            )
                            put = min(room, int(uinv[best_it]))
                            unit_acts.append(["PLACE", best_it, put])
                            shed_used += put
                        else:
                            unit_acts.append(["PASS"])
                else:
                    mv = moves_to(pos, nearest_shed(pos))
                    unit_acts.append(mv[0] if mv else ["PASS"])
                continue

            if hour < self.start_hour:
                unit_acts.append(["PASS"])
                continue

            if idx < len(self.queues) and self.queues[idx]:
                nxt = self.queues[idx][0]
                if nxt[0] == "PLANT":
                    c = nxt[1]
                    if plant_used[c] + 1 > seeds[c]:
                        unit_acts.append(["PASS"])
                        continue
                    plant_used[c] += 1
                    unit_acts.append(self.queues[idx].popleft())
                elif nxt[0] == "DROP":
                    if shed_used + carry <= SHED_CAPACITY:
                        unit_acts.append(self.queues[idx].popleft())
                        shed_used += carry
                    else:
                        room = max(0, SHED_CAPACITY - shed_used)
                        if room > 0 and carry > 0:
                            best_it = max(
                                (k for k, v in uinv.items() if int(v) > 0),
                                key=lambda k: qprice(k, int(obs["market"]["inventory"].get(k, MARKET_I0))) if k in PRODUCTS else 0,
                            )
                            put = min(room, int(uinv[best_it]))
                            unit_acts.append(["PLACE", best_it, put])
                            shed_used += put
                        else:
                            unit_acts.append(["PASS"])
                else:
                    unit_acts.append(self.queues[idx].popleft())
            elif carry > 0 and (day == LAST_DAY or carry >= 10):
                if pos in SHED_SET:
                    if shed_used + carry <= SHED_CAPACITY:
                        unit_acts.append(["DROP"])
                        shed_used += carry
                    else:
                        room = max(0, SHED_CAPACITY - shed_used)
                        if room > 0:
                            best_it = max(
                                (k for k, v in uinv.items() if int(v) > 0),
                                key=lambda k: qprice(k, int(obs["market"]["inventory"].get(k, MARKET_I0))) if k in PRODUCTS else 0,
                            )
                            put = min(room, int(uinv[best_it]))
                            unit_acts.append(["PLACE", best_it, put])
                            shed_used += put
                        else:
                            unit_acts.append(["PASS"])
                else:
                    mv = moves_to(pos, nearest_shed(pos))
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

            for it in self.slot0_priority:
                if len(orders) >= 10:
                    break
                avail = post_shed[it]
                keep = pickup_res[it]
                if not force_clear:
                    keep = max(keep, self.hinge_holds.get(it, 0))
                qty = max(0, avail - keep)
                if qty > 0:
                    orders.append(["SELL", it, qty])
                    post_shed[it] -= qty

        cleaned = self._sanitize(unit_acts[0], unit_acts[1:], orders, n_hands)
        self.tracker.record_own_orders(cleaned["market"], shed, float(farm["money"]), obs["market"]["inventory"])
        return cleaned


class Agent(TacticalCounterPlayAgent):
    pass


_INSTANCES: dict[int, TacticalCounterPlayAgent] = {}


def act(observation: dict) -> dict:
    p = int(observation["player"])
    ag = _INSTANCES.get(p)
    if ag is None or int(observation.get("step", 0)) == 0:
        ag = TacticalCounterPlayAgent()
        _INSTANCES[p] = ag
    return ag.act(observation)
