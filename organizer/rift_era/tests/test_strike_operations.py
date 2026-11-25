"""N10 hypersonic strike, blockade and interdict (strike_operations)."""
import unittest
from types import SimpleNamespace as NS
from unittest.mock import patch

from organizer.rift_era.engine.catalog import Catalog
from organizer.rift_era.engine.clock import ClockConfig
from organizer.rift_era.engine.determinism import fingerprint
from organizer.rift_era.engine.economy import opening_economy
from organizer.rift_era.engine.errors import RuleViolation
from organizer.rift_era.engine.initialization import initialize
from organizer.rift_era.engine.ledger import Account
from organizer.rift_era.engine.materials import Origin
from organizer.rift_era.engine.numeric import Q
from organizer.rift_era.engine.strike_operations import StrikeCommands
from organizer.rift_era.engine.world import generate_candidate


def ctx(tick):
    return NS(seed=1000, tick=tick, cycle=tick // 10, window=tick // 5, clock=ClockConfig())


class StrikeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.catalog, cls.world = Catalog(), generate_candidate(1000)

    def fixture(self, war=True):
        game = opening_economy(self.catalog, initialize(self.catalog, self.world), total_ticks=30,
                               debug=False, commerce=True)
        state = game.state
        if war:
            state.initial.diplomacy.phase1(0, declarations=(('n0', 'n1'),))
        stock = {Account('n0', 'n0-h0', 'available', 'hypersonic_missile'): 5000}
        for good in ('ammunition', 'fuel', 'food', 'precision_ammunition'):
            if good in self.catalog.goods:
                stock[Account('n0', 'n0-h0', 'available', good)] = 500000
        state.initial.inventory.transform({}, stock, reason='test_strike_stock',
                                          origins={a: Origin(a.owner, a.location) for a in stock})
        return game

    def strike(self, seq=1, qty=2, target='n1/P070/0'):
        return {'id': str(seq), 'kind': 'military', 'op': 'hypersonic_strike', 'origin_hub': 'n0-h0',
                'target_id': target, 'missile_qty': qty}

    def visible(self):
        return (patch('organizer.rift_era.engine.military_sensors.MilitarySensors.quality', return_value=(Q(1), None)),
                patch('organizer.rift_era.engine.military_sensors.MilitarySensors.distances',
                      side_effect=lambda region: {r: 1 for r in self.world_regions}))

    def test_strike_requires_n10_war_and_visible_target(self):
        state = self.fixture(war=False).state
        handler = StrikeCommands(state)
        with self.assertRaises(RuleViolation) as err:
            handler.prepare('n0', self.strike(), ctx(0))
        self.assertEqual(err.exception.code, 'ERR_TECH_LOCKED')
        state.research['n0'].known.add('N10')
        with self.assertRaises(RuleViolation) as err:
            handler.prepare('n0', self.strike(qty=21), ctx(0))
        self.assertEqual(err.exception.code, 'ERR_SCHEMA')
        self.world_regions = state.initial.map.regions
        q, d = self.visible()
        with q, d, self.assertRaises(RuleViolation) as err:
            handler.prepare('n0', self.strike(), ctx(0))
        self.assertEqual(err.exception.code, 'ERR_NOT_AT_WAR')
        before = fingerprint(state.snapshot())
        with self.assertRaises(RuleViolation):
            handler.prepare('n0', self.strike(target='n0/P070/0'), ctx(0))
        self.assertEqual(fingerprint(state.snapshot()), before)

    def test_strike_spends_missiles_records_action_loss_and_damages_target(self):
        state = self.fixture().state
        state.research['n0'].known.add('N10')
        self.world_regions = state.initial.map.regions
        handler = StrikeCommands(state)
        target = 'n1/P070/0'
        health = state.initial.assets[target].health_mU
        q, d = self.visible()
        with q, d:
            handler.prepare('n0', self.strike(), ctx(0)).commit()
        missile = Account('n0', 'n0-h0', 'available', 'hypersonic_missile')
        self.assertEqual(state.initial.inventory.balance(missile), 3000)
        state.strikes.advance(ctx(0))
        loss = state.strikes.action_cycles[0]['n0']
        self.assertEqual(loss, 2 * self.catalog.weights['hypersonic_missile'])
        self.assertTrue(all(s.launched for s in state.strikes.shots.values()))
        state.strikes.advance(ctx(1))
        self.assertFalse(state.strikes.shots)
        self.assertLess(state.initial.assets[target].health_mU, health)
        self.assertEqual([r['status'] for r in state.strikes.visible('n0')['recent']], ['LAUNCHED', 'ARRIVED'])
        state.audit()

    def test_blockade_blocks_edge_for_war_target_only_when_supplied(self):
        state = self.fixture().state
        handler = StrikeCommands(state)
        order = {'id': '3', 'kind': 'military', 'op': 'blockade', 'formation_id': 'n0/garrison/0/0',
                 'edge_id': 'road:n0-h0:n0-h1', 'target_nations': ['n1']}
        with self.assertRaises(RuleViolation) as err:
            handler.prepare('n0', {**order, 'target_nations': ['n2']}, ctx(0))
        self.assertEqual(err.exception.code, 'ERR_NOT_AT_WAR')
        with self.assertRaises(RuleViolation) as err:
            handler.prepare('n0', {**order, 'edge_id': 'road:n1-h0:n1-h1'}, ctx(0))
        self.assertIn(err.exception.code, ('ERR_SITE_INCOMPATIBLE', 'ERR_NO_VISIBLE_TARGET'))
        handler.prepare('n0', order, ctx(0)).commit()
        state.strikes.advance(ctx(0))
        edge = state.cargo.corridors['road:n0-h0:n0-h1']
        self.assertGreater(edge.blocked.get('n1', Q(0)), 0)
        self.assertNotIn('n2', edge.blocked)
        self.assertGreater(state.strikes.action_cycles[0]['n0'], 0)
        # Blockader's own region is NOT self-damaged by its blockade against n1.
        self.assertNotIn(state.initial.map.hubs['n0-h0'].region, state.strikes.region_ratios())
        # When the targeted enemy (n1) controls the opposite endpoint, its region IS penalized.
        r1 = state.initial.map.hubs['n0-h1'].region
        prev_ctrl = state.authority.regions[r1].controller
        state.authority.regions[r1].controller = 'n1'
        try:
            self.assertIn(r1, state.strikes.region_ratios())
            self.assertNotIn(state.initial.map.hubs['n0-h0'].region, state.strikes.region_ratios())
        finally:
            state.authority.regions[r1].controller = prev_ctrl
        # Cancel by command id: the edge reopens on the next P4.
        state.strikes.cancel('n0', '3').commit()
        state.strikes.advance(ctx(1))
        self.assertNotIn('n1', edge.blocked)
        state.audit()

    def test_interdict_rejects_unknown_track(self):
        state = self.fixture().state
        with self.assertRaises(RuleViolation) as err:
            StrikeCommands(state).prepare('n0', {'id': '4', 'kind': 'military', 'op': 'interdict',
                                                 'formation_id': 'n0/garrison/0/0',
                                                 'shipment_track_id': 'nope'}, ctx(0))
        self.assertEqual(err.exception.code, 'ERR_NO_VISIBLE_TARGET')

    def test_registered_in_engine_dispatch(self):
        game = self.fixture()
        ops = set(game.state.command_handlers.dispatch) if hasattr(game.state, 'command_handlers') else None
        from organizer.rift_era.engine.commands import EngineCommands
        dispatch = EngineCommands(game.state).dispatch
        for op in (('military', 'hypersonic_strike'), ('military', 'blockade'), ('military', 'interdict'),
                   ('logistics', 'forward_contract'), ('finance', 'fx_short'), ('finance', 'fx_cover'),
                   ('finance', 'secondary_offering')):
            self.assertIn(op, dispatch)
        del ops


if __name__ == '__main__':
    unittest.main()
