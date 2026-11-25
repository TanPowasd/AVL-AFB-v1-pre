"""03 G02 supply radius/field supply orders and 04 V07 restored-land regeneration."""
import unittest
from types import SimpleNamespace

from organizer.rift_era.engine import military_supply
from organizer.rift_era.engine.catalog import Catalog
from organizer.rift_era.engine.economy import opening_economy, v07_restored_regions
from organizer.rift_era.engine.errors import RuleViolation
from organizer.rift_era.engine.initialization import initialize
from organizer.rift_era.engine.ledger import Account, Transfer
from organizer.rift_era.engine.match import match_economy
from organizer.rift_era.engine.materials import Origin
from organizer.rift_era.engine.numeric import Q
from organizer.rift_era.engine.transport_operations import TransportCommands
from organizer.rift_era.engine.world import generate_candidate


class FieldSupplyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.catalog, cls.world = Catalog(), generate_candidate(1000)

    def setUp(self):
        self.game = opening_economy(self.catalog, initialize(self.catalog, self.world), total_ticks=60, debug=False)
        self.state = self.game.state
        self.formation = self.state.formations.rows['n0/garrison/0/0']
        self.give('n0-h3', food=20000, fuel=10000, vehicle=2000)
        self.give('n0-h1', food=20000)

    def give(self, hub, **goods):
        # Test endowment at a hub (worldgen-style genesis with a declared origin).
        accounts = {Account('n0', hub, 'available', g): q for g, q in goods.items()}
        self.state.initial.inventory.transform({}, accounts, reason='test_endowment',
                                               origins={a: Origin('n0', hub) for a in accounts})

    def order(self, **change):
        return dict({'id': '77', 'kind': 'logistics', 'op': 'route_shipment', 'origin_hub': 'n0-h3',
                     'dest_hub': 'n0-h0', 'corridor_path': ['road:n0-h0:n0-h3'], 'good': 'food',
                     'qty_mU': 5000, 'carrier_recipe': 'P036', 'formation_id': self.formation.id}, **change)

    def ctx(self):
        return SimpleNamespace(tick=self.game.tick, seed=1000, clock=self.game.clock, cycle=self.game.tick // 10)

    def test_radius_is_two_hops_or_five_from_a_g02_depot(self):
        state = self.state
        far = self.order(origin_hub='n0-h1', corridor_path=['road:n0-h1:n0-h2', 'road:n0-h2:n0-h3', 'road:n0-h0:n0-h3'])
        with self.assertRaises(RuleViolation) as error:
            military_supply.validate(state, 'n0', far, tuple(far['corridor_path']))
        self.assertIn('2 hops', error.exception.detail)
        state.research['n0'].known.add('G02')
        with self.assertRaises(RuleViolation):  # n0-h1 holds no vehicle x2
            military_supply.validate(state, 'n0', far, tuple(far['corridor_path']))
        self.give('n0-h1', vehicle=2000)
        self.assertEqual(military_supply.validate(state, 'n0', far, tuple(far['corridor_path']))[1], True)
        with self.assertRaises(RuleViolation):  # destination must be the formation's hub
            military_supply.validate(state, 'n0', self.order(dest_hub='n0-h3'), ('road:n0-h0:n0-h3',))
        with self.assertRaises(RuleViolation) as error:  # 2 units x 20 loads
            military_supply.validate(state, 'n0', self.order(qty_mU=41000), ('road:n0-h0:n0-h3',))
        self.assertEqual(error.exception.code, 'ERR_CAPACITY_EXCEEDED')

    def test_supply_order_ships_into_the_formation_store(self):
        state = self.state
        store = Account('n0', self.formation.id, 'available', 'food')
        before = state.initial.inventory.balance(store)
        TransportCommands(state).prepare('n0', self.order(), self.ctx()).commit()
        self.assertEqual(state.initial.inventory.balance(store), before)  # no teleport
        for _ in range(30):
            self.game.step()
            if state.initial.inventory.balance(store) > before:
                break
        self.assertGreater(state.initial.inventory.balance(store), before)
        self.assertFalse(any(a.bucket.startswith('field_supply:') and q for a, q in state.initial.inventory.balances().items()))
        state.audit()

    def test_undeliverable_supply_is_released_to_the_hub(self):
        state = self.state
        inv = state.initial.inventory
        drop = Account('n0', 'n0-h3', military_supply.bucket(self.formation.id), 'food')
        inv.transfer_many((Transfer(Account('n0', 'n0-h3', 'available', 'food'), drop, 3000),))  # same hub
        before = inv.balance(Account('n0', 'n0-h3', 'available', 'food'))
        military_supply.absorb(state, self.ctx())  # formation is at n0-h0, not n0-h3
        self.assertEqual(inv.balance(drop), 0)
        self.assertEqual(inv.balance(Account('n0', 'n0-h3', 'available', 'food')), before + 3000)


class V07Tests(unittest.TestCase):
    def test_closed_deposits_restore_regeneration_regions_for_v07_holders(self):
        catalog = Catalog()
        game = match_economy(catalog, initialize(catalog, generate_candidate(1000)), total_ticks=400,
                             fingerprint_each_tick=False)
        state = game.state
        scale = state.national_power.active_scale
        mid = next(m for m, mine in state.initial.mines.items() if mine.owner == 'n0')
        region = state.initial.map.hubs[state.resource_sites[state.mine_deposit[mid]].hub].region
        scale.mine_last[state.mine_deposit[mid]] = ('n0', 0)
        self.assertEqual(v07_restored_regions(state, 150), frozenset())  # no V07
        state.research['n0'].known.add('V07')
        self.assertEqual(v07_restored_regions(state, 119), frozenset())  # not yet 120 cycles
        self.assertEqual(v07_restored_regions(state, 121), frozenset({region}))
        # Hydrology: the restored region's natural water this cycle is x1.5.
        base = Catalog()
        other = match_economy(base, initialize(base, generate_candidate(1000)), total_ticks=400,
                              fingerprint_each_tick=False)
        scale._restored = (0, frozenset({region}))
        game.step()
        other.step()
        ours, theirs = state.hydrology.natural_cycle_mU, other.state.hydrology.natural_cycle_mU
        self.assertTrue(any(ours[r] != theirs[r] for r in ours))
        self.assertTrue(all(ours[r] == theirs[r] for r in ours if r != region))
        if region in ours:
            self.assertIn(ours[region] - (theirs[region] * 3) // 2, (-1, 0, 1))
        state.audit()


if __name__ == '__main__':
    unittest.main()
