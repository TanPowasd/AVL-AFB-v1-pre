"""Formal atlas wiring: remote deposits, atlas reserves, river DAG, acceptance (P0-3)."""
import unittest
from unittest import mock

from organizer.rift_era.engine import match as match_module
from organizer.rift_era.engine.catalog import Catalog
from organizer.rift_era.engine.economy import opening_economy
from organizer.rift_era.engine.errors import InfrastructureFault
from organizer.rift_era.engine.initialization import initialize
from organizer.rift_era.engine.kernel import TickContext
from organizer.rift_era.engine.ledger import Account
from organizer.rift_era.engine.world import BootstrapResult
from organizer.rift_era.engine.world_atlas import generate_candidate


class AtlasWiringTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.catalog = Catalog()
        cls.world = generate_candidate(1000)
        cls.engine = opening_economy(cls.catalog, initialize(cls.catalog, cls.world), debug=True, commerce=True)
        cls.state = cls.engine.state

    def test_all_atlas_sites_registered(self):
        atlas = self.state.initial.resource_atlas
        self.assertEqual(len(atlas), 572)
        self.assertTrue(set(atlas) <= set(self.state.deposits))
        self.assertTrue(set(atlas) <= set(self.state.resource_sites))
        remote = [s for s in atlas.values() if s.starting_mine is None]
        self.assertEqual(len(remote), 500)
        for site in remote:
            deposit = self.state.deposits[site.id]
            source = Account(*self.state.resource_sources[site.id], site.good)
            if site.good == 'water':
                self.assertEqual(self.state.resource_sources[site.id][1], f'basin:{site.hub}')
            else:
                self.assertEqual(deposit.initial_mU, site.reserve_mU)
                self.assertEqual(self.state.initial.inventory.balance(source), site.reserve_mU)
            self.assertEqual(self.state.resource_sites[site.id].hub, site.hub)

    def test_starting_reserves_read_atlas(self):
        for mid, mine in self.state.initial.mines.items():
            site = self.state.initial.resource_atlas[mid]
            if mine.good != 'water':
                self.assertEqual(self.state.deposits[mid].initial_mU, site.reserve_mU)
            self.assertEqual(mine.grade * 1000, site.grade_mU)

    def test_hydrology_reads_river_dag(self):
        hydro = self.state.hydrology
        for rid, outlets in self.world.river_downstream.items():
            self.assertEqual(hydro.downstream[rid], tuple(outlets))
        elevations = [self.world.regions[r].elevation for r in hydro.order]
        self.assertEqual(elevations, sorted(elevations, reverse=True))

    def test_remote_site_is_buildable_on_own_hub(self):
        from organizer.rift_era.engine.industry_commands import IndustryCommands
        state = self.state
        owned = next(s for s in state.initial.resource_atlas.values() if s.starting_mine is None and s.good != 'water'
                     and state.initial.map.hubs[s.hub].owner in state.initial.known
                     and state.authority.controls_hub(state.initial.map.hubs[s.hub].owner, s.hub))
        owner = state.initial.map.hubs[owned.hub].owner
        commands = IndustryCommands(state)
        ctx = TickContext(1000, 0, self.engine.clock, {})
        prepared = commands.prepare(owner, {'id': '7', 'kind': 'industry', 'op': 'build_mine', 'hub_id': owned.hub,
                                            'deposit_id': owned.id, 'good': owned.good, 'level': 'G1'}, ctx)
        self.assertIsNotNone(prepared)

    def test_runs_and_audits(self):
        for _ in range(2):
            self.engine.step()
        self.state.audit()

    def test_city_state_uses_atlas_sites_and_annexation_creates_no_duplicate_deposits(self):
        from organizer.rift_era.engine.errors import RuleViolation
        from organizer.rift_era.engine.industry_commands import IndustryCommands
        engine = opening_economy(self.catalog, initialize(self.catalog, self.world), debug=False, commerce=True)
        state = engine.state
        city_econ = state.city_economy
        c = city_econ.cities['city-0']
        atlas_on_hub = sorted(s.id for s in state.initial.resource_atlas.values() if s.hub == c.hub)
        self.assertEqual(len(atlas_on_hub), 2)
        for sid in c.sites[1:]:
            self.assertIn(sid, atlas_on_hub)
            self.assertEqual(city_econ.sites[sid].initial_mU, state.deposits[sid].initial_mU)
        deposits_before = {did for did, m in state.resource_sites.items() if m.hub == c.hub}
        self.assertEqual(deposits_before, set(atlas_on_hub))
        region = state.initial.map.hubs[c.hub].region
        auth = state.authority.regions[region]
        auth.controller = 'n0'
        auth.occupied_since_tick = 0
        auth.support_mU = 650
        self.assertTrue(city_econ.annex_if_eligible(c.id, 'n0', 240))
        engine.step()
        state.audit()
        for sid in atlas_on_hub:
            aid = f'{sid}/asset'
            self.assertIn(aid, state.initial.mines)
            self.assertEqual(state.mine_deposit[aid], sid)
            self.assertNotIn(aid, state.deposits)
            self.assertNotIn(aid, state.resource_sites)
        # Occupied atlas deposit on the annexed hub cannot have a second mine built on it.
        non_water = next(s for s in state.initial.resource_atlas.values() if s.hub == c.hub and s.good != 'water')
        commands = IndustryCommands(state)
        ctx = TickContext(1000, engine.tick, engine.clock, {})
        with self.assertRaises(RuleViolation):
            commands.prepare('n0', {'id': '99', 'kind': 'industry', 'op': 'build_mine', 'hub_id': c.hub,
                                    'deposit_id': non_water.id, 'good': non_water.good, 'level': 'G1'}, ctx)


class WorldAcceptanceTest(unittest.TestCase):
    def test_redraws_until_real_bootstrap_accepts(self):
        seen = []

        def fake(catalog, world, *, debug=False):
            seen.append(world.attempt)
            return BootstrapResult(world.attempt == 2, 150, () if world.attempt == 2 else ('food buffer exhausted',))

        with mock.patch.object(match_module, 'bootstrap_check', fake):
            report = []
            world = match_module.accepted_world(Catalog(), 1000, report=report)
        self.assertEqual(world.attempt, 2)
        self.assertTrue(world.accepted)
        self.assertEqual(seen, [0, 1, 2])
        self.assertEqual([r['accepted'] for r in report], [False, False, True])

    def test_short_bootstrap_never_accepted(self):
        def short(catalog, world, *, debug=False):
            return BootstrapResult(True, 149, ())

        with mock.patch.object(match_module, 'bootstrap_check', short), \
                mock.patch.dict(match_module.__dict__):
            from organizer.rift_era.engine import world_atlas
            with mock.patch.dict(world_atlas.PROFILE, {'maximum_attempts': 3}):
                with self.assertRaises(InfrastructureFault):
                    match_module.accepted_world(Catalog(), 1000)


if __name__ == '__main__':
    unittest.main()
