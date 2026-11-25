# -*- coding: utf-8 -*-
"""Kaggriculture farm agent.

Layers:
  * market model: exact price curve, prefix-sum revenue tables, town-consumption
    forecast (open shops + expected future unlocks), projected supply of both farms.
    The opponent is read from its public tiles (fertilising / care habits inferred)
    and is assumed to replant harvested crops.
  * economy: daily greedy selection of animal / crop / land projects by discounted
    marginal value. Values include own cannibalisation, feed-wheat trade, a
    workload-based marginal labour cost (Fibonacci wages), a tile shadow price for
    long-occupying crops, and a relative term: revenue taken from the opponent counts.
  * jobs: per-tile op lists with money values derived from live tile state + plan
  * routing: value-density cheapest insertion over all units with shed pickups, 2-opt;
    critical jobs (feeding at-risk animals, survival watering) are never dropped.
  * hiring: simulate routes for candidate crew sizes, maximise value minus wage
  * executor: validated op execution with an in-step shadow state; unloads never
    exceed shed room (overflow is destroyed by the game).
  * seller: trend-aware selling, end-of-day capacity planning, ripe melons rushed to
    market first, staged end-game dump.
"""
import math
from collections import defaultdict

N = 10
TPD = 24
LAST_STEP = 718
LAST_DAY = 29
SHED = ((4, 4), (5, 4), (4, 5), (5, 5))
SHED_SET = frozenset(SHED)
CROPS = {
    'WHEAT': {'seed': 10, 'fy': 2, 'myd': 4, 'iv': 0, 'my': 6, 'on': False},
    'CARROT': {'seed': 20, 'fy': 2, 'myd': 3, 'iv': 0, 'my': 4, 'on': False},
    'TOMATO': {'seed': 50, 'fy': 8, 'myd': 8, 'iv': 1, 'my': 4, 'on': True},
    'STRAWBERRY': {'seed': 100, 'fy': 10, 'myd': 10, 'iv': 2, 'my': 4, 'on': True},
    'MELON': {'seed': 80, 'fy': 10, 'myd': 12, 'iv': 0, 'my': 6, 'on': False},
}
ANIMALS = {
    'GOOSE': {'cost': 300, 'st': 'COOP', 'fy': 4, 'iv': 1, 'mh': 4, 'pr': 'EGG'},
    'COW': {'cost': 400, 'st': 'PASTURE', 'fy': 8, 'iv': 2, 'mh': 6, 'pr': 'MILK'},
    'SHEEP': {'cost': 500, 'st': 'PASTURE', 'fy': 6, 'iv': 3, 'mh': 6, 'pr': 'WOOL'},
}
PRODUCTS = ['WHEAT', 'CARROT', 'TOMATO', 'STRAWBERRY', 'MELON', 'EGG', 'MILK', 'WOOL', 'FERTILIZER']
MP = {
    'WHEAT': (25, 400, 'sqrt', 0.8, 'log', 0.2),
    'CARROT': (35, 450, 'hinge', 1.0, 'sqrt', 0.7),
    'TOMATO': (60, 200, 'hinge', 0.4, 'sqrt', 0.6),
    'STRAWBERRY': (120, 100, 'sqrt', 0.7, 'linear', 1.6),
    'MELON': (250, 300, 'log', 0.2, 'sq', 3.6),
    'EGG': (50, 332, 'hinge', 0.4, 'log', 0.2),
    'MILK': (160, 122, 'sqrt', 0.6, 'linear', 1.6),
    'WOOL': (200, 105, 'log', 0.2, 'sq', 3.2),
    'FERTILIZER': (100, 200, 'linear', 0.4, 'linear', 0.4),
}
I0 = 10000
SHOPS = {
    'BAKERY': ['EGG', 'WHEAT'], 'PIZZA_SHOP': ['MILK', 'TOMATO', 'WHEAT'],
    'BRUNCH_SPOT': ['EGG', 'WHEAT', 'STRAWBERRY'], 'YARN_STORE': ['WOOL'],
    'ICE_CREAM_SHOP': ['STRAWBERRY', 'MILK', 'WHEAT'], 'PET_CAFE': ['CARROT'],
    'SMOOTHIE_SHOP': ['STRAWBERRY', 'MILK'],
    'FARMERS_MARKET': ['WHEAT', 'CARROT', 'TOMATO', 'STRAWBERRY'],
}
LAND_PRICES = [1000, 2000, 4000]
LAND_ORDER = ['NE', 'SW', 'SE']

# Tuning table (values chosen by paired multi-seed self-tests and sparring).
PARAM = {
    'disc_early': 0.07,        # daily discount on day 0 (cash compounds early) ...
    'disc_slope': 0.004,       # ... shrinking by this much per day
    'labor_cost': 7.0,         # per-action cost used when filtering low-value jobs
    'min_value': 40.0,         # smallest project value worth starting
    'land_margin': 1.3,        # buy a quadrant only if filling it beats price * margin
    'max_hands': 15,
    'hire_floor': 3,
    'sell_band': 0.10,         # max price drop accepted in one smoothed sale
    'harvest_min': 99,         # animal harvest threshold (99 = only when cap would bind)
    'harvest_early_day': -1,
    'smooth_days': 3,
    'opening': [],             # optional forced day-0 projects (empty = planner decides)
    'labor_scale': 1.0,        # scale on workload-based marginal labour cost
    'trend_dump': -0.02,       # projected 3-day price change below which we dump
    'same_day_drop': True,
    'drop_min_value': 400.0,
    'lastday_drop_load': 12,   # day 29: unload whenever this many goods are carried
    'drop_slack': 0,
    'fert_wheat': False,
    'tile_lambda': 0.5,        # weight of the tile shadow price
    'tile_horizon': 5,
    'rel_w': 0.75,             # weight of opponent revenue reduction in valuations
    'opp_replant': 0.7,        # expected share of opponent tiles replanted after harvest
    'melon_rush': True,
}


def _shape(f, x, T):
    if x < 0:
        x = 0.0
    if f == 'linear':
        return x
    if f == 'sq':
        return x * x
    if f == 'sqrt':
        return math.sqrt(x)
    if f == 'log':
        return math.log(1.0 + x)
    if f == 'hinge':
        u = x / T
        return u + 8.0 * max(0.0, u - 1.0) ** 2
    return x


_AMP = {}
for _it, _p in MP.items():
    _AMP[_it] = (_p[3] * _p[0] / _shape(_p[2], _p[1], _p[1]), _p[5] * _p[0] / _shape(_p[4], _p[1], _p[1]))


def mprice(item, inv):
    p = MP[item]
    if inv < I0:
        v = p[0] + _AMP[item][0] * _shape(p[2], I0 - inv, p[1])
    else:
        v = p[0] - _AMP[item][1] * _shape(p[4], inv - I0, p[1])
    return max(1, int(round(v)))


PS_LO = I0 - 4000
PS_HI = I0 + 4000
PSUM = {}
for _it in PRODUCTS:
    _arr = [0.0]
    _s = 0.0
    for _i in range(PS_LO, PS_HI + 1):
        _s += mprice(_it, _i)
        _arr.append(_s)
    PSUM[_it] = _arr


def _ps(it, x):
    a = PSUM[it]
    k = x - PS_LO
    if k <= 0:
        return k * mprice(it, PS_LO)
    hi = len(a) - 1
    if k >= hi:
        return a[hi] + (k - hi) * 1.0
    i = int(k)
    f = k - i
    return a[i] + f * (a[i + 1] - a[i])


def cum_rev(it, inv, n):
    if n <= 0:
        return 0.0
    return _ps(it, inv + n) - _ps(it, inv)


def cum_cost(it, inv, n):
    if n <= 0:
        return 0.0
    return _ps(it, inv - 1) - _ps(it, inv - 1 - n)


def fib(k):
    a, b = 1, 1
    for _ in range(k):
        a, b = b, a + b
    return a


def hire_total(h):
    return fib(h + 1) - 1


def dist(a, b):
    return abs(a[0] - b[0]) + abs(a[1] - b[1])


def shed_dist(p):
    return min(dist(p, s) for s in SHED)


def nearest_shed(p):
    return min(SHED, key=lambda s: (dist(p, s), s))


def quad_of(x, y):
    return ('N' if y < 5 else 'S') + ('W' if x < 5 else 'E')


def step_toward(p, t):
    dx = t[0] - p[0]
    dy = t[1] - p[1]
    if dx == 0 and dy == 0:
        return 'PASS'
    if abs(dx) >= abs(dy):
        return 'EAST' if dx > 0 else 'WEST'
    return 'SOUTH' if dy > 0 else 'NORTH'


def G(o, k, d=None):
    try:
        v = o[k]
    except Exception:
        v = getattr(o, k, d)
    return d if v is None else v


def is_animal(t):
    return isinstance(t, dict) and 'animal' in t


def is_plant(t):
    return isinstance(t, dict) and t.get('kind') == 'PLANT'


def is_weed(t):
    return isinstance(t, dict) and t.get('kind') == 'WEED'


def is_struct(t):
    return isinstance(t, dict) and t.get('kind') in ('COOP', 'PASTURE') and 'animal' not in t


# ---------------------------------------------------------------------------
# production timelines
# ---------------------------------------------------------------------------

def animal_prod_today(t, day):
    a = ANIMALS[t['animal']]
    dsf = day + 1 - t['placed_day'] - a['fy']
    return dsf >= 0 and dsf % a['iv'] == 0


def animal_units(a, placed_day, today, pend, care=True):
    """[(end_day, units)] produced at end of days today..28 with daily feed (+care)."""
    ad = ANIMALS[a]
    out = []
    for e in range(today, LAST_DAY):
        dsf = e + 1 - placed_day - ad['fy']
        if dsf >= 0 and dsf % ad['iv'] == 0:
            out.append((e, min(ad['mh'], 1 + pend)))
            pend = 0
        if care:
            pend += 1
    return out


def next_animal_prod(t, day):
    a = ANIMALS[t['animal']]
    for e in range(day, LAST_DAY):
        dsf = e + 1 - t['placed_day'] - a['fy']
        if dsf >= 0 and dsf % a['iv'] == 0:
            return e
    return None


def ongoing_prod_days(planted_day, crop):
    cd = CROPS[crop]
    return [planted_day + cd['fy'] - 1 + k * cd['iv'] for k in range(cd['my'])]


def ongoing_prod_today(t, day):
    cd = CROPS[t['crop']]
    dsf = day + 1 - t['planted_day'] - cd['fy']
    if dsf < 0 or dsf % cd['iv'] != 0:
        return False
    return dsf // cd['iv'] + 1 <= cd['my']


