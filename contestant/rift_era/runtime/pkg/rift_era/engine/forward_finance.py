"""F03 forward contracts, F03 currency shorts and C30 secondary offerings.

Every leg is a real ledger movement: margins sit in escrow cash accounts
(already counted by NFA as cash), short borrowing moves actual world-pool
currency and records a historical-RU debt lot, and offering proceeds land in a
restricted CapexEscrow spendable only by listed same-sector construction.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .command_schema import fields
from .errors import InvariantViolation, RuleViolation
from .ledger import Account, CashAccount, CashTransfer, DeliveryPayment, Transfer
from .numeric import Q, ceil, floor
from .payments import FXRequest
from .protocol import PreparedCommand


def _tech(state, owner, tid):
    if tid not in state.research[owner].known:
        raise RuleViolation('ERR_TECH_LOCKED')


@dataclass
class ForwardContract:
    id: str
    seller: str
    buyer: str
    good: str
    quantity_mU: int
    strike_ru_mU: int
    delivery_cycle: int
    hub: str
    signed_cycle: int
    status: str = 'PROPOSED'
    proposal_command: str = ''

    @property
    def notional_ru_mU(self) -> int:
        return ceil(Q(self.quantity_mU, 1000) * self.strike_ru_mU)

    @property
    def margin_ru_mU(self) -> int:
        return ceil(Q(self.notional_ru_mU, 5))

    def margin(self, owner):
        return CashAccount(owner, 'forward_margin:' + self.id, 'RU')


@dataclass
class ShortPosition:
    owner: str
    currency: str
    principal_mU: int = 0
    interest_mU: Q = Q(0)
    debt_lots: dict = field(default_factory=dict)  # rate -> [native_mU, book_ru_mU]

    @property
    def owed_mU(self):
        return self.principal_mU + ceil(self.interest_mU)


class ForwardFinance:
    def __init__(self, state):
        self.s = state
        self.forwards: dict[str, ForwardContract] = {}
        self.shorts: dict[tuple[str, str], ShortPosition] = {}
        self.pool_opening: dict[str, int] = {}
        self.pending_shorts: list = []
        self.pending_covers: list = []
        self.escrows: dict[str, frozenset] = {}
        self.offered: dict[str, list] = {}
        self.last_cycle = -1
        self.last_tick = -1
        self.reports = {p: [] for p in state.initial.known}

    # ---- helpers ----
    @staticmethod
    def reserves(owner):
        return CashAccount(owner, 'reserves', 'RU')

    @staticmethod
    def short_cash(owner, currency):
        return CashAccount(owner, 'fx_short_cash:' + currency, currency)

    @staticmethod
    def collateral(owner, currency):
        return CashAccount(owner, 'fx_short_collateral:' + currency, 'RU')

    @staticmethod
    def escrow(cid, issuer):
        return CashAccount(issuer, 'capex:' + cid, issuer)

    def _report(self, owner, row):
        self.reports[owner] = (self.reports[owner] + [row])[-64:]

    # ---- P1 cycle start: pool limit snapshot, short interest ----
    def begin_tick(self, ctx):
        if ctx.tick % ctx.clock.cycle_ticks or ctx.cycle <= self.last_cycle:
            return
        self.last_cycle = ctx.cycle
        cash = self.s.initial.cash
        self.pool_opening = {q: cash.balance(CashAccount('world', 'pool', q)).native_mU for q in sorted(self.s.initial.known)}
        for key, pos in sorted(self.shorts.items()):
            if not pos.owed_mU:
                continue
            rate_pct = max(Q(0), Q(self.s.interest_bp[pos.currency], 100) + 2)
            added = pos.owed_mU * rate_pct / 10000
            pos.interest_mU += added
            fx = self.s.rates[pos.currency]
            lot = pos.debt_lots.setdefault(fx, [Q(0), Q(0)])
            lot[0] += added
            lot[1] += added * fx

    # ---- forward contracts ----
    def forward_prepare(self, owner, order, ctx):
        fields(order, {'counterparty': str, 'good': str, 'qty_mU': int, 'strike_price_ru_mU': int,
                       'delivery_cycle': int, 'delivery_hub': str})
        state = self.s
        other, good, hub = order['counterparty'], order['good'], order['delivery_hub']
        if other == owner or other not in state.initial.known:
            raise RuleViolation('ERR_NO_VISIBLE_TARGET')
        if good not in state.catalog.goods or state.catalog.goods[good]['kind'] != 'inventory' \
                or order['qty_mU'] <= 0 or order['strike_price_ru_mU'] <= 0 \
                or state.catalog.goods[good]['indivisible'] and order['qty_mU'] % 1000:
            raise RuleViolation('ERR_SCHEMA')
        if order['delivery_cycle'] - ctx.cycle not in (10, 20, 50) or hub not in state.initial.map.hubs:
            raise RuleViolation('ERR_SCHEMA')
        _tech(state, owner, 'F03')
        terms = (good, order['qty_mU'], order['strike_price_ru_mU'], order['delivery_cycle'], hub)
        match = next((f for f in sorted(self.forwards.values(), key=lambda f: f.id)
                      if f.status == 'PROPOSED' and f.seller == other and f.buyer == owner
                      and (f.good, f.quantity_mU, f.strike_ru_mU, f.delivery_cycle, f.hub) == terms), None)
        if match is None:
            # Proposer is the seller (delivers goods); the buyer accepts by a
            # mirror command. Margin locks only when both have signed.
            fid = f'forward:{owner}:{int(order["id"])}'
            row = ForwardContract(fid, owner, other, good, order['qty_mU'], order['strike_price_ru_mU'],
                                  order['delivery_cycle'], hub, ctx.cycle, proposal_command=str(int(order['id'])))
            if not state.export_controls.direct_allowed(other, owner, good):
                raise RuleViolation('ERR_NO_PERMISSION')
            return PreparedCommand(lambda: self.forwards.__setitem__(fid, row))
        _tech(state, other, 'F03')
        if not state.export_controls.direct_allowed(owner, other, good):
            raise RuleViolation('ERR_NO_PERMISSION')
        margin = match.margin_ru_mU
        plan = state.initial.cash.prepare_transfers((CashTransfer(self.reserves(match.seller), match.margin(match.seller), margin),
                                                     CashTransfer(self.reserves(match.buyer), match.margin(match.buyer), margin)))

        def commit():
            plan.commit()
            match.status = 'SIGNED'
            match.signed_cycle = ctx.cycle
        return PreparedCommand(commit)

    def settle_forwards(self, ctx):
        """P5: deliver every contract due this cycle at its first tick."""
        if ctx.tick % ctx.clock.cycle_ticks:
            return
        state = self.s
        cash, inv = state.initial.cash, state.initial.inventory
        for fid in sorted(self.forwards):
            row = self.forwards[fid]
            if row.status == 'PROPOSED' and ctx.cycle >= row.signed_cycle + 10:
                del self.forwards[fid]
                continue
            if row.status != 'SIGNED' or ctx.cycle != row.delivery_cycle:
                continue
            m_s, m_b = row.margin(row.seller), row.margin(row.buyer)
            refund = [CashTransfer(m_s, self.reserves(row.seller), cash.balance(m_s).native_mU),
                      CashTransfer(m_b, self.reserves(row.buyer), cash.balance(m_b).native_mU)]
            if not state.export_controls.direct_allowed(row.buyer, row.seller, row.good):
                cash.transfer_many(refund)
                row.status = 'VOID_BLOCKED'
                self._report(row.seller, {'id': fid, 'status': row.status})
                self._report(row.buyer, {'id': fid, 'status': row.status})
                continue
            source = Account(row.seller, row.hub, 'available', row.good)
            delivered = inv.balance(source) >= row.quantity_mU
            payable = cash.balance(self.reserves(row.buyer)).native_mU + cash.balance(m_b).native_mU >= row.notional_ru_mU
            if delivered and payable:
                from_margin = min(cash.balance(m_b).native_mU, row.notional_ru_mU)
                money = [CashTransfer(m_b, self.reserves(row.seller), from_margin),
                         CashTransfer(self.reserves(row.buyer), self.reserves(row.seller), row.notional_ru_mU - from_margin),
                         CashTransfer(m_b, self.reserves(row.buyer), cash.balance(m_b).native_mU - from_margin),
                         CashTransfer(m_s, self.reserves(row.seller), cash.balance(m_s).native_mU)]
                DeliveryPayment.prepare(inv, cash, (Transfer(source, Account(row.buyer, row.hub, 'available', row.good),
                                                             row.quantity_mU),), money).commit()
                row.status = 'DELIVERED'
            else:
                defaulter = row.seller if not delivered else row.buyer
                victim = row.buyer if defaulter == row.seller else row.seller
                seized = row.margin(defaulter)
                cash.transfer_many((CashTransfer(seized, self.reserves(victim), cash.balance(seized).native_mU),
                                    CashTransfer(row.margin(victim), self.reserves(victim), cash.balance(row.margin(victim)).native_mU)))
                state.credit[defaulter] = max(Q(1, 10), state.credit[defaulter] - Q(15, 100))
                row.status = 'DEFAULTED_' + ('SELLER' if defaulter == row.seller else 'BUYER')
            for p in (row.seller, row.buyer):
                self._report(p, {'id': fid, 'status': row.status})
        self.forwards = {k: v for k, v in self.forwards.items()
                         if v.status in ('PROPOSED', 'SIGNED')}

    def terminate_forward(self, owner, row, ctx):
        """Unilateral termination forfeits the terminator's margin."""
        cash = self.s.initial.cash
        victim = row.buyer if owner == row.seller else row.seller
        if row.status == 'PROPOSED':
            return PreparedCommand(lambda: self.forwards.pop(row.id)), False
        plan = cash.prepare_transfers((CashTransfer(row.margin(owner), self.reserves(victim), cash.balance(row.margin(owner)).native_mU),
                                       CashTransfer(row.margin(victim), self.reserves(victim), cash.balance(row.margin(victim)).native_mU)))

        def commit():
            plan.commit()
            self.forwards.pop(row.id)
        return PreparedCommand(commit), True

    # ---- currency short / cover ----
    def fx_short_prepare(self, owner, order, ctx):
        fields(order, {'currency': str, 'amount_mU': int})
        state = self.s
        q, amount = order['currency'], order['amount_mU']
        if q not in state.initial.known or amount <= 0:
            raise RuleViolation('ERR_SCHEMA')
        if q == owner:
            raise RuleViolation('ERR_NO_PERMISSION', 'a sovereign does not short its own currency from the world pool')
        _tech(state, owner, 'F03')
        pool = CashAccount('world', 'pool', q)
        opening = self.pool_opening.get(q, state.initial.cash.balance(pool).native_mU)
        outstanding = sum(p.principal_mU for (o, c), p in self.shorts.items() if c == q)
        pending = sum(a for o, c, a, _ in self.pending_shorts if c == q)
        if outstanding + pending + amount > opening // 4:
            raise RuleViolation('ERR_CAPACITY_EXCEEDED')
        fx = state.rates[q]
        collateral = ceil(Q(amount) * fx / 2)
        lock = CashAccount(owner, 'fx_short_lock:' + q, q)
        plan = state.initial.cash.prepare_transfers((CashTransfer(self.reserves(owner), self.collateral(owner, q), collateral),
                                                     CashTransfer(pool, lock, amount)))
        rid = f'fx_short:{owner}:{int(order["id"])}'

        def commit():
            before = state.initial.cash.balance(lock).book_ru_mU
            plan.commit()
            book = state.initial.cash.balance(lock).book_ru_mU - before
            pos = self.shorts.setdefault((owner, q), ShortPosition(owner, q))
            pos.principal_mU += amount
            lot = pos.debt_lots.setdefault(fx, [Q(0), Q(0)])
            lot[0] += amount
            lot[1] += book
            self.pending_shorts.append((owner, q, amount, rid))
            # The actual second leg: sell the borrowed q for RU this P1.
            state.fx_commands.append((FXRequest(rid, lock, self.reserves(owner), amount, fx), self.short_cash(owner, q)))
        return PreparedCommand(commit)

    def fx_cover_prepare(self, owner, order, ctx):
        fields(order, {'currency': str, 'amount_mU': int})
        state = self.s
        q, amount = order['currency'], order['amount_mU']
        pos = self.shorts.get((owner, q))
        if q not in state.initial.known or amount <= 0:
            raise RuleViolation('ERR_SCHEMA')
        if pos is None or not pos.owed_mU:
            raise RuleViolation('ERR_NO_VISIBLE_TARGET')
        _tech(state, owner, 'F03')
        already_queued = sum(a for o, c, a in self.pending_covers if o == owner and c == q)
        remaining = pos.owed_mU - already_queued
        if remaining <= 0:
            raise RuleViolation('ERR_STATE', 'short position already queued for full cover this window')
        amount = min(amount, remaining)
        have = max(0, state.initial.cash.balance(self.short_cash(owner, q)).native_mU - already_queued)
        missing = max(0, amount - have)
        rid = f'fx_cover:{owner}:{int(order["id"])}'
        fx = state.rates[q]
        lock = CashAccount(owner, 'fx_cover_lock:' + q, 'RU')
        offered = ceil(Q(missing) * fx * (1 + state.fx.fee) * Q(21, 20)) if missing else 0
        plan = state.initial.cash.prepare_transfers((CashTransfer(self.reserves(owner), lock, offered),)) if offered else None

        def commit():
            if plan is not None:
                plan.commit()
                state.fx_commands.append((FXRequest(rid, lock, self.short_cash(owner, q), offered, 1 / fx),
                                          self.reserves(owner)))
            self.pending_covers.append((owner, q, amount))
        return PreparedCommand(commit)

    def after_commands(self, ctx):
        """P1 after the FX batch: repay covers with actually held q."""
        state = self.s
        cash = state.initial.cash
        for owner, q, amount in self.pending_covers:
            pos = self.shorts.get((owner, q))
            if pos is None or not pos.owed_mU:
                continue
            available = cash.balance(self.short_cash(owner, q)).native_mU
            paid = min(amount, available, pos.owed_mU)
            if not paid:
                continue
            owed_before = pos.owed_mU
            interest_part = min(paid, ceil(pos.interest_mU))
            principal_part = paid - interest_part
            cash.transfer(self.short_cash(owner, q), CashAccount('world', 'pool', q), paid)
            ratio = Q(paid, owed_before)
            for lot in pos.debt_lots.values():
                lot[0] -= lot[0] * ratio
                lot[1] -= lot[1] * ratio
            pos.interest_mU = max(Q(0), pos.interest_mU - interest_part)
            released_share = Q(principal_part, pos.principal_mU) if pos.principal_mU else Q(1)
            pos.principal_mU -= principal_part
            coll = self.collateral(owner, q)
            release = floor(cash.balance(coll).native_mU * (released_share if pos.owed_mU else 1))
            cash.transfer(coll, self.reserves(owner), release)
            if not pos.owed_mU:
                del self.shorts[(owner, q)]
            self._report(owner, {'op': 'fx_cover', 'currency': q, 'paid_mU': paid})
        self.pending_covers.clear()
        self.pending_shorts.clear()

    def debt_book(self, owner):
        return sum((lot[1] for (o, _), pos in self.shorts.items() if o == owner for lot in pos.debt_lots.values()), Q(0))

    def long_book(self, owner):
        return Q(0)

    # ---- C30 secondary offering ----
    def offering_prepare(self, owner, order, ctx):
        fields(order, {'company': str, 'qty': int, 'min_price_mU': int, 'target_facilities': list})
        state = self.s
        market = state.stock_market
        cid = order['company']
        company = state.initial.companies.get(cid)
        if company is None:
            raise RuleViolation('ERR_NO_VISIBLE_TARGET')
        if company.issuer != owner:
            raise RuleViolation('ERR_NO_PERMISSION')
        _tech(state, owner, 'F01')
        if not company.listed:
            raise RuleViolation('ERR_NO_PERMISSION', 'secondary offering requires a completed IPO')
        qty, targets = order['qty'], order['target_facilities']
        if qty <= 0 or order['min_price_mU'] <= 0 or not targets or len(targets) > 32 \
                or any(type(t) is not str for t in targets) or len(set(targets)) != len(targets):
            raise RuleViolation('ERR_SCHEMA')
        from .economy_state import SECTOR
        for fid in targets:
            facility = state.initial.facilities.get(fid)
            if facility is None or facility.owner != owner:
                raise RuleViolation('ERR_NO_VISIBLE_TARGET')
            if SECTOR.get(facility.kind) != company.sector:
                raise RuleViolation('ERR_SITE_INCOMPATIBLE', 'capex escrow only funds same-sector facilities')
        recent = sum(n for c, n in self.offered.get(cid, ()) if c > ctx.cycle - 100)
        if recent + qty > 100_000:
            raise RuleViolation('ERR_CAPACITY_EXCEEDED')
        if market.shares.balance(market.account(owner, cid, 'unissued')) < qty * 1000:
            raise RuleViolation('ERR_CAPACITY_EXCEEDED', 'external holdings would exceed 490,000 shares')
        if state.active_order_count(owner) >= state.catalog.runtime['limits']['active_orders_per_nation']:
            raise RuleViolation('ERR_ENTITY_CAP_EXCEEDED')
        command_id = str(int(order['id']))
        resting = market._order(owner, cid, 'sell', qty, order['min_price_mU'], command_id, ctx.tick,
                                ctx.tick + 10 * ctx.clock.cycle_ticks, source='unissued',
                                proceeds=self.escrow(cid, owner))
        prepared = market.exchange.prepare_issue(resting, tick=ctx.tick)

        def commit():
            prepared.commit()
            market.issue_orders[resting.id] = 'secondary'
            self.offered.setdefault(cid, []).append((ctx.cycle, qty))
            self.escrows[cid] = frozenset(self.escrows.get(cid, frozenset()) | set(targets))
        return PreparedCommand(commit)

    def capex_payer(self, job):
        """Construction payer for a listed target facility with escrow funds."""
        for cid, targets in sorted(self.escrows.items()):
            if job.target in targets and job.kind in ('upgrade_facility', 'repair_asset', 'set_line'):
                company = self.s.initial.companies[cid]
                if company.issuer == job.owner:
                    return self.escrow(cid, company.issuer)
        return None

    def fund_capex(self, ctx):
        """Treasury may only top up (never draw from) an escrow funding its job."""
        state = self.s
        cash = state.initial.cash
        for job in sorted(state.work.jobs.values(), key=lambda j: j.id):
            capex = job.payload.get('capex')
            if not capex:
                continue
            escrow = CashAccount(*capex)
            required, money = state.work._required(job)
            money += state.work.material_budget(job, required)
            short = max(0, money - cash.balance(state.work.cash_lock(job)).native_mU - cash.balance(escrow).native_mU)
            treasury = state.native(job.owner, 'treasury')
            top = min(short, cash.balance(treasury).native_mU)
            if top:
                cash.transfer(treasury, escrow, top)

    # ---- views / audit ----
    def visible(self, owner):
        return {'forwards': [{'id': f.id, 'seller': f.seller, 'buyer': f.buyer, 'good': f.good, 'qty_mU': f.quantity_mU,
                              'strike_price_ru_mU': f.strike_ru_mU, 'delivery_cycle': f.delivery_cycle, 'delivery_hub': f.hub,
                              'status': f.status} for f in self.forwards.values() if owner in (f.seller, f.buyer)],
                'fx_shorts': [{'currency': c, 'principal_mU': p.principal_mU, 'interest_mU': ceil(p.interest_mU)}
                              for (o, c), p in sorted(self.shorts.items()) if o == owner],
                'capex_escrows': [{'company': cid, 'target_facilities': sorted(t),
                                   'balance_mU': self.s.initial.cash.balance(self.escrow(cid, owner)).native_mU}
                                  for cid, t in sorted(self.escrows.items()) if self.s.initial.companies[cid].issuer == owner],
                'reports': list(self.reports[owner])}

    def audit(self):
        cash = self.s.initial.cash
        for row in self.forwards.values():
            if row.status == 'SIGNED':
                for p in (row.seller, row.buyer):
                    if cash.balance(row.margin(p)).native_mU != row.margin_ru_mU:
                        raise InvariantViolation('forward margin differs from 20% of notional')
        for pos in self.shorts.values():
            if pos.principal_mU < 0 or pos.interest_mU < 0:
                raise InvariantViolation('negative currency short')
        if self.pending_covers or self.pending_shorts:
            raise InvariantViolation('currency short batch left unsettled')

    def snapshot(self):
        return {'forwards': self.forwards, 'shorts': [[*k, v.principal_mU, v.interest_mU,
                                                        [[r, *lot] for r, lot in sorted(v.debt_lots.items())]]
                                                       for k, v in sorted(self.shorts.items())],
                'pool_opening': self.pool_opening, 'escrows': self.escrows, 'offered': self.offered,
                'last_cycle': self.last_cycle, 'reports': self.reports}


class ForwardFinanceCommands:
    OPERATIONS = frozenset({('logistics', 'forward_contract'), ('finance', 'fx_short'), ('finance', 'fx_cover'),
                            ('finance', 'secondary_offering')})

    def __init__(self, state):
        self.state = state

    def prepare(self, owner, order, ctx):
        book = self.state.forward_finance
        op = order['op']
        if op == 'forward_contract':
            return book.forward_prepare(owner, order, ctx)
        if op == 'fx_short':
            return book.fx_short_prepare(owner, order, ctx)
        if op == 'fx_cover':
            return book.fx_cover_prepare(owner, order, ctx)
        return book.offering_prepare(owner, order, ctx)
