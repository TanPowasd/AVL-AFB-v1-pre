# -*- coding: utf-8 -*-
"""Style 2: Livestock Compounding, Feed-Logistics & Capital Scaling.

Livestock-dominant ranching empire (~65-80% livestock+manure revenue, 40-58+ animals)
built around four distinct engines:
1. Exact-Curve Annuity-IRR Herd Compounding Engine: evaluates GOOSE/COW/SHEEP
   placements and land unlocks (NE/SW/SE) by exact multi-day cumulative market
   curves (yield + CARE bonus compounding + daily COLLECT_FERTILIZER dividends
   minus feed & Fibonacci wages).
2. Concentric Barn-Ring Spatial Layout & On-Animal Storage: packs COOP/PASTURE
   barns into concentric inner rings around (4,4)-(5,5) across all 4 quadrants,
   storing MILK/WOOL/EGG directly on animals up to max_held to slash harvest
   labor and ride town-shop deficit pricing.
3. Closed-Loop Feed, Manure-Recycling & Smooth Monetizer: dynamically supplies
   feed via market WHEAT + outer-ring self-grown WHEAT, recycles surplus animal
   manure onto a strictly capped companion cash strip (<= 24 outer tiles of
   MELON/STRAWBERRY so rivals cannot free-ride cliff crops), and paces MILK/WOOL
   sales to avoid early above-I0 crashes.
4. Serpentine Quadrant-Chain Batch Scheduler: partitions ranch clusters along
   boustrophedon sweeps anchored to each quadrant's spawn shed tile, batching
   multi-item PICKUPs (WHEAT + ANIMAL + FERTILIZER) into dense zero-backtrack loops.
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
COMPANION_CROP_CAP = 24  # Strictly capped companion strip; ranch stays livestock-dominant!

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
ANIMAL_LIST = ("GOOSE", "COW", "SHEEP")
COMPANION_CROPS = ("MELON", "STRAWBERRY")
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


def _build_curves():
    lo, hi = MARKET_I0 - 5000, MARKET_I0 + 5000
    out = {}
    for item, (base, t_val, bf, bt, af, at) in MARKET_PARAMS.items():
        ab = bt * base / _shape(bf, t_val, t_val)
        aa = at * base / _shape(af, t_val, t_val)
        pxs = []
        pref = [0]
        acc = 0
        fl = hi
        hit = False
        for inv in range(lo, hi + 1):
            val = (base + ab * _shape(bf, MARKET_I0 - inv, t_val)) if inv < MARKET_I0 else (
                base - aa * _shape(af, inv - MARKET_I0, t_val)
            )
            p = max(1, int(round(val)))
            if not hit and inv >= MARKET_I0 and p == 1:
                fl = inv
                hit = True
            pxs.append(p)
            acc += p
            pref.append(acc)
        out[item] = (lo, hi, tuple(pxs), tuple(pref), fl, base, t_val, bf, ab, af, aa)
    return out


_CURVES = _build_curves()


def mprice(item: str, inv: int) -> int:
    lo, hi, pxs, _, _, base, t_val, bf, ab, af, aa = _CURVES[item]
    if lo <= inv <= hi:
        return pxs[inv - lo]
    val = (base + ab * _shape(bf, MARKET_I0 - inv, t_val)) if inv < MARKET_I0 else (
        base - aa * _shape(af, inv - MARKET_I0, t_val)
    )
    return max(1, int(round(val)))


def sell_cash(item: str, inv: int, qty: int) -> tuple[int, int]:
    if qty <= 0:
        return 0, inv
    lo, hi, _, pref, fl, *_ = _CURVES[item]
    if inv >= fl:
        return qty, inv
    mv = min(qty, fl - inv)
    if lo <= inv and inv + mv <= hi + 1:
        return pref[inv + mv - lo] - pref[inv - lo] + (qty - mv), inv + mv
    rev, cur = 0, inv
    for _ in range(qty):
        p = mprice(item, cur)
        rev += p
        if p > 1:
            cur += 1
    return rev, cur


def buy_cash(item: str, inv: int, qty: int) -> tuple[int, int]:
    if qty <= 0:
        return 0, inv
    lo, hi, _, pref, *_ = _CURVES[item]
    if inv - qty >= lo and inv <= hi + 1:
        return pref[inv - lo] - pref[inv - qty - lo], inv - qty
    cost, cur = 0, inv
    for _ in range(qty):
        cur -= 1
        cost += mprice(item, cur)
    return cost, cur


def fib_wages(n_hands: int) -> int:
    a, b, s = 1, 1, 0
    for _ in range(max(0, n_hands)):
        s += a
        a, b = b, a + b
    return s


def manhattan(a: tuple[int, int], b: tuple[int, int]) -> int:
    return abs(a[0] - b[0]) + abs(a[1] - b[1])


def shed_dist(p: tuple[int, int]) -> int:
    return min(manhattan(p, s) for s in SHED_TILES)


def nearest_shed(p: tuple[int, int]) -> tuple[int, int]:
    return min(SHED_TILES, key=lambda s: (manhattan(p, s), s))


def quadrant_of(x: int, y: int) -> str:
    return ("N" if y < 5 else "S") + ("W" if x < 5 else "E")


def walk_steps(a: tuple[int, int], b: tuple[int, int]) -> list[list[str]]:
    dx, dy = b[0] - a[0], b[1] - a[1]
    res = []
    if dx > 0:
        res.extend([["EAST"]] * dx)
    elif dx < 0:
        res.extend([["WEST"]] * (-dx))
    if dy > 0:
        res.extend([["SOUTH"]] * dy)
    elif dy < 0:
        res.extend([["NORTH"]] * (-dy))
    return res


def serpentine_key(pos: tuple[int, int]) -> tuple[int, int, int]:
    """Boustrophedon ordering within a quadrant radiating from the shed corner."""
    x, y = pos
    q = quadrant_of(x, y)
    lx = (4 - x) if q[1] == "W" else (x - 5)
    ly = (4 - y) if q[0] == "N" else (y - 5)
    col_key = lx if (ly % 2 == 0) else (4 - lx)
    return (ly, col_key, lx)


class RanchMarketBook:
    """Exact cumulative market curve evaluator for livestock, feed wheat, and manure."""

    def __init__(self, obs: dict, cur_day: int):
        self.day = cur_day
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

        self.town_daily = {}
        for it in PRODUCTS:
            arr = [0] * 30
            for d in range(cur_day, 30):
                fut = min(8 - self.n_shops, max(0, d // 3 - cur_day // 3))
                frac = 0.50 if d == cur_day else 1.0
                td = self.shop_demand[it] + self.exp_shop[it] * fut + (0.0 if it == "FERTILIZER" else 1.0)
                arr[d] = int(round(td * frac))
            self.town_daily[it] = arr

        self.own_sell = [Counter() for _ in range(30)]
        self.own_buy = [Counter() for _ in range(30)]
        self.rival_sell = [Counter() for _ in range(30)]
        self.daily_px = [dict() for _ in range(30)]
        self.recompute_all()

    def scan_rival_farm(self, rival_farm: dict):
        self.rival_sell = [Counter() for _ in range(30)]
        for row in rival_farm.get("tiles") or []:
            for tl in row:
                if not isinstance(tl, dict):
                    continue
                if "animal" in tl:
                    an = tl["animal"]
                    if an not in ANIMALS:
                        continue
                    ad = ANIMALS[an]
                    pday = int(tl.get("placed_day", self.day))
                    prod = ad["product"]
                    held = int(tl.get("yield_units", 0))
                    if held > 0:
                        self.rival_sell[min(LAST_DAY, self.day + 1)][prod] += held
                    for d in range(self.day, LAST_DAY):
                        self.rival_sell[min(LAST_DAY, d + 1)]["FERTILIZER"] += 1
                        dsf = (d + 1) - pday - ad["first"]
                        if dsf >= 0 and dsf % ad["interval"] == 0:
                            est_u = min(ad["max_held"], 1 + max(1, ad["interval"] - 1))
                            self.rival_sell[min(LAST_DAY, d + 1)][prod] += est_u
                elif tl.get("kind") == "PLANT":
                    crop = tl.get("crop")
                    if crop not in CROPS:
                        continue
                    cd = CROPS[crop]
                    pday = int(tl.get("planted_day", self.day))
                    held = int(tl.get("yield_units", 0))
                    if not cd["ongoing"]:
                        if pday + cd["first"] <= LAST_DAY:
                            hday = min(LAST_DAY, max(self.day, pday + cd["max_day"]))
                            self.rival_sell[min(LAST_DAY, hday + 1)][crop] += cd["max_yield"] if crop == "MELON" else max(held, cd["max_yield"] - 1)
                    else:
                        if held > 0 and self.day - pday >= cd["first"]:
                            self.rival_sell[min(LAST_DAY, self.day + 1)][crop] += held
                        for k in range(cd["max_yield"]):
                            yday = pday + cd["first"] + k * cd["interval"]
                            if self.day < yday <= LAST_DAY:
                                self.rival_sell[min(LAST_DAY, yday + 1)][crop] += 2
        self.recompute_all()

    def add_flows(self, sells: dict[int, Counter], buys: dict[int, Counter]):
        touched = set()
        for d, c in sells.items():
            if 0 <= d < 30:
                self.own_sell[d].update(c)
                touched.update(c.keys())
        for d, c in buys.items():
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
            sup = self.own_sell[d][it] + self.rival_sell[d][it]
            if sup > 0:
                rev, inv = sell_cash(it, inv, sup)
                self.daily_px[d][it] = rev / sup
            else:
                self.daily_px[d][it] = float(mprice(it, inv))

    def recompute_all(self):
        for it in PRODUCTS:
            self._recompute_item(it)

    def exact_annuity_net_cash(
        self,
        sells: dict[int, Counter],
        buys: dict[int, Counter],
        disc_rate: float,
        feed_cost_cap: float | None = None,
    ) -> float:
        """Exact discounted portfolio cash delta for an animal annuity or companion crop."""
        touched = set()
        for c in sells.values():
            touched.update(c.keys())
        for c in buys.values():
            touched.update(c.keys())

        delta = 0.0
        for it in touched:
            inv0 = self.base_inv[it]
            inv1 = self.base_inv[it]
            td_arr = self.town_daily[it]
            for d in range(self.day, 30):
                td = td_arr[d]
                dfac = (1.0 - disc_rate) ** max(0, d - self.day)
                base_b = self.own_buy[d][it]
                add_b = buys.get(d, Counter()).get(it, 0)

                inv0 -= td
                inv1 -= td
                if it == "WHEAT" and feed_cost_cap is not None:
                    if base_b > 0:
                        _, inv0 = buy_cash(it, inv0, base_b)
                    if add_b > 0:
                        c1_m, inv1 = buy_cash(it, inv1, add_b)
                        delta -= dfac * min(float(c1_m), add_b * feed_cost_cap)
                else:
                    c0, inv0 = buy_cash(it, inv0, base_b) if base_b > 0 else (0, inv0)
                    c1, inv1 = buy_cash(it, inv1, base_b + add_b) if (base_b + add_b) > 0 else (0, inv1)
                    delta -= dfac * (c1 - c0)

                base_o = self.own_sell[d][it]
                base_r = self.rival_sell[d][it]
                add_o = sells.get(d, Counter()).get(it, 0)

                tot0 = base_o + base_r
                if tot0 > 0:
                    r0, inv0 = sell_cash(it, inv0, tot0)
                    my0 = r0 * (base_o / tot0)
                else:
                    my0 = 0.0

                tot1 = base_o + add_o + base_r
                if tot1 > 0:
                    r1, inv1 = sell_cash(it, inv1, tot1)
                    my1 = r1 * ((base_o + add_o) / tot1)
                else:
                    my1 = 0.0

                delta += dfac * (my1 - my0)
        return delta


def simulate_animal_annuity(
    animal: str,
    start_day: int,
    tile: dict | None,
    book: RanchMarketBook,
    labor_unit: float,
    care_policy: int,
    feed_unit_cost: float,
    early_cash_mode: bool,
) -> dict | None:
    """Exact annuity schedule with on-animal max_held storage discipline."""
    ad = ANIMALS[animal]
    fresh = (tile is None) or ("animal" not in tile)
    pday = start_day if fresh else int(tile.get("placed_day", start_day))
    product = ad["product"]
    interval = ad["interval"]
    first = ad["first"]
    max_held = ad["max_held"]
    struct = ad["struct"]

    hungry = 0 if fresh else int(tile.get("consecutive_unfed", 0))
    bonus = 0 if fresh else int(tile.get("pending_care_bonus", 0))
    held = 0 if fresh else int(tile.get("yield_units", 0))
    fert_avail = False if fresh else bool(tile.get("fertilizer_available", False))

    prod_days = [
        d for d in range(start_day, LAST_DAY)
        if (d + 1 - pday - first) >= 0 and (d + 1 - pday - first) % interval == 0
    ]
    prod_set = set(prod_days)
    last_prod_end = prod_days[-1] if prod_days else -1

    schedule = {}
    sells = {}
    buys = {}
    total_ops = 0
    total_visits = 0
    any_output = False

    for d in range(start_day, LAST_DAY + 1):
        ops = []
        deliv = LAST_DAY if d == LAST_DAY else min(LAST_DAY, d + 1)

        if fresh and d == start_day:
            need_build = True
            if isinstance(tile, dict) and tile.get("kind") == struct and "animal" not in tile:
                need_build = False
            elif isinstance(tile, dict):
                ops.append(("DIG",))
            if need_build:
                ops.append(("BUILD_" + struct,))
            ops.append(("PLACE", animal))

        fed = (not fresh) and (d == start_day) and bool(tile.get("fed_today"))
        cared = (not fresh) and (d == start_day) and bool(tile.get("cared_today"))

        is_prod = d in prod_set
        has_future_prod_after = (last_prod_end > d)

        want_care = False
        if d < LAST_DAY - 1 and has_future_prod_after:
            nxt_p = next((pd for pd in prod_days if pd > d), LAST_DAY)
            before_first = d < (pday + first - 1)
            bonus_capped = before_first and (bonus >= max_held - 1)
            if not bonus_capped:
                if care_policy == 1:
                    want_care = True
                elif care_policy == 2:
                    px_next = book.daily_px[min(LAST_DAY, nxt_p + 1)][product]
                    if px_next > feed_unit_cost + 2.0 * labor_unit:
                        want_care = True

        need_feed = False
        if d < LAST_DAY:
            rem_dung_val = sum(max(0.0, book.daily_px[min(LAST_DAY, dd + 1)]["FERTILIZER"] - labor_unit)
                               for dd in range(d + 1, LAST_DAY + 1))
            worth_survive = (last_prod_end >= d) or (rem_dung_val > feed_unit_cost + labor_unit)
            if want_care:
                need_feed = True
            elif is_prod and bonus > 0:
                if bonus * book.daily_px[deliv][product] > feed_unit_cost + labor_unit:
                    need_feed = True
            elif hungry >= 1 and worth_survive:
                need_feed = True
            if fresh and d == start_day and not want_care and not is_prod:
                need_feed = False

        if need_feed and not fed:
            ops.append(("FEED",))
            buys.setdefault(d, Counter())["WHEAT"] += 1
            fed = True

        if want_care and fed and not cared:
            ops.append(("CARE",))
            cared = True

        # Store yield FREE on the animal until max_held binds (or early expansion cash / Day 28-29)
        if held > 0:
            next_gain = (1 + (bonus if fed else 0)) if is_prod else 0
            cap_binds = (held >= max_held) or (is_prod and held + next_gain > max_held)
            pull_early = early_cash_mode and d <= 12 and held >= max(3, max_held - 2)
            should_h = (d == LAST_DAY) or (d == LAST_DAY - 1 and held >= 3) or cap_binds or pull_early
            if should_h:
                ops.append(("HARVEST",))
                h_deliv = LAST_DAY if d == LAST_DAY else deliv
                sells.setdefault(h_deliv, Counter())[product] += held
                any_output = True
                held = 0

        if fert_avail:
            px_f = book.daily_px[deliv]["FERTILIZER"]
            if px_f > labor_unit + 2.0:
                ops.append(("COLLECT_FERTILIZER",))
                sells.setdefault(deliv, Counter())["FERTILIZER"] += 1
                any_output = True
                fert_avail = False

        if ops:
            schedule[d] = tuple(ops)
            total_ops += len(ops)
            total_visits += 1

        if d == LAST_DAY:
            break

        hungry = 0 if fed else hungry + 1
        if hungry >= 2:
            break
        if is_prod:
            held = min(max_held, held + 1 + (bonus if fed else 0))
            bonus = 0
        if cared and fed:
            bonus += 1
        fert_avail = True

    if not any_output:
        return None
    return {
        "animal": animal,
        "cost": ad["cost"] if fresh else 0,
        "schedule": schedule,
        "sells": sells,
        "buys": buys,
        "total_ops": total_ops,
        "total_visits": total_visits,
    }


def simulate_companion_crop(crop: str, start_day: int, tile: dict | None, use_free_fert: bool) -> dict | None:
    """Lifecycle for outer-ring feed WHEAT and manure-fed companion strip (MELON/STRAWBERRY)."""
    cd = CROPS[crop]
    fresh = tile is None
    pday = start_day if fresh else int(tile["planted_day"])
    if pday + cd["first"] > LAST_DAY:
        return None
    ongoing = cd["ongoing"]
    iv = cd["interval"]
    first = cd["first"]
    max_day = cd["max_day"]
    max_y = cd["max_yield"]
    win_start = (max_day + 1) // 2
    end_day = min(LAST_DAY, max(start_day, pday + first + (max_y - 1) * iv)) if ongoing else (
        min(LAST_DAY, pday + first) if crop == "MELON" else min(LAST_DAY, pday + max_day)
    )

    held = (0 if ongoing else 1) if fresh else int(tile.get("yield_units", 0))
    dry = 1 if fresh else int(tile.get("consecutive_unwatered", 0))
    until = -1 if fresh else int(tile.get("fertilized_until_day", -1))
    expiry = -1 if fresh else int(tile.get("max_lifespan_step", -1))

    schedule = {}
    sells = {}
    fert_use = {}
    total_ops = 0
    total_visits = 0
    any_out = False

    for d in range(start_day, end_day + 1):
        if expiry >= 0 and d * TURNS_PER_DAY >= expiry and d > start_day:
            break
        age = d - pday
        already_w = (not fresh) and (d == start_day) and bool(tile.get("watered_today"))
        ops = []
        if fresh and d == start_day:
            ops.append(("PLANT", crop))

        want_f = False
        if use_free_fert:
            if ongoing:
                nd = (d + 1) - pday - first
                if nd >= 0 and nd % iv == 0 and (nd // iv) < max_y and until < d:
                    want_f = True
            elif crop == "WHEAT" and age == win_start and until < d:
                want_f = True

        if ongoing:
            nd = (d + 1) - pday - first
            ptick = nd >= 0 and nd % iv == 0 and (nd // iv) < max_y
            need_w = (not already_w) and (dry >= 1 or want_f or (ptick and until >= d))
            if d >= end_day:
                need_w = False
                want_f = False
        else:
            in_w = win_start <= age <= max_day
            need_w = (not already_w) and (dry >= 1 or (in_w and held < max_y) or want_f)
            if d == end_day and held > 0 and not (in_w and held < max_y):
                need_w = False
                want_f = False

        if want_f:
            ops.append(("FERTILIZE",))
            fert_use[d] = 1
            until = max(until, d + 2)
            if not already_w:
                need_w = True

        watered = already_w or need_w
        if need_w:
            ops.append(("WATER",))
            if not ongoing and win_start <= age <= max_day:
                held = min(max_y, held + (2 if until >= d else 1))

        ripe = age >= first and held > 0
        do_h = False
        if ripe:
            if not ongoing:
                do_h = (d == end_day)
            else:
                nd = (d + 1) - pday - first
                nprod = nd >= 0 and nd % iv == 0 and (nd // iv) < max_y
                inc = 2 if (watered and until >= d) else 1
                do_h = (d == end_day) or (held >= max_y) or (nprod and held + inc > max_y)
        if do_h:
            ops.append(("HARVEST",))
            deliv = d if (crop == "MELON" or d == LAST_DAY) else min(LAST_DAY, d + 1)
            sells.setdefault(deliv, Counter())[crop] += held
            any_out = True
            held = 0

        if ops:
            schedule[d] = tuple(ops)
            total_ops += len(ops)
            total_visits += 1
        if do_h and not ongoing:
            break
        dry = 0 if watered else dry + 1
        if dry >= 2:
            break
        if ongoing:
            nd = (d + 1) - pday - first
            if nd >= 0 and nd % iv == 0:
                pc = nd // iv + 1
                if pc <= max_y:
                    held = min(max_y, held + (2 if (watered and until >= d) else 1))
                    if pc == max_y:
                        expiry = (d + 2) * TURNS_PER_DAY

    if not any_out:
        return None
    return {
        "crop": crop, "cost": cd["seed"] if fresh else 0,
        "schedule": schedule, "sells": sells, "fert_use": fert_use,
        "total_ops": total_ops, "total_visits": total_visits,
    }


def job_inputs(ops: tuple) -> Counter:
    c = Counter()
    for op in ops:
        if op[0] == "FEED":
            c["WHEAT"] += 1
        elif op[0] == "FERTILIZE":
            c["FERTILIZER"] += 1
        elif op[0] == "PLACE" and len(op) >= 2 and op[1] in ANIMALS:
            c[op[1]] += 1
    return c


def chain_steps(start: tuple[int, int], seq: list[int], jobs: list[dict], force_return: bool) -> tuple[int, Counter, int]:
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
        steps += manhattan(pos, j["pos"]) + len(j["ops"])
        pos = j["pos"]
    steps += sum(1 for v in ins.values() if v > 0)
    if outs > 0 and (force_return or rush >= 500.0):
        steps += shed_dist(pos) + 1
    return steps, ins, outs


def schedule_ranch_chains(
    jobs: list[dict],
    starts: list[tuple[int, int]],
    capacity: int,
    force_return: bool,
) -> tuple[list[list[int]], set[int], float]:
    """Quadrant-anchored serpentine chain scheduler with risk-first repair."""
    n_w = len(starts)
    if not jobs or n_w == 0:
        return [[] for _ in range(n_w)], set(), 0.0

    quad_jobs: dict[str, list[int]] = {"NW": [], "NE": [], "SW": [], "SE": []}
    for idx, j in enumerate(jobs):
        q = quadrant_of(j["pos"][0], j["pos"][1])
        quad_jobs[q].append(idx)
    for q in quad_jobs:
        quad_jobs[q].sort(key=lambda i: serpentine_key(jobs[i]["pos"]))

    worker_quads = [quadrant_of(s[0], s[1]) for s in starts]
    quad_workers: dict[str, list[int]] = {"NW": [], "NE": [], "SW": [], "SE": []}
    for w, q in enumerate(worker_quads):
        quad_workers[q].append(w)

    routes = [[] for _ in range(n_w)]
    unassigned = []

    for q in ("NW", "NE", "SW", "SE"):
        q_idxs = quad_jobs[q]
        if not q_idxs:
            continue
        ws = quad_workers[q]
        if not ws:
            unassigned.extend(q_idxs)
            continue
        w_ptr = 0
        for idx in q_idxs:
            placed = False
            for off in range(len(ws)):
                w = ws[(w_ptr + off) % len(ws)]
                cand = routes[w] + [idx]
                st, _, _ = chain_steps(starts[w], cand, jobs, force_return)
                if st <= capacity:
                    routes[w] = cand
                    placed = True
                    if st >= capacity - 4:
                        w_ptr = (w_ptr + off + 1) % len(ws)
                    break
            if not placed:
                unassigned.append(idx)

    unassigned.sort(key=lambda i: (1 if jobs[i]["risk"] else 0, jobs[i]["weight"]), reverse=True)
    still_unassigned = []
    for idx in unassigned:
        best = None
        for w in range(n_w):
            base_st, _, _ = chain_steps(starts[w], routes[w], jobs, force_return)
            for k in range(len(routes[w]) + 1):
                cand = routes[w][:k] + [idx] + routes[w][k:]
                st, _, _ = chain_steps(starts[w], cand, jobs, force_return)
                if st <= capacity:
                    delta = st - base_st + 0.025 * st
                    if best is None or delta < best[0]:
                        best = (delta, w, cand)
        if best is not None:
            routes[best[1]] = best[2]
        else:
            still_unassigned.append(idx)

    for idx in still_unassigned:
        if not jobs[idx]["risk"]:
            continue
        best_swap = None
        for w in range(n_w):
            for drop_k, drop_idx in enumerate(routes[w]):
                if jobs[drop_idx]["risk"]:
                    continue
                cand = routes[w][:drop_k] + routes[w][drop_k + 1:] + [idx]
                st, _, _ = chain_steps(starts[w], cand, jobs, force_return)
                if st <= capacity:
                    cost_evict = jobs[drop_idx]["weight"]
                    if best_swap is None or cost_evict < best_swap[0]:
                        best_swap = (cost_evict, w, cand)
        if best_swap is not None:
            routes[best_swap[1]] = best_swap[2]

    covered = set()
    score = 0.0
    for w in range(n_w):
        for idx in routes[w]:
            covered.add(idx)
            score += jobs[idx]["weight"]
    return routes, covered, score


class RanchCompounderAgent:
    """Style 2: Livestock Compounding, Feed-Logistics & Capital Scaling."""

    def __init__(self):
        self.reset()

    def reset(self):
        self.day = -1
        self.player = -1
        self.last_step = -1
        self.queues: list[deque] = []
        self.market_queue: deque = deque()
        self.start_hour = 2
        self.feed_reserve_wheat = 0
        self.manure_reserve_fert = 0
        self.smooth_caps: dict[str, int] = {}
        self.book: RanchMarketBook | None = None

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

        book = RanchMarketBook(obs, day)
        book.scan_rival_farm(rival)

        n_animals_existing = sum(
            1 for row in tiles for tl in row
            if isinstance(tl, dict) and "animal" in tl
        )
        n_plants_existing = sum(
            1 for row in tiles for tl in row
            if isinstance(tl, dict) and tl.get("kind") == "PLANT"
        )
        labor_unit = 3.2 + 0.03 * (n_animals_existing + n_plants_existing)
        disc_rate = max(0.0, 0.055 - 0.0025 * day)

        mkt_wheat_px = float(mprice("WHEAT", book.base_inv["WHEAT"]))
        feed_cost_cap = 15.0 if n_animals_existing >= 18 else mkt_wheat_px
        eff_feed_cost = min(mkt_wheat_px, feed_cost_cap)
        fert_px_now = float(mprice("FERTILIZER", book.base_inv["FERTILIZER"]))
        free_fert_mode = (n_animals_existing >= 14 and fert_px_now <= 58.0)

        early_cash_mode = (len(unlocked) < 4 or money < 3000)

        jobs = []
        open_slots = []
        total_daily_feed_need = 0
        existing_wheat_tiles = 0
        existing_companion_crops = 0

        # 1. Existing animals & companion/feed crops
        for y in range(BOARD_SIZE):
            for x in range(BOARD_SIZE):
                tl = tiles[y][x]
                pos = (x, y)
                if tl == "LOCKED":
                    continue
                if isinstance(tl, dict) and "animal" in tl:
                    an = tl["animal"]
                    d_pen = labor_unit * (0.95 + 0.26 * shed_dist(pos))
                    best_ann = None
                    best_val = -1e18
                    for cp in (1, 2, 0):
                        ann = simulate_animal_annuity(
                            an, day, tl, book, labor_unit, cp, eff_feed_cost, early_cash_mode
                        )
                        if ann is None:
                            continue
                        val = book.exact_annuity_net_cash(ann["sells"], ann["buys"], disc_rate, feed_cost_cap)
                        val -= labor_unit * ann["total_ops"] + d_pen * ann["total_visits"]
                        if val > best_val:
                            best_val = val
                            best_ann = ann
                    if best_ann is not None:
                        book.add_flows(best_ann["sells"], best_ann["buys"])
                        ops = best_ann["schedule"].get(day)
                        if ops:
                            ins = job_inputs(ops)
                            if ins["WHEAT"] > 0:
                                total_daily_feed_need += ins["WHEAT"]
                            deliv = LAST_DAY if day == LAST_DAY else min(LAST_DAY, day + 1)
                            out_u = sum(best_ann["sells"].get(deliv, Counter()).values()) if any(
                                op[0] in ("HARVEST", "COLLECT_FERTILIZER") for op in ops
                            ) else 0
                            imm = sum(
                                q * book.daily_px[deliv][it]
                                for it, q in best_ann["sells"].get(deliv, Counter()).items()
                            ) if out_u > 0 else 0.0
                            risk = bool(tl.get("consecutive_unfed", 0) >= 1 and any(op[0] == "FEED" for op in ops))
                            wt = imm + (0.75 * max(0.0, best_val) if risk else 0.08 * max(0.0, best_val)) + 15.0
                            if any(op[0] == "CARE" for op in ops):
                                wt += 25.0
                            jobs.append({
                                "pos": pos, "ops": ops, "inputs": ins,
                                "out_units": out_u, "rush_val": 0.0,
                                "weight": max(12.0, wt), "risk": risk,
                                "seed_crop": None, "new_animal": None,
                            })
                    continue

                if isinstance(tl, dict) and tl.get("kind") == "PLANT":
                    crop = tl["crop"]
                    cd = CROPS[crop]
                    pday = int(tl["planted_day"])
                    held = int(tl.get("yield_units", 0))
                    if cd["ongoing"] and held == 0 and (day >= pday + cd["first"] + (cd["max_yield"] - 1) * cd["interval"] or int(tl.get("max_lifespan_step", -1)) >= 0):
                        open_slots.append((pos, tl))
                        continue
                    if crop == "WHEAT":
                        existing_wheat_tiles += 1
                    else:
                        existing_companion_crops += 1
                    use_f = free_fert_mode or (crop == "STRAWBERRY" and n_animals_existing >= 8)
                    mc = simulate_companion_crop(crop, day, tl, use_free_fert=use_f)
                    if mc is not None:
                        if crop != "WHEAT":
                            book.add_flows(mc["sells"], {})
                        ops = mc["schedule"].get(day)
                        if ops:
                            ins = job_inputs(ops)
                            delivs = {d for d in (day, min(LAST_DAY, day + 1)) if d in mc["sells"]}
                            out_u = sum(sum(mc["sells"][dd].values()) for dd in delivs) if any(
                                op[0] == "HARVEST" for op in ops
                            ) else 0
                            imm = sum(
                                q * book.daily_px[dd][it] for dd in delivs for it, q in mc["sells"][dd].items()
                            ) if out_u > 0 else 0.0
                            risk = bool(tl.get("consecutive_unwatered", 0) >= 1 and any(op[0] == "WATER" for op in ops))
                            wt = imm + (90.0 if risk else 24.0)
                            jobs.append({
                                "pos": pos, "ops": ops, "inputs": ins,
                                "out_units": out_u, "rush_val": imm if crop == "MELON" else 0.0,
                                "weight": wt, "risk": risk,
                                "seed_crop": None, "new_animal": None,
                            })
                    continue

                open_slots.append((pos, tl))

        # 2. Aggressive Land Expansion (NE -> SW -> SE)
        shed_sale_val = sum(
            mprice(it, book.base_inv[it]) * int(cnt)
            for it, cnt in shed.items()
            if it in PRODUCTS and it != "WHEAT" and cnt > 0
        )
        wealth = money + shed_sale_val
        land_order = False
        new_spend = 0.0
        n_extra = len(unlocked) - 1
        if n_extra < len(LAND_ORDER) and day <= 20:
            lcost = LAND_PRICES[n_extra]
            close_free = sum(1 for pos, _ in open_slots if shed_dist(pos) <= 4)
            want_land = (
                (close_free <= 7 and wealth >= lcost + 550)
                or (len(open_slots) <= 10 and wealth >= lcost + 450)
            )
            if want_land:
                qn = LAND_ORDER[n_extra]
                for y in range(BOARD_SIZE):
                    for x in range(BOARD_SIZE):
                        if tiles[y][x] == "LOCKED" and quadrant_of(x, y) == qn:
                            open_slots.append(((x, y), None))
                land_order = True
                new_spend += lcost

        open_slots.sort(key=lambda e: (shed_dist(e[0]), 0 if e[1] is None else 1, e[0][1], e[0][0]))

        # 3. Early MELON/STRAWBERRY Manure-Companion Strip (strictly capped <= 24 tiles total!)
        #    Prevents rivals from getting 100k uncontested MELON+STRAWBERRY while keeping
        #    70-80% of the ranch dedicated to 40-58+ animals & feed wheat!
        animals_planned_today = 0
        remaining_slots = []
        if day <= 22 and open_slots:
            for pos, tl in open_slots:
                sd = shed_dist(pos)
                if sd >= 7 and (n_animals_existing + animals_planned_today) >= 34:
                    remaining_slots.append((pos, tl))
                    continue
                if len(jobs) >= 90:
                    remaining_slots.append((pos, tl))
                    continue
                d_pen = labor_unit * (0.95 + 0.26 * sd)
                wage_guard = fib_wages(min(15, max(5, (len(jobs) + 5) // 5)))
                net_feed_buy = max(0, total_daily_feed_need + 2 - int(shed.get("WHEAT", 0)) - existing_wheat_tiles)
                feed_guard = max(150.0, net_feed_buy * mkt_wheat_px)

                best_pick = None

                # Primary: Herd Compounding (GOOSE/COW/SHEEP)
                if animals_planned_today < 18:
                    for an in ANIMAL_LIST:
                        ad = ANIMALS[an]
                        acost = ad["cost"]
                        if day + ad["first"] > LAST_DAY:
                            continue
                        already_owned = max(0, int(shed.get(an, 0)) - sum(
                            1 for j in jobs if j["new_animal"] == an
                        ))
                        cash_cost = 0 if already_owned > 0 else acost
                        if wealth - new_spend < cash_cost + wage_guard + feed_guard:
                            continue
                        for cp in (1, 2):
                            ann = simulate_animal_annuity(
                                an, day, tl, book, labor_unit, cp, eff_feed_cost, early_cash_mode
                            )
                            if ann is None:
                                continue
                            cash_delta = book.exact_annuity_net_cash(ann["sells"], ann["buys"], disc_rate, feed_cost_cap)
                            work = labor_unit * ann["total_ops"] + d_pen * ann["total_visits"]
                            npv = cash_delta - cash_cost - work
                            score = npv / (1.0 + 0.00045 * cash_cost) if day <= 6 else npv
                            # Slight ranch specialization preference on inner ring
                            if sd <= 4:
                                score *= 1.08
                            if npv > 80.0 and (best_pick is None or score > best_pick[0]):
                                best_pick = (score, npv, "ANIMAL", an, ann, cash_cost)

                # Secondary: Capped Manure-Fed Companion Strip (MELON/STRAWBERRY <= COMPANION_CROP_CAP!)
                if existing_companion_crops < COMPANION_CROP_CAP and day <= 17:
                    for crop in COMPANION_CROPS:
                        cd = CROPS[crop]
                        if day + cd["first"] > LAST_DAY:
                            continue
                        if crop == "STRAWBERRY" and day < 3:
                            continue
                        sc = cd["seed"]
                        if wealth - new_spend < sc + wage_guard + feed_guard:
                            continue
                        use_f = (crop == "STRAWBERRY") and (free_fert_mode or n_animals_existing >= 6)
                        mc = simulate_companion_crop(crop, day, None, use_free_fert=use_f)
                        if mc is None:
                            continue
                        f_buys = {} if free_fert_mode else {dd: Counter({"FERTILIZER": q}) for dd, q in mc["fert_use"].items()}
                        cash_delta = book.exact_annuity_net_cash(mc["sells"], f_buys, disc_rate)
                        work = labor_unit * mc["total_ops"] + d_pen * mc["total_visits"] + (labor_unit * 1.5 if tl is not None else 0.0)
                        npv = cash_delta - sc - work
                        score = (npv * 0.90) / (1.0 + 0.00045 * sc) if day <= 6 else npv * 0.92
                        if npv > 180.0 and (best_pick is None or score > best_pick[0]):
                            best_pick = (score, npv, "CROP", crop, mc, sc)

                if best_pick is None:
                    remaining_slots.append((pos, tl))
                    continue

                _, npv, kind, label, plan_obj, cash_cost = best_pick
                ops = list(plan_obj["schedule"].get(day) or ())
                if not ops:
                    remaining_slots.append((pos, tl))
                    continue
                if kind == "CROP" and tl is not None:
                    ops.insert(0, ("DIG",))
                ops_t = tuple(ops)
                ins = job_inputs(ops_t)
                if ins["WHEAT"] > 0:
                    total_daily_feed_need += ins["WHEAT"]
                jobs.append({
                    "pos": pos, "ops": ops_t, "inputs": ins,
                    "out_units": 0, "rush_val": 0.0,
                    "weight": max(32.0, 0.22 * npv), "risk": False,
                    "seed_crop": label if kind == "CROP" else None,
                    "new_animal": label if kind == "ANIMAL" else None,
                })
                new_spend += cash_cost
                if kind == "ANIMAL":
                    animals_planned_today += 1
                    book.add_flows(plan_obj["sells"], plan_obj["buys"])
                else:
                    existing_companion_crops += 1
                    book.add_flows(plan_obj["sells"], {})
        else:
            remaining_slots = list(open_slots)

        # 4. Closed-Loop Feed-Wheat Belt (outer corners)
        total_herd = n_animals_existing + animals_planned_today
        if day <= 24 and total_herd >= 18 and mkt_wheat_px >= 31 and remaining_slots:
            target_wheat = min(18, max(5, int(total_herd * 0.28)))
            need_wheat = min(7, max(0, target_wheat - existing_wheat_tiles))
            remaining_slots.sort(key=lambda e: (-shed_dist(e[0]), 0 if e[1] is None else 1, e[0][1], e[0][0]))
            for pos, tl in remaining_slots:
                if need_wheat <= 0 or len(jobs) >= 96 or wealth - new_spend < 10 + 180:
                    break
                wf = simulate_companion_crop("WHEAT", day, None, use_free_fert=free_fert_mode)
                if wf is None:
                    continue
                ops = list(wf["schedule"].get(day) or ())
                if not ops:
                    continue
                if tl is not None:
                    ops.insert(0, ("DIG",))
                jobs.append({
                    "pos": pos, "ops": tuple(ops), "inputs": Counter(),
                    "out_units": 0, "rush_val": 0.0,
                    "weight": 35.0 + mkt_wheat_px, "risk": False,
                    "seed_crop": "WHEAT", "new_animal": None,
                })
                new_spend += 10
                need_wheat -= 1

        self.book = book
        self.feed_reserve_wheat = 0 if day >= LAST_DAY - 1 else min(58, max(total_daily_feed_need, total_herd))
        # Only retain organic manure for companion crops when manure is cheap AND cash is comfortable!
        self.manure_reserve_fert = (
            min(16, existing_companion_crops + existing_wheat_tiles)
            if (day < LAST_DAY - 1 and free_fert_mode and money >= 1800.0)
            else 0
        )
        self._compute_smooth_sell_caps(day, book)

        # 5. Crew Sizing via Quadrant Serpentine-Chain Scheduler (capped at 15 hands)
        is_final = (day == LAST_DAY)
        route_limit = 23 if is_final else 24
        best = None
        max_crew = min(16, max(4, len(jobs) + 1))

        for crew in range(1, max_crew + 1):
            wages = fib_wages(crew - 1)
            if crew > 2 and wages > wealth - 30:
                continue
            starts = self._spawn_positions(farm, crew)
            start_h = 2
            for _ in range(3):
                routes, covered, val_score = schedule_ranch_chains(
                    jobs, starts, route_limit - start_h, is_final
                )
                orders, expense, rev = self._build_opening_orders(
                    obs, jobs, covered, crew, land_order, day, book
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
            maint_jobs = [j for j in jobs if j["new_animal"] is None and j["seed_crop"] is None]
            for crew in range(1, 15):
                starts = self._spawn_positions(farm, crew)
                routes, covered, val_score = schedule_ranch_chains(
                    maint_jobs, starts, route_limit - 2, is_final
                )
                orders, expense, rev = self._build_opening_orders(
                    obs, maint_jobs, covered, crew, False, day, book
                )
                if expense > money + 0.98 * rev:
                    continue
                net = val_score - fib_wages(crew - 1)
                if best is None or net > best[0]:
                    best = (net, crew, starts, routes, covered, 2, orders)
                if len(covered) == len(maint_jobs):
                    break
            jobs = maint_jobs

        if best is None:
            starts = self._spawn_positions(farm, 1)
            routes, covered, _ = schedule_ranch_chains(jobs, starts, route_limit - 2, is_final)
            orders, _, _ = self._build_opening_orders(obs, jobs, covered, 1, False, day, book)
            best = (0.0, 1, starts, routes, covered, 2, orders)

        _, crew, starts, routes, covered, self.start_hour, orders = best
        self.market_queue = deque(orders)

        self.queues = []
        for w, seq in enumerate(routes):
            q = []
            ins = Counter()
            for idx in seq:
                ins.update(jobs[idx]["inputs"])
            for item in ("WHEAT", "FERTILIZER", "GOOSE", "COW", "SHEEP"):
                if ins[item] > 0:
                    q.append(["PICKUP", item, ins[item]])
            pos = starts[w]
            outs = 0
            rush_tot = 0.0
            for idx in seq:
                j = jobs[idx]
                q.extend(walk_steps(pos, j["pos"]))
                q.extend(list(op) for op in j["ops"])
                pos = j["pos"]
                outs += j["out_units"]
                rush_tot += j["rush_val"]
            if outs > 0 and (is_final or rush_tot >= 500.0 or outs >= 14):
                sh = nearest_shed(pos)
                ret = walk_steps(pos, sh) + [["DROP"]]
                if self.start_hour + len(q) + len(ret) <= route_limit:
                    q.extend(ret)
            self.queues.append(deque(q))

        self.day = day
        self.player = player

    def _compute_smooth_sell_caps(self, day: int, book: RanchMarketBook):
        """Smoothly pace uncontested MILK/WOOL/EGG sales; sell freely if rival contests the cliff."""
        self.smooth_caps = {}
        if day >= 26:
            for it in PRODUCTS:
                self.smooth_caps[it] = 999
            return
        for it in PRODUCTS:
            if it in ("MELON", "FERTILIZER"):
                self.smooth_caps[it] = 999
                continue
            rival_soon = sum(book.rival_sell[dd][it] for dd in range(day, min(30, day + 5)))
            if rival_soon >= 14:
                # Never hold while rival is actively supplying the same cliff!
                self.smooth_caps[it] = 999
                continue
            inv = book.base_inv[it]
            p_now = mprice(it, inv)
            p_fut = book.daily_px[min(LAST_DAY, day + 3)][it]
            if p_fut >= p_now * 1.03 and it in ("MILK", "WOOL", "EGG", "STRAWBERRY"):
                cap = 0
                cur = inv
                floor_px = max(1, int(p_now * 0.90))
                while cap < 60 and mprice(it, cur) >= floor_px:
                    if it in ("MILK", "WOOL") and cur >= MARKET_I0 + 12 and day <= 21:
                        break
                    cur += 1
                    cap += 1
                self.smooth_caps[it] = max(0, cap)
            else:
                self.smooth_caps[it] = 999

    def _build_opening_orders(
        self,
        obs: dict,
        jobs: list[dict],
        covered: set[int],
        crew: int,
        land_order: bool,
        day: int,
        book: RanchMarketBook,
    ) -> tuple[list[list], float, float]:
        farm = obs["farms"][obs["player"]]
        priv = obs["private"]
        shed = dict(priv.get("shed") or {})
        seeds = dict(priv.get("seeds") or {})
        money = float(farm["money"])
        inv = dict(obs["market"]["inventory"])

        req_inputs = Counter()
        req_seeds = Counter()
        expected_out = 0
        for idx in covered:
            req_inputs.update(jobs[idx]["inputs"])
            if jobs[idx]["seed_crop"]:
                req_seeds[jobs[idx]["seed_crop"]] += 1
            expected_out += jobs[idx]["out_units"]

        hires_need = max(0, crew - 1 - len(farm.get("hands") or []))
        hires_done = int(farm.get("hires_today", 0))
        wage_c = fib_wages(hires_done + hires_need) - fib_wages(hires_done)
        land_c = LAND_PRICES[len(farm["unlocked_quadrants"]) - 1] if (land_order and len(farm["unlocked_quadrants"]) < 4) else 0
        anim_buys = {an: max(0, req_inputs[an] - int(shed.get(an, 0))) for an in ANIMAL_LIST}
        anim_c = sum(ANIMALS[an]["cost"] * q for an, q in anim_buys.items())
        prod_buys = {it: max(0, req_inputs[it] - int(shed.get(it, 0))) for it in ("WHEAT", "FERTILIZER")}
        prod_c = sum(buy_cash(it, inv[it], q)[0] for it, q in prod_buys.items() if q > 0)
        seed_buys = {c: max(0, q - int(seeds.get(c, 0))) for c, q in req_seeds.items()}
        seed_c = sum(CROPS[c]["seed"] * q for c, q in seed_buys.items())
        total_need = wage_c + land_c + anim_c + prod_c + seed_c + 40.0

        orders = []
        expense = 0.0
        revenue = 0.0
        held_rem = {}
        shed_after = dict(shed)

        sell_seq = ("MELON", "WOOL", "MILK", "STRAWBERRY", "EGG", "FERTILIZER", "TOMATO", "CARROT", "WHEAT")
        for it in sell_seq:
            avail = int(shed.get(it, 0))
            if it == "WHEAT":
                hard_keep = req_inputs["WHEAT"]
                soft_keep = max(0, self.feed_reserve_wheat - hard_keep)
            elif it == "FERTILIZER":
                hard_keep = req_inputs["FERTILIZER"]
                soft_keep = max(0, self.manure_reserve_fert - hard_keep)
            else:
                hard_keep = 0
                soft_keep = 0
            sellable = max(0, avail - hard_keep)
            if sellable <= 0:
                continue
            cap = self.smooth_caps.get(it, 999)
            qty = min(max(0, sellable - soft_keep), cap)
            if sellable > qty:
                held_rem[it] = sellable - qty
            if qty > 0:
                orders.append(["SELL", it, qty])
                rev, inv[it] = sell_cash(it, inv[it], qty)
                revenue += rev
                shed_after[it] = shed_after.get(it, 0) - qty

        incoming_buys = sum(anim_buys.values()) + sum(prod_buys.values())
        shed_used_now = sum(max(0, int(v)) for v in shed_after.values()) + incoming_buys
        room_needed = max(0, shed_used_now + min(expected_out, 65) - (SHED_CAPACITY - 6))
        cash_short = max(0.0, total_need - (money + revenue))
        if (cash_short > 0 or room_needed > 0) and held_rem:
            for it in ("FERTILIZER", "EGG", "STRAWBERRY", "MILK", "WOOL", "WHEAT"):
                if it not in held_rem or (cash_short <= 0 and room_needed <= 0):
                    continue
                q_avail = held_rem[it]
                px = max(1, mprice(it, inv[it]))
                need_q = max(room_needed, int(math.ceil(cash_short / px)) if cash_short > 0 else 0)
                take = min(q_avail, max(1, need_q))
                orders.append(["SELL", it, take])
                rev, inv[it] = sell_cash(it, inv[it], take)
                revenue += rev
                cash_short -= rev
                room_needed -= take

        for _ in range(hires_need):
            orders.append(["HIRE"])
        expense += wage_c

        if land_order and land_c > 0:
            orders.append(["BUY_LAND"])
            expense += land_c

        for an in ANIMAL_LIST:
            qty = anim_buys[an]
            if qty > 0:
                orders.append(["BUY_ANIMAL", an, qty])
                expense += ANIMALS[an]["cost"] * qty

        for it in ("WHEAT", "FERTILIZER"):
            qty = prod_buys[it]
            if qty > 0:
                orders.append(["BUY_PRODUCT", it, qty])
                c, inv[it] = buy_cash(it, inv[it], qty)
                expense += c

        for c, qty in seed_buys.items():
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
                                key=lambda k: mprice(k, int(obs["market"]["inventory"].get(k, MARKET_I0))) if k in PRODUCTS else 0,
                            )
                            put = min(room, int(uinv[best_it]))
                            unit_acts.append(["PLACE", best_it, put])
                            shed_used += put
                        else:
                            unit_acts.append(["PASS"])
                else:
                    mv = walk_steps(pos, nearest_shed(pos))
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
                                key=lambda k: mprice(k, int(obs["market"]["inventory"].get(k, MARKET_I0))) if k in PRODUCTS else 0,
                            )
                            put = min(room, int(uinv[best_it]))
                            unit_acts.append(["PLACE", best_it, put])
                            shed_used += put
                        else:
                            unit_acts.append(["PASS"])
                else:
                    unit_acts.append(self.queues[idx].popleft())
            elif carry > 0 and (day == LAST_DAY or carry >= 12):
                if pos in SHED_SET:
                    if shed_used + carry <= SHED_CAPACITY:
                        unit_acts.append(["DROP"])
                        shed_used += carry
                    else:
                        room = max(0, SHED_CAPACITY - shed_used)
                        if room > 0:
                            best_it = max(
                                (k for k, v in uinv.items() if int(v) > 0),
                                key=lambda k: mprice(k, int(obs["market"]["inventory"].get(k, MARKET_I0))) if k in PRODUCTS else 0,
                            )
                            put = min(room, int(uinv[best_it]))
                            unit_acts.append(["PLACE", best_it, put])
                            shed_used += put
                        else:
                            unit_acts.append(["PASS"])
                else:
                    mv = walk_steps(pos, nearest_shed(pos))
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

            for it in ("MELON", "WOOL", "MILK", "STRAWBERRY", "EGG", "FERTILIZER", "TOMATO", "CARROT", "WHEAT"):
                if len(orders) >= 10:
                    break
                avail = post_shed[it]
                keep = pickup_res[it]
                if not force_clear:
                    if it == "WHEAT":
                        keep = max(keep, self.feed_reserve_wheat)
                    elif it == "FERTILIZER":
                        keep = max(keep, self.manure_reserve_fert)
                sellable = max(0, avail - keep)
                cap = 999 if force_clear else self.smooth_caps.get(it, 999)
                qty = min(sellable, cap)
                if qty > 0:
                    orders.append(["SELL", it, qty])
                    post_shed[it] -= qty

            if hour >= 22 and day < LAST_DAY and len(orders) < 10:
                over = sum(post_shed.values()) + eod_carry - (SHED_CAPACITY - 2)
                if over > 0:
                    for it in ("FERTILIZER", "EGG", "MILK", "WOOL", "STRAWBERRY", "MELON", "WHEAT"):
                        if over <= 0 or len(orders) >= 10:
                            break
                        avail = max(0, post_shed[it] - pickup_res[it])
                        if avail > 0:
                            take = min(avail, over)
                            orders.append(["SELL", it, take])
                            post_shed[it] -= take
                            over -= take

        return self._sanitize(unit_acts[0], unit_acts[1:], orders, n_hands)


class Agent(RanchCompounderAgent):
    pass


_INSTANCES: dict[int, RanchCompounderAgent] = {}


def act(observation: dict) -> dict:
    p = int(observation["player"])
    ag = _INSTANCES.get(p)
    if ag is None or int(observation.get("step", 0)) == 0:
        ag = RanchCompounderAgent()
        _INSTANCES[p] = ag
    return ag.act(observation)