HARVEST_AGE = {'WHEAT': 4, 'CARROT': 3, 'MELON': 10}
NOMINAL_YIELD = {'WHEAT': 4, 'CARROT': 3, 'MELON': 6}
CROP_ACTIONS = {'WHEAT': 6, 'CARROT': 5, 'MELON': 10, 'STRAWBERRY': 14, 'TOMATO': 13}


def crop_units(crop, d, fert=True):
    """[(sell_day, units)] for a crop planted on day d under my policy."""
    cd = CROPS[crop]
    out = []
    if cd['on']:
        per = 2 if fert else 1
        for e in ongoing_prod_days(d, crop):
            if e <= LAST_DAY - 1:
                out.append((min(LAST_DAY, e + 1), per))
        return out
    h = d + HARVEST_AGE[crop]
    if h <= LAST_DAY:
        y = NOMINAL_YIELD[crop]
        if crop == 'WHEAT' and fert and PARAM['fert_wheat']:
            y = 6
        return [(min(LAST_DAY, h + 1), y)]
    if d + cd['fy'] <= LAST_DAY:
        ws = (cd['myd'] + 1) // 2
        waters = max(0, LAST_DAY - (d + ws) + 1)
        return [(LAST_DAY, min(cd['my'], 1 + waters))]
    return []


CROP_STEPS = {'WHEAT': (11.0, 5), 'CARROT': (9.0, 4), 'MELON': (18.0, 11), 'STRAWBERRY': (26.0, 17),
              'TOMATO': (23.0, 12)}
ANIMAL_STEPS = 5.0


def step_profile(kind, name, day):
    prof = [0.0] * 31
    if kind == 'A':
        for t in range(day, LAST_DAY):
            prof[t] += ANIMAL_STEPS
        prof[LAST_DAY] += 2.0
        return prof
    tot, span = CROP_STEPS[name]
    end = min(LAST_DAY, day + span - 1)
    n = end - day + 1
    for t in range(day, end + 1):
        prof[t] += tot / n
    return prof


CROP_OCC = {'WHEAT': 5, 'CARROT': 4, 'MELON': 11, 'STRAWBERRY': 17, 'TOMATO': 12}


def occupancy(kind, name, day):
    if kind == 'A':
        return max(1, LAST_DAY - day)
    return max(1, min(LAST_DAY, day + CROP_OCC[name]) - day)


def marginal_step_cost(w):
    if w <= 22.0:
        return 0.2
    h = int(math.ceil((w - 22.0) / 21.0))
    return fib(h) / 21.0 + 0.2


def forecast_consumption(shops, day):
    cons = {p: [0.0] * 31 for p in PRODUCTS}
    for t in range(day, 30):
        for p in PRODUCTS:
            if p != 'FERTILIZER':
                cons[p][t] += 1.0
        for s in shops:
            items = SHOPS.get(s, [])
            m = 12.0 if len(items) == 1 else 6.0
            for p in items:
                cons[p][t] += m
    n_open = len(shops)
    for k in range(n_open + 1, 9):
        u = 3 * k
        for s, items in SHOPS.items():
            m = (12.0 if len(items) == 1 else 6.0) / 8.0
            for p in items:
                for t in range(max(u, day), 30):
                    cons[p][t] += m
    return cons


def wheat_trade(prod, feed, stock0, day):
    """Net wheat market flow per day (positive = sell, negative = buy) given my
    production and feed needs; keeps a small buffer for tomorrow's feed."""
    out = [0.0] * 31
    stock = float(stock0)
    for t in range(day, 30):
        stock += prod[t]
        need = feed[t]
        if stock < need:
            out[t] -= need - stock
            stock = need
        stock -= need
        nxt = feed[t + 1] if t + 1 < 30 else 0.0
        if stock > nxt + 2:
            out[t] += stock - nxt - 2
            stock = nxt + 2
    if stock > 0:
        out[LAST_DAY] += stock
    return out


class Market(object):
    """Projection of each product market over the remaining days."""

    def __init__(self, day, inv, shops, disc):
        self.day = day
        self.inv = dict(inv)
        self.cons = forecast_consumption(shops, day)
        self.my = {p: [0.0] * 31 for p in PRODUCTS}
        self.opp = {p: [0.0] * 31 for p in PRODUCTS}
        self.disc = disc
        self.wprod = [0.0] * 31
        self.wfeed = [0.0] * 31
        self.wstock = 0
        self.rel_w = PARAM['rel_w']

    def revenue(self, p, extra=None, flow=None):
        I = float(self.inv.get(p, I0))
        tot = 0.0
        my = flow if flow is not None else self.my[p]
        opp = self.opp[p]
        cons = self.cons[p]
        disc = self.disc
        for t in range(self.day, 30):
            m = my[t]
            if extra is not None:
                m += extra[t]
            o = opp[t]
            if m < 0:
                tot -= cum_cost(p, I, -m) * disc[t]
                I += m
                m = 0.0
            if o < 0:
                I += o
                o = 0.0
            n = m + o
            if n > 0:
                r = cum_rev(p, I, n)
                if m > 0:
                    tot += r * (m / n) * disc[t]
                if o > 0 and self.rel_w > 0:
                    tot -= self.rel_w * r * (o / n) * disc[t]
                I += n
            I -= cons[t]
        return tot

    def wheat_revenue(self, dprod=None, dfeed=None):
        prod = self.wprod
        feed = self.wfeed
        if dprod is not None:
            prod = [a + b for a, b in zip(prod, dprod)]
        if dfeed is not None:
            feed = [a + b for a, b in zip(feed, dfeed)]
        flow = wheat_trade(prod, feed, self.wstock, self.day)
        return self.revenue('WHEAT', flow=flow)

    def price_path(self, p):
        I = float(self.inv.get(p, I0))
        out = []
        for t in range(self.day, 30):
            out.append(mprice(p, int(I)))
            I += self.my[p][t] + self.opp[p][t] - self.cons[p][t]
        return out


class Unit(object):
    __slots__ = ('idx', 'pos', 'inv', 'avail')

    def __init__(self, idx, pos, inv, avail):
        self.idx = idx
        self.pos = pos
        self.inv = inv
        self.avail = avail


