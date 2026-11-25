"""Observation-based procurement with exact post-worker inventory and quotes."""
from collections import Counter
import copy
from . import rules


def preview(obs, units, cfg):
    farm, private = copy.deepcopy((obs['farms'][obs['player']], obs['private']))
    for i, op in enumerate(units):
        rules._apply_unit_action(farm, private, i, op, cfg.get('boardSize', 10),
                                obs['step']//cfg.get('turnsPerDay', 24),
                                cfg.get('turnsPerDay', 24), cfg.get('shedCapacity', 100))
    return farm, private


def sale_cash(item, n, inventory, params):
    cash = 0
    for _ in range(n):
        quote = rules.market_price(item, inventory, params)
        cash += quote
        inventory += quote > 1
    return cash


def orders(planner, obs, hour, units):
    cfg = planner.cfg
    farm, private = preview(obs, units, cfg)
    shed = Counter(private['shed'])
    prices = obs['market']['prices']
    params = obs['market'].get('params') or rules._resolve_market_params(cfg.get('marketParams'))
    limit = cfg.get('maxMarketOrdersPerTurn', 10)
    last_step = cfg.get('episodeSteps', 720)-2
    final = obs['step'] == last_step
    reserve = Counter()
    if not final:
        if hour < planner.p['setup_hours']:
            reserve.update(planner.want_resources)
        else:
            for queue in planner.queues:
                for op in queue:
                    if op and op[0] == 'PICKUP': reserve[op[1]] += op[2]
    result = []
    # Capacity is freed before any new purchase. The current worker's PICKUP
    # has already been subtracted; it must not be reserved a second time.
    for item in sorted(rules.PRODUCTS, key=lambda k: -max(0, shed[k]-reserve[k])*prices[k]):
        n = max(0, shed[item]-reserve[item])
        if n and len(result) < limit:
            result.append(['SELL', item, n])
            shed[item] -= n
    if final or hour >= planner.p['setup_hours']:
        return result
    # Spend only observed cash. Sale proceeds become usable on the next
    # callback, avoiding promises based on an unknown simultaneous rival sale.
    cash = farm['money']
    # Labor must be funded before optional seeds / livestock.
    hired = len(farm['hands'])
    while hired < planner.crew and len(result) < limit:
        cost = rules._fib(farm['hires_today']+hired-len(farm['hands'])) * cfg.get('farmHandCostMult', 1)
        if cash < cost: break
        result.append(['HIRE']); cash -= cost; hired += 1
    room = max(0, cfg.get('shedCapacity', 100)-sum(shed.values()))
    carried = Counter()
    for inv in private['inventories']: carried.update(inv)

    def buy(op, item, count, cost=None):
        nonlocal cash, room
        if len(result) >= limit: return
        count = max(0, int(count))
        if op != 'BUY_SEED': count = min(count, room)
        total = 0; n = 0
        for i in range(count):
            quote = cost if cost is not None else rules.market_price(
                item, obs['market']['inventory'][item]-i-1, params)
            if total + quote > cash: break
            n += 1; total += quote
        if n:
            result.append([op, item, n]); cash -= total
            if op != 'BUY_SEED': room -= n
    for item in ('WHEAT', 'FERTILIZER'):
        buy('BUY_PRODUCT', item, reserve[item]-shed[item]-carried[item])
    if planner.buy_land and len(farm['unlocked_quadrants']) < planner.target_quads:
        price = rules.LAND_PRICES[len(farm['unlocked_quadrants'])-1]
        if cash >= price+100 and len(result) < limit:
            result.append(['BUY_LAND']); cash -= price
    for a, n in planner.want_animals.items():
        buy('BUY_ANIMAL', a, n-shed[a]-carried[a], rules.ANIMALS[a]['cost'])
    for crop, n in planner.want_seeds.items():
        buy('BUY_SEED', crop, n-private['seeds'].get(crop, 0), rules.CROPS[crop]['seed'])
    return result
