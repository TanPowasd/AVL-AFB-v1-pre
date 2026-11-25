"""F03 forwards / currency shorts and C30 secondary offering (forward_finance)."""
import unittest
from types import SimpleNamespace as NS

from organizer.rift_era.engine.assets import Asset
from organizer.rift_era.engine.catalog import Catalog
from organizer.rift_era.engine.clock import ClockConfig
from organizer.rift_era.engine.economy import opening_economy
from organizer.rift_era.engine.errors import RuleViolation
from organizer.rift_era.engine.forward_finance import ForwardFinanceCommands
from organizer.rift_era.engine.initialization import Facility, initialize
from organizer.rift_era.engine.ledger import Account, CashAccount
from organizer.rift_era.engine.materials import Origin
from organizer.rift_era.engine.national_power import action_sources
from organizer.rift_era.engine.world import generate_candidate


def ctx(tick):
    return NS(seed=1000, tick=tick, cycle=tick // 10, window=tick // 5, clock=ClockConfig())


class ForwardFinanceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.catalog, cls.world = Catalog(), generate_candidate(1000)

    def fixture(self, ru=50_000_000):
        game = opening_economy(self.catalog, initialize(self.catalog, self.world), total_ticks=30,
                               debug=False, commerce=True)
        state = game.state
        cash = state.initial.cash
        for p in ('n0', 'n1'):
            state.research[p].known.add('F03')
            # Explicit test genesis of RU reserves (nations open with none).
            cash.transfer(CashAccount('world', 'pool', 'RU'), CashAccount(p, 'reserves', 'RU'), ru)
        stock = {Account('n0', 'n0-h0', 'available', 'steel'): 10000}
        state.initial.inventory.transform({}, stock, reason='test_forward_stock',
                                          origins={a: Origin(a.owner, a.location) for a in stock})
        return game

    def forward(self, seq, counterparty, cycle=10):
        return {'id': str(seq), 'kind': 'logistics', 'op': 'forward_contract', 'counterparty': counterparty,
                'good': 'steel', 'qty_mU': 4000, 'strike_price_ru_mU': 25000, 'delivery_cycle': cycle,
                'delivery_hub': 'n0-h0'}

    def test_registered_and_tech_locked(self):
        game = self.fixture()
        state = game.state
        handler = ForwardFinanceCommands(state)
        state.research['n1'].known.discard('F03')
        with self.assertRaises(RuleViolation) as err:
            handler.prepare('n1', {**self.forward(1, 'n0'), 'op': 'forward_contract'}, ctx(0))
        self.assertEqual(err.exception.code, 'ERR_TECH_LOCKED')
        with self.assertRaises(RuleViolation) as err:
            handler.prepare('n0', self.forward(2, 'n1', cycle=7), ctx(0))
        self.assertEqual(err.exception.code, 'ERR_SCHEMA')
        self.assertIn('strikes', [r[0] for r in action_sources(state)])

    def test_forward_signs_locks_margins_and_delivers(self):
        game = self.fixture()
        state = game.state
        cash, inv = state.initial.cash, state.initial.inventory
        ff = state.forward_finance
        handler = ForwardFinanceCommands(state)
        handler.prepare('n0', self.forward(1, 'n1'), ctx(0)).commit()
        handler.prepare('n1', self.forward(1, 'n0'), ctx(0)).commit()
        row, = ff.forwards.values()
        self.assertEqual(row.status, 'SIGNED')
        self.assertEqual(row.notional_ru_mU, 100000)
        self.assertEqual(cash.balance(row.margin('n0')).native_mU, 20000)
        self.assertEqual(cash.balance(row.margin('n1')).native_mU, 20000)
        state.audit()
        before_n0 = cash.balance(ff.reserves('n0')).native_mU
        before_n1 = cash.balance(ff.reserves('n1')).native_mU
        ff.settle_forwards(ctx(100))
        self.assertFalse(ff.forwards)
        self.assertEqual(inv.balance(Account('n1', 'n0-h0', 'available', 'steel')), 4000)  # buyer-owned at the delivery hub
        self.assertEqual(cash.balance(ff.reserves('n0')).native_mU - before_n0, 20000 + 100000)
        self.assertEqual(before_n1 - cash.balance(ff.reserves('n1')).native_mU, 100000 - 20000)
        self.assertEqual(ff.reports['n1'][-1]['status'], 'DELIVERED')
        state.audit()

    def test_seller_default_forfeits_margin_and_credit(self):
        game = self.fixture()
        state = game.state
        cash, ff = state.initial.cash, state.forward_finance
        handler = ForwardFinanceCommands(state)
        handler.prepare('n0', self.forward(1, 'n1'), ctx(0)).commit()
        handler.prepare('n1', self.forward(1, 'n0'), ctx(0)).commit()
        source = Account('n0', 'n0-h0', 'available', 'steel')
        state.initial.inventory.transform({source: state.initial.inventory.balance(source)}, {},
                                          reason='test_remove_stock')
        credit = state.credit['n0']
        before = cash.balance(ff.reserves('n1')).native_mU
        ff.settle_forwards(ctx(100))
        self.assertEqual(cash.balance(ff.reserves('n1')).native_mU - before, 40000)
        self.assertLess(state.credit['n0'], credit)
        self.assertEqual(ff.reports['n0'][-1]['status'], 'DEFAULTED_SELLER')
        state.audit()

    def test_fx_short_borrows_real_pool_and_books_debt_then_covers(self):
        game = self.fixture()
        state = game.state
        cash, ff = state.initial.cash, state.forward_finance
        pool = cash.balance(CashAccount('world', 'pool', 'n1')).native_mU
        game.step({'n0': [{'id': '1', 'kind': 'finance', 'op': 'fx_short', 'currency': 'n1', 'amount_mU': 100000}]})
        pos = ff.shorts[('n0', 'n1')]
        self.assertEqual(pos.principal_mU, 100000)
        self.assertGreater(ff.debt_book('n0'), 0)
        self.assertGreater(cash.balance(ff.collateral('n0', 'n1')).native_mU, 0)
        self.assertEqual(ff.visible('n0')['fx_shorts'][0]['principal_mU'], 100000)
        # Over the 25% opening-pool cap.
        with self.assertRaises(RuleViolation) as err:
            ForwardFinanceCommands(state).prepare('n0', {'id': '9', 'kind': 'finance', 'op': 'fx_short',
                                                         'currency': 'n1', 'amount_mU': pool}, ctx(5))
        self.assertEqual(err.exception.code, 'ERR_CAPACITY_EXCEEDED')
        state.audit()
        while game.tick % 5:
            game.step()
        game.step({'n0': [{'id': '2', 'kind': 'finance', 'op': 'fx_cover', 'currency': 'n1', 'amount_mU': 200000}]})
        self.assertNotIn(('n0', 'n1'), ff.shorts)
        self.assertEqual(ff.debt_book('n0'), 0)
        self.assertEqual(cash.balance(ff.collateral('n0', 'n1')).native_mU, 0)
        self.assertEqual(cash.balance(CashAccount('world', 'pool', 'n1')).native_mU >= pool - 0, True)
        state.audit()

    def test_own_currency_short_rejected(self):
        state = self.fixture().state
        with self.assertRaises(RuleViolation) as err:
            ForwardFinanceCommands(state).prepare('n0', {'id': '1', 'kind': 'finance', 'op': 'fx_short',
                                                         'currency': 'n0', 'amount_mU': 1000}, ctx(0))
        self.assertEqual(err.exception.code, 'ERR_NO_PERMISSION')


    def test_secondary_offering_rests_into_capex_escrow_for_same_sector_only(self):
        game = opening_economy(self.catalog, initialize(self.catalog, self.world), total_ticks=30,
                               debug=False, commerce=True)
        state = game.state
        state.research['n0'].known.add('F01')
        handler = ForwardFinanceCommands(state)
        for fid in ('test-lab', 'test-farm'):
            state.initial.assets[fid] = Asset(fid, 'n0', 'n0-h0', {})
        state.initial.facilities['test-lab'] = Facility('test-lab', 'n0', 'n0-h0', 'S2', '电子厂', ())
        state.initial.facilities['test-farm'] = Facility('test-farm', 'n0', 'n0-h0', 'S2', '农场', ())
        order = {'id': '7', 'kind': 'finance', 'op': 'secondary_offering', 'company': 'n0-tech', 'qty': 1000,
                 'min_price_mU': 100, 'target_facilities': ['test-lab']}
        with self.assertRaises(RuleViolation) as err:
            handler.prepare('n0', order, ctx(0))
        self.assertEqual(err.exception.code, 'ERR_NO_PERMISSION')  # no IPO yet
        game.step({'n0': [{'id': '1', 'kind': 'finance', 'op': 'ipo', 'company': 'n0-tech', 'qty': 1000,
                           'min_price_mU': 100}]})
        for _ in range(4):
            game.step()
        with self.assertRaises(RuleViolation) as err:
            handler.prepare('n0', {**order, 'target_facilities': ['test-farm']}, ctx(5))
        self.assertEqual(err.exception.code, 'ERR_SITE_INCOMPATIBLE')
        game.step({'n0': [order]})
        ff = state.forward_finance
        self.assertEqual(ff.escrows['n0-tech'], frozenset({'test-lab'}))
        self.assertIn('stock:n0:7', state.stock_market.issue_orders)
        job = NS(target='test-lab', kind='upgrade_facility', owner='n0')
        self.assertEqual(ff.capex_payer(job), CashAccount('n0', 'capex:n0-tech', 'n0'))
        self.assertIsNone(ff.capex_payer(NS(target='test-farm', kind='upgrade_facility', owner='n0')))
        state.audit()


if __name__ == '__main__':
    unittest.main()
