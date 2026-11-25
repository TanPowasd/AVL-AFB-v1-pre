"""02 AR-011 sovereign insolvency reorganisation and forced WorldClearPool auction.

Contract ``sovereign_insolvency_v1`` (IMPLEMENTATION_CONTRACT.md, needs author
confirmation).  Runs once per cycle in P7, after debt service, payables, loans
and social security and before population settlement appends the cycle row.

Trigger (02 §4): three consecutive cycle ends with
``Treasury_p + R_p/e_p < 0.01*max(mean20Tax_p, 25)`` and, over the last ten
cycles, unpaid wages plus defaulted (rolled) service above 20% of the ten-cycle
fiscal revenue.  On entry: every pledged bilateral-loan collateral of the
borrower is foreclosed to its creditor; credit -0.25 unless a sovereign default
was already charged inside the ten-cycle window.  While reorganising, non-food,
unlocked government stock in hubs the nation controls is sold each cycle to the
world clearing pool's inventory (same non-eligible pool leg as ``world_pool``) at
80% of the reference price ``10*w_cat`` RU, paid in the nation's own currency
from the pool's real balance of that currency, and the proceeds first
settle wage arrears, then stay in the treasury for overdue debt service.
"""
from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass, field

from .economic_ownership import EconomicOwner, prepare_purchase, record_income
from .errors import InvariantViolation
from .ledger import Account, CashAccount, Transfer
from .provenance import Leg
from .world_pool import POOL_LOCATION, POOL_OWNER, CARRIER
from .numeric import Q, floor

WINDOW = 10
STREAK = 3
EXCLUDED = frozenset({'food', 'grain', 'water'})        # 口粮 and the household water ration
WAGE_KINDS = frozenset({'wage', 'military_wage'})


@dataclass
class InsolvencyRow:
    streak: int = 0
    window: deque = field(default_factory=lambda: deque(maxlen=WINDOW))  # (arrears_mU, revenue_mU, defaulted)
    reorganizing: bool = False
    since_cycle: int | None = None
    events: int = 0
    auctions: list = field(default_factory=list)          # last cycle's [hub, good, qty_mU, paid_native_mU]
    sold_native_mU: int = 0
    wage_arrears_paid_mU: int = 0


