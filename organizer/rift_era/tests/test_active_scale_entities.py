"""07 ActiveScale meters that used to be pending (active_scale_entities_v1).

E12 intercontinental cables, L11 automated routes, L12 megaprojects, G02 field
depots, G07/G12 combined-arms and joint-command formations, V07 restored mines.
"""
import unittest
from types import SimpleNamespace

from organizer.rift_era.tests import test_transport_operations as fixture_source
from organizer.rift_era.engine.active_scale import ActiveScale, PENDING_METERS, intercontinental
from organizer.rift_era.engine.clock import ClockConfig
from organizer.rift_era.engine.errors import RuleViolation
from organizer.rift_era.engine.industry_commands import IndustryCommands
from organizer.rift_era.engine.ledger import Account
from organizer.rift_era.engine.numeric import Q
from organizer.rift_era.engine.utility_network import UtilityConstruction, UtilityLink
from organizer.rift_era.engine.world import SIZE


class StubPower:
    def __init__(self, regions):
        self.regions = regions

    def connected_regions(self, owner):
        return set(self.regions.get(owner, ()))


def ctx(tick=0):
    return SimpleNamespace(tick=tick, cycle=tick // 10, seed=1000, clock=ClockConfig())


class ActiveScaleEntityTests(unittest.TestCase):
    setUpClass = classmethod(fixture_source.CargoCommandTests.setUpClass.__func__)
    fixture = fixture_source.CargoCommandTests.fixture

    def atlas_state(self):
        from organizer.rift_era.engine.economy import opening_economy
        from organizer.rift_era.engine.initialization import initialize
        from organizer.rift_era.engine.world_atlas import generate_candidate
        if not hasattr(type(self), 'atlas'):
            type(self).atlas = generate_candidate(1000, 0)
        return opening_economy(self.catalog, initialize(self.catalog, self.atlas), total_ticks=10,
                               commerce=True, debug=False).state

    def stock(self, state, owner, hub, goods):
        state.initial.inventory.transform({}, {Account(owner, hub, 'available', g): q for g, q in goods.items()},
                                          reason='test_fixture_materials')

    def rules(self, state):
        return state.catalog.tables['scoring']['pillars']['tech']['active_scale_rules']

    def meter(self, scale, owner, tech, tick=9):
        return scale._meter(owner, tech, self.rules(scale.s)[tech], ctx(tick))

    def test_no_pending_meter_and_every_rule_is_metered(self):
        state = self.fixture().state
        self.assertFalse(PENDING_METERS)
        scale = ActiveScale(state, power=StubPower({}))
        scale.begin_tick(ctx(0))
        for owner in state.research:
            state.research[owner].known |= set(self.rules(state))
        scale._finish(ctx(9))
        self.assertFalse(scale.pending_meters())
        for tech in ('E12', 'L11', 'L12', 'G02', 'G07', 'G12', 'V07'):
            self.assertEqual(scale.values['n0'][tech], 0)

    def pair_on_two_continents(self, state):
        world = state.initial.map
        hubs = sorted(world.hubs.values(), key=lambda h: h.id)
        best = None
        for a in hubs:
            ca = world.regions[a.region].continent
            for b in hubs:
                cb = world.regions[b.region].continent
                if ca is None or cb is None or ca >= cb:
                    continue
                d = abs(a.x - b.x) + abs(a.y - b.y)
                if best is None or d < best[0]:
                    best = (d, a, b)
        return best

    def test_e12_flowing_intercontinental_cable_and_8pct_loss_cap(self):
        state = self.fixture().state
        _, a, b = self.pair_on_two_continents(state)
        link = UtilityLink('power:test', 'n0', a.id, b.id, 'power', 1, tuple(range(300)), 'asset:none')
        same = next(h for h in state.initial.map.hubs.values() if h.id != a.id and
                    state.initial.map.regions[h.region].continent == state.initial.map.regions[a.region].continent)
        local = UtilityLink('power:local', 'n0', a.id, same.id, 'power', 1, (0, 1), 'asset:none')
        self.assertTrue(intercontinental(state, link))
        self.assertFalse(intercontinental(state, local))
        state.utilities.links.update({link.id: link, local.id: local})
        scale = ActiveScale(state, power=StubPower({}))
        scale.begin_tick(ctx(0))
        state.frame.power_receipts = (SimpleNamespace(edges=(('power:test', 0), ('power:local', 500))),)
        scale._meter_entities(ctx(0))
        self.assertEqual(self.meter(scale, 'n0', 'E12'), 0)
        state.frame.power_receipts = (SimpleNamespace(edges=(('power:test', 7), ('power:local', 500))),)
        scale._meter_entities(ctx(1))
        self.assertEqual(self.meter(scale, 'n0', 'E12'), 1)
        self.assertEqual(self.meter(scale, 'n1', 'E12'), 0)
        # 299 cells at 1.5% per 10 cells would lose 35% (cap); E12 caps at 8%.
        self.assertEqual(state.utilities.power_rating(link)[1], 1 - Q(35, 100))
        state.research['n0'].known.add('E12')
        self.assertEqual(state.utilities.power_rating(link)[1], Q(92, 100))
        self.assertEqual(state.utilities.power_rating(local)[1], 1 - Q(15, 1000) * Q(1, 10))

    def l_path(self, a, b):
        cells = [a.y * SIZE + a.x]
        x, y = a.x, a.y
        while x != b.x:
            x += 1 if b.x > x else -1
            cells.append(y * SIZE + x)
        while y != b.y:
            y += 1 if b.y > y else -1
            cells.append(y * SIZE + x)
        return cells

    def test_e12_submarine_build_requires_tech_and_sixty_cells(self):
        # The legacy candidate has no coast within 60 cells of another
        # continent; the atlas map does (45 cells for seed 1000 attempt 0).
        state = self.atlas_state()
        world = state.initial.map
        hubs = sorted(world.hubs.values(), key=lambda h: h.id)
        d, a, b = self.pair_on_two_continents(state)
        cells = self.l_path(a, b)
        self.assertLessEqual(len(cells), 61)
        self.assertTrue(any(world.terrain_cells[x] == 0 for x in cells))
        palette = sorted(world.regions)
        for c in cells:
            if world.terrain_cells[c]:
                state.authority.regions[palette[world.region_cells[c]]].controller = 'n0'
        for h in (a, b):
            state.authority.regions[h.region].controller = 'n0'
        order = {'id': '5', 'origin_hub': a.id, 'dest_hub': b.id, 'mode': 'power', 'level': 1, 'path_cells': cells}
        industry = IndustryCommands(state)
        state.research['n0'].known.add('E02')
        with self.assertRaises(RuleViolation) as caught:
            UtilityConstruction(state, industry).prepare('n0', order, ctx())
        # International waters are refused without E12.
        self.assertIn(caught.exception.code, ('ERR_NO_PERMISSION', 'ERR_SITE_INCOMPATIBLE'))
        state.research['n0'].known.add('E12')
        self.stock(state, 'n0', a.id, {'copper': 100000, 'steel': 100000, 'cement': 100000, 'superconductor': 40000})
        before = set(state.work.jobs)
        UtilityConstruction(state, industry).prepare('n0', order, ctx()).commit()
        job, = [j for jid, j in state.work.jobs.items() if jid not in before]
        self.assertEqual(job.materials_mU['superconductor'], 40000)
        # Longer than one 60-cell submarine segment is refused.
        far = next((x, y, c) for x in hubs for y in hubs if x.id < y.id
                   for c in (self.l_path(x, y),) if len(c) > 61 and any(world.terrain_cells[q] == 0 for q in c))
        for c in far[2]:
            if world.terrain_cells[c]:
                state.authority.regions[palette[world.region_cells[c]]].controller = 'n0'
        for h in far[:2]:
            state.authority.regions[h.region].controller = 'n0'
        with self.assertRaises(RuleViolation) as caught:
            UtilityConstruction(state, industry).prepare('n0', dict(order, id='6', origin_hub=far[0].id,
                dest_hub=far[1].id, path_cells=far[2]), ctx())
        self.assertIn(caught.exception.code, ('ERR_SITE_INCOMPATIBLE', 'ERR_SCHEMA'))

    def test_l11_counts_only_auto_relays_moving_cargo(self):
        game = self.fixture()
        state = game.state
        order = fixture_source.CargoCommandTests.order(None, carrier_recipe='auto')
        state.research['n0'].known.add('L11')
        game.step({'n0': [order]})
        scale = ActiveScale(state, power=StubPower({}))
        scale.begin_tick(ctx(game.tick if hasattr(game, 'tick') else 1))
        for _ in range(3):
            scale._meter_entities(ctx(1))
            if scale.routes:
                break
            game.step()
        self.assertEqual(self.meter(scale, 'n0', 'L11'), 1)
        self.assertEqual(self.meter(scale, 'n1', 'L11'), 0)
        state.research['n0'].known.discard('L11')
        scale._reset(scale.cycle)
        scale._meter_entities(ctx(2))
        self.assertEqual(self.meter(scale, 'n0', 'L11'), 0)

    def test_l12_megaproject_build_and_completion(self):
        state = self.atlas_state()
        industry = IndustryCommands(state)
        world = state.initial.map
        coastal = next(h for h in sorted(world.hubs) if world.hubs[h].owner in state.research and
                       any(world.regions[r].terrain == 'ocean' for r in world.regions[world.hubs[h].region].adjacent))
        owner = world.hubs[coastal].owner
        if state.megaprojects.bridge_partner(owner, world.hubs[coastal])[0] is None:
            state.megaprojects.bridge_partner = lambda o, s: (f'{o}-h1', (0, 1))
        order = {'id': '7', 'kind': 'industry', 'op': 'build_facility', 'hub_id': coastal,
                 'facility_type': 'public', 'profile_id': 'L12_bridge'}
        with self.assertRaises(RuleViolation) as caught:
            industry.prepare(owner, order, ctx())
        self.assertEqual(caught.exception.code, 'ERR_TECH_LOCKED')
        state.research[owner].known.add('L12')
        with self.assertRaises(RuleViolation):
            industry.prepare(owner, dict(order, facility_type='工厂'), ctx())
        self.stock(state, owner, coastal, {'structure_module': 40000, 'steel': 80000, 'cement': 120000, 'metamaterial': 4000})
        industry.prepare(owner, order, ctx()).commit()
        job = next(j for j in state.work.jobs.values() if j.kind == 'build_megaproject')
        self.assertEqual(job.duration_ticks, 1200)
        self.assertEqual(job.materials_mU, {'structure_module': 40000, 'steel': 80000, 'cement': 120000, 'metamaterial': 4000})
        scale = ActiveScale(state, power=StubPower({}))
        self.assertEqual(self.meter(scale, owner, 'L12'), 0)
        # Completion through the industry work hook; the 120-cycle material
        # schedule itself is the ordinary construction path (not re-run here).
        asset = state.initial.assets[job.target]
        state.asset_lifecycle.commission = lambda aid: setattr(state.initial.assets[aid], 'under_construction', False)
        asset.health_mU = 1000
        industry.complete(job)
        self.assertTrue(state.megaprojects.rows[job.target].completed)
        self.assertGreater(asset.availability, 0)
        self.assertEqual(self.meter(ActiveScale(state, power=StubPower({})), owner, 'L12'), 1)
        state.megaprojects.audit()

    def test_g02_field_depot_supplies_formation_away_from_home(self):
        game = self.fixture()
        state = game.state
        formation = next(f for f in state.formations.rows.values() if f.owner == 'n0' and f.domain == 'Land')
        inv = state.initial.inventory
        for g in ('food', 'water'):
            own = Account('n0', formation.id, 'available', g)
            inv.transfer(own, Account('n0', formation.home_hub, 'available', g), inv.balance(own))
        game.step()
        self.assertEqual(state.frame.military_hub_draws.get(formation.id), formation.home_hub)
        scale = ActiveScale(state, power=StubPower({}))
        scale.begin_tick(ctx(0))
        scale._meter_entities(ctx(0))
        self.assertEqual(self.meter(scale, 'n0', 'G02'), 0)  # home base, not a field depot
        away = 'n0-h1'
        state.frame.military_hub_draws = {formation.id: away}
        scale._meter_entities(ctx(1))
        self.assertEqual(self.meter(scale, 'n0', 'G02'), 0)  # no vehicle x2 installed there
        inv.transform({}, {Account('n0', away, 'available', 'vehicle'): 2000}, reason='test_fixture_vehicles')
        scale._meter_entities(ctx(2))
        self.assertEqual(self.meter(scale, 'n0', 'G02'), 1)

    def test_g07_g12_ready_networked_combined_arms_and_joint_freeze(self):
        state = self.fixture().state
        rows = [f for f in state.formations.rows.values() if f.owner == 'n0']
        region = rows[0].region
        group = [f for f in rows if f.region == region]
        types = {state.initial.units[u].unit_type for f in group for u in f.unit_ids}
        self.assertGreaterEqual(len(types), 2)
        target = group[0]
        state.frame.military_need = {f.id: {'food': 1} for f in group}
        state.frame.military = {f.id: {'food': 1} for f in group}
        power = StubPower({'n0': {region}})
        scale = ActiveScale(state, power=power)
        scale.begin_tick(ctx(0))
        state.research['n0'].known |= {'G07', 'G12'}
        scale._meter_entities(ctx(0))
        self.assertEqual(self.meter(scale, 'n0', 'G07'), 0)  # no radio/sensor slot
        target.slot_a = 'radio'
        for t in range(1, 6):
            scale._meter_entities(ctx(t))
        self.assertEqual(self.meter(scale, 'n0', 'G07'), Q(5, 10))
        self.assertEqual(self.meter(scale, 'n0', 'G12'), Q(5, 10))
        # Unsupplied or disconnected formations are not ready.
        state.frame.military[target.id] = {'food': 0}
        scale._meter_entities(ctx(6))
        self.assertEqual(self.meter(scale, 'n0', 'G07'), Q(5, 10))
        state.frame.military[target.id] = {'food': 1}
        # Capital lost at tick 7: joint doctrine frozen for 10 ticks.
        power.regions = {}
        scale._meter_entities(ctx(7))
        power.regions = {'n0': {region}}
        for t in range(8, 10):
            scale._meter_entities(ctx(t))
        self.assertEqual(self.meter(scale, 'n0', 'G07'), Q(7, 10))
        self.assertEqual(self.meter(scale, 'n0', 'G12'), Q(5, 10))
        scale._reset(1)
        scale._meter_entities(ctx(17))
        self.assertEqual(self.meter(scale, 'n0', 'G12', 19), Q(1, 10))

    def test_v07_deposit_closed_for_120_full_cycles(self):
        state = self.fixture().state
        mid = next(m for m, mine in sorted(state.initial.mines.items()) if mine.owner == 'n0')
        scale = ActiveScale(state, power=StubPower({}))
        scale.begin_tick(ctx(30))
        scale.extracted(mid, 'n0', 500)
        self.assertEqual(scale.mine_last[state.mine_deposit[mid]], ('n0', 3))
        self.assertEqual(self.meter(scale, 'n0', 'V07', tick=1229), 0)
        self.assertEqual(self.meter(scale, 'n0', 'V07', tick=1239), 1)
        self.assertEqual(self.meter(scale, 'n1', 'V07', tick=1239), 0)
        scale.begin_tick(ctx(1240))
        scale.extracted(mid, 'n0', 1)
        self.assertEqual(self.meter(scale, 'n0', 'V07', tick=1249), 0)


if __name__ == '__main__':
    unittest.main()
