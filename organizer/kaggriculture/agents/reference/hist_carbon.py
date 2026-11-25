# -*- coding: utf-8 -*-
"""Whole-farm portfolio, online rival belief, fast route solver, and tactical market agent."""

from collections import Counter, defaultdict, deque
from functools import lru_cache
import json
import math
import random

# ============================================================================
# Layer 1: Exact Game Constants, State Helpers & Fast Integer Market Book
# ============================================================================

CROPS = {
    'WHEAT': {'seed': 10, 'first_yield_day': 2, 'max_yield_day': 4, 'interval': 0, 'max_yield': 6, 'ongoing': False},
    'CARROT': {'seed': 20, 'first_yield_day': 2, 'max_yield_day': 3, 'interval': 0, 'max_yield': 4, 'ongoing': False},
    'TOMATO': {'seed': 50, 'first_yield_day': 8, 'max_yield_day': 8, 'interval': 1, 'max_yield': 4, 'ongoing': True},
    'STRAWBERRY': {'seed': 100, 'first_yield_day': 10, 'max_yield_day': 10, 'interval': 2, 'max_yield': 4, 'ongoing': True},
    'MELON': {'seed': 80, 'first_yield_day': 10, 'max_yield_day': 12, 'interval': 0, 'max_yield': 6, 'ongoing': False},
}

ANIMALS = {
    'GOOSE': {'cost': 300, 'structure': 'COOP', 'first_yield_day': 4, 'interval': 1, 'max_held': 4, 'product': 'EGG'},
    'COW': {'cost': 400, 'structure': 'PASTURE', 'first_yield_day': 8, 'interval': 2, 'max_held': 6, 'product': 'MILK'},
    'SHEEP': {'cost': 500, 'structure': 'PASTURE', 'first_yield_day': 6, 'interval': 3, 'max_held': 6, 'product': 'WOOL'},
}

PRODUCTS = ('WHEAT', 'CARROT', 'TOMATO', 'STRAWBERRY', 'MELON', 'EGG', 'MILK', 'WOOL', 'FERTILIZER')
TOWN_ITEMS = ('WHEAT', 'CARROT', 'TOMATO', 'STRAWBERRY', 'MELON', 'EGG', 'MILK', 'WOOL')
MARKET_I0 = 10000
PRICE_FLOOR = 1
HINGE_GAIN = 8.0

MARKET_PARAMS = {
    'WHEAT': {'base': 25, 'I0': MARKET_I0, 'T': 400, 'below_func': 'sqrt', 'below_target': 0.8, 'above_func': 'log', 'above_target': 0.2},
    'CARROT': {'base': 35, 'I0': MARKET_I0, 'T': 450, 'below_func': 'hinge', 'below_target': 1.0, 'above_func': 'sqrt', 'above_target': 0.7},
    'TOMATO': {'base': 60, 'I0': MARKET_I0, 'T': 200, 'below_func': 'hinge', 'below_target': 0.4, 'above_func': 'sqrt', 'above_target': 0.6},
    'STRAWBERRY': {'base': 120, 'I0': MARKET_I0, 'T': 100, 'below_func': 'sqrt', 'below_target': 0.7, 'above_func': 'linear', 'above_target': 1.6},
    'MELON': {'base': 250, 'I0': MARKET_I0, 'T': 300, 'below_func': 'log', 'below_target': 0.2, 'above_func': 'sq', 'above_target': 3.6},
    'EGG': {'base': 50, 'I0': MARKET_I0, 'T': 332, 'below_func': 'hinge', 'below_target': 0.4, 'above_func': 'log', 'above_target': 0.2},
    'MILK': {'base': 160, 'I0': MARKET_I0, 'T': 122, 'below_func': 'sqrt', 'below_target': 0.6, 'above_func': 'linear', 'above_target': 1.6},
    'WOOL': {'base': 200, 'I0': MARKET_I0, 'T': 105, 'below_func': 'log', 'below_target': 0.2, 'above_func': 'sq', 'above_target': 3.2},
    'FERTILIZER': {'base': 100, 'I0': MARKET_I0, 'T': 200, 'below_func': 'linear', 'below_target': 0.4, 'above_func': 'linear', 'above_target': 0.4},
}

SHOPS = {
    'BAKERY': ('EGG', 'WHEAT'),
    'PIZZA_SHOP': ('MILK', 'TOMATO', 'WHEAT'),
    'BRUNCH_SPOT': ('EGG', 'WHEAT', 'STRAWBERRY'),
    'YARN_STORE': ('WOOL',),
    'ICE_CREAM_SHOP': ('STRAWBERRY', 'MILK', 'WHEAT'),
    'SMOOTHIE_SHOP': ('STRAWBERRY', 'MILK'),
    'PET_CAFE': ('CARROT',),
    'FARMERS_MARKET': ('WHEAT', 'CARROT', 'TOMATO', 'STRAWBERRY'),
}
SHOP_NAMES = tuple(sorted(SHOPS))

FARMER_MOVES = {'NORTH': (0, -1), 'SOUTH': (0, 1), 'EAST': (1, 0), 'WEST': (-1, 0)}
LAND_ORDER = ('NE', 'SW', 'SE')
LAND_PRICES = (1000, 2000, 4000)
SHED_TILES = ((4, 4), (5, 4), (4, 5), (5, 5))
SHED_SET = frozenset(SHED_TILES)

WORKER_OPS = frozenset((
    'NORTH', 'SOUTH', 'EAST', 'WEST', 'PASS',
    'PICKUP', 'PLANT', 'WATER', 'HARVEST', 'FERTILIZE',
    'BUILD_COOP', 'BUILD_PASTURE', 'DIG', 'PLACE',
    'FEED', 'COLLECT_FERTILIZER', 'CARE', 'DROP',
))
MARKET_OPS = frozenset(('BUY_SEED', 'BUY_PRODUCT', 'BUY_ANIMAL', 'SELL', 'HIRE', 'BUY_LAND'))


def sd(p):
    return max(0, 4 - p[0], p[0] - 5) + max(0, 4 - p[1], p[1] - 5)


def dist(a, b):
    return abs(a[0] - b[0]) + abs(a[1] - b[1])


def nearest_shed_tile(pos):
    return (min(5, max(4, pos[0])), min(5, max(4, pos[1])))


def moves_between(a, b):
    x, y = a
    tx, ty = b
    step_x = 'EAST' if tx > x else 'WEST'
    step_y = 'SOUTH' if ty > y else 'NORTH'
    return [[step_x] for _ in range(abs(tx - x))] + [[step_y] for _ in range(abs(ty - y))]


def quadrant_of(x, y):
    return ('N' if y < 5 else 'S') + ('W' if x < 5 else 'E')


@lru_cache(maxsize=64)
def fibcost(n):
    a, b, total = 1, 1, 0
    for _ in range(max(0, int(n))):
        total, a, b = total + a, b, a + b
    return total


def _curve_shape(func, x, T):
    if x <= 0.0:
        return 0.0
    if func == 'linear':
        return x
    if func == 'sq':
        return x * x
    if func == 'sqrt':
        return math.sqrt(x)
    if func == 'log':
        return math.log(1.0 + x)
    if func == 'log10':
        return math.log10(1.0 + x)
    if func == 'hinge':
        if not T or T <= 0:
            return x
        u = x / T
        return u + (HINGE_GAIN * (u - 1.0) * (u - 1.0) if u > 1.0 else 0.0)
    return x


def quote_price(item, inventory, params=None):
    p = (params or MARKET_PARAMS)[item]
    base = p['base']
    i0 = p['I0']
    t_val = p['T']
    if inventory < i0:
        f = p['below_func']
        amp = p['below_target'] * base / _curve_shape(f, t_val, t_val)
        val = base + amp * _curve_shape(f, i0 - inventory, t_val)
    else:
        f = p['above_func']
        amp = p['above_target'] * base / _curve_shape(f, t_val, t_val)
        val = base - amp * _curve_shape(f, inventory - i0, t_val)
    res = int(round(val))
    return PRICE_FLOOR if res < PRICE_FLOOR else res


_LOW = MARKET_I0 - 6000
_HIGH = MARKET_I0 + 6000
_TABLE = {}


def get_price_table(item):
    entry = _TABLE.get(item)
    if entry is None:
        prices = [quote_price(item, i) for i in range(_LOW, _HIGH + 1)]
        prefix = [0] * (len(prices) + 1)
        acc = 0
        for idx, px in enumerate(prices):
            acc += px
            prefix[idx + 1] = acc
        lo = int(MARKET_PARAMS[item]['I0'])
        span = 1
        while quote_price(item, lo + span) > 1:
            span *= 2
        hi = lo + span
        while lo + 1 < hi:
            mid = (lo + hi) // 2
            if quote_price(item, mid) == 1:
                hi = mid
            else:
                lo = mid
        entry = (tuple(prices), tuple(prefix), hi)
        _TABLE[item] = entry
    return entry


for _it in PRODUCTS:
    get_price_table(_it)


def sell_fast(item, inv, n):
    if n <= 0:
        return 0, inv
    _, prefix, floor = _TABLE[item]
    step = floor - inv
    if step < 0:
        step = 0
    elif step > n:
        step = n
    if _LOW <= inv and inv + step <= _HIGH + 1:
        rev = prefix[inv + step - _LOW] - prefix[inv - _LOW] + (n - step)
        return rev, inv + step
    rev = 0
    for _ in range(n):
        p = quote_price(item, inv)
        rev += p
        if p > 1:
            inv += 1
    return rev, inv


def buy_fast(item, inv, n):
    if n <= 0:
        return 0, inv
    _, prefix, _ = _TABLE[item]
    if inv - n >= _LOW and inv <= _HIGH + 1:
        return prefix[inv - _LOW] - prefix[inv - n - _LOW], inv - n
    cost = 0
    for j in range(n):
        cost += quote_price(item, inv - j - 1)
    return cost, inv - n


def own_trade(market, item, quantity, is_sell):
    quantity = int(quantity)
    if quantity <= 0:
        return 0, int(market['inventory'][item])
    inv = int(market['inventory'][item])
    params = market.get('params')
    if not params:
        return sell_fast(item, inv, quantity) if is_sell else buy_fast(item, inv, quantity)
    total = 0
    for _ in range(quantity):
        px = quote_price(item, inv if is_sell else inv - 1, params)
        total += px
        if is_sell:
            if px > 1:
                inv += 1
        else:
            inv -= 1
    return total, inv


def batch_int(item, inv, own, rival):
    if own >= 0 and rival >= 0:
        paired = own if own < rival else rival
        floor = _TABLE[item][2]
        changing = (floor - inv + 1) // 2
        if changing < 0:
            changing = 0
        if paired < changing:
            changing = paired
        inv += 2 * changing
        if own >= rival:
            val, inv = sell_fast(item, inv, own - paired)
            return val, inv
        val, inv = sell_fast(item, inv, rival - paired)
        return -val, inv
    if own <= 0 and rival <= 0:
        a = -own
        b = -rival
        paired = a if a < b else b
        inv -= 2 * paired
        if own < rival:
            val, inv = buy_fast(item, inv, rival - own)
            return -val, inv
        val, inv = buy_fast(item, inv, own - rival)
        return val, inv
    sign = 1 if own > 0 else -1
    sold = own if own > 0 else rival
    bought = -rival if own > 0 else -own
    paired = sold if sold < bought else bought
    val = 0
    prices, _, _ = _TABLE[item]
    for _ in range(paired):
        ps = prices[inv - _LOW] if _LOW <= inv <= _HIGH else quote_price(item, inv)
        pb = prices[inv - 1 - _LOW] if _LOW <= inv - 1 <= _HIGH else quote_price(item, inv - 1)
        val += sign * (ps + pb)
        inv += (1 if ps > 1 else 0) - 1
    if sold > bought:
        extra, inv = sell_fast(item, inv, sold - bought)
        val += sign * extra
    elif bought > sold:
        extra, inv = buy_fast(item, inv, bought - sold)
        val += sign * extra
    return val, inv


def daily_int(item, inv, own, other, consumption, phase):
    first = consumption >> 1
    inv -= first
    if phase == 0:
        val, inv = batch_int(item, inv, own, other)
    elif phase == 1:
        a, inv = batch_int(item, inv, own, 0)
        b, inv = batch_int(item, inv, 0, other)
        val = a + b
    else:
        b, inv = batch_int(item, inv, 0, other)
        a, inv = batch_int(item, inv, own, 0)
        val = a + b
    return val, inv - (consumption - first)


def shop_daily_demand(shops):
    out = {k: 1 for k in TOWN_ITEMS}
    out['FERTILIZER'] = 0
    for s in shops:
        goods = SHOPS.get(s, ())
        inc = 12 if len(goods) == 1 else 6
        for k in goods:
            out[k] += inc
    return out


def empty_flows():
    return {k: [0.0] * 30 for k in PRODUCTS}


def clone_farm_state(farm):
    return {
        'money': float(farm['money']),
        'tiles': [[dict(t) if isinstance(t, dict) else t for t in row] for row in farm['tiles']],
        'farmer': list(farm['farmer']),
        'hands': [list(h) for h in farm['hands']],
        'unlocked_quadrants': list(farm['unlocked_quadrants']),
        'hires_today': int(farm.get('hires_today', 0)),
    }


def clone_private_state(priv):
    return {
        'shed': dict(priv['shed']),
        'seeds': dict(priv['seeds']),
        'inventories': [dict(inv) for inv in priv['inventories']],
    }


def make_plant_dict(crop, day):
    cd = CROPS[crop]
    return {
        'kind': 'PLANT',
        'crop': crop,
        'planted_day': day,
        'watered_today': False,
        'consecutive_unwatered': 1,
        'yield_units': 0 if cd['ongoing'] else 1,
        'max_lifespan_step': -1 if cd['ongoing'] else (day + cd['max_yield_day'] + 1) * 24,
        'fertilized_until_day': -1,
    }


def make_animal_dict(animal, day):
    ad = ANIMALS[animal]
    return {
        'kind': ad['structure'],
        'animal': animal,
        'placed_day': day,
        'yield_units': 0,
        'consecutive_unfed': 0,
        'fed_today': False,
        'cared_today': False,
        'fertilizer_available': False,
        'pending_care_bonus': 0,
    }


def sim_unit_op(farm, priv, idx, cmd, day):
    """Exact local unit simulator for preflight collision/overflow checks."""
    if not cmd:
        return
    op = cmd[0]
    pos = farm['farmer'] if idx == 0 else (farm['hands'][idx - 1] if idx - 1 < len(farm['hands']) else None)
    if pos is None:
        return
    fx, fy = int(pos[0]), int(pos[1])
    while len(priv['inventories']) <= idx:
        priv['inventories'].append({})
    inv = priv['inventories'][idx]
    if op in FARMER_MOVES:
        dx, dy = FARMER_MOVES[op]
        nx, ny = fx + dx, fy + dy
        if 0 <= nx < 10 and 0 <= ny < 10:
            if idx == 0:
                farm['farmer'] = [nx, ny]
            else:
                farm['hands'][idx - 1] = [nx, ny]
        return
    if op == 'PASS':
        return
    tile = farm['tiles'][fy][fx]
    if op == 'DROP':
        if (fx, fy) not in SHED_SET:
            return
        shed = priv['shed']
        for item, n in list(inv.items()):
            if n > 0:
                room = max(0, 100 - sum(shed.values()))
                take = min(n, room)
                if take > 0:
                    shed[item] = shed.get(item, 0) + take
            del inv[item]
        return
    if op == 'PICKUP':
        if (fx, fy) not in SHED_SET or len(cmd) < 2:
            return
        item = cmd[1]
        n = int(cmd[2]) if len(cmd) >= 3 else 1
        n = min(max(0, n), priv['shed'].get(item, 0))
        if n > 0:
            priv['shed'][item] -= n
            inv[item] = inv.get(item, 0) + n
        return
    if op == 'PLACE':
        if len(cmd) < 2:
            return
        item = cmd[1]
        if item in ANIMALS and isinstance(tile, dict) and tile.get('kind') == ANIMALS[item]['structure'] and 'animal' not in tile:
            if inv.get(item, 0) >= 1:
                inv[item] -= 1
                if inv[item] == 0:
                    del inv[item]
                farm['tiles'][fy][fx] = make_animal_dict(item, day)
            return
        if (fx, fy) in SHED_SET:
            n = int(cmd[2]) if len(cmd) >= 3 else 1
            n = min(max(0, n), inv.get(item, 0), max(0, 100 - sum(priv['shed'].values())))
            if n > 0:
                inv[item] -= n
                if inv[item] == 0:
                    del inv[item]
                priv['shed'][item] = priv['shed'].get(item, 0) + n
        return
    if tile == 'LOCKED':
        return
    if op == 'PLANT' and len(cmd) >= 2:
        crop = cmd[1]
        if crop in CROPS and tile is None and priv['seeds'].get(crop, 0) > 0:
            priv['seeds'][crop] -= 1
            farm['tiles'][fy][fx] = make_plant_dict(crop, day)
        return
    if op == 'WATER':
        if isinstance(tile, dict) and tile.get('kind') == 'PLANT' and not tile.get('watered_today'):
            tile['watered_today'] = True
            cd = CROPS[tile['crop']]
            if not cd['ongoing']:
                age = day - tile['planted_day']
                if (cd['max_yield_day'] + 1) // 2 <= age <= cd['max_yield_day']:
                    bonus = 2 if tile.get('fertilized_until_day', -1) >= day else 1
                    tile['yield_units'] = min(cd['max_yield'], tile.get('yield_units', 0) + bonus)
        return
    if op == 'HARVEST':
        if not isinstance(tile, dict) or tile.get('yield_units', 0) <= 0:
            return
        if tile.get('kind') == 'PLANT':
            cd = CROPS[tile['crop']]
            if day - tile['planted_day'] >= cd['first_yield_day']:
                inv[tile['crop']] = inv.get(tile['crop'], 0) + tile['yield_units']
                tile['yield_units'] = 0
                if not cd['ongoing']:
                    farm['tiles'][fy][fx] = None
        elif 'animal' in tile:
            prod = ANIMALS[tile['animal']]['product']
            inv[prod] = inv.get(prod, 0) + tile['yield_units']
            tile['yield_units'] = 0
        return
    if op == 'FERTILIZE':
        if isinstance(tile, dict) and tile.get('kind') == 'PLANT' and inv.get('FERTILIZER', 0) >= 1:
            inv['FERTILIZER'] -= 1
            if inv['FERTILIZER'] == 0:
                del inv['FERTILIZER']
            tile['fertilized_until_day'] = max(tile.get('fertilized_until_day', -1), day + 2)
        return
    if op == 'DIG':
        if tile is not None and not (isinstance(tile, dict) and 'animal' in tile):
            farm['tiles'][fy][fx] = None
        return
    if op == 'BUILD_COOP' and tile is None:
        farm['tiles'][fy][fx] = {'kind': 'COOP'}
        return
    if op == 'BUILD_PASTURE' and tile is None:
        farm['tiles'][fy][fx] = {'kind': 'PASTURE'}
        return
    if op == 'FEED':
        if isinstance(tile, dict) and 'animal' in tile and not tile.get('fed_today') and inv.get('WHEAT', 0) >= 1:
            inv['WHEAT'] -= 1
            if inv['WHEAT'] == 0:
                del inv['WHEAT']
            tile['fed_today'] = True
        return
    if op == 'COLLECT_FERTILIZER':
        if isinstance(tile, dict) and 'animal' in tile and tile.get('fertilizer_available'):
            tile['fertilizer_available'] = False
            inv['FERTILIZER'] = inv.get('FERTILIZER', 0) + 1
        return
    if op == 'CARE':
        if isinstance(tile, dict) and 'animal' in tile and not tile.get('cared_today'):
            tile['cared_today'] = True


# ============================================================================
# Layer 2: Full-Product Online Opponent Tracker & Adaptive Scenario MarketBook
# ============================================================================

def cohort_supply(farm, day, care=0.9, expansion=0.8, crop_weights=None):
    flows = empty_flows()
    active_crops = 0
    for row in farm['tiles']:
        for t in row:
            if not isinstance(t, dict):
                continue
            if 'animal' in t:
                a = ANIMALS[t['animal']]
                p = a['product']
                held = t.get('yield_units', 0)
                bonus = float(t.get('pending_care_bonus', 0))
                flows[p][day] += held
                for d in range(day, 30):
                    if d < 29:
                        flows['WHEAT'][d] -= 1.0
                    if d > day or t.get('fertilizer_available', False):
                        flows['FERTILIZER'][d] += 0.85 * care + 0.15
                    age = d + 1 - t['placed_day'] - a['first_yield_day']
                    if d < 29 and age >= 0 and age % a['interval'] == 0:
                        flows[p][d + 1] += min(a['max_held'], 1.0 + bonus)
                        bonus = 0.0
                    bonus += care
            elif t.get('kind') == 'PLANT':
                active_crops += 1
                c = t['crop']
                cd = CROPS[c]
                pl = t['planted_day']
                if cd['ongoing']:
                    flows[c][day] += t.get('yield_units', 0)
                    fu = t.get('fertilized_until_day', -1)
                    for j in range(cd['max_yield']):
                        d = pl + cd['first_yield_day'] + j * cd['interval']
                        if day < d < 30:
                            flows[c][d] += 1.0 + care
                            svc = d - 1
                            if svc >= day and svc > fu:
                                flows['FERTILIZER'][svc] -= care
                                fu = svc + 2
                else:
                    span = {'WHEAT': 3, 'CARROT': 2, 'MELON': 10}[c]
                    yld = {'WHEAT': 4.5 + 1.0 * care, 'CARROT': 2.0 + 1.2 * care, 'MELON': 5.0 + 1.0 * care}[c]
                    d = max(day, pl + span)
                    if d < 30:
                        flows[c][d] += yld
                        if c == 'WHEAT' and care > 0.5:
                            fd = max(day, pl + 2)
                            if t.get('fertilized_until_day', -1) < fd < 30:
                                flows['FERTILIZER'][fd] -= 0.7 * care
                    if expansion > 0.0 and c in ('WHEAT', 'CARROT'):
                        cur = d + span
                        while cur < 30:
                            flows[c][cur] += expansion * yld
                            cur += span
    return flows


