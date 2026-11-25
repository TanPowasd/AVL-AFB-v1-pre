"""07 Kinfra: an edge counts half at each connected end (kinfra_edge_halves_v1)."""

import unittest

from organizer.rift_era.engine.assets import Asset
from organizer.rift_era.engine.catalog import Catalog
from organizer.rift_era.engine.initialization import initialize
from organizer.rift_era.engine.match import match_economy
from organizer.rift_era.engine.numeric import Q
from organizer.rift_era.engine.world import generate_candidate


class KinfraEdgeTests(unittest.TestCase):
    def setUp(self):
        catalog = Catalog()
        self.state = match_economy(catalog, initialize(catalog, generate_candidate(1000)), total_ticks=40,
                                   fingerprint_each_tick=False).state
        self.collector = self.state.national_power

    def add_edge_asset(self, eid, hub):
        aid = f'road:test:{eid}'
        asset = Asset(aid, 'n0', hub, {'stone': 4000000})
        self.state.initial.assets[aid] = asset
        self.state.cargo.road_assets[eid] = aid
        return sum((Q(q) * self.state.catalog.weights[g] for g, q in asset.book_nmU.items()), Q(0)) / 1000000

    def test_edge_with_one_connected_end_counts_half(self):
        connected = self.collector.connected_regions('n0')
        eid = 'road:city-33-h0:n0-h3'
        hubs = self.state.initial.map.hubs
        self.assertNotIn(hubs['city-33-h0'].region, connected)
        self.assertIn(hubs['n0-h3'].region, connected)
        before = self.collector._kinfra('n0', connected)
        book = self.add_edge_asset(eid, 'city-33-h0')   # build_corridor stores endpoint a as hub
        self.assertGreater(book, 0)
        self.assertEqual(self.collector._kinfra('n0', connected) - before, book / 2)

    def test_domestic_edge_counts_whole(self):
        connected = self.collector.connected_regions('n0')
        hubs = self.state.initial.map.hubs
        eid = next(e for e, edge in sorted(self.state.cargo.corridors.items())
                   if hubs[edge.a].region in connected and hubs[edge.b].region in connected)
        before = self.collector._kinfra('n0', connected)
        book = self.add_edge_asset(eid, self.state.cargo.corridors[eid].a)
        self.assertEqual(self.collector._kinfra('n0', connected) - before, book)


if __name__ == '__main__':
    unittest.main()