class SovereignInsolvency:
    def __init__(self, state):
        self.s = state
        self.rows = {p: InsolvencyRow() for p in sorted(state.initial.known)}
        self.last_cycle = -1
        self.events = []

    # ------------------------------------------------------------------ inputs
    def liquidity(self, owner):
        cash, rate = self.s.initial.cash, self.s.rates[owner]
        treasury = cash.balance(self.s.native(owner, 'treasury')).native_mU
        reserves = cash.balance(CashAccount(owner, 'reserves', 'RU')).native_mU
        return treasury + Q(reserves) / rate

    def threshold(self, owner):
        taxes = self.s.sovereign_finance.tax_history(owner)
        return Q(1, 100) * max(Q(sum(taxes[-20:]), 20), Q(25000))

    def service_shortfall(self, owner):
        report = self.s.sovereign_finance.reports.get(owner) or {}
        bonds = max(0, report.get('due_mU', 0) - report.get('paid_mU', 0))
        loans = self.s.contracts.loans
        loan = sum(max(0, r['due_mU'] - r['paid_mU']) for r in loans.reports
                   if r['priority'] == 1 and loans.rows[r['loan_id']].proposal.counterparty == owner)
        return bonds + loan

    def wage_arrears(self, owner):
        return {k: q for k, q in self.s.payments.arrears.items()
                if k[2] in WAGE_KINDS and k[0].owner == owner and q > 0}

    # --------------------------------------------------------------- P7 cycle
    def settle(self, ctx):
        if not ctx.clock.is_cycle_end(ctx.tick):
            return
        if ctx.cycle <= self.last_cycle:
            raise InvariantViolation('insolvency review repeated')
        self.last_cycle = ctx.cycle
        state = self.s
        self.events = []
        for owner, row in self.rows.items():
            metric = state.cycles[owner]
            defaulted = ctx.cycle in state.sovereign_finance.default_cycles[owner]
            row.window.append((metric.unpaid_wages_mU + self.service_shortfall(owner), metric.taxes_mU, defaulted))
            low = self.liquidity(owner) < self.threshold(owner)
            row.streak = row.streak + 1 if low else 0
            arrears = sum(a for a, _, _ in row.window)
            revenue = sum(r for _, r, _ in row.window)
            row.auctions = []
            if not row.reorganizing:
                if row.streak >= STREAK and 5 * arrears > revenue:
                    self.enter(owner, row, ctx)
            elif not low and not self.wage_arrears(owner) and not metric.unpaid_wages_mU and not self.service_shortfall(owner):
                row.reorganizing, row.since_cycle = False, None
                self.events.append({'event': 'reorganization_ended', 'nation': owner, 'cycle': ctx.cycle})
            if row.reorganizing:
                self.auction(owner, row, ctx)

    def enter(self, owner, row, ctx):
        state = self.s
        row.reorganizing, row.since_cycle = True, ctx.cycle
        row.events += 1
        loans = state.contracts.loans
        foreclosed = []
        for lid in sorted(loans.rows):
            loan = loans.rows[lid]
            if loan.proposal.counterparty == owner and loan.face_mU and loan.collateral:
                loans.foreclose(loan)
                foreclosed.append(lid)
        already = any(d for _, _, d in row.window)
        if not already:
            state.credit[owner] = max(Q(1, 10), state.credit[owner] - Q(25, 100))
        self.events.append({'event': 'reorganization', 'nation': owner, 'cycle': ctx.cycle,
                            'foreclosed_loans': foreclosed, 'credit_penalty': not already})

    def auction(self, owner, row, ctx):
        state = self.s
        inv, cash = state.initial.inventory, state.initial.cash
        target = sum(self.wage_arrears(owner).values()) + self.service_shortfall(owner)
        pool = CashAccount('world', 'pool', owner)
        budget = cash.balance(pool).native_mU
        if target <= 0 or budget <= 0:
            return
        rate = state.rates[owner]
        weights = state.catalog.weights
        titles = (None, EconomicOwner(owner, 'treasury'))
        candidates = []
        for hub in sorted(state.initial.map.hubs):
            if not state.authority.controls_hub(owner, hub):
                continue
            for good in sorted(state.catalog.goods):
                if good in EXCLUDED or state.catalog.goods[good]['kind'] != 'inventory':
                    continue
                account = Account(owner, hub, 'available', good)
                if inv.balance(account) <= 0:
                    continue
                government = sum((q for o, q in inv.materials.composition(account).items()
                                  if o.economic_owner in titles), Q(0))
                quantum = 1000 if state.catalog.goods[good]['indivisible'] else 1
                sellable = min(inv.balance(account), floor(government)) // quantum * quantum
                if sellable > 0:
                    candidates.append((-weights[good], hub, good, account, sellable, quantum))
        candidates.sort(key=lambda c: c[:3])
        remaining = min(Q(target), Q(budget))
        transfers, prices, plan = [], {}, []
        for _, hub, good, account, sellable, quantum in candidates:
            if remaining <= 0:
                break
            price = Q(8) * weights[good] / rate                # native mU per goods mU
            if price <= 0:
                continue
            need = -(-remaining // price)                       # ceil, in goods mU
            quantity = min(sellable, -(-need // quantum) * quantum)
            spent = sum((q * prices[g] for _, g, q in plan), Q(0))
            quantity = min(quantity, floor((Q(budget) - spent) / price) // quantum * quantum)
            if quantity <= 0:
                continue
            prices[good] = price
            plan.append((hub, good, quantity))
            transfers.append(Transfer(account, Account(POOL_OWNER, POOL_LOCATION, 'available', good), quantity,
                                      legs=(Leg(hub, POOL_LOCATION, CARRIER, 1, False),), retitle=True))
            remaining -= quantity * price
        if not transfers:
            return
        stock = inv.prepare_transfers(transfers)
        payment, recipients = prepare_purchase(state.payments, pool, stock.materials.deliveries, prices,
                                               kind='insolvency_auction', key=(ctx.seed, 'insolvency_auction', ctx.cycle, owner))
        payment.commit(stock)
        record_income(state, recipients, payment.amounts)
        row.sold_native_mU += sum(payment.amounts)
        row.auctions = [[h, g, q, floor(q * prices[g])] for h, g, q in plan]
        self.pay_wage_arrears(owner, row)

    def pay_wage_arrears(self, owner, row):
        state = self.s
        treasury = state.native(owner, 'treasury')
        for key in sorted(self.wage_arrears(owner)):
            due = state.payments.arrears[key]
            if key[1].currency != treasury.currency:
                continue
            available = state.initial.cash.balance(treasury).native_mU
            amount = min(due, available)
            if amount <= 0:
                break
            state.initial.cash.transfer(treasury, key[1], amount)
            if amount == due:
                del state.payments.arrears[key]
            else:
                state.payments.arrears[key] = due - amount
            row.wage_arrears_paid_mU += amount

    # ------------------------------------------------------------ boundaries
    def visible(self, owner):
        row = self.rows[owner]
        return {'reorganizing': row.reorganizing, 'since_cycle': row.since_cycle, 'low_liquidity_cycles': row.streak,
                'reorganizations': row.events, 'last_auction': [list(a) for a in row.auctions],
                'auction_proceeds_native_mU': row.sold_native_mU, 'wage_arrears_repaid_mU': row.wage_arrears_paid_mU}

    def audit(self):
        if set(self.rows) != set(self.s.initial.known):
            raise InvariantViolation('insolvency rows differ from nations')
        for row in self.rows.values():
            if row.streak < 0 or row.sold_native_mU < 0 or row.wage_arrears_paid_mU < 0:
                raise InvariantViolation('negative insolvency counter')
            if row.reorganizing != (row.since_cycle is not None) or len(row.window) > WINDOW:
                raise InvariantViolation('invalid insolvency state')

    def snapshot(self):
        return {'rows': {p: {'streak': r.streak, 'window': list(r.window), 'reorganizing': r.reorganizing,
                             'since_cycle': r.since_cycle, 'events': r.events, 'auctions': r.auctions,
                             'sold_native_mU': r.sold_native_mU, 'wage_arrears_paid_mU': r.wage_arrears_paid_mU}
                         for p, r in self.rows.items()},
                'last_cycle': self.last_cycle, 'events': self.events}