class Brain(object):
    RAISE = False

    def __init__(self):
        self.reset(-1)

    def reset(self, me):
        self.me = me
        self.last_step = -1
        self.plan_day_done = -1
        self.routes = {}
        self.plant_plan = {}
        self.place_plan = {}
        self.plan_value = {}
        self.queue = []
        self.hires_planned = 0
        self.last_hand_count = 0
        self.replan_flag = True
        self.wait = {}
        self.prev_minv = None
        self.prev_shops = None
        self.my_last_sales = defaultdict(int)
        self.opp_sold = defaultdict(float)
        self.wheat_reserve = 0
        self.pval = {p: float(MP[p][0]) for p in PRODUCTS}
        self.wheat_price = 25.0
        self.last_replan_step = -1
        self.drop_mode = False
        self.pending_buys = []
        self.trend = {}
        self.cap_tight = False
        self.crit_set = set()
        self.last_hires = 3
        self.lam = 0.0
        self.kappa = 0.0

    # ------------------------------------------------------------------
    def parse(self, obs):
        S = {}
        S['step'] = int(G(obs, 'step', 0))
        S['me'] = int(G(obs, 'player', 0))
        S['day'] = S['step'] // TPD
        S['hour'] = S['step'] % TPD
        farms = G(obs, 'farms', [])
        me = S['me']
        farm = farms[me]
        S['opp'] = farms[1 - me] if len(farms) > 1 else None
        S['money'] = float(G(farm, 'money', 0))
        S['tiles'] = G(farm, 'tiles', [])
        S['farmer'] = tuple(G(farm, 'farmer', [4, 4]))
        S['hands'] = [tuple(h) for h in (G(farm, 'hands', []) or [])]
        S['unlocked'] = list(G(farm, 'unlocked_quadrants', ['NW']) or ['NW'])
        S['hires_today'] = int(G(farm, 'hires_today', 0) or 0)
        mk = G(obs, 'market', {})
        S['minv'] = dict(G(mk, 'inventory', {}) or {})
        S['prices'] = dict(G(mk, 'prices', {}) or {})
        town = G(obs, 'town', {})
        S['shops'] = list(G(town, 'unlocked_shops', []) or [])
        pv = G(obs, 'private', {})
        S['shed'] = dict(G(pv, 'shed', {}) or {})
        S['seeds'] = dict(G(pv, 'seeds', {}) or {})
        S['invs'] = [dict(i or {}) for i in (G(pv, 'inventories', []) or [])]
        S['overage'] = float(G(obs, 'remainingOverageTime', 60.0) or 60.0)
        unl = []
        tl = S['tiles']
        for y in range(N):
            row = tl[y]
            for x in range(N):
                if row[x] != 'LOCKED':
                    unl.append((x, y))
        S['unl'] = unl
        S['unl_set'] = set(unl)
        return S

    # ------------------------------------------------------------------
    def act(self, obs):
        S = self.parse(obs)
        if S['step'] <= self.last_step or S['me'] != self.me:
            self.reset(S['me'])
        self.last_step = S['step']
        n_hands = len(S['hands'])
        try:
            self.track_opponent(S)
        except Exception:
            if self.RAISE:
                raise
        unit_actions = None
        try:
            if self.plan_day_done != S['day']:
                self.plan_day(S)
                self.plan_day_done = S['day']
                self.replan_flag = True
            if n_hands != self.last_hand_count:
                self.replan_flag = True
            self.last_hand_count = n_hands
            if self.replan_flag or self.need_replan(S):
                self.build_all_routes(S)
                self.replan_flag = False
                self.last_replan_step = S['step']
            unit_actions = self.execute(S)
        except Exception:
            if self.RAISE:
                raise
            unit_actions = None
        if unit_actions is None or len(unit_actions) != 1 + n_hands:
            unit_actions = [['PASS'] for _ in range(1 + n_hands)]
        try:
            market = self.market_orders(S)
        except Exception:
            if self.RAISE:
                raise
            market = []
        return {'farmer': unit_actions[0], 'hands': unit_actions[1:], 'market': market[:10]}

    def disc_series(self, day):
        r = max(0.0, PARAM['disc_early'] - PARAM['disc_slope'] * day)
        out = [1.0] * 31
        for t in range(day, 31):
            out[t] = 1.0 / ((1.0 + r) ** (t - day))
        return out

    # ------------------------------------------------------------------
    def track_opponent(self, S):
        minv = S['minv']
        if self.prev_minv is not None:
            step = S['step'] - 1
            shops = self.prev_shops or []
            for p in PRODUCTS:
                d = minv.get(p, I0) - self.prev_minv.get(p, I0)
                cons = 0
                if step % 4 == 0:
                    for s in shops:
                        items = SHOPS.get(s, [])
                        if p in items:
                            cons += 2 if len(items) == 1 else 1
                if step % 24 == 0 and p != 'FERTILIZER':
                    cons += 1
                opp = d + cons - self.my_last_sales.get(p, 0)
                if opp > 0:
                    self.opp_sold[p] += opp
        self.prev_minv = dict(minv)
        self.prev_shops = list(S['shops'])
        self.my_last_sales = defaultdict(int)

    # ------------------------------------------------------------------
    # supply bookkeeping
    # ------------------------------------------------------------------
    def opp_flags(self, tiles, day):
        fert_seen = 0
        ongoing = 0
        care_seen = 0
        old_animals = 0
        for y in range(N):
            for x in range(N):
                t = tiles[y][x]
                if is_plant(t) and CROPS[t['crop']]['on']:
                    ongoing += 1
                    if t.get('fertilized_until_day', -1) >= 0:
                        fert_seen += 1
                elif is_animal(t):
                    if day - t.get('placed_day', day) >= 1:
                        old_animals += 1
                        if (t.get('pending_care_bonus', 0) or 0) > 0 or t.get('cared_today'):
                            care_seen += 1
        fert = (fert_seen > 0) if ongoing > 0 else True
        care = (care_seen > 0) if old_animals > 0 else True
        return fert, care

    def farm_supply(self, tiles, day, mine, store, feed):
        n_animals = 0
        if mine:
            o_fert, o_care = True, True
        else:
            o_fert, o_care = self.opp_flags(tiles, day)
        for y in range(N):
            row = tiles[y]
            for x in range(N):
                t = row[x]
                if is_animal(t):
                    n_animals += 1
                    a = t['animal']
                    pr = ANIMALS[a]['pr']
                    pend = t.get('pending_care_bonus', 0) or 0
                    yu = t.get('yield_units', 0) or 0
                    if yu > 0:
                        store[pr][min(LAST_DAY, day + 1)] += yu
                    for e, u in animal_units(a, t['placed_day'], day, pend, o_care):
                        store[pr][min(LAST_DAY, e + 1)] += u
                    for e in range(day, LAST_DAY):
                        store['FERTILIZER'][min(LAST_DAY, e + 1)] += 1
                        feed[e] += 1
                elif is_plant(t):
                    crop = t['crop']
                    cd = CROPS[crop]
                    yu = t.get('yield_units', 0) or 0
                    if cd['on']:
                        per = 2 if o_fert else 1
                        if yu > 0:
                            store[crop][min(LAST_DAY, day + 1)] += yu
                        for e in ongoing_prod_days(t['planted_day'], crop):
                            if day <= e <= LAST_DAY - 1:
                                store[crop][min(LAST_DAY, e + 1)] += per
                        if not mine and PARAM['opp_replant'] > 0:
                            nd = ongoing_prod_days(t['planted_day'], crop)[-1] + 2
                            while nd <= LAST_DAY - 9:
                                for e in ongoing_prod_days(nd, crop):
                                    if day <= e <= LAST_DAY - 1:
                                        store[crop][min(LAST_DAY, e + 1)] += per * PARAM['opp_replant']
                                nd = ongoing_prod_days(nd, crop)[-1] + 2
                    else:
                        age = day - t['planted_day']
                        h = t['planted_day'] + HARVEST_AGE[crop]
                        if h < day:
                            h = day
                        if h > LAST_DAY:
                            h = LAST_DAY
                        ws = (cd['myd'] + 1) // 2
                        add = 0
                        for a2 in range(max(age, ws), cd['myd'] + 1):
                            if t['planted_day'] + a2 <= h:
                                add += 1
                        units = min(cd['my'], yu + add)
                        store[crop][min(LAST_DAY, h + 1)] += units
                        if not mine and PARAM['opp_replant'] > 0:
                            nh = h + HARVEST_AGE[crop]
                            while nh <= LAST_DAY:
                                store[crop][min(LAST_DAY, nh + 1)] += NOMINAL_YIELD[crop] * PARAM['opp_replant']
                                nh += HARVEST_AGE[crop]
        return n_animals

    # ------------------------------------------------------------------
    # economy (daily, at hour 0)
    # ------------------------------------------------------------------
    def plan_day(self, S):
        day = S['day']
        self.plant_plan = {}
        self.place_plan = {}
        self.plan_value = {}
        self.queue = []
        self.wait = {}
        self.drop_mode = False
        tiles = S['tiles']
        disc = self.disc_series(day)
        M = Market(day, S['minv'], S['shops'], disc)
        self.market_model = M
        mine = {p: [0.0] * 31 for p in PRODUCTS}
        feed = [0.0] * 31
        n_animals = self.farm_supply(tiles, day, True, mine, feed)
        opp_store = {p: [0.0] * 31 for p in PRODUCTS}
        opp_feed = [0.0] * 31
        if S['opp'] is not None:
            self.farm_supply(G(S['opp'], 'tiles', []), day, False, opp_store, opp_feed)
        for p in PRODUCTS:
            if p in ('FERTILIZER', 'WHEAT'):
                continue
            k = S['shed'].get(p, 0)
            if k > 0:
                mine[p][day] += k
        for p in PRODUCTS:
            if p == 'WHEAT':
                continue
            for t in range(31):
                M.my[p][t] += mine[p][t]
                M.opp[p][t] += opp_store[p][t]
        for t in range(31):
            M.opp['WHEAT'][t] += opp_store['WHEAT'][t] - opp_feed[t]
        M.wprod = list(mine['WHEAT'])
        M.wfeed = feed
        M.wstock = S['shed'].get('WHEAT', 0) + sum(i.get('WHEAT', 0) for i in S['invs'])
        fert_use = self.planned_fert_use(S, day)
        for t in range(31):
            M.my['FERTILIZER'][t] = max(0.0, M.my['FERTILIZER'][t] - fert_use[t])
        M.my['FERTILIZER'][day] += max(0, S['shed'].get('FERTILIZER', 0))
        # unit values for today's job valuation
        for p in PRODUCTS:
            self.pval[p] = float(mprice(p, S['minv'].get(p, I0)))
        wheat_price = mprice('WHEAT', S['minv'].get('WHEAT', I0) - 1)
        self.wheat_price = wheat_price
        self.pval['WHEAT'] = float(wheat_price)
        self.pval['FERTILIZER'] = max(self.pval['FERTILIZER'], 25.0)
        self.n_animals = n_animals
        self.work = self.base_workload(S, day)
        if day >= LAST_DAY - 1:
            self.finalize_purchases(S, n_animals, [])
            return
        expected_sales = self.expected_cash_today(S)
        reserve = 40 + n_animals * (wheat_price + 4) * 1.5
        budget = S['money'] + expected_sales - reserve
        free = []
        finishing = []
        for (x, y) in S['unl']:
            t = tiles[y][x]
            if t is None or is_weed(t) or is_struct(t):
                free.append((x, y))
            elif is_plant(t) and self.crop_frees_today(t, day):
                finishing.append((x, y))
        free.sort(key=lambda p: (shed_dist(p), p))
        chosen = []
        for a in ('COW', 'SHEEP', 'GOOSE'):
            k = S['shed'].get(a, 0) + sum(i.get(a, 0) for i in S['invs'])
            while k > 0 and free:
                st = ANIMALS[a]['st']
                pick = None
                for p in free:
                    t = tiles[p[1]][p[0]]
                    if is_struct(t) and t.get('kind') != st:
                        continue
                    pick = p
                    break
                if pick is None:
                    break
                free = [p for p in free if p != pick]
                chosen.append(('P', a, pick, 3000.0, 0))
                k -= 1
        self.lam = self.tile_shadow(S, M, day, disc, free, finishing, budget)
        if day == 0 and PARAM['opening']:
            budget, free = self.apply_opening(S, M, day, disc, free, budget, chosen, wheat_price)
        budget = self.greedy_projects(S, M, day, disc, free, finishing, budget, chosen, wheat_price)
        n_extra = len(S['unlocked']) - 1
        n_free_left = len(free) + len(finishing) - sum(1 for c in chosen if c[0] != 'P')
        if n_extra < 3 and day <= 21 and n_free_left <= 2:
            lp = LAND_PRICES[n_extra]
            if budget > lp + 300:
                q = LAND_ORDER[n_extra]
                new_tiles = [(x, y) for y in range(N) for x in range(N) if quad_of(x, y) == q]
                new_tiles.sort(key=lambda p: (shed_dist(p), p))
                trial = []
                self.greedy_projects(S, M, day, disc, list(new_tiles), [], budget - lp, trial,
                                     wheat_price, dry=True, newland=True)
                gain = sum(c[3] for c in trial)
                if gain > lp * PARAM['land_margin']:
                    self.queue.append(['BUY_LAND'])
                    budget -= lp
                    self.greedy_projects(S, M, day, disc, list(new_tiles), [], budget, chosen,
                                         wheat_price, newland=True)
        self.finalize_purchases(S, n_animals, chosen)
        self.compute_trend(M, S)

    def compute_trend(self, M, S):
        self.trend = {}
        for p in PRODUCTS:
            path = M.price_path(p)
            p0 = float(mprice(p, S['minv'].get(p, I0)))
            if len(path) >= 3 and p0 > 1:
                fut = min(path[1:4])
                self.trend[p] = (fut - p0) / p0
            else:
                self.trend[p] = -1.0

    def tile_shadow(self, S, M, day, disc, free, finishing, budget):
        if PARAM['tile_lambda'] <= 0:
            return 0.0
        base_cache = {p: M.revenue(p) for p in PRODUCTS if p != 'WHEAT'}
        base_cache['WHEAT'] = M.wheat_revenue()
        best_rate = 0.0
        for a in ANIMALS:
            v = self.candidate_value(M, day, disc, 'A', a, base_cache)[0]
            best_rate = max(best_rate, v / occupancy('A', a, day))
        if best_rate <= 0:
            return 0.0
        inc = 0.0
        h = PARAM['tile_horizon']
        for t in range(day + 1, min(LAST_DAY, day + h) + 1):
            for p in PRODUCTS:
                if p != 'WHEAT':
                    inc += M.my[p][t] * self.pval[p]
        cash = max(0.0, budget) + inc
        tiles_soon = len(free) + len(finishing)
        tl = S['tiles']
        for (x, y) in S['unl']:
            t = tl[y][x]
            if is_plant(t):
                cd = CROPS[t['crop']]
                if cd['on']:
                    endd = ongoing_prod_days(t['planted_day'], t['crop'])[-1] + 1
                else:
                    endd = t['planted_day'] + HARVEST_AGE[t['crop']]
                if day < endd <= day + h:
                    tiles_soon += 1
        n_extra = len(S['unlocked']) - 1
        if n_extra < 3:
            tiles_soon += 25
        kappa = min(1.0, (cash / 470.0) / max(1.0, tiles_soon))
        self.kappa = kappa
        return PARAM['tile_lambda'] * kappa * best_rate

    def base_workload(self, S, day):
        work = [0.0] * 31
        for (x, y) in S['unl']:
            t = S['tiles'][y][x]
            if is_animal(t):
                for d in range(day, LAST_DAY):
                    work[d] += ANIMAL_STEPS
                work[LAST_DAY] += 2.0
            elif is_plant(t):
                crop = t['crop']
                tot, span = CROP_STEPS[crop]
                end = min(LAST_DAY, t['planted_day'] + span - 1)
                n = max(1, end - t['planted_day'] + 1)
                for d in range(day, end + 1):
                    work[d] += tot / n
        return work

    def labor_cost_of(self, prof, disc, day):
        tot = 0.0
        w = self.work
        sc = PARAM['labor_scale']
        for t in range(day, 31):
            if prof[t] > 0:
                tot += prof[t] * marginal_step_cost(w[t] + prof[t] * 0.5) * disc[t]
        return tot * sc

    def apply_opening(self, S, M, day, disc, free, budget, chosen, wheat_price):
        base_cache = {p: M.revenue(p) for p in PRODUCTS if p != 'WHEAT'}
        base_cache['WHEAT'] = M.wheat_revenue()
        for kind, name, cnt in PARAM['opening']:
            for _ in range(cnt):
                if not free:
                    break
                v, cost, pr, extra, fx, dfeed = self.candidate_value(M, day, disc, kind, name, base_cache)
                extra_cash = 1.5 * (wheat_price + 4) if kind == 'A' else 0.0
                if cost + extra_cash > budget:
                    break
                if kind == 'A':
                    pick = free[0]
                else:
                    pick = free[-1]
                free = [p for p in free if p != pick]
                budget -= cost + extra_cash
                for t in range(31):
                    if extra is not None:
                        if pr == 'WHEAT':
                            M.wprod[t] += extra[t]
                        else:
                            M.my[pr][t] += extra[t]
                    if fx is not None:
                        M.my['FERTILIZER'][t] += fx[t]
                    if dfeed is not None:
                        M.wfeed[t] += dfeed[t]
                prof = step_profile(kind, name, day)
                for t in range(31):
                    self.work[t] += prof[t]
                chosen.append((kind, name, pick, max(v, 100.0), cost))
        return budget, free

    def expected_cash_today(self, S):
        tot = 0.0
        for p in PRODUCTS:
            k = S['shed'].get(p, 0)
            if k <= 0 or p == 'WHEAT':
                continue
            if p == 'FERTILIZER':
                k = max(0, k - self.fert_needed_next(S))
            tot += cum_rev(p, S['minv'].get(p, I0), k) * 0.9
        return tot

    def fert_needed_next(self, S):
        day = S['day']
        need = 0
        for (x, y) in S['unl']:
            t = S['tiles'][y][x]
            if is_plant(t) and CROPS[t['crop']]['on']:
                for e in ongoing_prod_days(t['planted_day'], t['crop'])[::2]:
                    if day <= e <= day + 1:
                        need += 1
            elif PARAM['fert_wheat'] and is_plant(t) and t['crop'] == 'WHEAT':
                age = day - t['planted_day']
                if 1 <= age <= 3 and t.get('fertilized_until_day', -1) < day:
                    need += 1
        return need

    def planned_fert_use(self, S, day):
        use = [0.0] * 31
        for (x, y) in S['unl']:
            t = S['tiles'][y][x]
            if is_plant(t) and CROPS[t['crop']]['on']:
                for e in ongoing_prod_days(t['planted_day'], t['crop'])[::2]:
                    if day <= e <= LAST_DAY - 1:
                        use[e] += 1
        return use

    def crop_frees_today(self, t, day):
        cd = CROPS[t['crop']]
        if cd['on']:
            last = ongoing_prod_days(t['planted_day'], t['crop'])[-1]
            return day > last
        return self.nonongoing_harvest_today(t, day)

    def nonongoing_harvest_today(self, t, day):
        cd = CROPS[t['crop']]
        age = day - t['planted_day']
        if age < cd['fy']:
            return False
        y = t.get('yield_units', 0) or 0
        if y <= 0:
            return False
        add = 0
        if not t.get('watered_today') and (cd['myd'] + 1) // 2 <= age <= cd['myd']:
            add = 2 if t.get('fertilized_until_day', -1) >= day else 1
        if y + add >= cd['my']:
            return True
        if age >= cd['myd'] or day >= LAST_DAY:
            return True
        if t['crop'] in HARVEST_AGE and age >= HARVEST_AGE[t['crop']]:
            return True
        return False

    def candidate_value(self, M, day, disc, kind, name, base_cache):
        lc = PARAM['labor_cost']
        if kind == 'A':
            ad = ANIMALS[name]
            pr = ad['pr']
            extra = [0.0] * 31
            for e, u in animal_units(name, day, day, 0, True):
                extra[min(LAST_DAY, e + 1)] += u
            dv = M.revenue(pr, extra) - base_cache[pr]
            fx = [0.0] * 31
            dfeed = [0.0] * 31
            for e in range(day, LAST_DAY):
                fx[min(LAST_DAY, e + 1)] += 1
                dfeed[e] += 1
            dv += M.revenue('FERTILIZER', fx) - base_cache['FERTILIZER']
            dv += M.wheat_revenue(dfeed=dfeed) - base_cache['WHEAT']
            lab = self.labor_cost_of(step_profile('A', name, day), disc, day)
            return dv - ad['cost'] - lab, ad['cost'], pr, extra, fx, dfeed
        cd = CROPS[name]
        units = crop_units(name, day, True)
        if not units:
            return -1e9, cd['seed'], name, None, None, None
        extra = [0.0] * 31
        for t, u in units:
            extra[t] += u
        fx = None
        if name == 'WHEAT':
            dv = M.wheat_revenue(dprod=extra) - base_cache['WHEAT']
            if PARAM['fert_wheat'] and day + 2 <= LAST_DAY - 1:
                fx = [0.0] * 31
                fx[day + 2] -= 1
                dv += M.revenue('FERTILIZER', fx) - base_cache['FERTILIZER']
        else:
            dv = M.revenue(name, extra) - base_cache[name]
        if cd['on']:
            fx = [0.0] * 31
            for e in ongoing_prod_days(day, name)[::2]:
                if e <= LAST_DAY - 1:
                    fx[e] -= 1
            dv += M.revenue('FERTILIZER', fx) - base_cache['FERTILIZER']
        lab = self.labor_cost_of(step_profile('C', name, day), disc, day)
        return dv - cd['seed'] - lab, cd['seed'], name, extra, fx, None

    def greedy_projects(self, S, M, day, disc, free, finishing, budget, chosen, wheat_price,
                        dry=False, newland=False):
        tiles = S['tiles']
        free = list(free)
        finishing = list(finishing)
        saved = None
        if dry:
            saved = ({p: list(M.my[p]) for p in PRODUCTS}, list(M.wprod), list(M.wfeed), list(self.work))
        guard = 0
        cap_iter = 120 if S['overage'] >= 8.0 else 25
        while guard < cap_iter:
            guard += 1
            base_cache = {p: M.revenue(p) for p in PRODUCTS if p != 'WHEAT'}
            base_cache['WHEAT'] = M.wheat_revenue()
            best = None
            for kind, name in (('A', 'COW'), ('A', 'SHEEP'), ('A', 'GOOSE'),
                               ('C', 'MELON'), ('C', 'STRAWBERRY'), ('C', 'TOMATO'),
                               ('C', 'WHEAT'), ('C', 'CARROT')):
                if not free and not finishing:
                    continue
                v, cost, pr, extra, fx, dfeed = self.candidate_value(M, day, disc, kind, name, base_cache)
                extra_cash = 1.5 * (wheat_price + 4) if kind == 'A' else 0.0
                if cost + extra_cash > budget:
                    continue
                if v < PARAM['min_value']:
                    continue
                v_adj = v - self.lam * occupancy(kind, name, day)
                if v_adj <= 0:
                    continue
                score = v_adj / (cost + 60.0)
                if best is None or score > best[0]:
                    best = (score, kind, name, v, cost, pr, extra, fx, dfeed, extra_cash)
            if best is None:
                break
            score, kind, name, v, cost, pr, extra, fx, dfeed, extra_cash = best
            if kind == 'A':
                st = ANIMALS[name]['st']
                pick = None
                for p in free:
                    t = None if newland else tiles[p[1]][p[0]]
                    if is_struct(t) and t.get('kind') != st:
                        continue
                    pick = p
                    break
                if pick is None and finishing:
                    pick = min(finishing, key=lambda q: (shed_dist(q), q))
                    finishing = [q for q in finishing if q != pick]
                elif pick is not None:
                    free = [p for p in free if p != pick]
                if pick is None:
                    break
            else:
                if finishing:
                    pick = finishing[-1]
                    finishing = finishing[:-1]
                else:
                    cands = [p for p in free if newland or not is_struct(tiles[p[1]][p[0]])]
                    if not cands:
                        cands = free
                    pick = cands[-1]
                    free = [p for p in free if p != pick]
            budget -= cost + extra_cash
            for t in range(31):
                if extra is not None:
                    if pr == 'WHEAT':
                        M.wprod[t] += extra[t]
                    else:
                        M.my[pr][t] += extra[t]
                if fx is not None:
                    M.my['FERTILIZER'][t] = M.my['FERTILIZER'][t] + fx[t]
                if dfeed is not None:
                    M.wfeed[t] += dfeed[t]
            prof = step_profile(kind, name, day)
            for t in range(31):
                self.work[t] += prof[t]
            chosen.append((kind, name, pick, v, cost))
        if dry:
            for p in PRODUCTS:
                M.my[p] = saved[0][p]
            M.wprod = saved[1]
            M.wfeed = saved[2]
            self.work = saved[3]
        return budget

    def finalize_purchases(self, S, n_animals, chosen):
        tiles = S['tiles']
        day = S['day']
        buy_animals = defaultdict(int)
        buy_seeds = defaultdict(int)
        seeds = dict(S['seeds'])
        for kind, name, p, v, cost in chosen:
            if kind in ('A', 'P'):
                self.place_plan[p] = name
                self.plan_value[p] = max(v, 500.0)
                if kind == 'A':
                    buy_animals[name] += 1
                n_animals += 1
            else:
                self.plant_plan[p] = name
                self.plan_value[p] = max(v, 20.0)
                if seeds.get(name, 0) > 0:
                    seeds[name] -= 1
                else:
                    buy_seeds[name] += 1
        for a, k in buy_animals.items():
            self.queue.append(['BUY_ANIMAL', a, k])
        wheat_have = S['shed'].get('WHEAT', 0) + sum(i.get('WHEAT', 0) for i in S['invs'])
        need = n_animals - wheat_have
        if need > 0 and day < LAST_DAY:
            self.queue.append(['BUY_PRODUCT', 'WHEAT', need])
        self.wheat_reserve = self.compute_wheat_reserve(day, n_animals)
        for c, k in buy_seeds.items():
            self.queue.append(['BUY_SEED', c, k])

    def compute_wheat_reserve(self, day, n_animals):
        M = getattr(self, 'market_model', None)
        if M is None or n_animals <= 0:
            return n_animals
        cp = 0.0
        cf = 0.0
        for t in range(day + 1, min(LAST_DAY, day + 5)):
            cp += M.wprod[t]
            cf += M.wfeed[t]
            if cp >= cf:
                return int(min(60, n_animals * (t - day)))
        return n_animals

    # ------------------------------------------------------------------
    # jobs with values
    # ------------------------------------------------------------------
    def animal_value(self, t, day):
        a = t['animal']
        ad = ANIMALS[a]
        pv = self.pval[ad['pr']]
        v = 0.0
        for e, u in animal_units(a, t['placed_day'], day, t.get('pending_care_bonus', 0) or 0, True):
            v += u * pv
        v += (LAST_DAY - day) * (self.pval['FERTILIZER'] - self.wheat_price)
        return max(200.0, v)

    def crop_value_left(self, t, day):
        crop = t['crop']
        cd = CROPS[crop]
        pv = self.pval[crop]
        yu = t.get('yield_units', 0) or 0
        if cd['on']:
            k = sum(1 for e in ongoing_prod_days(t['planted_day'], crop) if day <= e <= LAST_DAY - 1)
            return (yu + 1.5 * k) * pv
        age = day - t['planted_day']
        ws = (cd['myd'] + 1) // 2
        add = max(0, min(cd['my'] - yu, cd['myd'] - max(age, ws) + 1))
        if t['planted_day'] + cd['fy'] > LAST_DAY:
            return 0.0
        return (yu + add) * pv

    def make_jobs(self, S):
        day = S['day']
        tiles = S['tiles']
        jobs = []
        last = day >= LAST_DAY
        pval = self.pval
        targets = set(S['unl']) | set(self.plant_plan) | set(self.place_plan)
        for p in targets:
            x, y = p
            t = tiles[y][x] if p in S['unl_set'] else None
            ops = []
            need = defaultdict(int)
            val = 0.0
            crit = False
            if is_animal(t):
                a = t['animal']
                ad = ANIMALS[a]
                uv = pval[ad['pr']]
                nxt = next_animal_prod(t, day)
                pend = t.get('pending_care_bonus', 0) or 0
                if not last:
                    fed = t.get('fed_today')
                    if not fed:
                        ops.append(['FEED'])
                        need['WHEAT'] += 1
                        v = 0.0
                        if (t.get('consecutive_unfed', 0) or 0) >= 1 and day < LAST_DAY - 1:
                            v += self.animal_value(t, day)
                            crit = True
                        if nxt is not None and pend > 0 and nxt == day:
                            v += pend * uv
                        v += 0.5 * uv if nxt is not None else 0.0
                        val += v
                    if not t.get('cared_today') and nxt is not None and day < LAST_DAY - 1:
                        cap_ok = (1 + pend + 1) <= ad['mh'] or nxt > day
                        if cap_ok:
                            ops.append(['CARE'])
                            val += 0.9 * uv
                if t.get('fertilizer_available'):
                    ops.append(['COLLECT_FERTILIZER'])
                    val += pval['FERTILIZER']
                yu = t.get('yield_units', 0) or 0
                if yu > 0:
                    inc = 0
                    if animal_prod_today(t, day):
                        inc = 1 + (pend if (t.get('fed_today') or not last) else 0)
                    if last:
                        ops.append(['HARVEST'])
                        val += yu * uv
                    elif yu + inc > ad['mh']:
                        ops.append(['HARVEST'])
                        val += min(yu, yu + inc - ad['mh']) * uv + 0.3 * yu * uv
                    elif yu >= PARAM['harvest_min'] or day <= PARAM['harvest_early_day']:
                        ops.append(['HARVEST'])
                        val += 0.15 * yu * uv
            elif is_plant(t):
                crop = t['crop']
                cd = CROPS[crop]
                uv = pval[crop]
                age = day - t['planted_day']
                watered = t.get('watered_today')
                cu = t.get('consecutive_unwatered', 0) or 0
                yu = t.get('yield_units', 0) or 0
                if cd['on']:
                    prod = ongoing_prod_today(t, day)
                    fert_ok = t.get('fertilized_until_day', -1) >= day
                    want_fert = prod and not fert_ok and not last
                    if want_fert:
                        ops.append(['FERTILIZE'])
                        need['FERTILIZER'] += 1
                        cover = sum(1 for e in ongoing_prod_days(t['planted_day'], crop)
                                    if day <= e <= min(day + 2, LAST_DAY - 1))
                        val += cover * uv - pval['FERTILIZER']
                    if not watered and not last:
                        if cu >= 1:
                            ops.append(['WATER'])
                            val += self.crop_value_left(t, day)
                            crit = True
                        elif prod and (fert_ok or want_fert):
                            ops.append(['WATER'])
                            val += uv
                    inc = 0
                    if prod:
                        inc = 2 if (fert_ok or want_fert) else 1
                    pdays = ongoing_prod_days(t['planted_day'], crop)
                    done = day > pdays[-1]
                    if yu > 0 and age >= cd['fy'] and (yu + inc > cd['my'] or done or last):
                        ops.append(['HARVEST'])
                        val += yu * uv
                    if done and not last:
                        nc = self.plant_plan.get(p)
                        an = self.place_plan.get(p)
                        if nc or an:
                            ops.append(['DIG'])
                            val += self._append_new(ops, need, p, nc, an)
                else:
                    ws = (cd['myd'] + 1) // 2
                    in_win = ws <= age <= cd['myd'] and yu < cd['my']
                    hv = self.nonongoing_harvest_today(t, day)
                    mature_ok = t['planted_day'] + cd['fy'] <= LAST_DAY
                    if (crop == 'WHEAT' and PARAM['fert_wheat'] and in_win and age <= 3 and not last
                            and t.get('fertilized_until_day', -1) < day and yu <= 3 and not watered
                            and pval['FERTILIZER'] < 1.8 * uv):
                        ops.append(['FERTILIZE'])
                        need['FERTILIZER'] += 1
                        val += 2 * uv - pval['FERTILIZER']
                    if not watered and mature_ok:
                        if not last and cu >= 1:
                            ops.append(['WATER'])
                            val += self.crop_value_left(t, day)
                            crit = True
                        elif in_win and (not last or hv):
                            ops.append(['WATER'])
                            val += (2 if t.get('fertilized_until_day', -1) >= day else 1) * uv
                    if hv:
                        ops.append(['HARVEST'])
                        val += yu * uv
                        if not last:
                            val += self._append_new(ops, need, p, self.plant_plan.get(p), self.place_plan.get(p))
            elif is_weed(t) or is_struct(t):
                nc = self.plant_plan.get(p)
                an = self.place_plan.get(p)
                if is_struct(t) and an and t.get('kind') == ANIMALS[an]['st']:
                    ops.append(['PLACE', an])
                    need[an] += 1
                    ops.append(['FEED'])
                    need['WHEAT'] += 1
                    ops.append(['CARE'])
                    val += self.plan_value.get(p, 1000.0)
                    crit = True
                elif (nc or an) and not last:
                    ops.append(['DIG'])
                    val += self._append_new(ops, need, p, nc, an)
            elif t is None:
                nc = self.plant_plan.get(p)
                an = self.place_plan.get(p)
                if (nc or an) and not last:
                    val += self._append_new(ops, need, p, nc, an)
                    if an:
                        crit = True
            if ops:
                jobs.append({'pos': p, 'ops': ops, 'need': dict(need), 'val': val, 'crit': crit})
        return jobs

    def _append_new(self, ops, need, p, crop, animal):
        if animal:
            st = ANIMALS[animal]['st']
            ops.append(['BUILD_' + st])
            ops.append(['PLACE', animal])
            need[animal] += 1
            ops.append(['FEED'])
            need['WHEAT'] += 1
            ops.append(['CARE'])
            return self.plan_value.get(p, 1000.0)
        if crop:
            ops.append(['PLANT', crop])
            ops.append(['WATER'])
            return self.plan_value.get(p, 50.0) + 30.0
        return 0.0

    # ------------------------------------------------------------------
    # routing
    # ------------------------------------------------------------------
    def need_replan(self, S):
        if S['step'] - self.last_replan_step < 3:
            return False
        for idx in range(1 + len(S['hands'])):
            r = self.routes.get(idx)
            if not r:
                return True
        if S['hour'] % 3 == 0:
            self.insert_orphans(S)
        return False

    def insert_orphans(self, S):
        jobs = self.make_jobs(S)
        assigned = set()
        for r in self.routes.values():
            for st in r:
                assigned.add(tuple(st[0]))
        orphans = [j for j in jobs if j['crit'] and j['pos'] not in assigned and not j['need']]
        if not orphans:
            return
        units = self.unit_list(S)
        end_step = (LAST_STEP if S['day'] >= LAST_DAY else (S['day'] + 1) * TPD - 1)
        for j in sorted(orphans, key=lambda j: -j['val']):
            best = None
            for u in units:
                r = self.routes.get(u.idx)
                if r is None:
                    continue
                base = self.route_cost(u.pos, r)
                n = len(r)
                for k in range(0, n + 1):
                    if k == 0 and r and r[0][1] and r[0][1][0][0] == 'PICKUP':
                        continue
                    prev = r[k - 1][0] if k > 0 else u.pos
                    nxt = r[k][0] if k < n else None
                    add = dist(prev, j['pos']) + len(j['ops'])
                    if nxt is not None:
                        add += dist(j['pos'], nxt) - dist(prev, nxt)
                    if S['step'] + base + add > end_step:
                        continue
                    if best is None or add < best[0]:
                        best = (add, u.idx, k)
            if best is not None:
                _, idx, k = best
                self.routes[idx].insert(k, [j['pos'], [list(o) for o in j['ops']]])

    def unit_list(self, S, slack=0):
        end = LAST_STEP if S['day'] >= LAST_DAY else (S['day'] + 1) * TPD - 1
        avail = end - S['step'] + 1
        units = [Unit(0, S['farmer'], dict(S['invs'][0]) if S['invs'] else {}, avail - slack)]
        for i, h in enumerate(S['hands']):
            inv = dict(S['invs'][i + 1]) if i + 1 < len(S['invs']) else {}
            units.append(Unit(i + 1, h, inv, avail - slack))
        return units

    def projected_eod_load(self, S, jobs):
        tiles = S['tiles']
        load = 0
        for inv in S['invs']:
            load += sum(v for k, v in inv.items() if v > 0 and k not in ANIMALS and k != 'WHEAT')
        for j in jobs:
            p = j['pos']
            t = tiles[p[1]][p[0]] if p in S['unl_set'] else None
            for o in j['ops']:
                if o[0] == 'HARVEST' and isinstance(t, dict):
                    load += (t.get('yield_units', 0) or 0)
                elif o[0] == 'COLLECT_FERTILIZER':
                    load += 1
        shed_total = sum(v for v in S['shed'].values() if v > 0)
        keep = self.wheat_reserve + self.fert_needed_next(S) + sum(S['shed'].get(a, 0) for a in ANIMALS)
        return load + min(shed_total, keep)

    def drop_slack_for(self, S, jobs):
        if S['day'] >= LAST_DAY:
            return 0
        if self.projected_eod_load(S, jobs) > 92:
            return PARAM['drop_slack']
        return 0

    def build_all_routes(self, S):
        jobs = self.make_jobs(S)
        self.crit_set = set(j['pos'] for j in jobs if j['crit'])
        slack = self.drop_slack_for(S, jobs)
        units = self.unit_list(S, slack)
        self.routes, _ = self.assign(units, jobs, dict(S['shed']), S)
        if PARAM['melon_rush']:
            self.rush_melons(S, units)
        if S['day'] < LAST_DAY and PARAM['same_day_drop']:
            self.add_drop_stops(S, units)
        if S['day'] < LAST_DAY:
            self.capacity_drops(S, units)

    def route_carry(self, S, u, r):
        tiles = S['tiles']
        c = sum(v for k, v in u.inv.items() if v > 0 and k not in ANIMALS)
        for p, ops in r:
            t = tiles[p[1]][p[0]] if p in S['unl_set'] else None
            for o in ops:
                k = o[0]
                if k == 'PICKUP' and o[1] not in ANIMALS:
                    c += int(o[2]) if len(o) > 2 else 1
                elif k in ('FEED', 'FERTILIZE'):
                    c -= 1
                elif k == 'COLLECT_FERTILIZER':
                    c += 1
                elif k == 'HARVEST':
                    if isinstance(t, dict):
                        c += (t.get('yield_units', 0) or 0) + (1 if is_plant(t) and not CROPS[t['crop']]['on'] else 0)
                elif k == 'DROP':
                    c = 0
        return max(0, c)

    def capacity_drops(self, S, units):
        """Keep end-of-day carried load + kept shed stock <= 100 by inserting DROP stops
        at the cheapest point of the heaviest routes (trimming tail jobs if needed)."""
        day = S['day']
        end_step = (day + 1) * TPD - 1
        tiles = S['tiles']
        shed_total = sum(v for v in S['shed'].values() if v > 0)
        keep = self.wheat_reserve + self.fert_needed_next(S) + sum(S['shed'].get(a, 0) for a in ANIMALS)
        shed_after = min(shed_total, keep)
        margin = 100 - shed_after - 6
        info = []
        total = 0
        for u in units:
            r = self.routes.get(u.idx) or []
            prof = self.carry_profile(S, u, r)
            c = prof[-1] if prof else 0
            info.append([c, u, prof])
            total += c
        self.cap_tight = (total + shed_after) > 70
        if total <= margin:
            return
        info.sort(key=lambda x: -x[0])
        for item in info:
            if total <= margin:
                break
            c, u, prof = item
            if c <= 3:
                continue
            r = self.routes.get(u.idx)
            if not r:
                continue
            best = None
            n = len(r)
            base = self.route_cost(u.pos, r)
            for k in range(1, n + 1):
                prev = r[k - 1][0]
                nxt = r[k][0] if k < n else None
                sp = nearest_shed(prev)
                if nxt is not None:
                    det = dist(prev, sp) + dist(sp, nxt) - dist(prev, nxt) + 1
                else:
                    det = dist(prev, sp) + 1
                load_at = prof[k - 1]
                gain = load_at
                if gain <= 0:
                    continue
                if S['step'] + base + det > end_step:
                    continue
                score = gain - 2.0 * det
                if best is None or score > best[0]:
                    best = (score, k, sp, gain)
            if best is None:
                # trim tail jobs (lowest priority by position) until a final drop fits
                trimmed = 0
                while r and trimmed < 6:
                    last = r[-1]
                    if last[0] in SHED_SET and last[1] and last[1][0][0] == 'PICKUP':
                        break
                    if tuple(last[0]) in self.crit_set:
                        break
                    r.pop()
                    trimmed += 1
                    base = self.route_cost(u.pos, r)
                    endp = r[-1][0] if r else u.pos
                    sp = nearest_shed(endp)
                    if S['step'] + base + dist(endp, sp) + 1 <= end_step:
                        r.append([sp, [['DROP']]])
                        newc = self.carry_profile(S, u, r)
                        total -= c - (newc[-1] if newc else 0)
                        break
                continue
            _, k, sp, gain = best
            r.insert(k, [sp, [['DROP']]])
            newp = self.carry_profile(S, u, r)
            total -= c - (newp[-1] if newp else 0)

    def carry_profile(self, S, u, r):
        """Carried load after each stop of route r."""
        tiles = S['tiles']
        c = sum(v for k, v in u.inv.items() if v > 0 and k not in ANIMALS)
        out = []
        for p, ops in r:
            t = tiles[p[1]][p[0]] if p in S['unl_set'] else None
            for o in ops:
                k = o[0]
                if k == 'PICKUP' and o[1] not in ANIMALS:
                    c += int(o[2]) if len(o) > 2 else 1
                elif k in ('FEED', 'FERTILIZE'):
                    c -= 1
                elif k == 'COLLECT_FERTILIZER':
                    c += 1
                elif k == 'HARVEST':
                    if isinstance(t, dict):
                        c += (t.get('yield_units', 0) or 0) + (1 if is_plant(t) and not CROPS[t['crop']]['on'] else 0)
                elif k == 'DROP':
                    c = 0
            out.append(max(0, c))
        return out

    def rush_melons(self, S, units):
        """Harvest ripe melons first thing and unload them at once (first seller wins)."""
        tiles = S['tiles']
        end_step = (LAST_STEP if S['day'] >= LAST_DAY else (S['day'] + 1) * TPD - 1)
        for u in units:
            r = self.routes.get(u.idx)
            if not r:
                continue
            head = []
            body = r
            if r[0][0] in SHED_SET and (not r[0][1] or r[0][1][0][0] == 'PICKUP'):
                head = [r[0]]
                body = r[1:]
            mel = []
            rest = []
            for st in body:
                p = tuple(st[0])
                t = tiles[p[1]][p[0]] if p in S['unl_set'] else None
                if is_plant(t) and t['crop'] == 'MELON' and any(o[0] == 'HARVEST' for o in st[1]):
                    mel.append(st)
                else:
                    rest.append(st)
            if not mel:
                continue
            start = head[0][0] if head else u.pos
            mel.sort(key=lambda st: dist(start, st[0]))
            last = mel[-1][0]
            drop = [nearest_shed(last), [['DROP']]]
            newr = head + mel + [drop] + rest
            if S['step'] + self.route_cost(u.pos, newr) <= end_step + 2:
                self.routes[u.idx] = newr

    def add_drop_stops(self, S, units):
        tiles = S['tiles']
        day = S['day']
        end_step = (day + 1) * TPD - 1
        for u in units:
            r = self.routes.get(u.idx) or []
            if not r or (r[-1][1] and r[-1][1][0][0] == 'DROP'):
                continue
            val = 0.0
            for p, ops in r:
                if not any(o[0] == 'HARVEST' for o in ops):
                    continue
                t = tiles[p[1]][p[0]] if p in S['unl_set'] else None
                if not isinstance(t, dict):
                    continue
                item = t.get('crop') if is_plant(t) else ANIMALS[t['animal']]['pr'] if is_animal(t) else None
                if item is None:
                    continue
                if item == 'MELON' or self.trend.get(item, 0.0) < PARAM['trend_dump']:
                    val += (t.get('yield_units', 0) or 0) * self.pval.get(item, 0.0)
            if val < PARAM['drop_min_value']:
                continue
            cost = self.route_cost(u.pos, r)
            endp = r[-1][0]
            sp = nearest_shed(endp)
            if S['step'] + cost + dist(endp, sp) + 1 <= end_step - 1:
                r.append([sp, [['DROP']]])

    def route_cost(self, start, stops):
        c = 0
        p = start
        for st in stops:
            c += dist(p, st[0]) + len(st[1])
            p = st[0]
        return c

    def assign(self, units, jobs, stock, S, simulate=False):
        """Value-density cheapest insertion. Returns (routes, captured value)."""
        last_day = S['day'] >= LAST_DAY
        lab = PARAM['labor_cost']

        def key(j):
            dens = j['val'] / (len(j['ops']) + 1.0 + 0.5 * shed_dist(j['pos']))
            return (0 if j['crit'] else 1, -dens, j['pos'])
        jobs = sorted(jobs, key=key)
        routes = {}
        load = {}
        has_pick = {}
        have = {}
        for u in units:
            routes[u.idx] = []
            load[u.idx] = 0
            has_pick[u.idx] = False
            have[u.idx] = dict(u.inv)
        stock = dict(stock)
        captured = 0.0
        for j in jobs:
            nops = len(j['ops'])
            best = None
            for u in units:
                r = routes[u.idx]
                hv = have[u.idx]
                short = {}
                ok = True
                for item, k in j['need'].items():
                    h = hv.get(item, 0)
                    if h >= k:
                        continue
                    s = k - max(0, h)
                    if stock.get(item, 0) < s:
                        if item == 'FERTILIZER':
                            continue
                        ok = False
                        break
                    short[item] = s
                if not ok:
                    continue
                extra = 0
                first = 1 if has_pick[u.idx] else 0
                if short:
                    if not has_pick[u.idx]:
                        sp = nearest_shed(u.pos)
                        nxt0 = r[0][0] if r else j['pos']
                        extra = dist(u.pos, sp) + dist(sp, nxt0) - dist(u.pos, nxt0)
                        extra += len(short)
                    else:
                        cur = set(o[1] for o in r[0][1])
                        extra = sum(1 for it in short if it not in cur)
                n = len(r)
                bc = None
                bpos = None
                for k in range(first, n + 1):
                    if k > 0:
                        prev = r[k - 1][0]
                    elif short and not has_pick[u.idx]:
                        prev = nearest_shed(u.pos)
                    else:
                        prev = u.pos
                    nxt = r[k][0] if k < n else None
                    add = dist(prev, j['pos']) + nops
                    if nxt is not None:
                        add += dist(j['pos'], nxt) - dist(prev, nxt)
                    if bc is None or add < bc:
                        bc = add
                        bpos = k
                total = load[u.idx] + bc + extra
                chk = total
                if last_day:
                    endp = j['pos'] if bpos == n else r[-1][0]
                    chk = total + shed_dist(endp) + 2
                if chk > u.avail:
                    continue
                cost = bc + extra
                if not j['crit'] and j['val'] < cost * lab * 0.5:
                    continue
                kk = cost + 0.1 * load[u.idx]
                if best is None or kk < best[0]:
                    best = (kk, u, bpos, short)
            if best is None:
                continue
            _, u, bpos, short = best
            r = routes[u.idx]
            if short and not has_pick[u.idx]:
                sp = nearest_shed(u.pos)
                r.insert(0, [sp, []])
                has_pick[u.idx] = True
                bpos += 1
            if has_pick[u.idx] and bpos == 0:
                bpos = 1
            r.insert(bpos, [j['pos'], [list(o) for o in j['ops']]])
            for item, k in j['need'].items():
                h = have[u.idx].get(item, 0)
                if h < k:
                    take = min(k - max(0, h), stock.get(item, 0))
                    if take > 0:
                        stock[item] = stock.get(item, 0) - take
                        ops0 = r[0][1]
                        found = False
                        for o in ops0:
                            if o[1] == item:
                                o[2] += take
                                found = True
                        if not found:
                            ops0.append(['PICKUP', item, take])
                        have[u.idx][item] = h + take
                have[u.idx][item] = have[u.idx].get(item, 0) - k
            load[u.idx] = self.route_cost(u.pos, r)
            captured += j['val']
        if simulate:
            return None, captured
        for u in units:
            r = routes[u.idx]
            r = self.improve(r, u.pos)
            if last_day:
                r = self.lastday_drops(S, u, r)
                endp = r[-1][0] if r else u.pos
                r.append([nearest_shed(endp), [['DROP']]])
            routes[u.idx] = r
        return routes, captured

    def lastday_drops(self, S, u, r):
        """Insert shed unloads whenever enough goods are carried and a shed tile is near."""
        out = []
        tiles = S['tiles']
        load = sum(v for k, v in u.inv.items() if v > 0 and k not in ANIMALS)
        budget = u.avail - self.route_cost(u.pos, r) - 4
        for i, (p, ops) in enumerate(r):
            out.append([p, ops])
            t = tiles[p[1]][p[0]] if p in S['unl_set'] else None
            for o in ops:
                if o[0] == 'HARVEST' and isinstance(t, dict):
                    load += (t.get('yield_units', 0) or 0)
                elif o[0] == 'COLLECT_FERTILIZER':
                    load += 1
            if load >= PARAM['lastday_drop_load'] and i + 1 < len(r):
                sp = nearest_shed(p)
                nxt = r[i + 1][0]
                det = dist(p, sp) + dist(sp, nxt) - dist(p, nxt) + 1
                if det <= min(5, budget):
                    out.append([sp, [['DROP']]])
                    budget -= det
                    load = 0
        return out

    def improve(self, r, start):
        if len(r) < 3:
            return r
        head = []
        body = r
        if r[0][0] in SHED_SET and (not r[0][1] or r[0][1][0][0] == 'PICKUP'):
            head = [r[0]]
            body = r[1:]
            start = r[0][0]
        n = len(body)
        if n < 3:
            return head + body
        pts = [s[0] for s in body]
        order = list(range(n))
        improved = True
        it = 0
        while improved and it < 20:
            improved = False
            it += 1
            for i in range(n - 1):
                a = start if i == 0 else pts[order[i - 1]]
                b = pts[order[i]]
                for k in range(i + 1, n):
                    c = pts[order[k]]
                    d = pts[order[k + 1]] if k + 1 < n else None
                    old = dist(a, b) + (dist(c, d) if d is not None else 0)
                    new = dist(a, c) + (dist(b, d) if d is not None else 0)
                    if new < old:
                        order[i:k + 1] = order[i:k + 1][::-1]
                        improved = True
                        b = pts[order[i]]
        return head + [body[i] for i in order]

    # ------------------------------------------------------------------
    # hiring
    # ------------------------------------------------------------------
    def decide_hires(self, S, slots_now):
        jobs = self.make_jobs(S)
        if not jobs:
            return 0
        avail0 = (LAST_STEP if S['day'] >= LAST_DAY else (S['day'] + 1) * TPD - 1) - S['step'] + 1
        avail0 -= self.drop_slack_for(S, jobs)
        money = S['money'] + self.expected_cash_today(S) * 0.8
        occ = {s: 0 for s in SHED}
        occ[S['farmer']] = occ.get(S['farmer'], 0) + 1
        spawn = []
        for _ in range(PARAM['max_hands'] + 1):
            sp = sorted(SHED, key=lambda s: (occ.get(s, 0), SHED.index(s)))[0]
            occ[sp] = occ.get(sp, 0) + 1
            spawn.append(sp)
        stock0 = dict(S['shed'])
        for o in self.queue + self.pending_buys:
            if o[0] in ('BUY_ANIMAL', 'BUY_PRODUCT'):
                stock0[o[1]] = stock0.get(o[1], 0) + int(o[2])
        best_h = 0
        best_net = None
        results = {}
        lo = 0
        hi = PARAM['max_hands']
        cache = {}

        def evaluate(h):
            if h in cache:
                return cache[h]
            units = [Unit(0, S['farmer'], dict(S['invs'][0]) if S['invs'] else {}, avail0)]
            for i in range(h):
                av = avail0 - 1 if i < slots_now else avail0 - 2
                units.append(Unit(i + 1, spawn[i], {}, av))
            _, cap = self.assign(units, jobs, stock0, S, simulate=True)
            net = cap - hire_total(h)
            cache[h] = net
            return net
        hmax = 0
        while hmax < hi and hire_total(hmax + 1) <= max(15.0, 0.6 * money):
            hmax += 1
        start = min(hmax, max(0, self.last_hires))
        if S['overage'] < 8.0:
            return start
        look = 3 if S['overage'] >= 25.0 else 1
        best_h = start
        best_net = evaluate(start)
        h = start
        miss = 0
        while h < hmax and miss < look:
            h += 1
            net = evaluate(h)
            if net > best_net:
                best_net, best_h, miss = net, h, 0
            else:
                miss += 1
        h = start
        miss = 0
        while h > 0 and miss < look:
            h -= 1
            net = evaluate(h)
            if net > best_net:
                best_net, best_h, miss = net, h, 0
            else:
                miss += 1
        self.last_hires = best_h
        return best_h

    # ------------------------------------------------------------------
    # execution
    # ------------------------------------------------------------------
    def execute(self, S):
        units = [(0, S['farmer'])] + [(i + 1, h) for i, h in enumerate(S['hands'])]
        acts = []
        shadow = {}
        shed = dict(S['shed'])
        seeds = dict(S['seeds'])
        plant_count = defaultdict(int)
        self.update_drop_mode(S)
        for idx, pos in units:
            inv = dict(S['invs'][idx]) if idx < len(S['invs']) else {}
            r = self.routes.get(idx)
            if r is None:
                r = []
                self.routes[idx] = r
            if S['hour'] == 0 and S['day'] < LAST_DAY and idx == 0:
                acts.append(self.farmer_hour0(S, pos, inv, shadow, shed, seeds, plant_count))
                continue
            a = self.next_action(S, idx, pos, inv, r, shadow, shed, seeds, plant_count)
            acts.append(a)
        return acts

    def update_drop_mode(self, S):
        if S['day'] >= LAST_DAY:
            self.drop_mode = False
            return
        if S['hour'] < 18:
            return
        carried = 0
        for inv in S['invs']:
            carried += sum(v for k, v in inv.items() if v > 0)
        shed_total = sum(v for k, v in S['shed'].items() if v > 0)
        keep = self.wheat_reserve + self.fert_needed_next(S)
        if carried + min(shed_total, keep) > 92:
            self.drop_mode = True

    def farmer_hour0(self, S, pos, inv, shadow, shed, seeds, plant_count):
        t = self.tstate(S, shadow, pos)
        for op in (['CARE'], ['COLLECT_FERTILIZER']):
            if self.valid(S, op, pos, t, inv, shed, seeds, plant_count):
                self.apply_shadow(S, op, pos, t, inv, shed, seeds, plant_count, shadow)
                return op
        return ['PASS']

    def tstate(self, S, shadow, p):
        if p in shadow:
            return shadow[p]
        t = S['tiles'][p[1]][p[0]]
        if isinstance(t, dict):
            t = dict(t)
        shadow[p] = t
        return t

    def next_action(self, S, idx, pos, inv, r, shadow, shed, seeds, plant_count):
        guard = 0
        while r and guard < 60:
            guard += 1
            p, ops = r[0]
            p = tuple(p)
            if not ops:
                r.pop(0)
                continue
            if pos != p:
                return [step_toward(pos, p)]
            op = ops[0]
            if op[0] == 'DROP':
                if sum(v for v in inv.values() if v > 0) <= 0:
                    ops.pop(0)
                    continue
                a = self.safe_unload(pos, inv, shed)
                if a[0] == 'DROP':
                    ops.pop(0)
                return a
            t = self.tstate(S, shadow, p)
            if self.valid(S, op, p, t, inv, shed, seeds, plant_count):
                ops.pop(0)
                self.apply_shadow(S, op, p, t, inv, shed, seeds, plant_count, shadow)
                return list(op)
            if op[0] in ('PLANT', 'PICKUP', 'PLACE') and S['hour'] <= 3:
                k2 = (idx, p, op[0])
                w = self.wait.get(k2, 0)
                if w < 2:
                    self.wait[k2] = w + 1
                    return ['PASS']
            ops.pop(0)
        return self.idle_action(S, idx, pos, inv, shed)

    def idle_action(self, S, idx, pos, inv, shed):
        carried = sum(v for k, v in inv.items() if v > 0)
        if carried > 0 and (S['day'] >= LAST_DAY or (S['hour'] >= 12 and self.cap_tight)):
            sp = nearest_shed(pos)
            if pos == sp:
                return self.safe_unload(pos, inv, shed)
            return [step_toward(pos, sp)]
        return ['PASS']

    def safe_unload(self, pos, inv, shed):
        """DROP if everything fits, else PLACE the most valuable item partially, else wait."""
        if pos not in SHED_SET:
            return [step_toward(pos, nearest_shed(pos))]
        carried = sum(v for v in inv.values() if v > 0)
        room = 100 - sum(v for v in shed.values() if v > 0)
        if carried <= 0:
            return ['PASS']
        if carried <= room:
            for k, v in list(inv.items()):
                if v > 0:
                    shed[k] = shed.get(k, 0) + v
            inv.clear()
            return ['DROP']
        if room > 0:
            items = sorted([k for k, v in inv.items() if v > 0],
                           key=lambda k: -self.pval.get(k, 0.0))
            k = items[0]
            n = min(inv[k], room)
            inv[k] -= n
            shed[k] = shed.get(k, 0) + n
            return ['PLACE', k, n]
        return ['PASS']

    def valid(self, S, op, p, t, inv, shed, seeds, plant_count):
        day = S['day']
        o = op[0]
        if o == 'PICKUP':
            return p in SHED_SET and shed.get(op[1], 0) > 0
        if o == 'DROP':
            if p not in SHED_SET:
                return False
            carried = sum(v for v in inv.values() if v > 0)
            room = 100 - sum(v for v in shed.values() if v > 0)
            return 0 < carried <= room
        if o == 'PLACE':
            a = op[1]
            return (a in ANIMALS and is_struct(t) and t.get('kind') == ANIMALS[a]['st']
                    and inv.get(a, 0) > 0)
        if t == 'LOCKED':
            return False
        if o == 'FEED':
            return is_animal(t) and not t.get('fed_today') and inv.get('WHEAT', 0) > 0
        if o == 'CARE':
            return is_animal(t) and not t.get('cared_today')
        if o == 'COLLECT_FERTILIZER':
            return is_animal(t) and bool(t.get('fertilizer_available'))
        if o == 'HARVEST':
            if not isinstance(t, dict) or (t.get('yield_units', 0) or 0) <= 0:
                return False
            if is_plant(t):
                return day - t['planted_day'] >= CROPS[t['crop']]['fy']
            return is_animal(t)
        if o == 'WATER':
            return is_plant(t) and not t.get('watered_today')
        if o == 'FERTILIZE':
            return (is_plant(t) and inv.get('FERTILIZER', 0) > 0
                    and t.get('fertilized_until_day', -1) < day)
        if o == 'PLANT':
            c = op[1]
            return t is None and seeds.get(c, 0) - plant_count[c] > 0
        if o == 'DIG':
            return t is not None and not is_animal(t)
        if o in ('BUILD_COOP', 'BUILD_PASTURE'):
            return t is None
        return False

    def apply_shadow(self, S, op, p, t, inv, shed, seeds, plant_count, shadow):
        day = S['day']
        o = op[0]
        if o == 'PICKUP':
            k = min(int(op[2]) if len(op) > 2 else 1, shed.get(op[1], 0))
            shed[op[1]] = shed.get(op[1], 0) - k
            del op[2:]
            op.append(k)
            inv[op[1]] = inv.get(op[1], 0) + k
        elif o == 'DROP':
            for k, v in list(inv.items()):
                shed[k] = shed.get(k, 0) + v
            inv.clear()
        elif o == 'PLACE':
            inv[op[1]] -= 1
            shadow[p] = {'kind': ANIMALS[op[1]]['st'], 'animal': op[1], 'placed_day': day,
                         'yield_units': 0, 'consecutive_unfed': 0, 'fed_today': False,
                         'cared_today': False, 'fertilizer_available': False, 'pending_care_bonus': 0}
        elif o == 'FEED':
            t['fed_today'] = True
            inv['WHEAT'] -= 1
        elif o == 'CARE':
            t['cared_today'] = True
        elif o == 'COLLECT_FERTILIZER':
            t['fertilizer_available'] = False
            inv['FERTILIZER'] = inv.get('FERTILIZER', 0) + 1
        elif o == 'HARVEST':
            if is_plant(t):
                inv[t['crop']] = inv.get(t['crop'], 0) + t['yield_units']
                if CROPS[t['crop']]['on']:
                    t['yield_units'] = 0
                else:
                    shadow[p] = None
            else:
                pr = ANIMALS[t['animal']]['pr']
                inv[pr] = inv.get(pr, 0) + t['yield_units']
                t['yield_units'] = 0
        elif o == 'WATER':
            t['watered_today'] = True
        elif o == 'FERTILIZE':
            inv['FERTILIZER'] -= 1
            t['fertilized_until_day'] = max(t.get('fertilized_until_day', -1), day + 2)
        elif o == 'PLANT':
            plant_count[op[1]] += 1
            shadow[p] = {'kind': 'PLANT', 'crop': op[1], 'planted_day': day, 'watered_today': False,
                         'consecutive_unwatered': 1, 'yield_units': 0 if CROPS[op[1]]['on'] else 1,
                         'fertilized_until_day': -1}
        elif o == 'DIG':
            shadow[p] = None
        elif o == 'BUILD_COOP':
            shadow[p] = {'kind': 'COOP'}
        elif o == 'BUILD_PASTURE':
            shadow[p] = {'kind': 'PASTURE'}

    # ------------------------------------------------------------------
    # market orders
    # ------------------------------------------------------------------
    def market_orders(self, S):
        hour = S['hour']
        orders = []
        sells = self.sell_orders(S)
        if hour == 0:
            orders.extend(sells[:4])
            sells = sells[4:]
            pre = [o for o in self.queue if o[0] != 'BUY_SEED']
            orders.extend(pre)
            self.pending_buys = list(pre)
            self.queue = [o for o in self.queue if o[0] == 'BUY_SEED']
            slots = 10 - len(orders)
            self.hires_planned = self.decide_hires(S, slots)
            k = min(slots, self.hires_planned)
            for _ in range(max(0, k)):
                orders.append(['HIRE'])
            self.hires_planned -= max(0, k)
            room = 10 - len(orders)
            if room > 0 and self.queue:
                orders.extend(self.queue[:room])
                self.queue = self.queue[room:]
            room = 10 - len(orders)
            orders.extend(sells[:room])
        else:
            if self.hires_planned > 0 and hour <= 2:
                k = min(10, self.hires_planned)
                for _ in range(k):
                    orders.append(['HIRE'])
                self.hires_planned -= k
            room = 10 - len(orders)
            if self.queue and room > 0:
                orders.extend(self.queue[:room])
                self.queue = self.queue[room:]
            room = 10 - len(orders)
            orders.extend(sells[:room])
        for o in orders:
            if o[0] == 'SELL':
                self.my_last_sales[o[1]] += min(int(o[2]), S['shed'].get(o[1], 0))
        return orders[:10]

    def sell_orders(self, S):
        day = S['day']
        hour = S['hour']
        shed = S['shed']
        minv = S['minv']
        orders = []
        if day >= LAST_DAY:
            prods = sorted(PRODUCTS, key=lambda it: -mprice(it, minv.get(it, I0)))
            for it in prods:
                orders.append(['SELL', it, 999])
            return orders
        keep = {}
        keep['WHEAT'] = max(0, self.wheat_reserve)
        keep['FERTILIZER'] = self.fert_needed_next(S)
        want = {}
        days_left = LAST_DAY - day
        for it in PRODUCTS:
            n = shed.get(it, 0) - keep.get(it, 0)
            if n <= 0:
                continue
            if it in ('WHEAT', 'FERTILIZER') or days_left <= 1 or self.cap_tight or (it == 'MELON' and PARAM['melon_rush']):
                want[it] = n
                continue
            want[it] = self.smooth_qty(it, n, minv.get(it, I0), days_left)
        if self.drop_mode and hour >= 21:
            for it in PRODUCTS:
                if it not in ('WHEAT', 'FERTILIZER'):
                    want[it] = 999
        if hour >= 22:
            carried = 0
            for inv in S['invs']:
                carried += sum(v for k, v in inv.items() if v > 0)
            total = sum(v for k, v in shed.items() if v > 0)
            after = total - sum(min(v, shed.get(k, 0)) for k, v in want.items())
            over = after + carried - 100
            if over > 0:
                order = sorted([it for it in PRODUCTS if shed.get(it, 0) - want.get(it, 0) > 0],
                               key=lambda it: mprice(it, minv.get(it, I0)))
                for it in order:
                    if over <= 0:
                        break
                    k = min(over, shed.get(it, 0) - want.get(it, 0))
                    want[it] = want.get(it, 0) + k
                    over -= k
        for it, n in want.items():
            if n > 0:
                orders.append(['SELL', it, n])
        orders.sort(key=lambda o: -mprice(o[1], minv.get(o[1], I0)) * min(o[2], 50))
        return orders

    def smooth_qty(self, it, stock, inv, days_left):
        p0 = mprice(it, inv)
        if p0 <= 1:
            return stock
        if self.trend.get(it, -1.0) < PARAM['trend_dump']:
            return stock
        band = PARAM['sell_band']
        floor = p0 * (1.0 - band)
        k = 0
        I = inv
        while k < stock:
            pr = mprice(it, I)
            if pr < floor:
                break
            k += 1
            if pr > 1:
                I += 1
        H = max(1, min(PARAM['smooth_days'], days_left))
        minimum = int(math.ceil(stock / float(H)))
        return max(k, min(stock, minimum))


_BRAINS = {}


def act(observation):
    try:
        me = int(G(observation, 'player', 0))
    except Exception:
        me = 0
    b = _BRAINS.get(me)
    if b is None:
        b = Brain()
        _BRAINS[me] = b
    try:
        return b.act(observation)
    except Exception:
        try:
            farm = G(observation, 'farms')[me]
            n = len(G(farm, 'hands', []) or [])
        except Exception:
            n = 0
        return {'farmer': ['PASS'], 'hands': [['PASS'] for _ in range(n)], 'market': []}
