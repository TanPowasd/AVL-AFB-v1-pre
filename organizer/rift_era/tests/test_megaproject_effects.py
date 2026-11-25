"""03/04 L12: completed bridge opens an 800-load rail corridor plus an 800-power line;
completed island adds a near-shore hub that the economy treats like any other."""
import unittest
from types import SimpleNamespace

from organizer.rift_era.engine.assets import Asset
from organizer.rift_era.engine.catalog import Catalog
from organizer.rift_era.engine.economy import opening_economy
from organizer.rift_era.engine.errors import RuleViolation
from organizer.rift_era.engine.initialization import initialize
from organizer.rift_era.engine.megaprojects import BRIDGE_LOADS_mU, Megaproject, Megaprojects
from organizer.rift_era.engine.world import SIZE, generate_candidate


class MegaprojectEffectTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.catalog, cls.world = Catalog(), generate_candidate(1000)

    def setUp(self):
        self.game = opening_economy(self.catalog, initialize(self.catalog, self.world), total_ticks=60, debug=False)
        self.state = self.game.state

    def row(self, kind, hub='n0-h0', **extra):
        aid = f'mega:{kind}'
        self.state.initial.assets[aid] = Asset(aid, 'n0', hub, {}, under_construction=True)
        self.state.authority.sync_assets(self.state.initial.assets)
        row = Megaproject(aid, 'n0', hub, kind, **extra)
        self.state.megaprojects.rows[aid] = row
        return row

    def test_l_path_and_partner_rule(self):
        cells = Megaprojects.l_path((3, 4), (5, 2))
        self.assertEqual(cells, (4 * SIZE + 3, 4 * SIZE + 4, 4 * SIZE + 5, 3 * SIZE + 5, 2 * SIZE + 5))
        state = self.state
        site = state.initial.map.hubs['n0-h0']
        partner, path = state.megaprojects.bridge_partner('n0', site)
        if partner is not None:
            hub = state.initial.map.hubs[partner]
            self.assertLessEqual(abs(hub.x - site.x) + abs(hub.y - site.y), 8)
            self.assertTrue(any(state.initial.map.terrain_cells[c] == 0 for c in path))
            self.assertTrue(state.authority.controls_hub('n0', partner))

    def test_bridge_opens_rail_corridor_and_power_line(self):
        state = self.state
        a, b = state.initial.map.hubs['n0-h0'], state.initial.map.hubs['n0-h1']
        row = self.row('bridge', partner='n0-h1', cells=Megaprojects.l_path((a.x, a.y), (b.x, b.y)))
        state.megaprojects.complete(SimpleNamespace(target=row.id))
        edge = state.cargo.corridors[f'l12:{row.id}']
        self.assertEqual((edge.a, edge.b, edge.mode, edge.capacity_mU_cycle), ('n0-h0', 'n0-h1', 'rail', BRIDGE_LOADS_mU))
        link = state.utilities.links[f'power:l12:{row.id}']
        self.assertEqual(state.utilities.power_rating(link)[0], BRIDGE_LOADS_mU)
        self.assertIn(link.id, {e.id for e in state.utilities.dispatch_edges()})
        self.assertEqual(state.megaprojects.completed('n0'), 1)
        # The game advances with the HV line in the grid (grid_dispatch_v1).
        for _ in range(12):
            self.game.step()
        self.assertIn(link.id, {e.id for e in state.utilities.dispatch_edges()})
        self.assertTrue(state.frame.power_receipts)
        state.audit()

    def test_island_adds_a_working_near_shore_hub(self):
        state = self.state
        row = self.row('island')
        hubs_before = set(state.initial.map.hubs)
        state.megaprojects.complete(SimpleNamespace(target=row.id))
        new = set(state.initial.map.hubs) - hubs_before
        self.assertEqual(new, {'n0-h0-isl0'})
        hub = state.initial.map.hubs['n0-h0-isl0']
        self.assertEqual(state.initial.map.terrain_cells[hub.y * SIZE + hub.x], 0)
        self.assertEqual(hub.region, state.initial.map.hubs['n0-h0'].region)
        self.assertTrue(state.authority.controls_hub('n0', hub.id))
        for _ in range(12):
            self.game.step()
        self.assertEqual(state.initial.warehouse_mU[hub.id], 300000)
        state.audit()

    def test_bridge_order_needs_a_strait_partner(self):
        state = self.state
        state.megaprojects.bridge_partner = lambda owner, site: (None, ())
        builder = SimpleNamespace(_owned_hub=lambda owner, hub: state.initial.map.hubs[hub],
                                  _tech=lambda owner, techs: None)
        coastal = next((h for h, hub in state.initial.map.hubs.items() if hub.owner == 'n0' and any(
            state.initial.map.regions[r].terrain == 'ocean' for r in state.initial.map.regions[hub.region].adjacent)), None)
        if coastal is None:
            self.skipTest('no coastal n0 hub on this map')
        with self.assertRaises(RuleViolation):
            state.megaprojects.prepare(builder, 'n0', {'facility_type': 'public', 'profile_id': 'L12_bridge',
                                                       'hub_id': coastal, 'id': '5'}, None)


if __name__ == '__main__':
    unittest.main()
