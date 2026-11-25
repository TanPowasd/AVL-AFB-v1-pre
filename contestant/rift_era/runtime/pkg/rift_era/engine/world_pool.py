"""02 §NPC市场与参考价: the finite world pool for physical commodities (r7/r9).

``trade/pool_buy`` and ``trade/pool_sell`` are immediate-or-cancel requests
prepared in P1 and cleared once in P5 (``Phase.MARKETS``) of the same tick.

* Price: symmetric bid/ask around ``10*w_cat[g]`` RU per unit — ``pool_buy``
  pays ``ceil(11.5*w_cat[g]*1000)`` mU RU/unit from ``reserves`` RU (real FX
  first, never implicit conversion), ``pool_sell`` receives
  ``floor(8.5*w_cat[g]*1000)`` mU RU/unit.
* Cap: each nation, each inventory good, each direction, at most 40 units per
  cycle (counted on accepted requests, released by unfilled remainders).
* Sales: paid only from the world pool's real RU balance at P5. All countries
  and goods share ONE proportional batch (``market.finite_pool_fills``).
* Purchases: only from the pool's real stock of that good at P5 start (10,000
  units each at genesis, an explicit endowment, never regenerated). Sales of
  the same P5 add stock for later ticks only. Over-subscription is cut by
  proportional floors with hash tails.
* Provenance: lots keep their origin nation and path; a purchase draws only
  origins the buyer may receive under export controls/embargoes (no
  laundering through the pool). The world→Hub hop is a non-eligible leg, so
  it earns nobody transit credit and nation ``world`` has export credit 0.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from .command_schema import fields as _schema
from .economic_ownership import income_account, record_income
from .errors import InvariantViolation, RuleViolation
from .ledger import Account, CashAccount, CashTransfer, DeliveryPayment, Transfer
from .market import finite_pool_fills
from .numeric import Q, ceil, floor, proportional, split_payment, units
from .protocol import PreparedCommand
from .provenance import Leg

POOL_LOCATION = 'world_pool'
POOL_OWNER = 'world'
POOL_BUCKET = 'available'
CARRIER = 'world_pool'


@dataclass(frozen=True)
class PoolOrder:
    id: str
    command_id: str
    owner: str
    side: str  # buy | sell
    hub: str
    good: str
    quantity_mU: int
    locked_mU: int  # RU cash for buys, goods for sells

    @property
    def cash_lock(self) -> CashAccount:
        return CashAccount(self.owner, f'world_pool_order:{self.id}', 'RU')

    @property
    def stock_lock(self) -> Account:
        return Account(self.owner, self.hub, f'locked_world_pool:{self.id}', self.good)


class WorldPool:
    OPERATIONS = frozenset({('trade', 'pool_buy'), ('trade', 'pool_sell')})

    def __init__(self, state):
        self.s = state
        rules = state.catalog.runtime['world_pool']
        self.cap_mU = units(rules['per_nation_good_cycle_cap_units'])
        self.multiplier = Q(rules['price_w_cat_multiplier'])
        self.buy_multiplier = Q(rules.get('buy_price_w_cat_multiplier', rules['price_w_cat_multiplier']))
        self.sell_multiplier = Q(rules.get('sell_price_w_cat_multiplier', rules['price_w_cat_multiplier']))
        self.orders: dict[str, PoolOrder] = {}
        self.used: dict[tuple[str, str, str], int] = {}
        self.cycle = -1
        self.last_fills: list[dict] = []
        self.last_tick = -1
        self.last_budget_mU = 0

    # ---- public quantities ----
    def price_mU(self, good: str) -> int:
        return units(self.multiplier * self.s.catalog.weights[good])

    def buy_price_mU(self, good: str) -> int:
        return ceil(self.buy_multiplier * self.s.catalog.weights[good] * 1000)

    def sell_price_mU(self, good: str) -> int:
        return floor(self.sell_multiplier * self.s.catalog.weights[good] * 1000)

    def pool_cash(self) -> CashAccount:
        return CashAccount(POOL_OWNER, 'pool', 'RU')

    def pool_stock(self, good: str) -> Account:
        return Account(POOL_OWNER, POOL_LOCATION, POOL_BUCKET, good)

    def _roll(self, cycle: int) -> None:
        if cycle != self.cycle:
            self.used = {}
            self.cycle = cycle

    # ---- P1 ----
    def prepare(self, owner: str, order: dict, ctx) -> PreparedCommand:
        state = self.s
        _schema(order, {'hub_id': str, 'good': str, 'qty_mU': int})
        side = 'buy' if order['op'] == 'pool_buy' else 'sell'
        hub, good, quantity = order['hub_id'], order['good'], order['qty_mU']
        row = state.catalog.goods.get(good)
        site = state.initial.map.hubs.get(hub)
        if row is None or row['kind'] != 'inventory' or site is None or quantity <= 0:
            raise RuleViolation('ERR_SCHEMA', 'world pool trades inventory goods at a known Hub')
        if row.get('indivisible', False) and quantity % 1000:
            raise RuleViolation('ERR_SCHEMA', 'indivisible good trades in whole units')
        if not state.authority.controls_hub(owner, hub):
            raise RuleViolation('ERR_NO_PERMISSION', 'world pool delivers only at a controlled Hub')
        self._roll(ctx.cycle)
        key = (owner, good, side)
        pending = sum(o.quantity_mU for o in self.orders.values() if (o.owner, o.good, o.side) == key)
        if self.used.get(key, 0) + pending + quantity > self.cap_mU:
            raise RuleViolation('ERR_CAPACITY_EXCEEDED', 'world pool: 40 units per nation, good and cycle')
        oid = f'{owner}/{int(order["id"])}'
        if oid in self.orders:
            raise RuleViolation('ERR_DUPLICATE_ID')
        if side == 'buy':
            price = self.buy_price_mU(good)
            locked = ceil(Q(quantity * price, 1000))
            pool_order = PoolOrder(oid, str(int(order['id'])), owner, side, hub, good, quantity, locked)
            plan = state.initial.cash.prepare_transfers((CashTransfer(
                CashAccount(owner, 'reserves', 'RU'), pool_order.cash_lock, locked),))
        else:
            pool_order = PoolOrder(oid, str(int(order['id'])), owner, side, hub, good, quantity, quantity)
            plan = state.initial.inventory.prepare_transfers((Transfer(
                Account(owner, hub, 'available', good), pool_order.stock_lock, quantity),))

        def commit():
            plan.commit()
            self.orders[oid] = pool_order

        return PreparedCommand(commit)

    # ---- P5 ----
    def _quantum(self, good: str) -> int:
        return 1000 if self.s.catalog.goods[good].get('indivisible', False) else 1

    def _permitted(self, buyer: str, good: str, nation: str) -> bool:
        if nation in (POOL_OWNER, buyer):
            return True
        return self.s.export_controls.direct_allowed(buyer, nation, good)

    def clear(self, state, ctx) -> None:
        if ctx.tick <= self.last_tick:
            raise InvariantViolation('world pool cleared twice in one tick')
        self.last_tick = ctx.tick
        self._roll(ctx.cycle)
        if not self.orders:
            if ctx.clock.is_decision(ctx.tick):
                self.last_fills = []
            return
        inventory, cash, materials = state.initial.inventory, state.initial.cash, state.initial.inventory.materials
        orders = dict(sorted(self.orders.items()))
        fills: dict[str, int] = {}
        # Sales: one combined RU batch over every nation and good.
        sells = {oid: o for oid, o in orders.items() if o.side == 'sell'}
        if sells:
            requested = {oid: floor(Q(o.quantity_mU * self.sell_price_mU(o.good), 1000)) for oid, o in sells.items()}
            budget = cash.balance(self.pool_cash()).native_mU
            self.last_budget_mU = budget
            granted = finite_pool_fills(ctx.seed, ctx.tick, max(0, budget), requested)
            for oid, o in sells.items():
                q = o.quantity_mU if granted[oid] == requested[oid] else \
                    min(o.quantity_mU, floor(Q(granted[oid] * 1000, self.sell_price_mU(o.good))))
                q -= q % self._quantum(o.good)
                fills[oid] = q
        # Purchases: per good, against the stock present before this P5 and
        # available warehouse capacity at each buyer's destination Hub.
        buys = defaultdict(dict)
        for oid, o in orders.items():
            if o.side == 'buy':
                buys[o.good][oid] = o
        draws: dict[str, dict[str, int]] = {}
        free_cap: dict[str, Q] = {}
        if buys:
            load = state.warehouse_load()
            for group in buys.values():
                for o in group.values():
                    if o.hub not in free_cap:
                        free_cap[o.hub] = max(Q(0), Q(state.initial.warehouse_mU.get(o.hub, 0)) - load.get(o.hub, Q(0)))
            for oid, o in sells.items():
                q = fills.get(oid, 0)
                if q and o.hub in free_cap:
                    free_cap[o.hub] += q * state.cargo_weights[o.good]
        for good, group in sorted(buys.items()):
            account = self.pool_stock(good)
            by_nation = defaultdict(Q)
            for origin, q in (materials.composition(account) if materials else {}).items():
                by_nation[origin.nation] += q
            remaining = {n: floor(q) for n, q in by_nation.items()}
            quantum = self._quantum(good)
            unit_w = state.cargo_weights[good]
            capped = {}
            for oid, o in group.items():
                if not state.authority.controls_hub(o.owner, o.hub):
                    capped[oid] = 0
                    continue
                allowed = sum(q for n, q in remaining.items() if self._permitted(o.owner, good, n))
                wh_cap = floor(free_cap[o.hub] / unit_w) if unit_w else o.quantity_mU
                capped[oid] = min(o.quantity_mU, allowed, max(0, wh_cap)) // quantum
            stock = sum(remaining.values()) // quantum
            allocation = proportional(stock, capped, key=(ctx.seed, 'world_pool_buy', ctx.tick, good))
            # Origin draws: buyers in hash order take from permitted origins,
            # each origin pro rata to what is left; never an embargoed origin.
            for oid in sorted(group, key=lambda k: (fingerprint_key(ctx, good, k), k)):
                o, want = group[oid], allocation[oid] * quantum
                if not state.authority.controls_hub(o.owner, o.hub):
                    fills[oid] = 0
                    draws[oid] = {}
                    continue
                wh_cap = floor(free_cap[o.hub] / unit_w) if unit_w else want
                want = min(want, max(0, wh_cap - wh_cap % quantum))
                pieces = {}
                allowed = {n: q for n, q in sorted(remaining.items()) if q and self._permitted(o.owner, good, n)}
                total = sum(allowed.values())
                want = min(want, total - total % quantum)
                if want:
                    share = proportional(want, allowed, key=(ctx.seed, 'world_pool_origin', ctx.tick, oid)) \
                        if total > want else dict(allowed)
                    for n, q in share.items():
                        if q:
                            pieces[n] = q
                            remaining[n] -= q
                filled = sum(pieces.values())
                fills[oid] = filled
                draws[oid] = pieces
                if filled:
                    free_cap[o.hub] = max(Q(0), free_cap[o.hub] - filled * unit_w)
        goods, money, receipts = [], [], []
        sale_lines = {}
        for oid, o in orders.items():
            q = fills.get(oid, 0)
            if o.side == 'sell':
                if q:
                    goods.append(Transfer(o.stock_lock, self.pool_stock(o.good), q,
                                          legs=(Leg(o.hub, POOL_LOCATION, CARRIER, 1, False),), retitle=True))
                    sale_lines[oid] = len(goods) - 1
                if o.quantity_mU - q:
                    goods.append(Transfer(o.stock_lock, Account(o.owner, o.hub, 'available', o.good), o.quantity_mU - q))
            else:
                for nation, part in sorted(draws.get(oid, {}).items()):
                    goods.append(Transfer(self.pool_stock(o.good), Account(o.owner, o.hub, 'next_available', o.good), part,
                                          legs=(Leg(POOL_LOCATION, o.hub, CARRIER, 1, False),),
                                          origin_nation=nation, retitle=True))
        plan = materials.transfers(tuple(goods)) if materials is not None else None
        titles = defaultdict(lambda: defaultdict(Q))
        if plan is not None:
            locks = {o.stock_lock: oid for oid, o in sells.items()}
            for delivery in plan.deliveries:
                oid = locks.get(delivery.source)
                if oid is not None and delivery.destination.owner == POOL_OWNER:
                    titles[oid][income_account(delivery.origin, sells[oid].owner, 'RU',
                                               government_account='reserves')] += delivery.quantity_mU
        income = defaultdict(int)
        report = []
        for oid, o in orders.items():
            q = fills.get(oid, 0)
            if o.side == 'sell':
                paid = floor(Q(q * self.sell_price_mU(o.good), 1000))
                if paid:
                    weights = titles.get(oid) or {CashAccount(o.owner, 'reserves', 'RU'): Q(q)}
                    names = {f'{a.owner}|{a.name}|{a.currency}': a for a in weights}
                    split = split_payment(paid, {k: weights[a] for k, a in names.items()},
                                          key=(ctx.seed, 'world_pool_sale', ctx.tick, oid))
                    for k, amount in sorted(split.items()):
                        if amount:
                            money.append(CashTransfer(self.pool_cash(), names[k], amount))
                            income[names[k]] += amount
            else:
                paid = ceil(Q(q * self.buy_price_mU(o.good), 1000))
                if paid > o.locked_mU:
                    raise InvariantViolation('world pool purchase exceeds its cash lock')
                if paid:
                    money.append(CashTransfer(o.cash_lock, self.pool_cash(), paid))
                if o.locked_mU - paid:
                    money.append(CashTransfer(o.cash_lock, CashAccount(o.owner, 'reserves', 'RU'), o.locked_mU - paid))
            report.append({'owner': o.owner, 'id': o.command_id, 'side': o.side, 'hub_id': o.hub, 'good': o.good,
                           'requested_mU': o.quantity_mU, 'filled_mU': q, 'paid_ru_mU': paid})
        transaction = DeliveryPayment.prepare(inventory, cash, goods, money, material_plan=plan)
        transaction.commit()
        recipients = tuple(sorted(income))
        record_income(state, recipients, tuple(income[a] for a in recipients))
        for oid, o in orders.items():
            key = (o.owner, o.good, o.side)
            if fills.get(oid, 0):
                self.used[key] = self.used.get(key, 0) + fills[oid]
        self.orders.clear()
        self.last_fills = report

    # ---- observation / persistence ----
    def visible(self, owner: str) -> dict:
        state = self.s
        stock = {g: state.initial.inventory.balance(self.pool_stock(g))
                 for g, row in sorted(state.catalog.goods.items()) if row['kind'] == 'inventory'}
        return {'price_ru_mU': {g: self.price_mU(g) for g in stock},
                'buy_price_ru_mU': {g: self.buy_price_mU(g) for g in stock},
                'sell_price_ru_mU': {g: self.sell_price_mU(g) for g in stock},
                'stock_mU': stock,
                'cash_ru_mU': state.initial.cash.balance(self.pool_cash()).native_mU,
                'cap_mU_per_good_cycle': self.cap_mU,
                'cycle': self.cycle,
                'used_mU': [[g, side, q] for (p, g, side), q in sorted(self.used.items()) if p == owner],
                'last_fills': [{k: v for k, v in r.items() if k != 'owner'} for r in self.last_fills if r['owner'] == owner]}

    def snapshot(self) -> dict:
        return {'orders': [self.orders[k] for k in sorted(self.orders)],
                'used': [[*k, q] for k, q in sorted(self.used.items())],
                'cycle': self.cycle, 'last_tick': self.last_tick, 'last_fills': self.last_fills,
                'last_budget_mU': self.last_budget_mU}

    def audit(self) -> None:
        if any(q < 0 or q > self.cap_mU for q in self.used.values()):
            raise InvariantViolation('world pool cycle cap violated')
        state = self.s
        for o in self.orders.values():
            if o.side == 'buy' and state.initial.cash.balance(o.cash_lock).native_mU != o.locked_mU:
                raise InvariantViolation('world pool cash lock differs from its order')
            if o.side == 'sell' and state.initial.inventory.balance(o.stock_lock) != o.locked_mU:
                raise InvariantViolation('world pool stock lock differs from its order')


def fingerprint_key(ctx, good, oid):
    from .determinism import digest
    return digest(ctx.seed, 'world_pool_draw_order', ctx.tick, good, oid)
