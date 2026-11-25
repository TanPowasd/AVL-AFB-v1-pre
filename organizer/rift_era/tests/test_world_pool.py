"""02 §NPC市场与参考价: world-pool commodity trades (r7, decision 0014)."""
import unittest

from organizer.rift_era.engine.catalog import Catalog
from organizer.rift_era.engine.economy import opening_economy
from organizer.rift_era.engine.export_controls import ExportPolicy
from organizer.rift_era.engine.initialization import initialize
from organizer.rift_era.engine.ledger import Account, CashAccount, Transfer
from organizer.rift_era.engine.provenance import Leg
from organizer.rift_era.engine.world import generate_candidate

POOL_RU = CashAccount('world', 'pool', 'RU')


def pool_stock(good):
    return Account('world', 'world_pool', 'available', good)


class WorldPoolTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.catalog = Catalog()
        cls.world = generate_candidate(1000)

    def game(self):
        return opening_economy(self.catalog, initialize(self.catalog, self.world), total_ticks=40, debug=False, commerce=True)

    def fund(self, game, owner, amount):
        account = CashAccount(owner, 'reserves', 'RU')
        game.state.initial.cash.transfer(POOL_RU, account, amount)
        return account

    def order(self, cid, op, good, qty, hub):
        return {'id': str(cid), 'kind': 'trade', 'op': op, 'hub_id': hub, 'good': good, 'qty_mU': qty}

    def status(self, game, owner):
        return game.state.command_streams[owner].recent[-1]

    def stock(self, game, owner, hub, good):
        inv = game.state.initial.inventory
        return sum(inv.balance(Account(owner, hub, b, good)) for b in ('available', 'next_available'))

    def test_buy_pays_fixed_price_and_delivers_at_own_hub(self):
        game = self.game()
        state = game.state
        mid = state.world_pool.price_mU('medicine')
        buy_price = state.world_pool.buy_price_mU('medicine')
        sell_price = state.world_pool.sell_price_mU('medicine')
        self.assertEqual(mid, 157250)       # 10 * w_cat 15.725 RU
        self.assertEqual(buy_price, 180838)  # ceil(11.5 * 15.725 * 1000)
        self.assertEqual(sell_price, 133662) # floor(8.5 * 15.725 * 1000)
        reserves = self.fund(game, 'n0', 10_000_000)
        before_home = self.stock(game, 'n0', 'n0-h0', 'medicine')
        game.step({'n0': (self.order(1, 'pool_buy', 'medicine', 5000, 'n0-h0'),)})
        self.assertEqual(self.status(game, 'n0').status, 'ACCEPTED')
        self.assertEqual(state.initial.inventory.balance(pool_stock('medicine')), 10_000_000 - 5000)
        self.assertEqual(state.initial.cash.balance(reserves).native_mU, 10_000_000 - 5 * buy_price)
        state.audit()
        self.assertGreaterEqual(self.stock(game, 'n0', 'n0-h0', 'medicine') - before_home, 5000 - 200)
        obs = state.world_pool.visible('n0')
        self.assertEqual(obs['buy_price_ru_mU']['medicine'], buy_price)
        self.assertEqual(obs['sell_price_ru_mU']['medicine'], sell_price)
        self.assertEqual(obs['last_fills'][0]['filled_mU'], 5000)
        self.assertEqual(obs['used_mU'], [['medicine', 'buy', 5000]])
        self.assertEqual(obs['stock_mU']['medicine'], 10_000_000 - 5000)
        self.assertIn('world_pool', game.state.snapshot())

    def test_forty_unit_cap_per_nation_good_direction_and_cycle(self):
        game = self.game()
        self.fund(game, 'n0', 100_000_000)
        game.step({'n0': (self.order(1, 'pool_buy', 'steel', 30000, 'n0-h0'),
                          self.order(2, 'pool_buy', 'steel', 15000, 'n0-h0'),
                          self.order(3, 'pool_sell', 'steel', 10000, 'n0-h0'))})
        statuses = [r.status for r in game.state.command_streams['n0'].recent[-3:]]
        self.assertEqual(statuses[0], 'ACCEPTED')
        self.assertNotEqual(statuses[1], 'ACCEPTED')
        self.assertEqual(statuses[2], 'ACCEPTED')  # the sell direction has its own cap
        for _ in range(4):
            game.step()
        game.step({'n0': (self.order(4, 'pool_buy', 'steel', 10000, 'n0-h0'),)})  # same cycle (tick 5)
        self.assertEqual(self.status(game, 'n0').status, 'ACCEPTED')
        for _ in range(4):
            game.step()
        game.step({'n0': (self.order(5, 'pool_buy', 'steel', 40000, 'n0-h0'),)})  # next cycle
        self.assertEqual(self.status(game, 'n0').status, 'ACCEPTED')
        game.state.audit()

    def test_rejections_leave_no_lock(self):
        game = self.game()
        state = game.state
        game.step({'n0': (self.order(1, 'pool_buy', 'steel', 1000, 'n0-h0'),     # no RU reserves
                          self.order(2, 'pool_buy', 'steel', 1000, 'n1-h0'),     # foreign Hub
                          self.order(3, 'pool_buy', 'vehicle', 500, 'n0-h0'),    # indivisible
                          self.order(4, 'pool_sell', 'steel', 10**9, 'n0-h0'),   # no such stock
                          self.order(5, 'pool_buy', 'electricity', 1000, 'n0-h0'))})
        receipts = state.command_streams['n0'].recent[-5:]
        self.assertTrue(all(r.status != 'ACCEPTED' for r in receipts))
        self.assertFalse(state.world_pool.orders)
        self.assertEqual(state.world_pool.last_fills, [])
        state.audit()

    def test_sale_is_paid_from_real_pool_cash_and_scaled_in_one_batch(self):
        game = self.game()
        state = game.state
        cash = state.initial.cash
        sink = CashAccount('n7', 'reserves', 'RU')
        price = state.world_pool.sell_price_mU('steel')
        # Leave the pool little RU; other P5 users may draw on it first, so
        # the test reads the budget the world pool actually saw.
        cash.transfer(POOL_RU, sink, cash.balance(POOL_RU).native_mU - 2_000_000)
        before = {p: self.stock(game, p, f'{p}-h0', 'steel') for p in ('n0', 'n1')}
        game.step({'n0': (self.order(1, 'pool_sell', 'steel', 40000, 'n0-h0'),),
                   'n1': (self.order(1, 'pool_sell', 'steel', 40000, 'n1-h0'),)})
        budget = state.world_pool.last_budget_mU
        self.assertLess(budget, 80 * price)
        fills = {r['owner']: r['filled_mU'] for r in state.world_pool.last_fills}
        paid = {r['owner']: r['paid_ru_mU'] for r in state.world_pool.last_fills}
        self.assertLessEqual(sum(paid.values()), max(0, budget))
        self.assertGreater(sum(paid.values()), budget - 2 * price)
        self.assertLessEqual(abs(fills['n0'] - fills['n1']), 2)
        self.assertEqual(state.initial.inventory.balance(pool_stock('steel')), 10_000_000 + sum(fills.values()))
        for p in ('n0', 'n1'):
            self.assertEqual(paid[p], fills[p] * price // 1000)
            self.assertEqual(cash.balance(CashAccount(p, 'reserves', 'RU')).native_mU, paid[p])
            # unfilled remainder returned; filled part left the Hub
            self.assertLessEqual(self.stock(game, p, f'{p}-h0', 'steel'), before[p] - fills[p] + 1000)
        self.assertFalse(any(b.startswith('locked_world_pool') for b in
                             {a.bucket for a in state.initial.inventory.balances()}))
        state.audit()

    def test_purchase_never_launders_an_embargoed_origin(self):
        game = self.game()
        state = game.state
        inv = state.initial.inventory
        good = 'advanced_chip'
        # Move every world-origin chip out of the pool into a fixture Hub, and
        # give n2 its own chips (origin n2) to sell.
        stock = inv.balance(pool_stock(good))
        inv.transfer_many((Transfer(pool_stock(good), Account('n7', 'n7-h0', 'available', good), stock,
                                    legs=(Leg('world_pool', 'n7-h0', 'world_pool', 1, False),)),))
        inv.transform({}, {Account('n2', 'n2-h0', 'available', good): 20000}, reason='test_fixture')
        self.assertEqual(inv.balance(pool_stock(good)), 0)
        game.step({'n2': (self.order(1, 'pool_sell', good, 20000, 'n2-h0'),)})
        self.assertEqual(inv.balance(pool_stock(good)), 20000)
        state.export_controls.policies['n2'] = ExportPolicy(frozenset({good}), frozenset({'n1'}))
        for p in ('n1', 'n3'):
            self.fund(game, p, 10_000_000)
        for _ in range(4):
            game.step()
        game.step({'n1': (self.order(2, 'pool_buy', good, 10000, 'n1-h0'),),
                   'n3': (self.order(2, 'pool_buy', good, 10000, 'n3-h0'),)})
        fills = {r['owner']: r['filled_mU'] for r in state.world_pool.last_fills}
        self.assertEqual(fills, {'n1': 0, 'n3': 10000})
        self.assertEqual(state.initial.cash.balance(CashAccount('n1', 'reserves', 'RU')).native_mU, 10_000_000)
        origins = {o.nation for b in ('next_available', 'available')
                   for o in inv.materials.composition(Account('n3', 'n3-h0', b, good))}
        self.assertEqual(origins, {'n2'})
        state.audit()


if __name__ == '__main__':
    unittest.main()