class OpponentTracker:
    def __init__(self):
        self.reset()

    def reset(self):
        self.prev_step = -1
        self.prev_inv = None
        self.prev_shops = ()
        self.prev_opp_tiles = None
        self.last_own_orders = ()
        self.prev_own_shed = None
        self.prev_own_money = 0.0
        self.unsold = {k: 0.0 for k in PRODUCTS}
        self.care_sum = 4.8
        self.care_cnt = 6.0
        self.active_days = 0
        self.opp_crop_pref = Counter()

    def remember(self, obs, action):
        p = int(obs['player'])
        opp = 1 - p
        self.prev_step = int(obs['step'])
        self.prev_inv = dict(obs['market']['inventory'])
        self.prev_shops = tuple(obs['town']['unlocked_shops'])
        self.prev_opp_tiles = [
            [dict(t) if isinstance(t, dict) else t for t in row]
            for row in obs['farms'][opp]['tiles']
        ]
        self.last_own_orders = tuple(tuple(o) for o in (action.get('market') or ()) if o)
        self.prev_own_shed = dict(obs['private']['shed'])
        self.prev_own_money = float(obs['farms'][p]['money'])

    def update(self, obs):
        step = int(obs['step'])
        if self.prev_inv is None or step != self.prev_step + 1:
            return
        p = int(obs['player'])
        opp = 1 - p
        opp_farm = obs['farms'][opp]
        curr_inv = obs['market']['inventory']
        day_prev = self.prev_step // 24

        # 1. Estimate own executed market deltas on prev_step
        own_delta = Counter()
        shed_sim = Counter(self.prev_own_shed)
        cash_sim = self.prev_own_money
        for order in self.last_own_orders[:10]:
            op = order[0]
            if op == 'SELL' and len(order) >= 3:
                it = order[1]
                n = min(int(order[2]), shed_sim.get(it, 0))
                if n > 0:
                    rev, nxt_i = sell_fast(it, self.prev_inv[it] + own_delta[it], n)
                    own_delta[it] += nxt_i - (self.prev_inv[it] + own_delta[it])
                    shed_sim[it] -= n
                    cash_sim += rev
            elif op == 'BUY_PRODUCT' and len(order) >= 3:
                it = order[1]
                n = min(int(order[2]), max(0, 100 - sum(shed_sim.values())))
                bought = 0
                cur_i = self.prev_inv[it] + own_delta[it]
                for _ in range(n):
                    px = quote_price(it, cur_i - 1)
                    if cash_sim < px:
                        break
                    cash_sim -= px
                    cur_i -= 1
                    bought += 1
                own_delta[it] -= bought
                shed_sim[it] += bought
            elif op == 'HIRE':
                pass

        # 2. Town consumption on prev_step
        cons = Counter()
        if self.prev_step % 4 == 0:
            for s in self.prev_shops:
                goods = SHOPS.get(s, ())
                inc = 2 if len(goods) == 1 else 1
                for k in goods:
                    cons[k] += inc
        if self.prev_step % 24 == 0:
            for k in TOWN_ITEMS:
                cons[k] += 1

        # 3. Rival field harvests between prev_step and step
        harvested = Counter()
        if step % 24 != 0 and self.prev_opp_tiles is not None:
            for y in range(10):
                for x in range(10):
                    old_t = self.prev_opp_tiles[y][x]
                    new_t = opp_farm['tiles'][y][x]
                    if not isinstance(old_t, dict):
                        continue
                    if 'animal' in old_t:
                        prod = ANIMALS[old_t['animal']]['product']
                        old_y = old_t.get('yield_units', 0)
                        new_y = new_t.get('yield_units', 0) if isinstance(new_t, dict) else 0
                        if old_y > new_y:
                            harvested[prod] += old_y - new_y
                        if old_t.get('fertilizer_available') and isinstance(new_t, dict) and not new_t.get('fertilizer_available'):
                            harvested['FERTILIZER'] += 1
                    elif old_t.get('kind') == 'PLANT':
                        c = old_t['crop']
                        cd = CROPS[c]
                        old_y = old_t.get('yield_units', 0)
                        if cd['ongoing']:
                            new_y = new_t.get('yield_units', 0) if isinstance(new_t, dict) and new_t.get('crop') == c else 0
                            if old_y > new_y and day_prev - old_t['planted_day'] >= cd['first_yield_day']:
                                harvested[c] += old_y - new_y
                        else:
                            if (new_t is None or (isinstance(new_t, dict) and new_t.get('planted_day') == step // 24)) and day_prev - old_t['planted_day'] >= cd['first_yield_day']:
                                harvested[c] += max(1, old_y)

        # 4. Infer rival market sales and update unsold stock
        cap = 100.0 + 16.0 * (1 + len(opp_farm.get('hands') or ()))
        for k in PRODUCTS:
            floor = _TABLE[k][2]
            expected_no_rival = self.prev_inv[k] + own_delta[k] - cons[k]
            rival_delta = curr_inv[k] - expected_no_rival
            sold = max(0, rival_delta) if curr_inv[k] < floor - 2 else int(round(self.unsold[k] * 0.5))
            val = self.unsold[k] * (0.92 if step % 24 == 0 else 0.995) + harvested[k] - sold
            self.unsold[k] = min(cap, max(0.0, val))

        # 5. End-of-day rival behavioral profile update
        if step % 24 == 22:
            cared = 0
            animals = 0
            crops_seen = Counter()
            for row in opp_farm['tiles']:
                for t in row:
                    if isinstance(t, dict):
                        if 'animal' in t:
                            animals += 1
                            if t.get('cared_today') and t.get('fed_today'):
                                cared += 1
                        elif t.get('kind') == 'PLANT':
                            crops_seen[t['crop']] += 1
            if animals > 0:
                self.care_sum += cared
                self.care_cnt += animals
            elif len(opp_farm.get('hands') or ()) == 0 and sum(crops_seen.values()) == 0:
                self.care_cnt += 1.5
            if len(opp_farm.get('hands') or ()) > 0 or sum(crops_seen.values()) > 0 or animals > 0:
                self.active_days += 1
            for c, n in crops_seen.items():
                self.opp_crop_pref[c] = 0.7 * self.opp_crop_pref[c] + 0.3 * n

    def care_estimate(self):
        return max(0.05, min(1.0, self.care_sum / max(1.0, self.care_cnt)))

    def renewal_estimate(self, obs):
        opp_farm = obs['farms'][1 - int(obs['player'])]
        hands = len(opp_farm.get('hands') or ())
        quads = len(opp_farm.get('unlocked_quadrants') or ('NW',))
        if hands == 0 and self.active_days == 0 and int(obs['step']) >= 48:
            return 0.05
        return min(1.15, 0.35 + 0.05 * hands + 0.12 * (quads - 1))


class MarketBook:
    def __init__(self, obs, tracker=None, extra_rival_flow=None, fixed_rival_flow=None, n_scenarios=9):
        self.obs = obs
        self.day = int(obs['step']) // 24
        self.known = list(obs['town']['unlocked_shops'])
        self.farm = obs['farms'][int(obs['player'])]
        self.quads = len(self.farm['unlocked_quadrants'])
        self._parts = {}
        self._marginals = {}
        self.initial = {k: int(obs['private']['shed'].get(k, 0)) for k in PRODUCTS}
        for inv in obs['private']['inventories']:
            for k, n in inv.items():
                if k in self.initial:
                    self.initial[k] += int(n)
        self.hold_inputs = True
        self.sales_lag = 0.0

        base_care = tracker.care_estimate() if tracker is not None else 0.85
        base_renew = tracker.renewal_estimate(obs) if tracker is not None else 0.90
        unsold = tracker.unsold if tracker is not None else {}
        crop_pref = tracker.opp_crop_pref if tracker is not None else None
        opp_farm = obs['farms'][1 - int(obs['player'])]

        care_levels = (max(0.0, base_care - 0.20), base_care, min(1.0, base_care + 0.12))
        renew_levels = (max(0.0, base_renew * 0.65), base_renew, min(1.25, base_renew * 1.20 + 0.10))

        opp_active = 0
        opp_perennial_crops = 0
        opp_animals = 0
        for row in opp_farm['tiles']:
            for t in row:
                if isinstance(t, dict):
                    opp_active += 1
                    if 'animal' in t:
                        opp_animals += 1
                    elif t.get('crop') in ('STRAWBERRY', 'TOMATO'):
                        opp_perennial_crops += 1
        opp_hands = len(opp_farm.get('hands') or ())

        self.scenarios = []
        for idx in range(n_scenarios):
            rng = random.Random(191919 + self.day * 97 + idx)
            shops = self.known[:]
            demands = []
            for d in range(30):
                if d >= self.day:
                    target_n = min(8, d // 3)
                    while len(shops) < target_n:
                        shops.append(rng.choice(SHOP_NAMES))
                demands.append(shop_daily_demand(shops))
            c_lvl = care_levels[idx % 3]
            r_lvl = renew_levels[(idx // 3) % 3]
            if fixed_rival_flow is not None:
                riv = {k: [float(v) for v in fixed_rival_flow[k]] for k in PRODUCTS}
            else:
                riv = cohort_supply(opp_farm, self.day, c_lvl, r_lvl, crop_pref)

                # Project staggered future rival entry into long-cycle crops & livestock so CropDP does not procrastinate
                if 1 <= self.day <= 16 and (opp_active > 0 or opp_hands > 0):
                    scale = min(1.0, c_lvl * min(1.0, (opp_active + 4 * opp_hands) / 20.0))
                    if scale > 0.15 and r_lvl > 0.20:
                        rem_crops = max(0.0, 24.0 * scale - opp_perennial_crops) * min(1.0, r_lvl)
                        rem_anim = max(0.0, 12.0 * scale - opp_animals) * min(1.0, r_lvl)
                        waves = sorted({min(15, max(5, self.day + 1)), min(15, max(7, self.day + 4)), min(16, max(10, self.day + 7))})
                        waves = [w_d for w_d in waves if w_d > self.day and w_d <= 16]
                        if waves:
                            w_share = 1.0 / len(waves)
                            pref_s = 1.0 + 0.12 * (crop_pref.get('STRAWBERRY', 0.0) if crop_pref else 0.0)
                            pref_t = 1.0 + 0.12 * (crop_pref.get('TOMATO', 0.0) if crop_pref else 0.0)
                            for w_d in waves:
                                dem_c = demands[min(29, w_d + 10)]
                                ws = (1.8 + dem_c['STRAWBERRY'] * 1.40) * pref_s
                                wt = (1.0 + dem_c['TOMATO'] * 0.70) * pref_t
                                c_tot = rem_crops * w_share
                                n_st = c_tot * (ws / (ws + wt))
                                n_tm = c_tot - n_st
                                for j in range(CROPS['STRAWBERRY']['max_yield']):
                                    hd = w_d + CROPS['STRAWBERRY']['first_yield_day'] + j * CROPS['STRAWBERRY']['interval']
                                    if hd < 30:
                                        riv['STRAWBERRY'][hd] += n_st * (1.0 + c_lvl)
                                for j in range(CROPS['TOMATO']['max_yield']):
                                    hd = w_d + CROPS['TOMATO']['first_yield_day'] + j * CROPS['TOMATO']['interval']
                                    if hd < 30:
                                        riv['TOMATO'][hd] += n_tm * (1.0 + c_lvl)
                                dem_a = demands[min(29, w_d + 7)]
                                wm = 1.4 + dem_a['MILK'] * 0.85
                                ww = 1.1 + dem_a['WOOL'] * 1.50
                                a_tot = rem_anim * w_share
                                n_cw = a_tot * (wm / (wm + ww))
                                n_sh = a_tot - n_cw
                                hd = w_d + ANIMALS['COW']['first_yield_day']
                                while hd < 30:
                                    riv['MILK'][hd] += n_cw * (1.0 + 2.0 * c_lvl)
                                    hd += ANIMALS['COW']['interval']
                                hd = w_d + ANIMALS['SHEEP']['first_yield_day']
                                while hd < 30:
                                    riv['WOOL'][hd] += n_sh * (1.0 + 3.0 * c_lvl)
                                    hd += ANIMALS['SHEEP']['interval']

            for k in PRODUCTS:
                u = unsold.get(k, 0.0)
                if u >= 1.0:
                    riv[k][self.day] += u * (0.75 if idx % 3 == 0 else 1.0)
                if extra_rival_flow and k in extra_rival_flow:
                    for d in range(self.day, 30):
                        riv[k][d] += extra_rival_flow[k][d]
            self.scenarios.append((
                idx % 3,
                {k: tuple(int(round(v)) for v in riv[k]) for k in PRODUCTS},
                {k: tuple(int(demands[d][k]) for d in range(30)) for k in PRODUCTS},
            ))

        rate = 0.018 if self.day <= 12 else (0.010 if self.day <= 18 else 0.0)
        self.day_weight = [
            1.0 if d <= self.day else (1.0 / (1.0 + rate * (d - self.day)))
            for d in range(30)
        ]

    def _effective_flows(self, item, flows):
        lag = self.sales_lag if item not in ('WHEAT', 'FERTILIZER', 'MELON') else 0.0
        if lag <= 0.0:
            return flows
        out = list(flows)
        for d in range(self.day, 29):
            if out[d] > 0.0:
                shift = lag * out[d]
                out[d] -= shift
                out[d + 1] += shift
        return out

    def material_trades(self, item, flows):
        trades = [int(round(v)) for v in flows]
        stock = self.initial[item]
        if not self.hold_inputs or item != 'WHEAT' or int(self.obs['market']['inventory']['WHEAT']) >= 10050:
            trades[self.day] += stock
            return trades
        cap = 22
        for d in range(self.day, 30):
            stock += trades[d]
            if stock < 0:
                trades[d] = stock
                stock = 0
                continue
            needed = 0
            for fut in range(d + 1, min(30, d + 4)):
                needed -= int(round(flows[fut]))
            keep = min(cap, max(0, needed)) if d < 29 else 0
            sold = max(0, stock - keep)
            trades[d] = sold
            stock -= sold
        trades[29] += stock
        return trades

    def _forward_value(self, item, start_day, start_inv, own, rival, cons, phase):
        total = 0.0
        inv = start_inv
        w = self.day_weight
        for d in range(start_day, 30):
            gain, inv = daily_int(item, inv, own[d], rival[d], cons[d], phase)
            total += gain * w[d]
        return total

    def component(self, item, raw_flows, want_gradient=False):
        key = (item, tuple(round(v, 3) for v in raw_flows[self.day:]))
        if key in self._parts and (not want_gradient or key in self._marginals):
            return self._parts[key], self._marginals.get(key)
        flows = self._effective_flows(item, raw_flows)
        own = self.material_trades(item, flows)
        mat_paths = None
        if want_gradient and self.hold_inputs and item == 'WHEAT' and int(self.obs['market']['inventory']['WHEAT']) < 10050:
            mat_paths = {}
            for d in range(self.day, 30):
                p_f = list(raw_flows)
                m_f = list(raw_flows)
                p_f[d] += 1.0
                m_f[d] -= 1.0
                pa = self.material_trades(item, self._effective_flows(item, p_f))
                ma = self.material_trades(item, self._effective_flows(item, m_f))
                last_d = d
                for t in range(29, self.day - 1, -1):
                    if pa[t] != own[t] or ma[t] != own[t]:
                        last_d = t
                        break
                mat_paths[d] = (pa, ma, max(self.day, d - 3), last_d)

        inv0 = int(self.obs['market']['inventory'][item])
        w = self.day_weight
        values = []
        gradients = []
        lag = self.sales_lag if item not in ('WHEAT', 'FERTILIZER', 'MELON') else 0.0

        for phase, rival_map, cons_map in self.scenarios:
            riv = rival_map[item]
            cons = cons_map[item]
            invs = [0] * 31
            gains = [0.0] * 30
            invs[self.day] = inv0
            tot = 0.0
            for d in range(self.day, 30):
                gain, nxt = daily_int(item, invs[d], own[d], riv[d], cons[d], phase)
                gains[d] = gain * w[d]
                invs[d + 1] = nxt
                tot += gains[d]
            values.append(tot)

            if want_gradient:
                # Backward O(T) shift-DP table for shifts -2..+2 around baseline invs[d]
                dp_shift = [[0.0] * 5 for _ in range(31)]
                for d in range(29, self.day - 1, -1):
                    row_dp = dp_shift[d]
                    nxt_dp = dp_shift[d + 1]
                    base_i = invs[d]
                    nxt_base = invs[d + 1]
                    own_d = own[d]
                    riv_d = riv[d]
                    cons_d = cons[d]
                    wd = w[d]
                    row_dp[2] = gains[d] + nxt_dp[2]
                    for s_idx in (0, 1, 3, 4):
                        gs, ns = daily_int(item, base_i + (s_idx - 2), own_d, riv_d, cons_d, phase)
                        ds = ns - nxt_base
                        tail = nxt_dp[ds + 2] if -2 <= ds <= 2 else self._forward_value(item, d + 1, ns, own, riv, cons, phase)
                        row_dp[s_idx] = gs * wd + tail

                g = [0.0] * 30
                if mat_paths is not None:
                    base_tot = dp_shift[self.day][2]
                    for d in range(self.day, 30):
                        pa, ma, t0, t1 = mat_paths[d]
                        prefix = base_tot - dp_shift[t0][2]
                        ip = invs[t0]
                        im = ip
                        vp = prefix
                        vm = prefix
                        for t in range(t0, t1 + 1):
                            wt = w[t]
                            gp_t, ip = daily_int(item, ip, pa[t], riv[t], cons[t], phase)
                            gm_t, im = daily_int(item, im, ma[t], riv[t], cons[t], phase)
                            vp += gp_t * wt
                            vm += gm_t * wt
                        if t1 + 1 <= 29:
                            dsp = ip - invs[t1 + 1]
                            dsm = im - invs[t1 + 1]
                            vp += dp_shift[t1 + 1][dsp + 2] if -2 <= dsp <= 2 else self._forward_value(item, t1 + 1, ip, pa, riv, cons, phase)
                            vm += dp_shift[t1 + 1][dsm + 2] if -2 <= dsm <= 2 else self._forward_value(item, t1 + 1, im, ma, riv, cons, phase)
                        g[d] = 0.5 * (vp - vm)
                else:
                    raw_g = [0.0] * 30
                    for d in range(self.day, 30):
                        cur_i = invs[d]
                        nxt_i = invs[d + 1]
                        nxt_dp = dp_shift[d + 1]
                        gp, np_i = daily_int(item, cur_i, own[d] + 1, riv[d], cons[d], phase)
                        gm, nm_i = daily_int(item, cur_i, own[d] - 1, riv[d], cons[d], phase)
                        dsp = np_i - nxt_i
                        dsm = nm_i - nxt_i
                        tp = nxt_dp[dsp + 2] if -2 <= dsp <= 2 else self._forward_value(item, d + 1, np_i, own, riv, cons, phase)
                        tm = nxt_dp[dsm + 2] if -2 <= dsm <= 2 else self._forward_value(item, d + 1, nm_i, own, riv, cons, phase)
                        raw_g[d] = 0.5 * (gp * w[d] + tp - gm * w[d] - tm)
                    if lag > 0.0:
                        for d in range(self.day, 29):
                            g[d] = (1.0 - lag) * raw_g[d] + lag * raw_g[d + 1]
                        g[29] = raw_g[29]
                    else:
                        g = raw_g
                gradients.append(g)

        self._parts[key] = values
        if want_gradient:
            self._marginals[key] = gradients
        return values, gradients

    @staticmethod
    def downside_idx(count):
        return max(0, min(count - 1, int((count - 1) * 0.20)))

    def score(self, own_flows, risk=0.25):
        n_s = len(self.scenarios)
        totals = [0.0] * n_s
        for k in PRODUCTS:
            vals, _ = self.component(k, own_flows[k], False)
            for i in range(n_s):
                totals[i] += vals[i]
        totals.sort()
        return (1.0 - risk) * (sum(totals) / n_s) + risk * totals[self.downside_idx(n_s)]

    def marginal(self, own_flows, risk=0.25):
        n_s = len(self.scenarios)
        totals = [0.0] * n_s
        grads_by_item = {}
        for k in PRODUCTS:
            vals, grads = self.component(k, own_flows[k], True)
            grads_by_item[k] = grads
            for i in range(n_s):
                totals[i] += vals[i]
        tail_i = sorted(range(n_s), key=totals.__getitem__)[self.downside_idx(n_s)]
        out = {k: [0.0] * 30 for k in PRODUCTS}
        inv_n = 1.0 / n_s
        for k in PRODUCTS:
            grads = grads_by_item[k]
            row_out = out[k]
            for d in range(self.day, 30):
                s = 0.0
                for g in grads:
                    s += g[d]
                row_out[d] = (1.0 - risk) * (s * inv_n) + risk * grads[tail_i][d]
        return out


# ============================================================================
# Layer 3: Fast Finite-Horizon CropDP & Animal Lifecycle DP
# ============================================================================

class Project:
    __slots__ = ('value', 'days')

    def __init__(self, value, days):
        self.value = float(value)
        self.days = days

    def first(self, day):
        for row in self.days:
            if row[0] == day:
                return row
            if row[0] > day:
                break
        return (day, (), (), ())


EMPTY_PROJECT = Project(0.0, ())


@lru_cache(maxsize=65536)
def _crop_transitions(crop, planted, day, dry, fertilized_until, held, end, already_watered=False):
    cd = CROPS[crop]
    ongoing = cd['ongoing']
    age = day - planted
    last = cd['first_yield_day'] + (cd['max_yield'] - 1) * cd['interval'] if ongoing else cd['max_yield_day']
    transitions = []
    water_choices = (0,) if already_watered else (0, 1)
    for water in water_choices:
        eff_water = bool(water or already_watered)
        if not eff_water and dry >= 1 and day < end:
            continue
        for fertilize in (0, 1):
            if fertilize:
                if not eff_water or fertilized_until >= day + 1:
                    continue
                if already_watered and not ongoing:
                    continue
                if not ongoing and not ((cd['max_yield_day'] + 1) // 2 <= age <= last):
                    continue
            fu = day + 2 if fertilize else fertilized_until
            ripe = held
            if water and not ongoing and (cd['max_yield_day'] + 1) // 2 <= age <= last:
                ripe = min(cd['max_yield'], ripe + (2 if fu >= day else 1))
            can_harvest = ripe > 0 and age >= cd['first_yield_day']
            for harvest in ((0, 1) if can_harvest else (0,)):
                ops = (('FERTILIZE',),) * fertilize + (('WATER',),) * water + (('HARVEST',),) * harvest
                outputs = ((crop, ripe),) if harvest else ()
                inputs = (('FERTILIZER', 1),) if fertilize else ()
                short = bool(harvest and not ongoing)
                if short:
                    nxt = None
                else:
                    next_held = 0 if harvest else ripe
                    if ongoing:
                        since = age + 1 - cd['first_yield_day']
                        if since >= 0 and since % cd['interval'] == 0 and since // cd['interval'] < cd['max_yield']:
                            next_held = min(cd['max_yield'], next_held + (2 if eff_water and fu >= day else 1))
                    nxt = (day + 1, 0 if eff_water else dry + 1, fu if fu >= day + 1 else -1, next_held)
                transitions.append((fertilize, water, harvest, ripe, short, nxt, ops, outputs, inputs))
    return tuple(transitions)


@lru_cache(maxsize=65536)
def _animal_transitions(animal, placed, end, d, hungry, bonus, held, fert, already_fed, already_cared):
    ad = ANIMALS[animal]
    product = ad['product']
    transitions = []
    for feed in ((0,) if already_fed else (0, 1)):
        for care in ((0,) if already_cared else (0, 1)):
            eff_feed = bool(feed or already_fed)
            if care and (not eff_feed or d >= end - 1):
                continue
            for harvest in ((0, 1) if held else (0,)):
                for collect in ((0, 1) if fert else (0,)):
                    count = feed + care + harvest + collect
                    starvation = 0 if eff_feed else hungry + 1
                    if starvation >= 2:
                        nxt = None
                    else:
                        nxt_held = 0 if harvest else held
                        b = bonus
                        age = d + 1 - placed - ad['first_yield_day']
                        if age >= 0 and age % ad['interval'] == 0:
                            nxt_held = min(ad['max_held'], nxt_held + 1 + (b if eff_feed else 0))
                            b = 0
                        if eff_feed and (care or already_cared):
                            b += 1
                        nxt = (d + 1, starvation, b, nxt_held, 1)
                    ops = (
                        (('FEED',),) * feed
                        + (('CARE',),) * care
                        + (('HARVEST',),) * harvest
                        + (('COLLECT_FERTILIZER',),) * collect
                    )
                    inputs = (('WHEAT', 1),) if feed else ()
                    outputs = (((product, held),) if harvest else ()) + ((('FERTILIZER', 1),) if collect else ())
                    transitions.append((feed, harvest, collect, count, nxt, ops, inputs, outputs))
    return tuple(transitions)


class CropDP:
    def __init__(self, prices, labor, visit, end=29, allowed=None, start=0, melon_race_days=None, no_plant_today=False):
        self.prices = prices
        self.labor = labor
        self.visit = visit
        self.end = end
        self.allowed = tuple(() if allowed is not None and len(allowed) == 0 else (allowed or CROPS))
        self.melon_race_days = melon_race_days or {}
        self.free = {end + 1: EMPTY_PROJECT}
        self._reuse_parts = {}
        self._solvers = {}
        for d in range(end, start - 1, -1):
            best = self.free[d + 1]
            if not (no_plant_today and d == start):
                for crop in self.allowed:
                    cd = CROPS[crop]
                    if d + cd['first_yield_day'] > end:
                        continue
                    future = self._solver(crop, d)(d + 1, 0, -1, 0 if cd['ongoing'] else 1, False)
                    cost = cd['seed'] + 2.0 * self.labor[d] + self.visit[d]
                    val = future.value - cost
                    if val > best.value:
                        best = Project(
                            val,
                            ((d, (('PLANT', crop), ('WATER',)), (), (('SEED_' + crop, 1),)),) + future.days,
                        )
            self.free[d] = best

    def reuse_parts(self, day):
        part = self._reuse_parts.get(day)
        if part is None:
            proj = self.free[day]
            part = (proj.first(day), tuple(r for r in proj.days if r[0] != day))
            self._reuse_parts[day] = part
        return part

    def _solver(self, crop, planted):
        key = (crop, planted)
        cached = self._solvers.get(key)
        if cached is not None:
            return cached
        cd = CROPS[crop]
        ongoing = cd['ongoing']
        last = cd['first_yield_day'] + (cd['max_yield'] - 1) * cd['interval'] if ongoing else cd['max_yield_day']
        is_melon = (crop == 'MELON')
        race_map = self.melon_race_days

        @lru_cache(None)
        def solve(day, dry, fertilized_until, held, already_watered=False):
            if day > self.end:
                return EMPTY_PROJECT
            age = day - planted
            if age > last:
                following = self.free[day]
                today, tail = self.reuse_parts(day)
                return Project(
                    following.value - self.labor[day] - (0.0 if today[1] else self.visit[day]),
                    ((day, (('DIG',),) + today[1], today[2], today[3]),) + tail,
                )
            best_value = -1e100
            winner = None
            reuse = self.free[day]
            reuse_today, reuse_tail = self.reuse_parts(day)
            endings = [((('DIG',),), (), 0.0)]
            if ongoing and held > 0 and age >= cd['first_yield_day']:
                endings.append(((('HARVEST',), ('DIG',)), ((crop, held),), held * self.prices[crop][day]))
            for finish_ops, finish_out, rev in endings:
                val = reuse.value + rev - len(finish_ops) * self.labor[day]
                if not reuse_today[1]:
                    val -= self.visit[day]
                if val > best_value:
                    best_value = val
                    winner = ('ending', finish_ops, finish_out, reuse_today, reuse_tail)

            crop_px = self.prices[crop][day]
            fert_px = self.prices['FERTILIZER'][day]
            lab = self.labor[day]
            vis = self.visit[day]
            race_bonus_unit = race_map.get(day, 0.0) if is_melon else 0.0

            for fertilize, water, harvest, ripe, short, nxt, ops, outputs, inputs in _crop_transitions(
                crop, planted, day, dry, fertilized_until, held, self.end, already_watered
            ):
                val = (ripe * crop_px if harvest else 0.0) - fertilize * fert_px
                if harvest and race_bonus_unit > 0.0 and water == 0 and ripe >= 6:
                    # Dawn-ready 6-yield melon harvests in 1 op and beats rival Day-10 cohorts to market!
                    val += ripe * race_bonus_unit
                val -= len(ops) * lab + (vis if ops else 0.0)
                if short:
                    future = self.free[day]
                    r_today, r_tail = self.reuse_parts(day)
                    if r_today[1]:
                        val += vis
                else:
                    future = solve(nxt[0], nxt[1], nxt[2], nxt[3], False)
                    r_today, r_tail = None, None
                val += future.value
                if val > best_value:
                    best_value = val
                    winner = ('action', short, ops, outputs, inputs, future, r_today, r_tail)

            if winner[0] == 'ending':
                _, f_ops, f_out, r_today, r_tail = winner
                path = ((day, f_ops + r_today[1], f_out + r_today[2], r_today[3]),) + r_tail
            else:
                _, short, ops, outputs, inputs, future, r_today, r_tail = winner
                if short:
                    path = ((day, ops + r_today[1], outputs + r_today[2], inputs + r_today[3]),) + r_tail
                else:
                    path = ((day, ops, outputs, inputs),) + future.days
            return Project(best_value, path)

        self._solvers[key] = solve
        return solve

    def existing(self, tile, day):
        fu = tile.get('fertilized_until_day', -1)
        return self._solver(tile['crop'], tile['planted_day'])(
            day,
            int(tile.get('consecutive_unwatered', 0)),
            fu if fu >= day else -1,
            int(tile.get('yield_units', 0)),
            bool(tile.get('watered_today', False)),
        )


def animal_project(animal, placed, day, tile, prices, labor, visit, end=29, reuse=None):
    ad = ANIMALS[animal]
    product = ad['product']
    death_cache = {}

    def after_death(d):
        if reuse is None or d >= end:
            return EMPTY_PROJECT
        cached = death_cache.get(d)
        if cached is not None:
            return cached
        free = reuse[d + 1]
        row = free.first(d + 1)
        if row[1]:
            fut = Project(
                free.value - labor[d + 1],
                ((d + 1, (('DIG',),) + row[1], row[2], row[3]),) + tuple(x for x in free.days if x[0] != d + 1),
            )
        else:
            path = []
            cleared = False
            adj_val = free.value
            for entry in free.days:
                dd, oo, yy, ii = entry
                if oo and not cleared:
                    oo = (('DIG',),) + oo
                    cleared = True
                    adj_val -= labor[dd]
                path.append((dd, oo, yy, ii))
            fut = Project(adj_val, tuple(path))
        death_cache[d] = fut
        return fut

    @lru_cache(None)
    def solve(d, hungry, bonus, held, fert):
        if d > end:
            return EMPTY_PROJECT
        best_val = 0.0
        winner = None
        already_fed = (d == day and bool(tile.get('fed_today', False)))
        already_cared = (d == day and bool(tile.get('cared_today', False)))
        prod_px = prices[product][d]
        fert_px = prices['FERTILIZER'][d]
        wheat_px = prices['WHEAT'][d]
        lab = labor[d]
        vis = visit[d]
        for feed, harvest, collect, count, nxt, ops, inputs, outputs in _animal_transitions(
            animal, placed, end, d, hungry, bonus, held, fert, already_fed, already_cared
        ):
            val = (held * prod_px if harvest else 0.0) + (fert_px if collect else 0.0) - feed * wheat_px
            val -= count * lab + (vis if count else 0.0)
            fut = after_death(d) if nxt is None else solve(*nxt)
            val += fut.value
            if val > best_val:
                best_val = val
                winner = (ops, outputs, inputs, fut)
        if winner is None:
            return EMPTY_PROJECT
        ops, outputs, inputs, fut = winner
        return Project(best_val, ((d, ops, outputs, inputs),) + fut.days)

    return solve(
        day,
        int(tile.get('consecutive_unfed', 0)),
        int(tile.get('pending_care_bonus', 0)),
        int(tile.get('yield_units', 0)),
        int(bool(tile.get('fertilizer_available', False))),
    )


# ============================================================================
# Layer 5: Ultra-Fast 5-Tuple Resource-Aware Route Solver
# ============================================================================

RES_NAMES = ('WHEAT', 'FERTILIZER', 'COW', 'SHEEP', 'GOOSE')
RES_IDX = {'WHEAT': 0, 'FERTILIZER': 1, 'COW': 2, 'SHEEP': 3, 'GOOSE': 4}


def job_needs_counter(ops):
    out = Counter()
    for op in ops:
        if not op:
            continue
        k = op[0]
        if k == 'FEED':
            out['WHEAT'] += 1
        elif k == 'FERTILIZE':
            out['FERTILIZER'] += 1
        elif k == 'PLACE' and len(op) > 1 and op[1] in ANIMALS:
            out[op[1]] += 1
    return out


def solve_routes(
    jobs,
    workers,
    shed,
    last_hour=23,
    shed_positions=SHED_TILES,
    track_collect=True,
    evaluation_budget=6000,
    return_to_shed=False,
    end_service=1,
    selective_return=False,
    delivery_jobs=None,
):
    n = len(jobs)
    m = len(workers)
    if m == 0:
        return {
            'routes': [],
            'unassigned': list(range(n)),
            'pickups': [],
            'pickup_positions': [],
            'end_positions': [],
            'return_to_shed': return_to_shed,
            'finish_hours': [],
            'stats': {'evaluations': 0, 'assigned': 0, 'reserved': {}},
        }

    positions = [tuple(j['pos']) for j in jobs]
    lengths = [len(j.get('ops', ())) for j in jobs]
    deadlines = [min(last_hour, int(j.get('deadline', last_hour))) + 1 for j in jobs]
    has_cargo_job = []
    need_vecs = []
    net_vecs = []

    for idx_j, job in enumerate(jobs):
        net = [0, 0, 0, 0, 0]
        need = [0, 0, 0, 0, 0]
        gives_cargo = False
        for op in job.get('ops', ()):
            if not op:
                continue
            kind = op[0]
            r_i = -1
            delta = 0
            if kind == 'FEED':
                r_i, delta = 0, -1
            elif kind == 'FERTILIZE':
                r_i, delta = 1, -1
            elif kind == 'PLACE' and len(op) > 1 and op[1] in RES_IDX:
                r_i, delta = RES_IDX[op[1]], -1
            elif kind == 'COLLECT_FERTILIZER':
                if last_hour == 22:
                    gives_cargo = True
                if track_collect:
                    r_i, delta = 1, 1
            elif kind == 'HARVEST':
                gives_cargo = True
            if r_i >= 0:
                net[r_i] += delta
                if -net[r_i] > need[r_i]:
                    need[r_i] = -net[r_i]
        sup = job.get('supply')
        if sup:
            for k, qty in sup.items():
                if k in RES_IDX:
                    net[RES_IDX[k]] += int(qty)
        if delivery_jobs is not None:
            has_cargo_job.append(idx_j in delivery_jobs)
        else:
            has_cargo_job.append(gives_cargo)
        need_vecs.append(tuple(need))
        net_vecs.append(tuple(net))

    dist_mat = [[abs(a[0] - b[0]) + abs(a[1] - b[1]) for b in positions] for a in positions]
    shed_dist_job = [min(abs(p[0] - s[0]) + abs(p[1] - s[1]) for s in shed_positions) for p in positions]

    starts = [tuple(w['pos']) for w in workers]
    hours = [int(w.get('hour', 0)) for w in workers]
    w_invs = []
    w_has_any_inv = []
    for w in workers:
        inv = w.get('inv') or {}
        w_invs.append(tuple(int(inv.get(rn, 0)) for rn in RES_NAMES))
        w_has_any_inv.append(any(v > 0 for v in inv.values()))

    nearest_shed = [min(shed_positions, key=lambda s: abs(s[0] - p[0]) + abs(s[1] - p[1])) for p in starts]
    start_to_shed = [abs(starts[w][0] - nearest_shed[w][0]) + abs(starts[w][1] - nearest_shed[w][1]) for w in range(m)]
    shed_avail = [int(shed.get(rn, 0)) for rn in RES_NAMES]

    routes = [[] for _ in range(m)]
    pick_vecs = [(0, 0, 0, 0, 0) for _ in range(m)]
    reserved = [0, 0, 0, 0, 0]
    ends = list(hours)
    evals = 0
    limit_hour = last_hour + 1

    def score_route(w, route):
        nonlocal evals
        evals += 1
        f0 = f1 = f2 = f3 = f4 = 0
        m0 = m1 = m2 = m3 = m4 = 0
        cargo = w_has_any_inv[w]
        for j in route:
            nd = need_vecs[j]
            nt = net_vecs[j]
            if nd[0] - f0 > m0:
                m0 = nd[0] - f0
            if nd[1] - f1 > m1:
                m1 = nd[1] - f1
            if nd[2] - f2 > m2:
                m2 = nd[2] - f2
            if nd[3] - f3 > m3:
                m3 = nd[3] - f3
            if nd[4] - f4 > m4:
                m4 = nd[4] - f4
            f0 += nt[0]
            f1 += nt[1]
            f2 += nt[2]
            f3 += nt[3]
            f4 += nt[4]
            if has_cargo_job[j]:
                cargo = True

        inv_w = w_invs[w]
        pw = pick_vecs[w]
        t0 = m0 - inv_w[0] if m0 > inv_w[0] else 0
        if t0 and t0 + reserved[0] - pw[0] > shed_avail[0]:
            return None
        t1 = m1 - inv_w[1] if m1 > inv_w[1] else 0
        if t1 and t1 + reserved[1] - pw[1] > shed_avail[1]:
            return None
        t2 = m2 - inv_w[2] if m2 > inv_w[2] else 0
        if t2 and t2 + reserved[2] - pw[2] > shed_avail[2]:
            return None
        t3 = m3 - inv_w[3] if m3 > inv_w[3] else 0
        if t3 and t3 + reserved[3] - pw[3] > shed_avail[3]:
            return None
        t4 = m4 - inv_w[4] if m4 > inv_w[4] else 0
        if t4 and t4 + reserved[4] - pw[4] > shed_avail[4]:
            return None

        take_cnt = (t0 > 0) + (t1 > 0) + (t2 > 0) + (t3 > 0) + (t4 > 0)
        if take_cnt:
            pos = nearest_shed[w]
            t = hours[w] + start_to_shed[w] + take_cnt
        else:
            pos = starts[w]
            t = hours[w]

        prev = -1
        for j in route:
            if prev < 0:
                pj = positions[j]
                leg = abs(pos[0] - pj[0]) + abs(pos[1] - pj[1])
            else:
                leg = dist_mat[prev][j]
            t += leg + lengths[j]
            if t > deadlines[j]:
                return None
            prev = j

        if return_to_shed and (route or w_has_any_inv[w]) and (not selective_return or cargo):
            if prev >= 0:
                leg = shed_dist_job[prev]
            else:
                leg = abs(pos[0] - nearest_shed[w][0]) + abs(pos[1] - nearest_shed[w][1])
            t += leg + end_service

        if t > limit_hour:
            return None
        return (t, (t0, t1, t2, t3, t4))

    def install(w, route, res):
        old_p = pick_vecs[w]
        new_p = res[1]
        for idx_r in range(5):
            reserved[idx_r] += new_p[idx_r] - old_p[idx_r]
        routes[w] = route
        ends[w] = res[0]
        pick_vecs[w] = new_p

    def best_insertion(j):
        best = None
        lj = lengths[j]
        dj = deadlines[j]
        for w in range(m):
            ew = ends[w]
            if ew + lj > limit_hour:
                continue
            rt = routes[w]
            if not rt and hours[w] + lj > dj:
                continue
            for k in range(len(rt) + 1):
                cand = rt[:k] + [j] + rt[k:]
                res = score_route(w, cand)
                if res is None:
                    continue
                sc = (res[0] - ew) + 0.035 * res[0]
                if best is None or sc < best[0]:
                    best = (sc, w, cand, res)
        return best

    if return_to_shed:
        for w in range(m):
            init_res = score_route(w, [])
            if init_res is not None:
                install(w, [], init_res)

    ordered = sorted(
        range(n),
        key=lambda j: (
            -bool(jobs[j].get('mandatory', False)),
            -float(jobs[j].get('priority', 1.0)),
            deadlines[j],
            -min((abs(s[0] - positions[j][0]) + abs(s[1] - positions[j][1]) for s in starts), default=0),
            -lengths[j],
        ),
    )

    # Always complete full greedy insertion across all jobs so mandatory feed/water/plant jobs are never budget-dropped!
    unassigned = []
    for j in ordered:
        ins = best_insertion(j)
        if ins is None:
            unassigned.append(j)
        else:
            install(ins[1], ins[2], ins[3])

    post_limit = evals + (evaluation_budget if evaluation_budget is not None else 4000)

    # Intra-route 2-opt segment reversal
    improved = True
    while improved and evals < post_limit:
        improved = False
        for w in range(m):
            rt = routes[w]
            if len(rt) < 2:
                continue
            for a in range(len(rt) - 1):
                for b in range(a + 1, len(rt)):
                    if evals >= post_limit:
                        break
                    cand = rt[:a] + rt[a:b + 1][::-1] + rt[b + 1:]
                    res = score_route(w, cand)
                    if res is not None and res[0] < ends[w]:
                        install(w, cand, res)
                        rt = cand
                        improved = True
                if evals >= post_limit:
                    break

    # Cross-worker single-job relocate
    if evals < post_limit:
        for src in range(m):
            for j in list(routes[src]):
                if evals >= post_limit:
                    break
                old_rt = list(routes[src])
                old_end = ends[src]
                old_pv = pick_vecs[src]
                reduced = [x for x in old_rt if x != j]
                red_res = score_route(src, reduced)
                if red_res is None:
                    continue
                install(src, reduced, red_res)
                ins = best_insertion(j)
                if ins is not None and (ins[3][0] - ends[ins[1]]) < (old_end - ends[src] - 0.001):
                    install(ins[1], ins[2], ins[3])
                else:
                    install(src, old_rt, (old_end, old_pv))

    # Retry unassigned jobs after shortenings
    left = []
    for j in unassigned:
        ins = best_insertion(j)
        if ins is None:
            left.append(j)
        else:
            install(ins[1], ins[2], ins[3])

    pickups_out = [
        {RES_NAMES[i]: pv[i] for i in range(5) if pv[i] > 0}
        for pv in pick_vecs
    ]
    reserved_out = {RES_NAMES[i]: reserved[i] for i in range(5) if reserved[i] > 0}
    end_positions = []
    for w in range(m):
        last_p = positions[routes[w][-1]] if routes[w] else (nearest_shed[w] if pickups_out[w] else starts[w])
        cargo = w_has_any_inv[w] or any(has_cargo_job[j] for j in routes[w])
        if return_to_shed and (routes[w] or w_has_any_inv[w]) and (not selective_return or cargo):
            end_positions.append(min(shed_positions, key=lambda s: abs(s[0] - last_p[0]) + abs(s[1] - last_p[1])))
        else:
            end_positions.append(last_p)

    return {
        'routes': routes,
        'unassigned': left,
        'pickups': pickups_out,
        'pickup_positions': [nearest_shed[w] if pickups_out[w] else starts[w] for w in range(m)],
        'end_positions': end_positions,
        'return_to_shed': return_to_shed,
        'finish_hours': ends,
        'stats': {'evaluations': evals, 'assigned': n - len(left), 'reserved': reserved_out},
    }


# ============================================================================
# Layer 4: Whole-Farm Portfolio Optimizer, Structural Proposals & Response Game
# ============================================================================

def project_resources(project, pos, today=-1):
    flow = empty_flows()
    work = [0.0] * 30
    cost = 0.0
    inv_cnt = Counter()
    today_list = []
    d_pad = 1.35 + sd(pos) / 8.0
    for d, ops, outputs, inputs in project.days:
        out_sum = 0
        for k, n in outputs:
            if k in flow:
                flow[k][d] += n
            out_sum += n
        for k, n in inputs:
            if k in flow:
                flow[k][d] -= n
            if k.startswith('SEED_'):
                cost += n * CROPS[k[5:]]['seed']
                inv_cnt[k] += n
                if d == today:
                    today_list.append((k, n))
            elif k in ANIMALS:
                cost += n * ANIMALS[k]['cost']
                inv_cnt[k] += n
                if d == today:
                    today_list.append((k, n))
        if ops:
            work[d] = len(ops) + d_pad + out_sum / 8.5
    return (flow, work, cost, inv_cnt, tuple(today_list))


def labor_curve_cost(work):
    val = 0.0
    for hours in work:
        hands = max(0.0, hours / 20.0 - 1.0)
        lo = min(18, int(hands))
        frac = hands - lo
        val += fibcost(lo) + (fibcost(lo + 1) - fibcost(lo)) * frac
        if hours > 305.0:
            val += (hours - 305.0) * 26.0
    return val


def shadow_wages(work):
    out = []
    for h in work:
        idx = min(17, max(1, math.ceil(h / 20.0) - 1))
        w = max(1.0, (fibcost(idx + 1) - fibcost(idx)) / 20.0) + (26.0 if h > 305.0 else 0.0)
        out.append(w)
    return out


def prepend_dig_to_project(proj, tile, labor):
    if tile in (None, 'LOCKED') or not proj.days:
        return proj
    first_d, first_ops, first_out, first_in = proj.days[0]
    if first_ops and first_ops[0] == ('DIG',):
        return proj
    return Project(
        proj.value - labor[first_d],
        ((first_d, (('DIG',),) + first_ops, first_out, first_in),) + proj.days[1:],
    )


def detect_melon_race_days(obs):
    opp = 1 - int(obs['player'])
    counts = Counter()
    for row in obs['farms'][opp]['tiles']:
        for t in row:
            if isinstance(t, dict) and t.get('kind') == 'PLANT' and t.get('crop') == 'MELON':
                due = int(t['planted_day']) + 10
                if 0 <= due < 30:
                    counts[due] += 1
    out = {}
    for d, n in counts.items():
        if n >= 3:
            bonus = min(46.0, 4.2 * n)
            out[d] = max(out.get(d, 0.0), bonus)
    return out


class Portfolio:
    def __init__(self, obs, positions, tracker=None, saved_continuation=None, budget=None, book_override=None):
        self.obs = obs
        self.day = int(obs['step']) // 24
        self.tracker = tracker
        self.book = book_override if book_override is not None else MarketBook(obs, tracker)
        self.melon_race = detect_melon_race_days(obs)
        self.flow = empty_flows()
        self.work = [0.0] * 30
        self.cost = 0.0
        self.projects = {}
        self.parts = {}
        self.accepted = 0
        self.proposed = 0
        self.farm = obs['farms'][int(obs['player'])]
        self.labor_multiplier = [1.0] * 30
        self.offer_min_gain = 1.0
        w_px = float(obs['market']['prices']['WHEAT'])
        self.animal_feed_bridge = (w_px + 2.0) if (6 <= self.day <= 28) else 0.0

        self.paid_stock = Counter()
        self.investment = Counter()
        self.today_inputs = Counter()
        for k, n in obs['private']['seeds'].items():
            if n:
                self.paid_stock['SEED_' + k] += int(n)
        for stock in [obs['private']['shed']] + list(obs['private']['inventories']):
            for a, n in stock.items():
                if a in ANIMALS and n:
                    self.paid_stock[a] += int(n)

        prelim = cohort_supply(self.farm, self.day, 1.0, 1.0)
        prices = self.book.marginal(prelim)
        # Initialize existing crops/animals with ONLY their current lifecycle (no synchronized future replant tails!)
        init_dp = CropDP(prices, [4.0] * 30, [2.0] * 30, allowed=(), start=self.day, melon_race_days=self.melon_race)
        a_cache = {}
        for pos in positions:
            t = self.farm['tiles'][pos[1]][pos[0]]
            if isinstance(t, dict) and 'animal' in t:
                key = tuple(sorted(t.items()))
                if key not in a_cache:
                    a_cache[key] = animal_project(
                        t['animal'], t['placed_day'], self.day, t, prices, [4.0] * 30, [2.0] * 30, reuse=None
                    )
                proj = a_cache[key]
            elif isinstance(t, dict) and t.get('kind') == 'PLANT':
                proj = init_dp.existing(t, self.day)
            else:
                proj = EMPTY_PROJECT
            self.install(pos, proj)

        self.value = self.objective()

        if saved_continuation and saved_continuation.get('day') == self.day:
            cols = saved_continuation.get('columns', {})
            exp = saved_continuation.get('expected', {})
            for pos, proj in cols.items():
                if pos in self.projects and self.farm['tiles'][pos[1]][pos[0]] == exp.get(pos):
                    self.offer(pos, proj, budget=budget)

    def clone(self):
        c = Portfolio.__new__(Portfolio)
        c.obs = self.obs
        c.day = self.day
        c.tracker = self.tracker
        c.book = self.book
        c.melon_race = self.melon_race
        c.flow = {k: v[:] for k, v in self.flow.items()}
        c.work = self.work[:]
        c.cost = self.cost
        c.projects = dict(self.projects)
        c.parts = dict(self.parts)
        c.accepted = self.accepted
        c.proposed = self.proposed
        c.farm = self.farm
        c.labor_multiplier = self.labor_multiplier[:]
        c.offer_min_gain = self.offer_min_gain
        c.animal_feed_bridge = self.animal_feed_bridge
        c.paid_stock = self.paid_stock.copy()
        c.investment = self.investment.copy()
        c.today_inputs = self.today_inputs.copy()
        c.value = self.value
        return c

    def priced_work(self):
        return [w * m for w, m in zip(self.work, self.labor_multiplier)]

    def capital_credit(self):
        if not self.paid_stock:
            return 0.0
        tot = 0.0
        for k, n in self.investment.items():
            ps = self.paid_stock.get(k, 0)
            if ps > 0 and n > 0:
                unit = CROPS[k[5:]]['seed'] if k.startswith('SEED_') else ANIMALS[k]['cost']
                tot += min(n, ps) * unit
        return tot

    def objective(self):
        return self.book.score(self.flow) - labor_curve_cost(self.priced_work()) - self.cost + self.capital_credit()

    def _delta(self, part, sign):
        flows, work, cost, inv_cnt, today_cnt = part
        self.cost += sign * cost
        if self.paid_stock:
            for k, n in inv_cnt.items():
                self.investment[k] += sign * n
        for k, n in today_cnt:
            self.today_inputs[k] += sign * n
        for k in PRODUCTS:
            fk = self.flow[k]
            pk = flows[k]
            for d in range(self.day, 30):
                fk[d] += sign * pk[d]
        wk = self.work
        for d in range(self.day, 30):
            wk[d] += sign * work[d]

    def install(self, pos, proj):
        if pos in self.parts:
            self._delta(self.parts[pos], -1)
        part = project_resources(proj, pos, self.day)
        self._delta(part, 1)
        self.projects[pos] = proj
        self.parts[pos] = part

    def today_capital_spend(self):
        spend = 0.0
        for k, n in self.today_inputs.items():
            if n > 0:
                rem = n - self.paid_stock.get(k, 0)
                if rem > 0:
                    unit = CROPS[k[5:]]['seed'] if k.startswith('SEED_') else (ANIMALS[k]['cost'] + self.animal_feed_bridge)
                    spend += rem * unit
        return spend

    def tile_today_cost(self, pos):
        if pos not in self.parts:
            return 0.0
        tot = 0.0
        for k, n in self.parts[pos][4]:
            if n > 0:
                unit = CROPS[k[5:]]['seed'] if k.startswith('SEED_') else (ANIMALS[k]['cost'] + self.animal_feed_bridge)
                tot += n * unit
        return tot

    def offer(self, pos, proj, budget=None):
        self.proposed += 1
        if self.projects[pos].days == proj.days:
            return False
        old = self.parts[pos]
        part = project_resources(proj, pos, self.day)
        old_spend = self.today_capital_spend() if budget is not None else 0.0
        self._delta(old, -1)
        self._delta(part, 1)
        if budget is not None:
            new_spend = self.today_capital_spend()
            if new_spend > budget + 1e-6 and new_spend > old_spend + 1e-6:
                self._delta(part, -1)
                self._delta(old, 1)
                return False
        sc = self.objective()
        if sc > self.value + self.offer_min_gain:
            self.value = sc
            self.projects[pos] = proj
            self.parts[pos] = part
            self.accepted += 1
            return True
        self._delta(part, -1)
        self._delta(old, 1)
        return False

    def reprice_critical_routes(self):
        cands = sorted(range(self.day, 29), key=lambda d: self.work[d], reverse=True)
        days = sorted({self.day, 29, *cands[:2]}) if self.day < 29 else [29]
        spawn_pos = [(4, 4)]
        for _ in range(16):
            spawn_pos.append(min(SHED_TILES, key=spawn_pos.count))
        sampled_factors = []
        for d in days:
            jobs = []
            stock = Counter()
            out_units = 0
            for pos, proj in self.projects.items():
                row = proj.first(d)
                ops = [list(o) for o in row[1]]
                if not ops:
                    continue
                jobs.append({'pos': pos, 'ops': ops, 'priority': 1.0, 'mandatory': True})
                out_units += sum(n for _, n in row[2])
                stock.update(job_needs_counter(ops))
            if not jobs:
                continue
            lo = max(1, min(15, math.ceil(self.work[d] / 19.5) - 2))
            chosen = 15
            unassigned = 0
            res = None
            hands = lo
            prev_h = lo - 1
            while hands <= 15:
                r_hour = 1 if hands <= 8 else 2
                workers = [{'pos': p, 'hour': r_hour, 'inv': {}} for p in spawn_pos[:hands + 1]]
                res = solve_routes(
                    jobs,
                    workers,
                    dict(stock),
                    last_hour=22 if d == 29 else 23,
                    return_to_shed=(d == 29),
                    end_service=2 if d == 29 else 1,
                    track_collect=True,
                    evaluation_budget=500,
                )
                unassigned = len(res['unassigned'])
                if not unassigned:
                    chosen = hands
                    if hands - prev_h > 1:
                        mid_h = hands - 1
                        r_m = 1 if mid_h <= 8 else 2
                        w_m = [{'pos': p, 'hour': r_m, 'inv': {}} for p in spawn_pos[:mid_h + 1]]
                        res_m = solve_routes(
                            jobs,
                            w_m,
                            dict(stock),
                            last_hour=22 if d == 29 else 23,
                            return_to_shed=(d == 29),
                            end_service=2 if d == 29 else 1,
                            track_collect=True,
                            evaluation_budget=500,
                        )
                        if not res_m['unassigned']:
                            chosen = mid_h
                            res = res_m
                    break
                prev_h = hands
                hands += 2 if (unassigned >= 4 and hands + 2 <= 15) else 1
            eff = (chosen + 1) * 20.0
            if d != 29 and out_units > 100:
                eff += min(35.0, (out_units - 100) / 5.0)
            if unassigned and res is not None:
                eff += sum(len(jobs[j]['ops']) + 1 for j in res['unassigned'])
            factor = min(1.85, max(1.0, eff / max(1.0, self.work[d])))
            if factor > self.labor_multiplier[d]:
                self.labor_multiplier[d] = factor
            if d != 29:
                sampled_factors.append(factor)
        if sampled_factors:
            base_f = sum(sampled_factors) / len(sampled_factors)
            for t in range(self.day, 29):
                if self.work[t] > 35.0 and base_f > self.labor_multiplier[t]:
                    self.labor_multiplier[t] = base_f
        self.value = self.objective()

    def optimize(self, budget, only_positions=None, sweeps=2, animal_cap=None):
        positions = sorted(
            self.projects if only_positions is None else only_positions,
            key=lambda p: (sd(p), p[1], p[0]),
        )
        dp = None
        cdp_cache = {}
        prices = None
        labor = None
        refreshed_at = -1
        a_cache = {}
        new_animals = 0
        b_cap = float(budget)
        base_animals = Counter(
            t['animal']
            for row in self.farm['tiles']
            for t in row
            if isinstance(t, dict) and 'animal' in t
        )
        base_herd = sum(base_animals.values())
        base_melons = sum(
            1 for row in self.farm['tiles'] for t in row if isinstance(t, dict) and t.get('crop') == 'MELON'
        )
        egg_shops = sum(s in ('BAKERY', 'BRUNCH_SPOT') for s in self.obs['town']['unlocked_shops'])
        goose_cap = 10 if egg_shops >= 3 else (7 if egg_shops == 2 else (4 if egg_shops == 1 else 2))
        yarn_shops = sum(s == 'YARN_STORE' for s in self.obs['town']['unlocked_shops'])
        sheep_cap = 18 if yarn_shops >= 2 else (10 if yarn_shops == 1 else 3)
        milk_shops = sum(s in ('PIZZA_SHOP', 'ICE_CREAM_SHOP', 'SMOOTHIE_SHOP') for s in self.obs['town']['unlocked_shops'])
        n_open_shops = len(self.obs['town']['unlocked_shops'])
        milk_inv = int(self.obs['market']['inventory']['MILK'])
        if milk_shops >= 3:
            cow_cap = 16
        elif milk_shops == 2:
            cow_cap = 11 if milk_inv >= 10025 else 13
        elif milk_shops == 1:
            cow_cap = 7 if milk_inv >= 10012 else 9
        else:
            cow_cap = 6 if (n_open_shops <= 1 and milk_inv <= 10005) else 4
        known_dem = shop_daily_demand(self.obs['town']['unlocked_shops'])
        livestock_dem = known_dem['MILK'] + known_dem['WOOL']
        straw_dem = known_dem['STRAWBERRY']
        n_tiles = len(self.projects)
        if livestock_dem >= 18 and livestock_dem >= straw_dem + 10:
            max_total_herd = 30 if n_tiles >= 90 else 24
        else:
            max_total_herd = 23 if n_tiles >= 90 else (20 if egg_shops >= 3 else 18)
        has_empty_inner = any(
            self.farm['tiles'][p[1]][p[0]] is None and p not in SHED_SET and sd(p) <= 4
            for p in positions
        )

        def get_affordable_dp(avail_cash):
            if avail_cash < 10.0:
                key_c = ()
            elif self.day <= 16 and avail_cash < 100.0:
                key_c = tuple(c for c in ('WHEAT', 'CARROT') if CROPS[c]['seed'] <= avail_cash + 1e-6)
            else:
                allow_m = (self.day == 0) or (
                    14 <= self.day <= 19
                    and base_melons + max(0, self.today_inputs['SEED_MELON']) < 6
                    and int(self.obs['market']['inventory']['MELON']) <= 10140
                )
                key_c = tuple(
                    c for c, spec in CROPS.items()
                    if spec['seed'] <= avail_cash + 1e-6 and (c != 'MELON' or allow_m)
                )
            if key_c not in cdp_cache:
                cdp_cache[key_c] = CropDP(
                    prices,
                    labor,
                    [2.0] * 30,
                    allowed=key_c,
                    start=self.day,
                    melon_race_days=self.melon_race,
                    no_plant_today=(len(key_c) == 0),
                )
            return cdp_cache[key_c]

        for sweep in range(sweeps):
            seq = positions if sweep == 0 else positions[::-1]
            step_refresh = 5 if sweep == 0 else 8
            for pos in seq:
                allow_melon_now = (self.day == 0) or (
                    14 <= self.day <= 19
                    and base_melons + max(0, self.today_inputs['SEED_MELON']) < 6
                    and int(self.obs['market']['inventory']['MELON']) <= 10140
                )
                if dp is None or (self.accepted - refreshed_at >= step_refresh) or (not allow_melon_now and getattr(dp, '_has_melon', False)):
                    prices = self.book.marginal(self.flow)
                    labor = [v * m for v, m in zip(shadow_wages(self.priced_work()), self.labor_multiplier)]
                    dp_allowed = None if allow_melon_now else tuple(c for c in CROPS if c != 'MELON')
                    dp = CropDP(prices, labor, [2.0] * 30, allowed=dp_allowed, start=self.day, melon_race_days=self.melon_race)
                    dp._has_melon = allow_melon_now
                    cdp_cache = {}
                    a_cache = {}
                    refreshed_at = self.accepted
                t = self.farm['tiles'][pos[1]][pos[0]]
                avail_for_pos = max(0.0, b_cap - self.today_capital_spend() + self.tile_today_cost(pos))

                if isinstance(t, dict) and 'animal' in t:
                    key = tuple(sorted(t.items()))
                    if key not in a_cache:
                        a_cache[key] = animal_project(
                            t['animal'], t['placed_day'], self.day, t, prices, labor, [2.0] * 30, reuse=dp.free
                        )
                    self.offer(pos, a_cache[key], budget=b_cap)
                    continue

                is_plant = isinstance(t, dict) and t.get('kind') == 'PLANT'
                if is_plant:
                    ex_proj = dp.existing(t, self.day)
                    if not self.offer(pos, ex_proj, budget=b_cap) and avail_for_pos < 100.0:
                        cdp = get_affordable_dp(avail_for_pos)
                        self.offer(pos, cdp.existing(t, self.day), budget=b_cap)
                else:
                    proj = prepend_dig_to_project(dp.free[self.day], t, labor)
                    if not self.offer(pos, proj, budget=b_cap) and avail_for_pos >= 10.0 and avail_for_pos < 100.0 and self.day <= 27:
                        cdp = get_affordable_dp(avail_for_pos)
                        cproj = prepend_dig_to_project(cdp.free[self.day], t, labor)
                        self.offer(pos, cproj, budget=b_cap)

                # Animal placement on inner non-depot tiles, existing structures, or short-cycle Wheat/Carrot slots
                can_place_on_plant = False
                plant_ripe = False
                if is_plant and t.get('crop') in ('WHEAT', 'CARROT'):
                    pc = t['crop']
                    plant_ripe = t.get('yield_units', 0) > 0 and (self.day - t['planted_day'] >= CROPS[pc]['first_yield_day'])
                    can_place_on_plant = plant_ripe or (self.day >= 7 and not has_empty_inner)
                if is_plant and not can_place_on_plant:
                    continue

                avail_for_pos = max(0.0, b_cap - self.today_capital_spend() + self.tile_today_cost(pos))
                is_empty_struct = isinstance(t, dict) and t.get('kind') in ('COOP', 'PASTURE')
                cur_new_animals = sum(max(0, self.today_inputs[aa]) for aa in ANIMALS)
                cur_herd = base_herd + cur_new_animals
                max_g_day = 20 if egg_shops >= 2 else 17
                max_s_day = 18 if yarn_shops >= 2 else 15
                max_c_day = 15
                max_any_anim_day = max(max_g_day, max_s_day, max_c_day)
                if (
                    sweep == 0
                    and pos not in SHED_SET
                    and (sd(pos) <= (5 if max_total_herd >= 23 else 4) or is_empty_struct)
                    and self.day <= max_any_anim_day
                    and cur_herd < max_total_herd
                    and (animal_cap is None or cur_new_animals < animal_cap)
                    and avail_for_pos >= 300.0
                ):
                    cand_animals = []
                    top_species_val = -1e18
                    for a in ('SHEEP', 'COW', 'GOOSE'):
                        max_a_day = max_g_day if a == 'GOOSE' else (max_s_day if a == 'SHEEP' else max_c_day)
                        if self.day > max_a_day:
                            continue
                        if a == 'GOOSE' and (base_animals['GOOSE'] + max(0, self.today_inputs['GOOSE'])) >= goose_cap:
                            continue
                        if a == 'SHEEP' and (base_animals['SHEEP'] + max(0, self.today_inputs['SHEEP'])) >= sheep_cap:
                            continue
                        if a == 'COW' and (base_animals['COW'] + max(0, self.today_inputs['COW'])) >= cow_cap:
                            continue
                        acost = ANIMALS[a]['cost']
                        is_paid = self.paid_stock.get(a, 0) > self.investment.get(a, 0)
                        ap = animal_project(
                            a,
                            self.day,
                            self.day,
                            make_animal_dict(a, self.day),
                            prices,
                            labor,
                            [2.0 + sd(pos) / 3.0] * 30,
                            reuse=dp.free,
                        )
                        arow = ap.first(self.day)
                        st_kind = ANIMALS[a]['structure']
                        ex_out = ()
                        ex_bonus = 0.0
                        if is_plant:
                            if plant_ripe:
                                pc = t['crop']
                                b_ops = (('HARVEST',), ('BUILD_' + st_kind,), ('PLACE', a))
                                ex_out = ((pc, t['yield_units']),)
                                ex_bonus = t['yield_units'] * prices[pc][self.day] - 3.0 * labor[self.day]
                            else:
                                b_ops = (('DIG',), ('BUILD_' + st_kind,), ('PLACE', a))
                                ex_bonus = -3.0 * labor[self.day]
                        elif isinstance(t, dict) and t.get('kind') == st_kind:
                            b_ops = (('PLACE', a),)
                            ex_bonus = -labor[self.day]
                        else:
                            b_ops = ((('DIG',),) if t not in (None, 'LOCKED') else ()) + (
                                ('BUILD_' + st_kind,),
                                ('PLACE', a),
                            )
                            ex_bonus = -len(b_ops) * labor[self.day]
                        cand_p = Project(
                            ap.value - acost + ex_bonus,
                            ((self.day, b_ops + arow[1], ex_out + arow[2], ((a, 1),) + arow[3]),)
                            + tuple(r for r in ap.days if r[0] != self.day),
                        )
                        if cand_p.value > top_species_val:
                            top_species_val = cand_p.value
                        cand_animals.append((cand_p.value, a, acost, is_paid, cand_p))

                    best_a = None
                    for c_val, a, acost, is_paid, cand_p in cand_animals:
                        if not is_paid and avail_for_pos < acost + self.animal_feed_bridge + 25.0:
                            continue
                        if not is_paid and self.day < 14 and c_val < top_species_val - 85.0:
                            continue
                        if best_a is None or c_val > best_a[0]:
                            best_a = (c_val, a, cand_p)
                    if best_a is not None and self.offer(pos, best_a[2], budget=b_cap):
                        new_animals += 1
                        dp = None
                        cdp_cache = {}
                        a_cache = {}

        if self.paid_stock:
            self.recover_paid_animals(only_positions)
        return self.projects

    def recover_paid_animals(self, only_positions=None):
        unplaced = [a for a in ANIMALS if self.paid_stock.get(a, 0) > self.investment.get(a, 0)]
        if not unplaced:
            return
        prices = self.book.marginal(self.flow)
        labor = [v * m for v, m in zip(shadow_wages(self.priced_work()), self.labor_multiplier)]
        cand_pos = sorted(
            self.projects if only_positions is None else only_positions,
            key=lambda p: (p in SHED_SET, sd(p), p[1], p[0]),
        )
        cand_pos = [
            p
            for p in cand_pos
            if not isinstance(self.farm['tiles'][p[1]][p[0]], dict)
            or ('animal' not in self.farm['tiles'][p[1]][p[0]] and self.farm['tiles'][p[1]][p[0]].get('kind') != 'PLANT')
        ][:12]
        for a in unplaced:
            for pos in cand_pos:
                if self.investment.get(a, 0) >= self.paid_stock.get(a, 0):
                    break
                t = self.farm['tiles'][pos[1]][pos[0]]
                ap = animal_project(a, self.day, self.day, make_animal_dict(a, self.day), prices, labor, [2.0 + sd(pos) / 3.0] * 30)
                arow = ap.first(self.day)
                st_kind = ANIMALS[a]['structure']
                b_ops = (
                    (('PLACE', a),)
                    if isinstance(t, dict) and t.get('kind') == st_kind
                    else ((('DIG',),) if t not in (None, 'LOCKED') else ()) + (('BUILD_' + st_kind,), ('PLACE', a))
                )
                cand_p = Project(
                    ap.value - ANIMALS[a]['cost'],
                    ((self.day, b_ops + arow[1], arow[2], ((a, 1),) + arow[3]),)
                    + tuple(r for r in ap.days if r[0] != self.day),
                )
                self.offer(pos, cand_p)


def build_day0_compounding_candidate(base_model, obs, budget):
    """Balanced Day-0 compounding seed (2 Sheep + 2 Cow at sd=1..2, 9 Wheat at sd=2..4 freeing inner slots Day 3, 12 Melons at (4,4)+outer NW)."""
    farm = obs['farms'][int(obs['player'])]
    if int(obs['step']) != 0 or budget < 2400:
        return None
    empty_nw = [
        (x, y)
        for y in range(5)
        for x in range(5)
        if farm['tiles'][y][x] is None
    ]
    if len(empty_nw) < 22:
        return None

    positions = [(x, y) for y in range(10) for x in range(10) if farm['tiles'][y][x] != 'LOCKED']
    trial = Portfolio(obs, positions, base_model.tracker, None, budget=budget)
    prices = trial.book.marginal(trial.flow)
    labor = [3.5] * 30

    # 1. Place 2 SHEEP + 2 COW on closest non-depot NW tiles (sd == 1..2)
    animal_slots = sorted([p for p in empty_nw if p not in SHED_SET], key=lambda p: (sd(p), p[1], p[0]))[:4]
    species_plan = ('SHEEP', 'SHEEP', 'COW', 'COW')
    used = set()
    spent = 0.0
    for pos, a in zip(animal_slots, species_plan):
        if spent + ANIMALS[a]['cost'] > budget - 120:
            break
        ap = animal_project(a, 0, 0, make_animal_dict(a, 0), prices, labor, [2.0 + sd(pos) / 3.0] * 30)
        arow = ap.first(0)
        st_kind = ANIMALS[a]['structure']
        cand_p = Project(
            ap.value - ANIMALS[a]['cost'],
            ((0, (('BUILD_' + st_kind,), ('PLACE', a)) + arow[1], arow[2], ((a, 1),) + arow[3]),)
            + tuple(r for r in ap.days if r[0] != 0),
        )
        trial.install(pos, cand_p)
        used.add(pos)
        spent += ANIMALS[a]['cost']

    # Recompute marginal prices with the 4 livestock installed; single-cycle CropDP avoids synchronized tail flood
    prices = trial.book.marginal(trial.flow)
    single_dp = CropDP(prices, labor, [2.0] * 30, allowed=(), start=0, melon_race_days={10: 28.0})
    melon_proj = single_dp._solver('MELON', 0)(1, 0, -1, 1, False)
    melon_full = Project(
        melon_proj.value - CROPS['MELON']['seed'] - 2 * labor[0] - 2.0,
        ((0, (('PLANT', 'MELON'), ('WATER',)), (), (('SEED_MELON', 1),)),) + melon_proj.days,
    )
    wheat_proj = single_dp._solver('WHEAT', 0)(1, 0, -1, 1, False)
    wheat_full = Project(
        wheat_proj.value - CROPS['WHEAT']['seed'] - 2 * labor[0] - 2.0,
        ((0, (('PLANT', 'WHEAT'), ('WATER',)), (), (('SEED_WHEAT', 1),)),) + wheat_proj.days,
    )

    # Reserve 4 closest inner slots (sd=2..3) for WHEAT (freeing Day 3 for Cow/Sheep),
    # place MELON on (4,4) + mid-inner ring (sd=3..5) for rapid Day-10 dawn blitz, and WHEAT on far corners!
    rem_non_shed = sorted([p for p in empty_nw if p not in used and p not in SHED_SET], key=lambda p: (sd(p), p[1], p[0]))
    rem_shed = [p for p in empty_nw if p not in used and p in SHED_SET]
    inner_wheat_reserve = rem_non_shed[:4]
    mid_ring = rem_non_shed[4:]
    melon_priority = rem_shed + mid_ring + inner_wheat_reserve

    target_melons = min(12, int(max(0.0, budget - spent - (len(empty_nw) - len(used)) * 10.0) // 70.0))
    melons_placed = 0
    for pos in melon_priority:
        if melons_placed < target_melons and spent + 80.0 <= budget:
            trial.install(pos, melon_full)
            spent += 80.0
            melons_placed += 1
        elif spent + 10.0 <= budget:
            trial.install(pos, wheat_full)
            spent += 10.0
    trial.value = trial.objective()
    return trial


def demand_structure_candidates(model, obs, budget, max_families=2):
    known = shop_daily_demand(obs['town']['unlocked_shops'])
    day = model.day
    if day >= 27 or budget < 120.0:
        return []
    farm_sup = [cohort_supply(f, day, 0.95, 0.5) for f in obs['farms']]
    prices = model.book.marginal(model.flow)
    labor = [v * m for v, m in zip(shadow_wages(model.priced_work()), model.labor_multiplier)]

    def rank_item(item, delay):
        if day + delay >= 30:
            return -1e9
        span = range(day + delay, 30)
        gap = sum(known[item] - (farm_sup[0][item][d] + farm_sup[1][item][d]) for d in span)
        avg_px = max(1.0, sum(prices[item][d] for d in span) / len(span))
        return (max(0.0, gap) + known[item]) * avg_px / (1.0 + delay)

    crop_focus = [
        k for k, v in CROPS.items() if day + v['first_yield_day'] < 30 and known[k] > 1
    ]
    crop_focus.sort(key=lambda k: (-rank_item(k, CROPS[k]['first_yield_day']), k))
    focuses = [('crop', k) for k in crop_focus[:2]]
    cur_herd = sum(1 for row in model.farm['tiles'] for t in row if isinstance(t, dict) and 'animal' in t)
    cur_herd += sum(max(0, model.today_inputs[aa]) for aa in ANIMALS)
    cur_geese = sum(1 for row in model.farm['tiles'] for t in row if isinstance(t, dict) and t.get('animal') == 'GOOSE')
    cur_geese += max(0, model.today_inputs['GOOSE'])
    cur_sheep = sum(1 for row in model.farm['tiles'] for t in row if isinstance(t, dict) and t.get('animal') == 'SHEEP')
    cur_sheep += max(0, model.today_inputs['SHEEP'])
    cur_cows = sum(1 for row in model.farm['tiles'] for t in row if isinstance(t, dict) and t.get('animal') == 'COW')
    cur_cows += max(0, model.today_inputs['COW'])
    cur_melons = sum(1 for row in model.farm['tiles'] for t in row if isinstance(t, dict) and t.get('crop') == 'MELON')
    cur_melons += max(0, model.today_inputs['SEED_MELON'])
    egg_shops_n = sum(s in ('BAKERY', 'BRUNCH_SPOT') for s in obs['town']['unlocked_shops'])
    g_cap = 10 if egg_shops_n >= 3 else (7 if egg_shops_n == 2 else (4 if egg_shops_n == 1 else 2))
    yarn_shops_n = sum(s == 'YARN_STORE' for s in obs['town']['unlocked_shops'])
    s_cap = 18 if yarn_shops_n >= 2 else (10 if yarn_shops_n == 1 else 3)
    milk_shops_n = sum(s in ('PIZZA_SHOP', 'ICE_CREAM_SHOP', 'SMOOTHIE_SHOP') for s in obs['town']['unlocked_shops'])
    n_open_dem = len(obs['town']['unlocked_shops'])
    milk_inv_dem = int(obs['market']['inventory']['MILK'])
    if milk_shops_n >= 3:
        c_cap = 16
    elif milk_shops_n == 2:
        c_cap = 11 if milk_inv_dem >= 10025 else 13
    elif milk_shops_n == 1:
        c_cap = 7 if milk_inv_dem >= 10012 else 9
    else:
        c_cap = 6 if (n_open_dem <= 1 and milk_inv_dem <= 10005) else 4
    l_dem = known['MILK'] + known['WOOL']
    if l_dem >= 18 and l_dem >= known['STRAWBERRY'] + 10:
        max_h_dem = 30 if len(model.projects) >= 90 else 24
    else:
        max_h_dem = 23 if len(model.projects) >= 90 else (20 if egg_shops_n >= 3 else 18)
    max_g_d = 20 if egg_shops_n >= 2 else 17
    max_s_d = 18 if yarn_shops_n >= 2 else 15
    max_c_d = 15
    max_dem_anim_day = max(max_g_d, max_s_d, max_c_d)
    if day <= max_dem_anim_day and budget >= 350.0 and cur_herd < max_h_dem:
        anim_focus = [
            k for k, v in ANIMALS.items()
            if day + v['first_yield_day'] < 30
            and day <= (max_g_d if k == 'GOOSE' else (max_s_d if k == 'SHEEP' else max_c_d))
            and known[v['product']] > 1
            and (k != 'GOOSE' or cur_geese < g_cap)
            and (k != 'SHEEP' or cur_sheep < s_cap)
            and (k != 'COW' or cur_cows < c_cap)
        ]
        anim_focus.sort(key=lambda k: (-rank_item(ANIMALS[k]['product'], ANIMALS[k]['first_yield_day']), k))
        if anim_focus:
            focuses.append(('animal', anim_focus[0]))

    allow_m_dem = (day == 0) or (
        14 <= day <= 19
        and cur_melons < 6
        and int(obs['market']['inventory']['MELON']) <= 10140
    )
    dp_allowed_dem = None if allow_m_dem else tuple(c for c in CROPS if c != 'MELON')

    raw = []
    seen = set()
    for kind, focus in focuses:
        item = focus if kind == 'crop' else ANIMALS[focus]['product']
        biased = {k: v[:] for k, v in prices.items()}
        biased[item] = [max(1.0, v) * 1.32 for v in biased[item]]
        dp = CropDP(biased, labor, [2.0] * 30, allowed=dp_allowed_dem, start=day, melon_race_days=model.melon_race) if kind == 'crop' else None
        props = []
        for pos, cur in sorted(model.projects.items()):
            t = model.farm['tiles'][pos[1]][pos[0]]
            if t == 'LOCKED' or (isinstance(t, dict) and 'animal' in t):
                continue
            if kind == 'animal':
                if pos in SHED_SET or sd(pos) > (5 if max_h_dem >= 24 else 4):
                    continue
                if isinstance(t, dict) and t.get('crop') in ('STRAWBERRY', 'TOMATO', 'MELON'):
                    continue
                prefix = ()
                ex_out = ()
                if isinstance(t, dict) and t.get('kind') == 'PLANT':
                    c = t['crop']
                    if t.get('yield_units', 0) > 0 and day - t['planted_day'] >= CROPS[c]['first_yield_day']:
                        prefix = (('HARVEST',),)
                        ex_out = ((c, t['yield_units']),)
                    else:
                        prefix = (('DIG',),)
                elif t is not None:
                    prefix = (('DIG',),)
                base_a = animal_project(focus, day, day, make_animal_dict(focus, day), biased, labor, [2.0 + sd(pos) / 3.0] * 30, reuse=None)
                arow = base_a.first(day)
                spec = ANIMALS[focus]
                proj = Project(
                    base_a.value - spec['cost'],
                    ((day, prefix + (('BUILD_' + spec['structure'],), ('PLACE', focus)) + arow[1], ex_out + arow[2], ((focus, 1),) + arow[3]),)
                    + tuple(r for r in base_a.days if r[0] != day),
                )
            elif isinstance(t, dict) and t.get('kind') == 'PLANT':
                if t.get('crop') in ('MELON', 'STRAWBERRY', 'TOMATO'):
                    continue
                proj = dp.existing(t, day)
            else:
                proj = dp.free[day]
                if t is not None and proj.first(day)[1]:
                    prow = proj.first(day)
                    proj = Project(
                        proj.value,
                        ((day, (('DIG',),) + prow[1], prow[2], prow[3]),) + tuple(r for r in proj.days if r[0] != day),
                    )
            if proj is None or proj.days == cur.days:
                continue
            new_p = project_resources(proj, pos)
            old_p = model.parts[pos]
            rk = sum(biased[k][d] * (new_p[0][k][d] - old_p[0][k][d]) for k in PRODUCTS for d in range(day, 30))
            rk -= (new_p[2] - old_p[2]) + sum(labor[d] * (new_p[1][d] - old_p[1][d]) for d in range(day, 30))
            props.append((rk, pos, proj))
        props.sort(key=lambda r: (-r[0], r[1]))
        for sz in ((4, 8) if kind == 'crop' else (2, 4)):
            sel = props[:sz]
            if not sel:
                continue
            sig = tuple((pos, id(p.days)) for _, pos, p in sel)
            if sig in seen:
                continue
            seen.add(sig)
            trial = model.clone()
            for _, pos, p in sel:
                trial.install(pos, p)
            if trial.today_capital_spend() <= budget + 1e-6:
                trial.value = trial.objective()
                raw.append((trial.value, (kind, focus), trial))

    out = []
    fams = set()
    base_acap = 3 if day <= 5 else 4
    for _, fam, trial in sorted(raw, key=lambda e: -e[0]):
        if fam in fams:
            continue
        if len(fams) >= max_families:
            break
        fams.add(fam)
        trial.optimize(budget, sweeps=1, animal_cap=(base_acap + 1 if fam[0] == 'animal' else base_acap))
        if trial.today_capital_spend() <= budget + 1e-6:
            out.append(trial)
    return out


def build_rival_stress_flow(obs):
    """Fast analytical rival best-response supply shock on open shop bottlenecks."""
    day = int(obs['step']) // 24
    opp = 1 - int(obs['player'])
    opp_farm = obs['farms'][opp]
    free_tiles = sum(
        1 for row in opp_farm['tiles'] for t in row if t is None
    )
    opp_cash = float(opp_farm['money'])
    if free_tiles <= 2 or opp_cash < 150.0 or day >= 27:
        return None
    known = shop_daily_demand(obs['town']['unlocked_shops'])
    extra = empty_flows()
    # Model rival putting a portion of free capacity into highest-demand open shop items
    top_items = sorted(TOWN_ITEMS, key=lambda k: (-known[k], -MARKET_PARAMS[k]['base']))[:2]
    for it in top_items:
        if known[it] <= 1:
            continue
        if it in CROPS:
            cd = CROPS[it]
            n_tiles = min(free_tiles // 2, int(opp_cash // max(10, cd['seed'] * 2)))
            if n_tiles <= 0:
                continue
            if cd['ongoing']:
                for j in range(cd['max_yield']):
                    yd = day + cd['first_yield_day'] + j * cd['interval']
                    if yd < 30:
                        extra[it][yd] += n_tiles * 1.8
            else:
                span = {'WHEAT': 3, 'CARROT': 2, 'MELON': 10}[it]
                yld = {'WHEAT': 5.0, 'CARROT': 3.0, 'MELON': 5.5}[it]
                yd = day + span
                while yd < 30:
                    extra[it][yd] += n_tiles * yld * 0.6
                    yd += span
        else:
            for a, spec in ANIMALS.items():
                if spec['product'] == it and day <= 18:
                    n_a = min(4, free_tiles // 4, int(opp_cash // (spec['cost'] * 2)))
                    for d in range(day + spec['first_yield_day'], 30, spec['interval']):
                        extra[it][d] += n_a * min(spec['max_held'], 1 + spec['interval'])
    return extra


def build_rival_shadow_flow(obs, tracker):
    """Detached 1-sweep economic best-response for an active strong opponent using only public state + legal unsold belief."""
    day = int(obs['step']) // 24
    opp = 1 - int(obs['player'])
    opp_farm = obs['farms'][opp]
    opp_hands = len(opp_farm.get('hands') or ())
    opp_active = sum(1 for row in opp_farm['tiles'] for t in row if isinstance(t, dict))
    if not (4 <= day <= 25) or (opp_hands < 2 and opp_active < 8):
        return None
    unsold = tracker.unsold if tracker is not None else {}
    u_pos = {k: max(0.0, float(unsold.get(k, 0.0))) for k in PRODUCTS}
    u_scale = min(1.0, 100.0 / max(1.0, sum(u_pos.values())))
    priv_shed = {k: int(u_pos[k] * u_scale) for k in PRODUCTS if int(u_pos[k] * u_scale) > 0}
    rival_obs = {
        'step': int(obs['step']),
        'player': opp,
        'farms': obs['farms'],
        'market': obs['market'],
        'town': obs['town'],
        'private': {
            'shed': priv_shed,
            'seeds': {c: 0 for c in CROPS},
            'inventories': [{} for _ in range(1 + opp_hands)],
        },
    }
    opp_pos = [(x, y) for y in range(10) for x in range(10) if opp_farm['tiles'][y][x] != 'LOCKED']
    opp_herd = sum(1 for row in opp_farm['tiles'] for t in row if isinstance(t, dict) and 'animal' in t)
    opp_quads = len(opp_farm.get('unlocked_quadrants') or ('NW',))
    est_shed_val = sum(n * obs['market']['prices'].get(k, 0) * 0.90 for k, n in priv_shed.items())
    base_c = (5, 7, 10, 12)[min(3, opp_quads - 1)]
    opp_budget = max(
        0.0,
        float(opp_farm['money'])
        + est_shed_val
        - max(0, fibcost(base_c) - fibcost(opp_hands))
        - max(0, opp_herd - priv_shed.get('WHEAT', 0)) * (obs['market']['prices']['WHEAT'] + 2)
        - 80.0,
    )
    sh_book = MarketBook(rival_obs, tracker=None, n_scenarios=3)
    sh_model = Portfolio(rival_obs, opp_pos, tracker=None, saved_continuation=None, budget=opp_budget, book_override=sh_book)
    sh_model.optimize(opp_budget, sweeps=1, animal_cap=4)
    return sh_model.flow


def cfr_select_candidate(cands, books, iterations=160):
    """Regret-matching zero-sum CFR response-game selector over certified whole-farm plans."""
    if len(cands) <= 1:
        return cands[0][0]
    day = cands[0][0]['model'].day
    common_mult = [
        max(opt['model'].labor_multiplier[d] for opt, _ in cands)
        for d in range(30)
    ]
    matrix = []
    for opt, _ in cands:
        mdl = opt['model']
        wk = [w * f for w, f in zip(mdl.work, common_mult)]
        wk[day] = 0.0
        base_deduct = mdl.cost - mdl.capital_credit() + labor_curve_cost(wk) + fibcost(opt['crew']) + opt['uncovered']
        row = [bk.score(mdl.flow) - base_deduct for bk in books]
        matrix.append(row)

    n_r = len(matrix)
    n_c = len(books)
    if n_c == 1:
        best_i = 0
        best_sc = matrix[0][0]
        for i in range(1, n_r):
            if matrix[i][0] > matrix[0][0] + cands[i][1] and matrix[i][0] > best_sc:
                best_i = i
                best_sc = matrix[i][0]
        return cands[best_i][0]

    # Column-normalized counterfactual regret matrix removes scenario price-level bias
    col_best = [max(matrix[k][j] for k in range(n_r)) for j in range(n_c)]
    regret_m = [[col_best[j] - matrix[i][j] for j in range(n_c)] for i in range(n_r)]

    r_reg = [0.0] * n_r
    c_reg = [0.0] * n_c
    c_sum = [0.0] * n_c
    for _ in range(iterations):
        r_pos = [x if x > 0.0 else 0.0 for x in r_reg]
        r_tot = sum(r_pos)
        r_p = [x / r_tot for x in r_pos] if r_tot > 1e-12 else [1.0 / n_r] * n_r

        c_pos = [x if x > 0.0 else 0.0 for x in c_reg]
        c_tot = sum(c_pos)
        c_p = [x / c_tot for x in c_pos] if c_tot > 1e-12 else [1.0 / n_c] * n_c

        r_pay = [-sum(c_p[j] * regret_m[i][j] for j in range(n_c)) for i in range(n_r)]
        ev = sum(r_p[i] * r_pay[i] for i in range(n_r))
        c_pay = [sum(r_p[i] * regret_m[i][j] for i in range(n_r)) for j in range(n_c)]
        for i in range(n_r):
            r_reg[i] += r_pay[i] - ev
        for j in range(n_c):
            c_reg[j] += c_pay[j] + ev
            c_sum[j] += c_p[j]

    c_sum_tot = sum(c_sum)
    col_cfr = [x / c_sum_tot for x in c_sum] if c_sum_tot > 1e-12 else [1.0 / n_c] * n_c
    col_w = [(0.65 if j == 0 else 0.0) + 0.35 * col_cfr[j] for j in range(n_c)]

    best_idx = 0
    best_gain = 0.0
    for i in range(1, n_r):
        req_g = cands[i][1]
        emp_gain = matrix[i][0] - matrix[0][0]
        exp_gain = sum(col_w[j] * (matrix[i][j] - matrix[0][j]) for j in range(n_c))
        worst_delta = min(matrix[i][j] - matrix[0][j] for j in range(n_c))
        if exp_gain > req_g and emp_gain >= 0.5 * req_g and worst_delta >= -250.0 and exp_gain > best_gain:
            best_idx = i
            best_gain = exp_gain
    return cands[best_idx][0]


def remember_continuation(obs, projects):
    day = int(obs['step']) // 24
    farm = clone_farm_state(obs['farms'][int(obs['player'])])
    priv = {'shed': {k: 0 for k in PRODUCTS + tuple(ANIMALS)}, 'seeds': {k: 0 for k in CROPS}, 'inventories': [{}]}
    cols = {}
    expected = {}
    for pos, proj in projects.items():
        x, y = pos
        if farm['tiles'][y][x] == 'LOCKED':
            continue
        farm['farmer'] = [x, y]
        priv['inventories'] = [{}]
        priv['seeds'] = {k: 0 for k in CROPS}
        row = proj.first(day)
        for k, n in row[3]:
            if k.startswith('SEED_'):
                priv['seeds'][k[5:]] = int(n)
            elif k in PRODUCTS or k in ANIMALS:
                priv['inventories'][0][k] = int(n)
        for raw_op in row[1]:
            sim_unit_op(farm, priv, 0, list(raw_op), day)
        # Simulate midnight plant/animal refresh on this tile
        t = farm['tiles'][y][x]
        if isinstance(t, dict):
            if t.get('kind') == 'PLANT':
                cd = CROPS[t['crop']]
                if not cd['ongoing'] and t.get('max_lifespan_step', -1) >= 0 and (day * 24 + 23) >= t['max_lifespan_step']:
                    t['yield_units'] = max(0, t.get('yield_units', 0) - 12)
                    if t['yield_units'] == 0:
                        farm['tiles'][y][x] = None
                        t = None
                if t is not None:
                    if t.get('watered_today'):
                        t['consecutive_unwatered'] = 0
                    else:
                        t['consecutive_unwatered'] = t.get('consecutive_unwatered', 0) + 1
                    if t['consecutive_unwatered'] >= 2:
                        farm['tiles'][y][x] = {'kind': 'WEED'}
                    else:
                        if cd['ongoing']:
                            since = (day + 1 - t['planted_day']) - cd['first_yield_day']
                            if since >= 0 and since % cd['interval'] == 0 and since // cd['interval'] < cd['max_yield']:
                                inc = 2 if (t.get('watered_today') and t.get('fertilized_until_day', -1) >= day) else 1
                                t['yield_units'] = min(cd['max_yield'], t.get('yield_units', 0) + inc)
                        t['watered_today'] = False
            elif 'animal' in t:
                ad = ANIMALS[t['animal']]
                fed = bool(t.get('fed_today'))
                if fed:
                    t['consecutive_unfed'] = 0
                else:
                    t['consecutive_unfed'] = t.get('consecutive_unfed', 0) + 1
                if t['consecutive_unfed'] >= 2:
                    farm['tiles'][y][x] = {'kind': ad['structure']}
                else:
                    since = (day + 1 - t['placed_day']) - ad['first_yield_day']
                    if since >= 0 and since % ad['interval'] == 0:
                        bonus = int(t.get('pending_care_bonus', 0)) if fed else 0
                        t['yield_units'] = min(ad['max_held'], t.get('yield_units', 0) + 1 + bonus)
                        t['pending_care_bonus'] = 0
                    if t.get('cared_today') and fed:
                        t['pending_care_bonus'] = int(t.get('pending_care_bonus', 0)) + 1
                    t['fertilizer_available'] = True
                    t['fed_today'] = False
                    t['cared_today'] = False
        cols[pos] = Project(proj.value, tuple(r for r in proj.days if r[0] > day))
        cur_t = farm['tiles'][y][x]
        expected[pos] = dict(cur_t) if isinstance(cur_t, dict) else cur_t
    return {'day': day + 1, 'columns': cols, 'expected': expected}


# ============================================================================
# Layer 6: Tactical Market, Certified Dispatcher & Zero-Error Master Agent
# ============================================================================

def sanitize_action(raw, n_hands):
    """Guarantee 0 structural errors under engine validation."""
    if not isinstance(raw, dict):
        return {'farmer': ['PASS'], 'hands': [['PASS'] for _ in range(n_hands)], 'market': []}

    def clean_unit(cmd):
        if not isinstance(cmd, list) or not cmd or cmd[0] not in WORKER_OPS:
            return ['PASS']
        op = cmd[0]
        if op in ('NORTH', 'SOUTH', 'EAST', 'WEST', 'PASS', 'WATER', 'HARVEST', 'FERTILIZE',
                  'BUILD_COOP', 'BUILD_PASTURE', 'DIG', 'FEED', 'COLLECT_FERTILIZER', 'CARE', 'DROP'):
            return [op]
        if op == 'PLANT':
            return [op, cmd[1]] if len(cmd) >= 2 and cmd[1] in CROPS else ['PASS']
        if op in ('PICKUP', 'PLACE'):
            if len(cmd) < 2:
                return ['PASS']
            it = cmd[1]
            if it not in PRODUCTS and it not in ANIMALS:
                return ['PASS']
            if len(cmd) >= 3:
                n = int(cmd[2])
                return [op, it, n] if n > 0 else ['PASS']
            return [op, it]
        return ['PASS']

    farmer = clean_unit(raw.get('farmer'))
    raw_hands = raw.get('hands')
    if not isinstance(raw_hands, list):
        raw_hands = []
    hands = [clean_unit(raw_hands[i]) if i < len(raw_hands) else ['PASS'] for i in range(n_hands)]

    market_out = []
    raw_mkt = raw.get('market')
    if isinstance(raw_mkt, list):
        for m in raw_mkt:
            if len(market_out) >= 10:
                break
            if not isinstance(m, list) or not m or m[0] not in MARKET_OPS:
                continue
            mop = m[0]
            if mop in ('HIRE', 'BUY_LAND'):
                market_out.append([mop])
            elif len(m) >= 3:
                it = m[1]
                try:
                    qty = int(m[2])
                except (TypeError, ValueError):
                    continue
                if qty <= 0:
                    continue
                if mop == 'SELL' and it in PRODUCTS:
                    market_out.append([mop, it, qty])
                elif mop == 'BUY_PRODUCT' and it in ('WHEAT', 'FERTILIZER'):
                    market_out.append([mop, it, qty])
                elif mop == 'BUY_SEED' and it in CROPS:
                    market_out.append([mop, it, qty])
                elif mop == 'BUY_ANIMAL' and it in ANIMALS:
                    market_out.append([mop, it, qty])
    return {'farmer': farmer, 'hands': hands, 'market': market_out}


class MasterAgent:
    def __init__(self):
        self.reset()

    def reset(self):
        self.last_step = -1
        self.player = None
        self.tracker = OpponentTracker()
        self.continuation = None
        self.day = -1
        self.jobs = []
        self.queues = []
        self.plan_ready = False
        self.procure_deadline_hour = 3
        self.crew = 0
        self.buy_land = False
        self.target_quads = 1
        self.delivery = False
        self.want_seeds = Counter()
        self.want_animals = Counter()
        self.want_resources = Counter()
        self.keep_buffer = Counter()
        self.intraday_expanded_day = -1
        self.economic_model = None
        self.day_witness = None

    def liquid_cash_and_shed(self, obs):
        farm = obs['farms'][int(obs['player'])]
        shed = obs['private']['shed']
        mkt = obs['market']
        val = float(farm['money'])
        for k, n in shed.items():
            if k in PRODUCTS and n > 0:
                val += own_trade(mkt, k, n, True)[0]
        return val

    def owned_total(self, obs, item):
        priv = obs['private']
        tot = int(priv['shed'].get(item, 0))
        for inv in priv['inventories']:
            tot += int(inv.get(item, 0))
        return tot

    def compute_invest_budget(self, obs):
        day = int(obs['step']) // 24
        farm = obs['farms'][int(obs['player'])]
        quads = len(farm['unlocked_quadrants'])
        already_hired = len(farm.get('hands') or ())
        herd = sum(1 for row in farm['tiles'] for t in row if isinstance(t, dict) and 'animal' in t)
        cash = self.liquid_cash_and_shed(obs)
        mkt_px = obs['market']['prices']
        wheat_owned = self.owned_total(obs, 'WHEAT')
        wheat_px = mkt_px['WHEAT']
        feed_need = max(0, herd - wheat_owned)
        base_crew = (5, 7, 10, 12)[min(3, quads - 1)]
        rem_wage = max(0, fibcost(base_crew) - fibcost(already_hired))
        if day == 0:
            reserve = max(0, fibcost(5) - fibcost(already_hired)) + max(0, 4 - wheat_owned) * (wheat_px + 2) + 18.0
            return max(0.0, cash - reserve)

        if day <= 5:
            land_save = 220.0 if (quads == 1 and day == 5) else 45.0
            reserve = rem_wage + feed_need * (wheat_px + 2) + land_save
            return max(0.0, cash - reserve)

        # Light 1-day trough bridge ONLY on zero-harvest days (e.g. pre-Melon D7-D9) so compounding never stalls
        d1_bridge = 0.0
        if day <= 27 and herd >= 6:
            wheat_d0 = 0
            rev_d0 = 0.0
            for row in farm['tiles']:
                for t in row:
                    if not isinstance(t, dict):
                        continue
                    if 'animal' in t:
                        yu = int(t.get('yield_units', 0))
                        if yu > 0:
                            rev_d0 += 0.75 * yu * mkt_px[ANIMALS[t['animal']]['product']]
                    elif t.get('kind') == 'PLANT':
                        c = t['crop']
                        yu = int(t.get('yield_units', 0))
                        if yu > 0 and day - int(t['planted_day']) >= CROPS[c]['first_yield_day']:
                            if c == 'WHEAT':
                                wheat_d0 += yu
                            else:
                                rev_d0 += 0.75 * yu * mkt_px[c]
            d1_feed = max(0, herd - max(0, wheat_owned - herd) - wheat_d0) * (wheat_px + 2)
            d1_burn = fibcost(base_crew) + d1_feed
            if rev_d0 < 80.0:
                d1_bridge = max(0.0, d1_burn - rev_d0)

        reserve = rem_wage + feed_need * (wheat_px + 2) + d1_bridge + 95.0
        return max(0.0, cash - reserve)

    def build_candidate_plan(self, obs, model, positions, budget, eval_budget=2200):
        day = model.day
        farm = obs['farms'][int(obs['player'])]
        priv = obs['private']
        prices = model.book.marginal(model.flow)
        jobs = []
        goods = Counter()
        blitz_goods = Counter()
        rem_b = float(budget)
        used_seeds = Counter()
        used_anims = Counter()

        order = sorted(
            positions,
            key=lambda pos: (not isinstance(farm['tiles'][pos[1]][pos[0]], dict), sd(pos), pos[1], pos[0]),
        )
        for pos in order:
            t = farm['tiles'][pos[1]][pos[0]]
            proj = model.projects[pos]
            row = proj.first(day)
            if not row[1]:
                continue
            ops = []
            existing = isinstance(t, dict) and ('animal' in t or t.get('kind') == 'PLANT')
            for raw in row[1]:
                op = list(raw)
                if op[0] == 'PLANT':
                    c = op[1]
                    have_free = priv['seeds'].get(c, 0) - used_seeds[c]
                    cost = 0.0 if have_free > 0 else CROPS[c]['seed']
                    if rem_b < cost - 1e-6:
                        break
                    rem_b -= cost
                    used_seeds[c] += 1
                elif op[0] in ('BUILD_COOP', 'BUILD_PASTURE'):
                    a = next((x[1] for x in row[1] if x[0] == 'PLACE' and len(x) > 1 and x[1] in ANIMALS), None)
                    if a:
                        have_free = self.owned_total(obs, a) - used_anims[a]
                        cost = 0.0 if have_free > 0 else (ANIMALS[a]['cost'] + model.animal_feed_bridge)
                        if rem_b < cost + (10.0 if day == 0 else 60.0):
                            break
                        rem_b -= cost
                        used_anims[a] += 1
                elif op[0] == 'PLACE' and len(op) > 1 and op[1] in ANIMALS:
                    a = op[1]
                    if not any(x[0] in ('BUILD_COOP', 'BUILD_PASTURE') for x in ops):
                        have_free = self.owned_total(obs, a) - used_anims[a]
                        cost = 0.0 if have_free > 0 else (ANIMALS[a]['cost'] + model.animal_feed_bridge)
                        if rem_b < cost - 1e-6:
                            break
                        rem_b -= cost
                        used_anims[a] += 1
                ops.append(op)
            if not ops:
                continue
            out_dict = dict(row[2])
            val = sum(n * prices.get(k, [0.0] * 30)[day] for k, n in row[2])
            urgent = existing and day < 29 and (t.get('consecutive_unwatered', 0) >= 1 or t.get('consecutive_unfed', 0) >= 1)
            is_blitz = bool(
                out_dict.get('MELON', 0) > 0
                or (out_dict.get('WOOL', 0) > 0 and (len(farm['unlocked_quadrants']) == 1 or obs['market']['prices']['WOOL'] >= 150))
            )
            prio = val + 0.1 * max(0.0, proj.value) + (12000.0 if urgent else 0.0) + (6000.0 if is_blitz else 0.0)
            if day == 29:
                skip_loss = max(0.0, val)
            elif urgent:
                asset_c = ANIMALS[t['animal']]['cost'] if 'animal' in t else CROPS[t['crop']]['seed']
                skip_loss = max(0.0, val) + asset_c + min(1200.0, max(200.0, proj.value)) + 1600.0
            elif existing and 'animal' in t:
                skip_loss = max(0.0, val) + 95.0
            elif existing and t.get('crop') in ('STRAWBERRY', 'TOMATO', 'MELON'):
                skip_loss = max(0.0, val) + 65.0
            else:
                skip_loss = max(0.0, val) + 25.0 + 0.05 * max(0.0, proj.value)
            jobs.append({
                'pos': pos,
                'ops': ops,
                'priority': prio,
                'skip_loss': skip_loss,
                'mandatory': urgent,
                'existing': existing,
                'outputs': out_dict,
                'blitz': is_blitz,
            })
            goods.update(out_dict)
            if is_blitz:
                blitz_goods.update(out_dict)

        shed_now = sum(priv['shed'].values())
        delivery = (
            day == 29
            or (shed_now + sum(goods.values()) > 92)
            or sum(blitz_goods.values()) >= 6
            or (len(farm['unlocked_quadrants']) < 4 and day <= 12 and sum(goods.get(k, 0) * obs['market']['prices'][k] for k in ('MELON', 'WOOL', 'MILK', 'STRAWBERRY')) >= 1200)
        )
        deliv_set = self.select_delivery_set(jobs, day, shed_now, len(farm['unlocked_quadrants']))

        want_res = Counter()
        for j in jobs:
            want_res.update(job_needs_counter(j['ops']))
        res_avail = Counter(priv['shed'])
        for inv in priv['inventories']:
            res_avail.update(inv)
        for k, n in want_res.items():
            res_avail[k] = max(res_avail[k], n)

        existing_hands = len(farm['hands'])
        spawn_pos = [tuple(farm['farmer'])] + [tuple(h) for h in farm['hands']]
        while len(spawn_pos) < 17:
            occ = {st: spawn_pos.count(st) for st in SHED_TILES}
            nxt_p = min(SHED_TILES, key=lambda st: (occ[st], SHED_TILES.index(st)))
            spawn_pos.append(nxt_p)

        now_hour = int(obs['step']) % 24
        n_quads = max(
            len(farm['unlocked_quadrants']),
            4 if len(model.projects) >= 90 else (3 if len(model.projects) >= 65 else (2 if len(model.projects) >= 40 else 1)),
        )
        cur_anims = sum(1 for row in farm['tiles'] for t in row if isinstance(t, dict) and 'animal' in t)
        lo = max(existing_hands, min(14, max(1, math.ceil((sum(len(j['ops']) for j in jobs) + len(jobs)) / max(8, 22 - now_hour)) - 2)))
        if day <= 4 and n_quads == 1:
            cap_hands = max(existing_hands, 6)
        elif day == 29:
            cap_hands = max(existing_hands, 15 if n_quads >= 4 else 14)
        elif n_quads <= 3:
            cap_hands = max(existing_hands, 13 if cur_anims >= 20 else 11)
        else:
            cap_hands = max(existing_hands, 15)

        melon_blitz_day = (day < 29 and blitz_goods.get('MELON', 0) >= 18)

        def eval_crew(hands_n):
            r_hour = max(now_hour + 1, 1 if hands_n <= 8 else 2)
            workers = []
            for wi in range(hands_n + 1):
                inv_w = priv['inventories'][wi] if wi < len(priv['inventories']) else {}
                wh = now_hour if (wi <= existing_hands and now_hour >= 1) else r_hour
                workers.append({'pos': spawn_pos[wi], 'hour': wh, 'inv': dict(inv_w)})
            res_c = solve_routes(
                jobs,
                workers,
                dict(res_avail),
                last_hour=19 if melon_blitz_day else (22 if day == 29 else 23),
                track_collect=True,
                evaluation_budget=500,
                return_to_shed=delivery,
                end_service=2 if delivery else 1,
                selective_return=True,
                delivery_jobs=deliv_set,
            )
            lost_c = sum(jobs[j]['skip_loss'] for j in res_c['unassigned'])
            return (fibcost(hands_n) + lost_c, hands_n, res_c)

        best = None
        h_cur = min(lo, cap_hands)
        prev_h = h_cur - 1
        while h_cur <= cap_hands:
            cur_tup = eval_crew(h_cur)
            if best is None or cur_tup[0] < best[0]:
                best = cur_tup
            un_cnt = len(cur_tup[2]['unassigned'])
            if un_cnt == 0:
                if h_cur - prev_h > 1:
                    mid_tup = eval_crew(h_cur - 1)
                    if mid_tup[0] < best[0]:
                        best = mid_tup
                break
            prev_h = h_cur
            h_cur += 2 if (un_cnt >= 5 and h_cur + 2 <= cap_hands) else 1

        crew = best[1]
        dropped = set(best[2]['unassigned'])
        uncovered = sum(jobs[j]['skip_loss'] for j in dropped)
        kept_jobs = [j for i, j in enumerate(jobs) if i not in dropped or (j['existing'] and day < 29)]
        witness = best[2] if not dropped else None
        return {
            'model': model,
            'jobs': kept_jobs,
            'crew': crew,
            'delivery': delivery,
            'uncovered': uncovered,
            'witness': witness,
            'source_jobs': jobs,
        }

    def select_delivery_set(self, jobs, day, shed_now=0, quads=4):
        if day == 29:
            return {i for i, j in enumerate(jobs) if j.get('outputs')}
        chosen = {i for i, j in enumerate(jobs) if j.get('blitz')}
        if quads < 4 and day <= 12:
            for i, j in enumerate(jobs):
                outs = j.get('outputs') or {}
                if any(outs.get(k, 0) >= 4 for k in ('MELON', 'WOOL', 'MILK', 'STRAWBERRY')):
                    chosen.add(i)
        total = shed_now + sum(sum((j.get('outputs') or {}).values()) for j in jobs)
        already = sum(sum((jobs[i].get('outputs') or {}).values()) for i in chosen)
        needed = max(0, total - 88) - already
        if needed > 0:
            ranked = sorted(
                (i for i in range(len(jobs)) if i not in chosen),
                key=lambda i: -(sum((jobs[i].get('outputs') or {}).values()) / (1.0 + sd(jobs[i]['pos']))),
            )
            acc = 0
            for i in ranked:
                if acc >= needed:
                    break
                amt = sum((jobs[i].get('outputs') or {}).values())
                if amt > 0:
                    chosen.add(i)
                    acc += amt
        return chosen

    def refresh_requirements(self, obs, model):
        self.want_seeds = Counter()
        self.want_animals = Counter()
        self.want_resources = Counter()
        for j in self.jobs:
            self.want_resources.update(job_needs_counter(j['ops']))
            for op in j['ops']:
                if op[0] == 'PLANT':
                    self.want_seeds[op[1]] += 1
                elif op[0] == 'PLACE' and len(op) > 1 and op[1] in ANIMALS:
                    self.want_animals[op[1]] += 1

        # Compute 2-day forward input retention buffer so we don't churn home-grown Wheat/Fertilizer
        self.keep_buffer = Counter()
        if model is not None and self.day < 28 and int(obs['market']['inventory']['WHEAT']) < 10050:
            fut_wheat = 0
            for d in range(self.day + 1, min(30, self.day + 3)):
                if model.flow['WHEAT'][d] < 0:
                    fut_wheat += int(round(-model.flow['WHEAT'][d]))
            self.keep_buffer['WHEAT'] = min(20, fut_wheat)

    def is_procurement_pending(self, obs, hour):
        """State-driven procurement check: keep buying until all affordable hires/inputs/seeds are acquired (up to deadline)."""
        if hour >= self.procure_deadline_hour:
            return False
        farm = obs['farms'][int(obs['player'])]
        priv = obs['private']
        mkt = obs['market']
        avail_cash = float(farm['money'])
        sellable_units = 0
        for k in PRODUCTS:
            ex_n = max(0, int(priv['shed'].get(k, 0)) - self.want_resources[k])
            if ex_n > 0:
                avail_cash += own_trade(mkt, k, ex_n, True)[0]
                sellable_units += ex_n

        # Emergency survival check: Fertilizer in shed can be sold if needed to fund mandatory HIRE or WHEAT feed
        hired = len(farm['hands'])
        mand_wage = max(0, fibcost(self.crew) - fibcost(hired))
        need_wheat = max(0, self.want_resources['WHEAT'] - self.owned_total(obs, 'WHEAT'))
        surv_need = mand_wage + (own_trade(mkt, 'WHEAT', need_wheat, False)[0] if need_wheat > 0 else 0)
        fert_shed = int(priv['shed'].get('FERTILIZER', 0))
        fert_reserved_in_shed = min(fert_shed, self.want_resources['FERTILIZER'])
        if avail_cash < surv_need and fert_reserved_in_shed > 0:
            avail_cash += own_trade(mkt, 'FERTILIZER', fert_reserved_in_shed, True)[0]
            sellable_units += fert_reserved_in_shed
        shed_room = max(0, 100 - sum(priv['shed'].values())) + sellable_units

        if hired < self.crew:
            nxt_wage = fibcost(hired + 1) - fibcost(hired)
            if avail_cash >= nxt_wage:
                return True
        if self.buy_land and len(farm['unlocked_quadrants']) < self.target_quads:
            l_px = LAND_PRICES[len(farm['unlocked_quadrants']) - 1]
            if avail_cash >= l_px:
                return True
        for it in ('WHEAT', 'FERTILIZER'):
            if self.want_resources[it] > self.owned_total(obs, it) and shed_room > 0:
                u_px = quote_price(it, int(mkt['inventory'][it]) - 1, mkt.get('params'))
                if avail_cash >= u_px:
                    return True
        for a, n in self.want_animals.items():
            if n > self.owned_total(obs, a) and shed_room > 0 and avail_cash >= ANIMALS[a]['cost']:
                return True
        for c, n in self.want_seeds.items():
            if n > int(priv['seeds'].get(c, 0)) and avail_cash >= CROPS[c]['seed']:
                return True
        return False

    def begin_day(self, obs, intraday=False):
        step = int(obs['step'])
        day = step // 24
        hour = step % 24
        self.day = day
        self.jobs = []
        self.queues = []
        self.plan_ready = False
        self.day_witness = None
        farm = obs['farms'][int(obs['player'])]
        overage = float(obs.get('remainingOverageTime', 60.0))
        fast_mode = overage < 22.0

        budget = self.compute_invest_budget(obs)
        quads = len(farm['unlocked_quadrants'])
        self.buy_land = False
        self.target_quads = quads

        positions = [(x, y) for y in range(10) for x in range(10) if farm['tiles'][y][x] != 'LOCKED']
        saved = None if intraday else self.continuation
        model = Portfolio(obs, positions, self.tracker, saved, budget=budget)
        if intraday and hour >= 2:
            model.labor_multiplier[day] = max(model.labor_multiplier[day], 21.0 / max(11.0, 22.5 - hour))
            model.value = model.objective()
        sweeps = 1 if fast_mode else 2
        k_dem0 = shop_daily_demand(obs['town']['unlocked_shops'])
        yarn0 = sum(s == 'YARN_STORE' for s in obs['town']['unlocked_shops'])
        egg0 = sum(s in ('BAKERY', 'BRUNCH_SPOT') for s in obs['town']['unlocked_shops'])
        dom_livestock = (k_dem0['MILK'] + k_dem0['WOOL'] >= 18 and k_dem0['MILK'] + k_dem0['WOOL'] >= k_dem0['STRAWBERRY'] + 10)
        a_cap = 4 if day == 0 else (3 if day <= 5 else (6 if (dom_livestock or yarn0 >= 2 or egg0 >= 3) else 4))
        used_day0_seed = False

        if step == 0:
            d0_cand = build_day0_compounding_candidate(model, obs, budget)
            if d0_cand is not None:
                model = d0_cand
                used_day0_seed = True
            else:
                model.optimize(budget, sweeps=sweeps, animal_cap=a_cap)
        else:
            model.optimize(budget, sweeps=sweeps, animal_cap=a_cap)

        # Disciplined Quadrant Unlock: gate SE ($4000) by inner-ring (sd<=5) saturation & zero-perturbation clone trial!
        inner_idle = sum(
            1 for p in positions
            if sd(p) <= 5 and farm['tiles'][p[1]][p[0]] is None and not model.projects[p].first(day)[1]
        )
        perm_tiles = sum(
            1
            for row in farm['tiles']
            for t in row
            if isinstance(t, dict) and ('animal' in t or t.get('crop') in ('STRAWBERRY', 'TOMATO'))
        )
        max_land_day = 20
        max_idle = 2 if quads == 3 else 3
        if quads < 4 and 4 <= day <= max_land_day and inner_idle <= max_idle and not (intraday and quads >= 3):
            land_px = LAND_PRICES[quads - 1]
            se_w_margin = 1200.0 if perm_tiles >= 20 else 2200.0
            se_min_gain = 550.0 if perm_tiles >= 20 else 1200.0
            work_margin = 450.0 if quads == 1 else (800.0 if quads == 2 else se_w_margin)
            min_npv_gain = 140.0 if quads == 1 else (260.0 if quads == 2 else se_min_gain)
            if budget >= land_px + work_margin:
                q_name = LAND_ORDER[quads - 1]
                new_pos = [(x, y) for y in range(10) for x in range(10) if quadrant_of(x, y) == q_name]
                all_pos = positions + new_pos
                t_acap = 3 if quads == 1 else (6 if (dom_livestock or yarn0 >= 2 or egg0 >= 3) else 4)
                if quads == 3 and model.today_capital_spend() <= budget - land_px - 600.0:
                    trial = model.clone()
                    for np_p in new_pos:
                        trial.install(np_p, EMPTY_PROJECT)
                    trial.value = trial.objective()
                    trial.optimize(budget - land_px, only_positions=new_pos, sweeps=2 if not fast_mode else 1, animal_cap=t_acap)
                else:
                    trial = Portfolio(obs, all_pos, self.tracker, saved, budget=budget - land_px)
                    if intraday and hour >= 2:
                        trial.labor_multiplier[day] = max(trial.labor_multiplier[day], 21.0 / max(11.0, 22.5 - hour))
                        trial.value = trial.objective()
                    trial.optimize(budget - land_px, sweeps=1, animal_cap=t_acap)
                new_active = sum(1 for p in new_pos if trial.projects[p].first(day)[1])
                land_gain = trial.value - model.value - land_px
                if land_gain > min_npv_gain and new_active >= 6:
                    old_bc = (5, 7, 10, 12)[min(3, quads - 1)]
                    new_bc = (5, 7, 10, 12)[min(3, quads)]
                    extra_w = max(0, fibcost(new_bc) - fibcost(old_bc))
                    model = trial
                    self.buy_land = True
                    quads += 1
                    self.target_quads = quads
                    budget = max(0.0, budget - land_px - extra_w)
                    positions = all_pos

        if not fast_mode:
            model.reprice_critical_routes()
            if not used_day0_seed:
                model.optimize(budget, sweeps=1, animal_cap=a_cap)

        # Build baseline certified plan + labor-feedback + structural candidates, ranked via Zero-Sum CFR Response Game!
        eval_b = 1600 if fast_mode else 2400
        incumbent = self.build_candidate_plan(obs, model, positions, budget, eval_budget=eval_b)
        cands = [(incumbent, 0.0)]

        if not fast_mode and not used_day0_seed:
            est_wage = labor_curve_cost([model.priced_work()[day]])
            act_wage = fibcost(incumbent['crew'])
            if act_wage > est_wage + 55.0:
                fb_model = model.clone()
                factor = (incumbent['crew'] + 1) * 21.0 / max(1.0, fb_model.work[day])
                if factor > fb_model.labor_multiplier[day] * 1.02:
                    fb_model.labor_multiplier[day] = factor
                    fb_model.value = fb_model.objective()
                    fb_model.optimize(budget, sweeps=1, animal_cap=a_cap)
                    fb_cand = self.build_candidate_plan(obs, fb_model, positions, budget, eval_budget=eval_b)
                    cands.append((fb_cand, 25.0))

        if not fast_mode and overage > 34.0 and day >= 3:
            max_fams = 2 if overage > 44.0 else 1
            struct_models = demand_structure_candidates(incumbent['model'], obs, budget, max_families=max_fams)
            for sm in struct_models:
                sm.reprice_critical_routes()
                scand = self.build_candidate_plan(obs, sm, positions, budget, eval_budget=eval_b)
                cands.append((scand, 75.0))

        if len(cands) > 1:
            books = [incumbent['model'].book]
            if not fast_mode and overage > 32.0 and 4 <= day <= 24:
                sh_flow = build_rival_shadow_flow(obs, self.tracker)
                if sh_flow is not None:
                    books.append(MarketBook(obs, self.tracker, fixed_rival_flow=sh_flow))
            if not fast_mode and day >= 3:
                st_flow = build_rival_stress_flow(obs)
                if st_flow is not None:
                    books.append(MarketBook(obs, self.tracker, extra_rival_flow=st_flow))
            incumbent = cfr_select_candidate(cands, books)

        self.economic_model = incumbent['model']
        self.jobs = incumbent['jobs']
        self.crew = incumbent['crew']
        self.delivery = incumbent['delivery']
        self.day_witness = incumbent['witness']
        self.source_jobs = incumbent['source_jobs']
        self.refresh_requirements(obs, self.economic_model)

        if not intraday:
            self.continuation = remember_continuation(obs, self.economic_model.projects)
            self.procure_deadline_hour = 3 if any(j.get('blitz') for j in self.jobs) else 4
        else:
            self.procure_deadline_hour = min(20, hour + 3)

    def compare_certified_candidates(self, obs, base_opt, cand_opt, stress_book=None, min_gain=25.0):
        m_a = base_opt['model']
        m_b = cand_opt['model']
        day = m_a.day
        common_mult = [max(m_a.labor_multiplier[d], m_b.labor_multiplier[d]) for d in range(30)]

        def cert_score(opt, book_override=None):
            mdl = opt['model']
            wk = [w * f for w, f in zip(mdl.work, common_mult)]
            wk[day] = 0.0
            bk = book_override if book_override is not None else mdl.book
            return (
                bk.score(mdl.flow)
                - mdl.cost
                + mdl.capital_credit()
                - labor_curve_cost(wk)
                - fibcost(opt['crew'])
                - opt['uncovered']
            )

        sc_a = cert_score(base_opt)
        sc_b = cert_score(cand_opt)
        if stress_book is not None:
            st_a = cert_score(base_opt, stress_book)
            st_b = cert_score(cand_opt, stress_book)
            eff_a = 0.70 * sc_a + 0.30 * st_a
            eff_b = 0.70 * sc_b + 0.30 * st_b
            if eff_b > eff_a + min_gain and st_b >= st_a - 450.0:
                return cand_opt
            return base_opt
        return cand_opt if sc_b > sc_a + min_gain else base_opt

    def sync_jobs_with_live_board(self, obs):
        """Filter already-executed ops and unpurchased inputs before building live worker queues."""
        farm = obs['farms'][int(obs['player'])]
        priv = obs['private']
        seeds_left = Counter(priv['seeds'])
        anim_left = Counter({a: self.owned_total(obs, a) for a in ANIMALS})
        collectable_fert = sum(
            1
            for row in farm['tiles']
            for t in row
            if isinstance(t, dict) and 'animal' in t and t.get('fertilizer_available')
        )
        fert_left = self.owned_total(obs, 'FERTILIZER') + collectable_fert
        fert_allow_idx = set()
        if fert_left > 0:
            fert_ranked = sorted(
                (
                    idx for idx, j in enumerate(self.jobs)
                    if any(op[0] == 'FERTILIZE' for op in j['ops'])
                ),
                key=lambda idx: -float(self.jobs[idx].get('priority', 0.0)),
            )
            fert_allow_idx = set(fert_ranked[:fert_left])

        live_jobs = []
        for idx_j, j in enumerate(self.jobs):
            x, y = j['pos']
            raw_t = farm['tiles'][y][x]
            if raw_t == 'LOCKED':
                continue
            sim_t = dict(raw_t) if isinstance(raw_t, dict) else raw_t
            a_req = next((op[1] for op in j['ops'] if op[0] == 'PLACE' and len(op) > 1 and op[1] in ANIMALS), None)
            has_anim_stock = True
            if a_req is not None:
                if anim_left[a_req] > 0:
                    anim_left[a_req] -= 1
                else:
                    has_anim_stock = False
            ops = []
            for op in j['ops']:
                k = op[0]
                if k in ('BUILD_COOP', 'BUILD_PASTURE', 'PLACE') and a_req is not None and not has_anim_stock:
                    break
                if k == 'FERTILIZE' and idx_j not in fert_allow_idx:
                    continue
                if k == 'WATER' and isinstance(sim_t, dict) and sim_t.get('watered_today'):
                    continue
                if k == 'FEED' and isinstance(sim_t, dict) and sim_t.get('fed_today'):
                    continue
                if k == 'CARE' and isinstance(sim_t, dict) and sim_t.get('cared_today'):
                    continue
                if k == 'COLLECT_FERTILIZER' and isinstance(sim_t, dict) and not sim_t.get('fertilizer_available'):
                    continue
                if k == 'HARVEST':
                    if not isinstance(sim_t, dict) or sim_t.get('yield_units', 0) <= 0:
                        continue
                    if sim_t.get('kind') == 'PLANT' and not CROPS[sim_t['crop']]['ongoing']:
                        sim_t = None
                    else:
                        sim_t['yield_units'] = 0
                elif k == 'DIG':
                    if sim_t is None or (isinstance(sim_t, dict) and 'animal' in sim_t):
                        continue
                    sim_t = None
                elif k == 'PLANT':
                    c = op[1]
                    if seeds_left[c] <= 0:
                        break
                    seeds_left[c] -= 1
                    sim_t = {'kind': 'PLANT', 'crop': c, 'watered_today': False, 'yield_units': 0}
                elif k == 'WATER' and isinstance(sim_t, dict):
                    sim_t['watered_today'] = True
                ops.append(op)
            if ops:
                nj = dict(j, ops=ops)
                live_jobs.append(nj)
        return live_jobs

    def plan_live_routes(self, obs, hour):
        farm = obs['farms'][int(obs['player'])]
        priv = obs['private']
        units_pos = [tuple(farm['farmer'])] + [tuple(h) for h in farm['hands']]
        workers = []
        for idx, pos in enumerate(units_pos):
            inv = priv['inventories'][idx] if idx < len(priv['inventories']) else {}
            workers.append({'pos': pos, 'hour': hour, 'inv': dict(inv)})

        live_jobs = self.sync_jobs_with_live_board(obs)
        shed_now = sum(priv['shed'].values())
        n_quads_now = len(farm['unlocked_quadrants'])
        deliv_set = self.select_delivery_set(live_jobs, self.day, shed_now, n_quads_now)
        overage = float(obs.get('remainingOverageTime', 60.0))
        eb = 3500 if overage > 35.0 else (1800 if overage > 15.0 else 600)
        last_h = 22 if self.day == 29 else 23

        res_std = solve_routes(
            live_jobs,
            workers,
            dict(priv['shed']),
            last_hour=last_h,
            track_collect=True,
            evaluation_budget=eb,
            return_to_shed=self.delivery,
            end_service=2 if self.delivery else 1,
            selective_return=True,
            delivery_jobs=deliv_set,
        )
        # Never allow optional FERTILIZE timing/shortage to block WATER or HARVEST on a tile!
        fert_blocked = [
            ji for ji in res_std['unassigned']
            if len(live_jobs[ji]['ops']) > 1 and any(op[0] == 'FERTILIZE' for op in live_jobs[ji]['ops'])
        ]
        if fert_blocked:
            for ji in fert_blocked:
                stripped_ops = [op for op in live_jobs[ji]['ops'] if op[0] != 'FERTILIZE']
                if stripped_ops:
                    live_jobs[ji] = dict(live_jobs[ji], ops=stripped_ops)
            deliv_set = self.select_delivery_set(live_jobs, self.day, shed_now, n_quads_now)
            res_std = solve_routes(
                live_jobs,
                workers,
                dict(priv['shed']),
                last_hour=last_h,
                track_collect=True,
                evaluation_budget=max(800, eb // 2),
                return_to_shed=self.delivery,
                end_service=2 if self.delivery else 1,
                selective_return=True,
                delivery_jobs=deliv_set,
            )

        active_jobs = live_jobs
        active_res = res_std
        active_deliv = deliv_set
        s_tiles_won = [[] for _ in range(len(workers))]

        if self.day < 29:
            def _sprint_prefix_len(jb):
                if not jb.get('blitz') or not jb['ops']:
                    return 0
                if jb['ops'][0][0] == 'HARVEST':
                    return 1
                if len(jb['ops']) >= 2 and jb['ops'][0][0] == 'WATER' and jb['ops'][1][0] == 'HARVEST':
                    return 2
                return 0

            sprint_cands = [
                (ji, _sprint_prefix_len(live_jobs[ji]))
                for ji in range(len(live_jobs))
                if _sprint_prefix_len(live_jobs[ji]) > 0
            ]
            if sprint_cands:
                mand_std = sum(1 for j in res_std['unassigned'] if live_jobs[j].get('mandatory'))
                lost_std = sum(live_jobs[j]['skip_loss'] for j in res_std['unassigned'])

                def _try_pre_sprint(max_drop_h, max_per_w, max_total_sprints):
                    s_tiles = [[] for _ in range(len(workers))]
                    s_cur_pos = [w['pos'] for w in workers]
                    s_arr_h = [w['hour'] for w in workers]
                    s_items = [
                        {k for k in ('MELON', 'WOOL', 'STRAWBERRY', 'MILK') if w['inv'].get(k, 0) > 0}
                        for w in workers
                    ]
                    elig_w = [
                        wi for wi, w in enumerate(workers)
                        if not any(w['inv'].get(a, 0) > 0 for a in ANIMALS)
                    ]
                    rem_cands = list(sprint_cands)
                    assigned_cnt = 0
                    while rem_cands and elig_w and assigned_cnt < max_total_sprints:
                        best_choice = None
                        for wi in elig_w:
                            if len(s_tiles[wi]) >= max_per_w:
                                continue
                            for cand in rem_cands:
                                ji, plen = cand
                                tpos = tuple(live_jobs[ji]['pos'])
                                new_arr = s_arr_h[wi] + dist(s_cur_pos[wi], tpos) + plen
                                home_s = nearest_shed_tile(tpos)
                                out_p = {
                                    k for k, v in (live_jobs[ji].get('outputs') or {}).items()
                                    if v > 0 and k in PRODUCTS
                                }
                                n_places = max(1, len(s_items[wi] | out_p))
                                new_drop = new_arr + dist(tpos, home_s) + n_places
                                if new_drop <= max_drop_h:
                                    ckey = (new_drop, len(s_tiles[wi]), dist(s_cur_pos[wi], tpos), wi, ji)
                                    if best_choice is None or ckey < best_choice[0]:
                                        best_choice = (ckey, wi, cand, tpos, new_arr, out_p)
                        if best_choice is None:
                            break
                        _, b_wi, b_cand, b_pos, b_arr, b_out = best_choice
                        s_tiles[b_wi].append(b_cand)
                        s_cur_pos[b_wi] = b_pos
                        s_arr_h[b_wi] = b_arr
                        s_items[b_wi].update(b_out)
                        rem_cands = [x for x in rem_cands if x != b_cand]
                        assigned_cnt += 1

                    if not any(s_tiles):
                        return None

                    workers_sp = []
                    for wi, w in enumerate(workers):
                        if s_tiles[wi]:
                            home_s = nearest_shed_tile(s_cur_pos[wi])
                            inv_sp = dict(w['inv'])
                            p_items = sorted(s_items[wi])
                            for pk in p_items:
                                inv_sp.pop(pk, None)
                            drop_fin = s_arr_h[wi] + dist(s_cur_pos[wi], home_s) + len(p_items)
                            workers_sp.append({'pos': home_s, 'hour': drop_fin, 'inv': inv_sp})
                        else:
                            workers_sp.append({'pos': w['pos'], 'hour': w['hour'], 'inv': dict(w['inv'])})

                    s_map = {ji: plen for wl in s_tiles for ji, plen in wl}
                    s_units = Counter()
                    trimmed = []
                    for ji, jb in enumerate(live_jobs):
                        if ji in s_map:
                            for k, v in (jb.get('outputs') or {}).items():
                                s_units[k] += v
                            rem_ops = jb['ops'][s_map[ji]:]
                            if rem_ops:
                                rep_c = next((op[1] for op in rem_ops if op[0] == 'PLANT' and len(op) > 1), None)
                                has_anim_p = any(op[0] == 'PLACE' for op in rem_ops)
                                r_loss = 2500.0 if has_anim_p else (350.0 if rep_c in ('STRAWBERRY', 'TOMATO') else 35.0)
                                r_prio = 3500.0 if has_anim_p else (950.0 if rep_c in ('STRAWBERRY', 'TOMATO') else 220.0)
                                trimmed.append(dict(
                                    jb,
                                    ops=rem_ops,
                                    outputs={},
                                    blitz=False,
                                    mandatory=has_anim_p,
                                    existing=False,
                                    priority=r_prio,
                                    skip_loss=r_loss,
                                ))
                        else:
                            trimmed.append(jb)

                    deliv_sp = self.select_delivery_set(trimmed, self.day, shed_now, n_quads_now)
                    res_sp = solve_routes(
                        trimmed,
                        workers_sp,
                        dict(priv['shed']),
                        last_hour=last_h,
                        track_collect=True,
                        evaluation_budget=max(1200, eb // 2),
                        return_to_shed=self.delivery,
                        end_service=2 if self.delivery else 1,
                        selective_return=True,
                        delivery_jobs=deliv_sp,
                    )
                    mand_sp = sum(1 for j in res_sp['unassigned'] if trimmed[j].get('mandatory'))
                    lost_sp = sum(trimmed[j]['skip_loss'] for j in res_sp['unassigned'])
                    s_gain = (
                        s_units['MELON'] * 42.0
                        + s_units['WOOL'] * 26.0
                        + sum(v * 10.0 for k, v in s_units.items() if k not in ('MELON', 'WOOL'))
                    )
                    if mand_sp <= mand_std and (lost_sp - lost_std) <= min(160.0, s_gain * 0.35):
                        return (trimmed, res_sp, deliv_sp, s_tiles)
                    return None

                sp_attempt = None
                for mh, mpw, mts in ((15, 2, 99), (14, 2, 8), (13, 1, 6), (11, 1, 4)):
                    sp_attempt = _try_pre_sprint(mh, mpw, mts)
                    if sp_attempt is not None:
                        break
                if sp_attempt is not None:
                    active_jobs, active_res, active_deliv, s_tiles_won = sp_attempt

        self.queues = [deque() for _ in range(len(workers))]

        for wi, route in enumerate(active_res['routes']):
            inv_w = dict(workers[wi]['inv'])
            picks = active_res['pickups'][wi]
            q = []
            cur = units_pos[wi]
            extra_t = 0

            if s_tiles_won[wi]:
                s_out_items = set()
                for orig_ji, plen in s_tiles_won[wi]:
                    tpos = tuple(live_jobs[orig_ji]['pos'])
                    q.extend(moves_between(cur, tpos))
                    q.extend([list(op) for op in live_jobs[orig_ji]['ops'][:plen]])
                    cur = tpos
                    for k, v in (live_jobs[orig_ji].get('outputs') or {}).items():
                        if v > 0 and k in PRODUCTS:
                            s_out_items.add(k)
                home_s = nearest_shed_tile(cur)
                q.extend(moves_between(cur, home_s))
                cur = home_s
                place_items = sorted(
                    s_out_items | {k for k in ('MELON', 'WOOL', 'STRAWBERRY', 'MILK') if inv_w.get(k, 0) > 0}
                )
                for pk in place_items:
                    q.append(['PLACE', pk, 99])
                    inv_w.pop(pk, None)
                if picks:
                    for it, qty in picks.items():
                        if qty > 0:
                            q.append(['PICKUP', it, qty])
            else:
                pre_places = [
                    k for k in ('MELON', 'WOOL', 'STRAWBERRY', 'MILK')
                    if self.day < 29 and inv_w.get(k, 0) > 0
                ]
                has_blitz_inv = self.day < 29 and (inv_w.get('MELON', 0) > 0 or inv_w.get('WOOL', 0) > 0)
                if picks:
                    home = nearest_shed_tile(cur)
                    q.extend(moves_between(cur, home))
                    cur = home
                    if pre_places and active_res['finish_hours'][wi] + len(pre_places) <= last_h + 1:
                        for pk in pre_places:
                            q.append(['PLACE', pk, 99])
                            inv_w.pop(pk, None)
                        extra_t += len(pre_places)
                    for it, qty in picks.items():
                        if qty > 0:
                            q.append(['PICKUP', it, qty])
                elif has_blitz_inv:
                    home = nearest_shed_tile(cur)
                    nxt_d = (
                        dist(home, tuple(active_jobs[route[0]]['pos'])) - dist(cur, tuple(active_jobs[route[0]]['pos']))
                        if route else 0
                    )
                    pre_cost = dist(cur, home) + len(pre_places) + nxt_d
                    if active_res['finish_hours'][wi] + pre_cost <= last_h + 1:
                        q.extend(moves_between(cur, home))
                        cur = home
                        for pk in pre_places:
                            q.append(['PLACE', pk, 99])
                            inv_w.pop(pk, None)
                        extra_t += pre_cost

            # Mid-route blitz deposit using PLACE (never DROP, so WHEAT/FERTILIZER/ANIMALS stay in pocket!)
            blitz_indices = [idx_r for idx_r, ji in enumerate(route) if active_jobs[ji].get('blitz')]
            did_mid_drop = False
            last_blitz_pos_idx = blitz_indices[-1] if blitz_indices else -1

            for idx_r, ji in enumerate(route):
                job = active_jobs[ji]
                tpos = tuple(job['pos'])
                q.extend(moves_between(cur, tpos))
                q.extend([list(op) for op in job['ops']])
                cur = tpos

                if idx_r == last_blitz_pos_idx and idx_r < len(route) - 1 and self.day < 29:
                    b_prods = sorted({
                        k
                        for r_i in blitz_indices
                        for k, v in (active_jobs[route[r_i]].get('outputs') or {}).items()
                        if v > 0 and k in ('MELON', 'WOOL')
                    })
                    if b_prods:
                        home = nearest_shed_tile(cur)
                        nxt_pos = tuple(active_jobs[route[idx_r + 1]]['pos'])
                        detour = dist(cur, home) + len(b_prods) + dist(home, nxt_pos) - dist(cur, nxt_pos)
                        if active_res['finish_hours'][wi] + extra_t + detour <= last_h + 1:
                            q.extend(moves_between(cur, home))
                            for bp in b_prods:
                                q.append(['PLACE', bp, 99])
                            cur = home
                            extra_t += detour
                            did_mid_drop = True

            route_outs = sum(
                sum((active_jobs[ji].get('outputs') or {}).values())
                for idx_r, ji in enumerate(route)
                if not (did_mid_drop and idx_r <= last_blitz_pos_idx)
            )
            has_end_cargo = (
                any(v > 0 for v in inv_w.values())
                or (not self.delivery and self.day < 29 and route_outs >= 2)
                or any(
                    (ji in active_deliv) and not (did_mid_drop and idx_r <= last_blitz_pos_idx)
                    for idx_r, ji in enumerate(route)
                )
            )
            if (self.delivery or (self.day < 29 and (route_outs >= 2 or any(v > 0 for v in inv_w.values())))) and (route or any(v > 0 for v in inv_w.values())) and has_end_cargo:
                home = nearest_shed_tile(cur)
                suffix = moves_between(cur, home) + [['DROP']]
                if hour + len(q) + len(suffix) <= last_h + 1:
                    q.extend(suffix)

            self.queues[wi] = deque(q)
        self.plan_ready = True

    def early_setup_unit_actions(self, obs, hour):
        """While morning procurement is running, let existing workers step toward or water/harvest zero-input Blitz tiles!"""
        farm = obs['farms'][int(obs['player'])]
        priv = obs['private']
        units_pos = [tuple(farm['farmer'])] + [tuple(h) for h in farm['hands']]
        blitz_targets = []
        for j in self.jobs:
            if j.get('blitz') and j['ops'] and j['ops'][0][0] in ('WATER', 'HARVEST'):
                x, y = j['pos']
                t = farm['tiles'][y][x]
                if isinstance(t, dict) and t.get('yield_units', 0) > 0:
                    first_op = 'HARVEST' if (j['ops'][0][0] == 'HARVEST' or t.get('watered_today')) else 'WATER'
                    blitz_targets.append((tuple(j['pos']), first_op))
        used_targets = set()
        cmds = []
        for i, pos in enumerate(units_pos):
            inv = priv['inventories'][i] if i < len(priv['inventories']) else {}
            if any(v > 0 for v in inv.values()) and pos in SHED_SET:
                cmds.append(['DROP'])
                continue
            if inv.get('MELON', 0) >= 5 or inv.get('WOOL', 0) >= 5:
                home_e = nearest_shed_tile(pos)
                cmds.append(moves_between(pos, home_e)[0] if pos != home_e else ['DROP'])
                continue
            if blitz_targets:
                avail = [(tp, fop) for tp, fop in blitz_targets if tp not in used_targets]
                if avail:
                    tp, fop = min(avail, key=lambda item: dist(pos, item[0]))
                    used_targets.add(tp)
                    if pos == tp:
                        cmds.append([fop])
                    else:
                        cmds.append(moves_between(pos, tp)[0])
                    continue
            cmds.append(['PASS'])
        return cmds

    def should_delay_sale_for_tick(self, obs, item, step, urgent_cash_or_space):
        if urgent_cash_or_space or step >= 716 or item in ('MELON', 'FERTILIZER'):
            return False
        hour = step % 24
        opp = 1 - int(obs['player'])
        # Check if rival has unsold stock or ripe/active producers of item
        if self.tracker.unsold.get(item, 0.0) >= 1.0:
            return False
        for row in obs['farms'][opp]['tiles']:
            for t in row:
                if isinstance(t, dict):
                    if t.get('kind') == 'PLANT' and t.get('crop') == item:
                        return False
                    if 'animal' in t and ANIMALS[t['animal']]['product'] == item:
                        return False
        # Uncontested commodity: if a consumption tick happens at the end of THIS step, wait 1 hour!
        if hour == 0:
            return True
        if step % 4 == 0:
            shops = obs['town']['unlocked_shops']
            if any(item in SHOPS.get(s, ()) for s in shops):
                return True
        return False

    def build_market_orders(self, obs, step, hour, post_shed):
        farm = obs['farms'][int(obs['player'])]
        priv = obs['private']
        mkt = {'inventory': dict(obs['market']['inventory']), 'params': obs['market'].get('params')}
        shed = Counter(post_shed)
        cash = float(farm['money'])
        orders = []

        morning_procurement = (not self.plan_ready) and self.is_procurement_pending(obs, hour)
        reserve = Counter()
        if morning_procurement or not self.plan_ready:
            for item in ('WHEAT', 'FERTILIZER'):
                carried_n = sum(int(inv.get(item, 0)) for inv in priv['inventories'])
                reserve[item] = max(0, self.want_resources[item] - carried_n)
        else:
            # Reserve any remaining queued PICKUPs
            for q in self.queues:
                for op in q:
                    if op and op[0] == 'PICKUP' and len(op) >= 2:
                        reserve[op[1]] += int(op[2]) if len(op) >= 3 else 1

        hired = len(farm['hands'])
        target_crew = self.crew if morning_procurement else hired

        buys = []
        land_cost = None
        if morning_procurement:
            carried_wheat = sum(int(inv.get('WHEAT', 0)) for inv in priv['inventories'])
            need_wheat = max(0, self.want_resources['WHEAT'] - shed['WHEAT'] - carried_wheat)
            surv_need = max(0, fibcost(target_crew) - fibcost(hired)) + (
                own_trade(mkt, 'WHEAT', need_wheat, False)[0] if need_wheat > 0 else 0
            )
            non_fert_liq = cash + sum(
                own_trade(mkt, k, max(0, shed[k] - reserve[k]), True)[0]
                for k in PRODUCTS if k != 'FERTILIZER' and shed[k] > reserve[k]
            )
            survival_triage = non_fert_liq < surv_need
            if survival_triage and reserve['FERTILIZER'] > 0 and shed['FERTILIZER'] > 0:
                while reserve['FERTILIZER'] > 0:
                    reserve['FERTILIZER'] -= 1
                    sell_f = max(0, shed['FERTILIZER'] - reserve['FERTILIZER'])
                    if non_fert_liq + own_trade(mkt, 'FERTILIZER', sell_f, True)[0] >= surv_need:
                        break

            if need_wheat > 0:
                buys.append(('BUY_PRODUCT', 'WHEAT', need_wheat, 0))
            if not survival_triage:
                carried_fert = sum(int(inv.get('FERTILIZER', 0)) for inv in priv['inventories'])
                need_fert = max(0, self.want_resources['FERTILIZER'] - shed['FERTILIZER'] - carried_fert)
                if need_fert > 0:
                    buys.append(('BUY_PRODUCT', 'FERTILIZER', need_fert, 0))
            if self.buy_land and len(farm['unlocked_quadrants']) < self.target_quads:
                land_cost = LAND_PRICES[len(farm['unlocked_quadrants']) - 1]
            for c, n in self.want_seeds.items():
                if n > priv['seeds'].get(c, 0):
                    buys.append(('BUY_SEED', c, n - priv['seeds'].get(c, 0), CROPS[c]['seed']))
            for a, n in self.want_animals.items():
                owned_a = shed[a] + sum(int(inv.get(a, 0)) for inv in priv['inventories'])
                if n > owned_a:
                    buys.append(('BUY_ANIMAL', a, n - owned_a, ANIMALS[a]['cost']))

        def total_commitment():
            c_sum = (land_cost or 0) + max(0, fibcost(target_crew) - fibcost(hired))
            for op, k, n, px in buys:
                c_sum += own_trade(mkt, k, n, False)[0] if op == 'BUY_PRODUCT' else n * px
            return c_sum

        buy_shed_items = sum(n for op, _, n, _ in buys if op != 'BUY_SEED')
        space_deficit = buy_shed_items - max(0, 100 - sum(shed.values()))
        need_urgent = (cash < total_commitment()) or (space_deficit > 0) or (sum(shed.values()) >= 92)

        # Compute sellable excess; keep 2-day Wheat/Fertilizer buffer ONLY when cash & space are comfortable
        excess = {}
        for k in PRODUCTS:
            keep = reserve[k]
            if not need_urgent and step < 672 and k in ('WHEAT', 'FERTILIZER') and cash > total_commitment() + 450:
                keep = max(keep, self.keep_buffer[k])
            excess[k] = max(0, shed[k] - keep)

        # 1. SLOT-0 FRONT-RUN: Put crash-sensitive / contested SELLs FIRST in Slot 0-1 BEFORE HIREs!
        race_priority = ('MELON', 'WOOL', 'STRAWBERRY', 'MILK', 'CARROT', 'TOMATO', 'EGG', 'FERTILIZER', 'WHEAT')
        front_items = [
            k for k in race_priority
            if excess[k] > 0
            and (
                k in ('MELON', 'WOOL', 'STRAWBERRY', 'MILK')
                or self.tracker.unsold.get(k, 0.0) >= 1.0
                or step >= 716
            )
            and not self.should_delay_sale_for_tick(obs, k, step, need_urgent)
        ]
        req_slots = max(0, target_crew - hired) + (1 if land_cost is not None else 0) + len(buys)
        if morning_procurement:
            if hour >= 1:
                max_front_slots = 0
            elif cash >= total_commitment() and space_deficit <= 0 and req_slots <= 10:
                has_crash_race = any(
                    k in ('MELON', 'WOOL') or (k in ('STRAWBERRY', 'MILK') and own_trade(mkt, k, excess[k], True)[0] >= 350)
                    for k in front_items
                )
                max_front_slots = max(2 if has_crash_race else 0, 10 - req_slots)
            else:
                max_front_slots = 2
        else:
            max_front_slots = 4
        for k in front_items[:max_front_slots]:
            if len(orders) >= 10:
                break
            n = excess[k]
            rev, nxt_i = own_trade(mkt, k, n, True)
            orders.append(['SELL', k, n])
            excess[k] = 0
            shed[k] -= n
            space_deficit -= n
            cash += rev
            mkt['inventory'][k] = nxt_i

        # 2. Sell any remaining excess needed to fund today's commitment or clear shed space
        while (cash < total_commitment() or space_deficit > 0) and len(orders) < 10:
            opts = [k for k, n in excess.items() if n > 0]
            if not opts:
                # Dip into non-mandatory keep_buffer if cash/space is needed!
                for k in ('FERTILIZER', 'WHEAT'):
                    avail_buf = max(0, shed[k] - reserve[k]) - excess[k]
                    if avail_buf > 0:
                        excess[k] += avail_buf
                opts = [k for k, n in excess.items() if n > 0]
                if not opts:
                    break
            it = max(
                opts,
                key=lambda k: (
                    excess[k] if space_deficit > 0 else own_trade(mkt, k, excess[k], True)[0],
                    own_trade(mkt, k, excess[k], True)[0],
                ),
            )
            n = excess[it]
            rev, nxt_i = own_trade(mkt, it, n, True)
            orders.append(['SELL', it, n])
            excess[it] = 0
            shed[it] -= n
            space_deficit -= n
            cash += rev
            mkt['inventory'][it] = nxt_i

        # 3. Execute HIREs
        while hired < target_crew and len(orders) < 10:
            wage = fibcost(hired + 1) - fibcost(hired)
            if cash < wage:
                break
            orders.append(['HIRE'])
            hired += 1
            cash -= wage

        # 4. Execute BUY_LAND + BUY_PRODUCT + BUY_ANIMAL + BUY_SEED
        if land_cost is not None and len(orders) < 10 and cash >= land_cost:
            orders.append(['BUY_LAND'])
            cash -= land_cost
            land_cost = None

        for op, it, n, px in buys:
            if len(orders) >= 10:
                break
            if op != 'BUY_SEED':
                n = min(n, max(0, 100 - sum(shed.values())))
            if n <= 0:
                continue
            if op == 'BUY_PRODUCT':
                bought = 0
                spent = 0
                cur_i = int(mkt['inventory'][it])
                params = mkt.get('params')
                for _ in range(n):
                    u_px = quote_price(it, cur_i - 1, params)
                    if spent + u_px > cash:
                        break
                    spent += u_px
                    cur_i -= 1
                    bought += 1
                n = bought
                mkt['inventory'][it] = cur_i
            else:
                n = min(n, int(cash // max(1, px)))
                spent = n * px
            if n > 0:
                orders.append([op, it, n])
                cash -= spent
                if op != 'BUY_SEED':
                    shed[it] += n

        if land_cost is not None and len(orders) < 10 and cash >= land_cost:
            orders.append(['BUY_LAND'])
            cash -= land_cost

        # 5. Fill remaining slots with profitable unheld excess sales
        rem_sells = sorted(
            (k for k, n in excess.items() if n > 0 and not self.should_delay_sale_for_tick(obs, k, step, False)),
            key=lambda k: -own_trade(mkt, k, excess[k], True)[0],
        )
        for k in rem_sells:
            if len(orders) >= 10:
                break
            n = excess[k]
            if n > 0:
                orders.append(['SELL', k, n])
                excess[k] = 0

        return orders

    def preflight_and_repair(self, obs, raw_units, step, hour):
        """Simulate all worker actions in-order: prevent seed mass-blocks, shed DROP overflow, & repair idle cargo."""
        day = step // 24
        farm = clone_farm_state(obs['farms'][int(obs['player'])])
        priv = clone_private_state(obs['private'])
        units = [list(c) if c else ['PASS'] for c in raw_units]

        # 1. Idle worker cargo return & Day-29 terminal harvest rescue
        for i, cmd in enumerate(units):
            idle = (cmd == ['PASS']) and (i >= len(self.queues) or not self.queues[i])
            if not idle:
                continue
            pos = tuple(farm['farmer'] if i == 0 else farm['hands'][i - 1])
            inv = priv['inventories'][i] if i < len(priv['inventories']) else {}
            home = nearest_shed_tile(pos)
            has_products = any(n > 0 and k in PRODUCTS for k, n in inv.items())
            if day == 29:
                # Step 718 (hour 22) is the final action step!
                if has_products and hour + dist(pos, home) + 1 <= 23:
                    path = moves_between(pos, home)
                    cmd = path[0] if path else ['DROP']
                elif 19 <= hour <= 21 and hour + dist(pos, home) + 2 <= 23:
                    t = farm['tiles'][pos[1]][pos[0]]
                    if isinstance(t, dict):
                        ripe = t.get('yield_units', 0) > 0 and (
                            'animal' in t
                            or (t.get('kind') == 'PLANT' and day - t['planted_day'] >= CROPS[t['crop']]['first_yield_day'])
                        )
                        if ripe:
                            cmd = ['HARVEST']
                        elif 'animal' in t and t.get('fertilizer_available'):
                            cmd = ['COLLECT_FERTILIZER']
            else:
                # Intraday idle cargo delivery if carrying marketable goods
                cargo_val = sum(n * obs['market']['prices'].get(k, 0) for k, n in inv.items() if k in PRODUCTS)
                if has_products and cargo_val >= 60 and hour + dist(pos, home) + 1 <= 24:
                    path = moves_between(pos, home)
                    cmd = path[0] if path else ['DROP']
            units[i] = cmd

        # 2. Prevent PLANT seed-shortage mass-blocking (engine cancels ALL plants of crop C if demand > seeds[C]!)
        seed_left = Counter(priv['seeds'])
        for i, cmd in enumerate(units):
            if cmd and cmd[0] == 'PLANT' and len(cmd) >= 2:
                c = cmd[1]
                pos = tuple(farm['farmer'] if i == 0 else farm['hands'][i - 1])
                t = farm['tiles'][pos[1]][pos[0]]
                if seed_left[c] <= 0 or t is not None:
                    if i < len(self.queues):
                        self.queues[i].appendleft(cmd)
                    units[i] = ['PASS']
                else:
                    seed_left[c] -= 1

        # 3. Step-by-step unit simulation + DROP shed-capacity overflow protection
        for i, cmd in enumerate(units):
            inv = priv['inventories'][i] if i < len(priv['inventories']) else {}
            if cmd and cmd[0] == 'DROP':
                pos = tuple(farm['farmer'] if i == 0 else farm['hands'][i - 1])
                if pos not in SHED_SET:
                    units[i] = ['PASS']
                else:
                    tot_inv = sum(v for v in inv.values() if v > 0)
                    room = max(0, 100 - sum(priv['shed'].values()))
                    if tot_inv == 0:
                        units[i] = ['PASS']
                    elif tot_inv > room:
                        opts = [k for k, n in inv.items() if n > 0 and k in PRODUCTS]
                        if room > 0 and opts:
                            best_k = max(opts, key=lambda k: obs['market']['prices'].get(k, 0))
                            units[i] = ['PLACE', best_k, min(room, inv[best_k])]
                        else:
                            units[i] = ['PASS']
                        if i < len(self.queues):
                            self.queues[i].appendleft(['DROP'])
            elif cmd and cmd[0] in ('FEED', 'FERTILIZE'):
                req = 'WHEAT' if cmd[0] == 'FEED' else 'FERTILIZER'
                if inv.get(req, 0) <= 0:
                    units[i] = ['PASS']
            sim_unit_op(farm, priv, i, units[i], day)

        return units, priv['shed']

    def check_intraday_expansion(self, obs, day, hour):
        """Wave-2 intraday expansion right after major morning courier sales (NE/SW only, never interrupting Melon Blitz!)."""
        if self.intraday_expanded_day == day or not (5 <= day <= 12):
            return False
        if self.buy_land or self.is_procurement_pending(obs, hour):
            return False
        farm = obs['farms'][int(obs['player'])]
        priv = obs['private']
        quads = len(farm['unlocked_quadrants'])
        if quads >= 3:
            return False
        max_h = 8 if quads == 1 else 6
        if not (2 <= hour <= max_h):
            return False
        # Never interrupt active Melon or Day-6 Wool blitz harvesting/delivery!
        ripe_melons = sum(
            1
            for row in farm['tiles']
            for t in row
            if isinstance(t, dict) and t.get('crop') == 'MELON' and day - t['planted_day'] >= 10
        )
        if ripe_melons >= 2:
            return False
        if quads == 1:
            ripe_wool = sum(
                t.get('yield_units', 0)
                for row in farm['tiles']
                for t in row
                if isinstance(t, dict) and t.get('animal') == 'SHEEP'
            )
            if ripe_wool >= 4:
                return False
        # Wait 1 step if crash-sensitive courier goods are still in worker pockets or sitting in shed unsold
        if priv['shed'].get('MELON', 0) >= 4 or priv['shed'].get('WOOL', 0) >= 4:
            return False
        for inv in priv['inventories']:
            if any(inv.get(a, 0) > 0 for a in ANIMALS):
                return False
            if inv.get('MELON', 0) >= 4 or inv.get('WOOL', 0) >= 4:
                return False
        empty_cnt = sum(1 for row in farm['tiles'] for t in row if t is None)
        if empty_cnt > 4:
            return False
        next_land = LAND_PRICES[quads - 1]
        margin = 550.0 if quads == 1 else (1200.0 if quads == 2 else 1800.0)
        if float(farm['money']) >= next_land + margin:
            self.intraday_expanded_day = day
            return True
        return False

    def act(self, obs):
        step = int(obs['step'])
        player = int(obs['player'])
        if step == 0 or step <= self.last_step or self.player != player:
            self.reset()
        self.last_step = step
        self.player = player

        self.tracker.update(obs)

        day, hour = divmod(step, 24)
        farm = obs['farms'][player]
        n_hands = len(farm.get('hands') or ())

        if hour == 0 or day != self.day:
            self.begin_day(obs, intraday=False)
        elif self.check_intraday_expansion(obs, day, hour):
            self.begin_day(obs, intraday=True)

        if not self.plan_ready and not self.is_procurement_pending(obs, hour):
            self.plan_live_routes(obs, hour)

        if not self.plan_ready:
            raw_units = self.early_setup_unit_actions(obs, hour)
        else:
            total_u = 1 + n_hands
            while len(self.queues) < total_u:
                self.queues.append(deque())
            raw_units = [self.queues[i].popleft() if self.queues[i] else ['PASS'] for i in range(total_u)]

        final_units, post_shed = self.preflight_and_repair(obs, raw_units, step, hour)
        mkt_orders = self.build_market_orders(obs, step, hour, post_shed)

        action = sanitize_action(
            {
                'farmer': final_units[0],
                'hands': final_units[1:1 + n_hands],
                'market': mkt_orders,
            },
            n_hands,
        )
        self.tracker.remember(obs, action)
        return action


_AGENT_INSTANCE = MasterAgent()


class Agent:
    def __init__(self):
        self.impl = MasterAgent()

    def act(self, observation):
        return self.impl.act(observation)


def act(observation):
    return _AGENT_INSTANCE.act(observation)
