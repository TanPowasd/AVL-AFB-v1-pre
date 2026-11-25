"""E4: city_land_commerce_default_v1 (neutral city land corridors admit commerce)."""

import unittest

from organizer.rift_era.engine.catalog import Catalog
from organizer.rift_era.engine.diplomacy import Pair, Relation
from organizer.rift_era.engine.initialization import initialize
from organizer.rift_era.engine.ledger import Account
from organizer.rift_era.engine.match import match_economy
from organizer.rift_era.engine.world import generate_candidate

CITY_EDGE = 'road:city-22-h0:n6-h0'       # legacy seed 1000: n6 capital <-> city-22
FOREIGN_EDGE = 'road:city-33-h0:n0-h3'


def fixture(ticks=40):
    catalog = Catalog()
    return match_economy(catalog, initialize(catalog, generate_candidate(1000)), total_ticks=ticks,
                         fingerprint_each_tick=False)


class CityCommerceAdmissionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.engine = fixture()
        cls.state = cls.engine.state
        cls.cargo = cls.state.cargo

    def test_city_operates_its_edges_and_admits_commerce_without_toll(self):
        self.assertEqual(self.cargo.operator(CITY_EDGE), 'city-22')
        self.assertEqual(self.cargo.corridors[CITY_EDGE].access_tier, {})
        self.assertTrue(self.cargo.permitted('n0', FOREIGN_EDGE, 'food'))
        # The other endpoint is n0's: a third nation still needs n0's own ACL.
        self.assertFalse(self.cargo.permitted('n1', FOREIGN_EDGE, 'food'))
        self.assertTrue(self.cargo.permitted('n6', CITY_EDGE))
        self.assertEqual(self.cargo.entry_fee('n6', CITY_EDGE, 1000, {'food': 1000}), 0)
        self.assertEqual(self.cargo.route_tolls('n6', (CITY_EDGE,), 1000, {'food': 1000}), [0])

    def test_military_cargo_still_needs_tier_two(self):
        war_good = sorted(self.state.export_controls.war_goods - {'food'})[0]
        if not self.cargo.military_cargo(war_good):
            war_good = next(g for g in sorted(self.state.export_controls.war_goods) if self.cargo.military_cargo(g))
        self.assertFalse(self.cargo.permitted('n6', CITY_EDGE, war_good))

    def test_war_with_the_city_closes_its_edges(self):
        dip = self.state.initial.diplomacy
        key = tuple(sorted(('n6', 'city-22')))
        saved = dip.pairs.get(key)
        dip.pairs[key] = Pair(Relation.WAR, frozenset({'n6'}))
        try:
            self.assertFalse(self.cargo.permitted('n6', CITY_EDGE, 'food'))
        finally:
            if saved is None:
                dip.pairs.pop(key)
            else:
                dip.pairs[key] = saved
        self.assertTrue(self.cargo.permitted('n6', CITY_EDGE, 'food'))

    def test_sea_and_air_terminals_still_need_a_port_lease(self):
        edge = self.cargo.corridors[CITY_EDGE]
        for mode in ('sea', 'air'):
            edge.mode = mode
            try:
                self.assertFalse(self.cargo.city_commerce('n6', CITY_EDGE, ('food',)))
            finally:
                edge.mode = 'road'

    def test_nation_operated_foreign_edges_keep_their_own_acl(self):
        controller = lambda h: self.state.authority.regions[self.state.initial.map.hubs[h].region].controller
        edge_id = next(eid for eid, e in sorted(self.cargo.corridors.items())
                       if {str(controller(e.a))[:1], str(controller(e.b))[:1]} == {'n'}
                       and controller(e.a) != controller(e.b))
        edge = self.cargo.corridors[edge_id]
        outsider = controller(edge.b) if self.cargo.operator(edge_id) == controller(edge.a) else controller(edge.a)
        self.assertFalse(self.cargo.city_commerce(outsider, edge_id, ('food',)))
        self.assertFalse(self.cargo.permitted(outsider, edge_id, 'food'))


class CityExportEndToEndTests(unittest.TestCase):
    def test_food_delivered_by_land_is_bought_and_counts_as_export(self):
        engine = fixture(40)
        state, inv = engine.state, engine.state.initial.inventory
        serial = iter(range(1, 10**6))
        cmd = lambda op, **f: {'id': str(next(serial)), 'kind': 'logistics', 'op': op} | f
        sold = 0
        while engine.tick < 40:
            tick, orders = engine.tick, []
            if engine.clock.is_decision(tick):
                if tick == 0:
                    orders.append(cmd('route_shipment', origin_hub='n6-h0', dest_hub='city-22-h0',
                                      corridor_path=[CITY_EDGE], good='food', qty_mU=8000, carrier_recipe='manual'))
                food = inv.balance(Account('n6', 'city-22-h0', 'available', 'food'))
                if food:
                    orders.append(cmd('market_ask', hub_id='city-22-h0', good='food', qty_mU=food,
                                      limit_price_mU=1, expire_cycles=1))
            engine.step({'n6': tuple(orders)} if orders else None)
            sold += sum(t.quantity_mU for c in state.market_events if c.tick == tick
                        for t in c.trades if t.seller == 'n6' and t.buyer == 'city-22')
        receipts = list(state.command_streams['n6'].recent)
        self.assertEqual(receipts[0].status, 'ACCEPTED', receipts[0])
        self.assertGreater(sold, 0)
        self.assertGreater(state.national_power.rows['n6'][-1].international, 0)
        self.assertEqual(state.national_power.rows['n5'][-1].international, 0)
        state.audit()


if __name__ == '__main__':
    unittest.main()
