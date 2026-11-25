"""V08 climate intervention + consent, R08 backup hubs and C10 conditional plans."""
import unittest
from types import SimpleNamespace as NS

from organizer.rift_era.engine.catalog import Catalog
from organizer.rift_era.engine.climate import ClimateCommands
from organizer.rift_era.engine.clock import ClockConfig
from organizer.rift_era.engine.continuity import ContinuityCommands
from organizer.rift_era.engine.economy import opening_economy
from organizer.rift_era.engine.errors import RuleViolation
from organizer.rift_era.engine.initialization import initialize
from organizer.rift_era.engine.ledger import Account
from organizer.rift_era.engine.materials import Origin
from organizer.rift_era.engine.numeric import Q
from organizer.rift_era.engine.world import generate_candidate


def ctx(tick):
    return NS(seed=1000, tick=tick, cycle=tick // 10, window=tick // 5, clock=ClockConfig())


class ClimateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.catalog, cls.world = Catalog(), generate_candidate(1000)

    def fixture(self):
        game = opening_economy(self.catalog, initialize(self.catalog, self.world), total_ticks=30,
                               debug=False, commerce=True)
        state = game.state
        state.research['n0'].known.add('V08')
        stock = {Account('n0', 'n0-h0', 'available', 'remediation_agent'): 200000}
        state.initial.inventory.transform({}, stock, reason='test_climate_stock',
                                          origins={a: Origin(a.owner, a.location) for a in stock})
        return game

    def home(self, state):
        return state.initial.map.hubs['n0-h0'].region

    def test_requires_v08_a_real_region_and_respects_cooldown(self):
        state = self.fixture().state
        handler = ClimateCommands(state)
        order = {'id': '1', 'kind': 'governance', 'op': 'climate_intervention',
                 'target_region': self.home(state), 'mode': 'enhance'}
        with self.assertRaises(RuleViolation) as err:
            handler.prepare('n0', {**order, 'mode': 'boost'}, ctx(0))
        self.assertEqual(err.exception.code, 'ERR_SCHEMA')
        with self.assertRaises(RuleViolation) as err:
            handler.prepare('n1', order, ctx(0))
        self.assertEqual(err.exception.code, 'ERR_TECH_LOCKED')
        handler.prepare('n0', order, ctx(0)).commit()
        row, = state.climate.rows.values()
        self.assertEqual(row.end_cycle, 60)
        self.assertEqual(row.regions[0], self.home(state))
        self.assertEqual(len(row.regions), len(state.climate.downwind(self.home(state))))
        with self.assertRaises(RuleViolation) as err:
            handler.prepare('n0', {**order, 'id': '2'}, ctx(300))
        self.assertEqual(err.exception.code, 'ERR_CAPACITY_EXCEEDED')
        state.audit()

    def test_unfunded_cycle_has_no_effect_and_is_not_made_up(self):
        game = self.fixture()
        state = game.state
        ClimateCommands(state).prepare('n0', {'id': '1', 'kind': 'governance', 'op': 'climate_intervention',
                                              'target_region': self.home(state), 'mode': 'enhance'}, ctx(0)).commit()
        row, = state.climate.rows.values()
        state.frame.power_received.pop(f'climate:{row.id}', None)
        state.climate.advance(ctx(9))
        self.assertEqual(row.paid_cycles, 0)
        self.assertFalse(state.climate.next_effects)
        self.assertEqual(state.climate.visible('n0')['reports'][-1]['status'], 'UNFUNDED')
        state.audit()

    def test_paid_cycle_shifts_the_target_and_reverses_downwind(self):
        state = self.fixture().state
        ClimateCommands(state).prepare('n0', {'id': '1', 'kind': 'governance', 'op': 'climate_intervention',
                                              'target_region': self.home(state), 'mode': 'enhance'}, ctx(0)).commit()
        row, = state.climate.rows.values()
        state.frame.power_received[f'climate:{row.id}'] = 10000
        state.climate.advance(ctx(9))
        self.assertEqual(row.paid_cycles, 1)
        self.assertEqual(state.climate.next_effects[self.home(state)][1], Q(15, 100))
        downwind = row.regions[1]
        self.assertEqual(state.climate.next_effects[downwind][1], -Q(15, 100))
        # Unconsented spillover is hostile once: Credit -0.10 and Loss_action.
        self.assertEqual(state.credit['n0'], Q(85, 100) - Q(1, 10))
        self.assertGreater(state.climate.action_cycles[0]['n0'], 0)
        state.climate.begin_tick(ctx(10))
        self.assertEqual(state.climate.factor(self.home(state)), 1 + Q(15, 100))
        state.audit()

    def test_consent_covers_only_the_grantor_s_own_regions(self):
        state = self.fixture().state
        handler = ClimateCommands(state)
        foreign = state.initial.map.hubs['n1-h0'].region
        order = {'id': '1', 'kind': 'diplomacy', 'op': 'grant_climate_consent', 'target_nation': 'n0',
                 'region_ids': [foreign], 'duration_cycles': 50}
        with self.assertRaises(RuleViolation) as err:
            handler.prepare('n2', order, ctx(0))
        self.assertEqual(err.exception.code, 'ERR_NO_PERMISSION')
        handler.prepare('n1', order, ctx(0)).commit()
        self.assertTrue(state.climate.consented('n0', foreign, 10))
        self.assertFalse(state.climate.consented('n0', foreign, 50))
        state.audit()


class ContinuityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.catalog, cls.world = Catalog(), generate_candidate(1000)

    def fixture(self):
        game = opening_economy(self.catalog, initialize(self.catalog, self.world), total_ticks=30,
                               debug=False, commerce=True)
        state = game.state
        state.research['n0'].known.update({'R08', 'C10'})
        return game

    def backup_stock(self, state, hub='n0-h1'):
        stock = {Account('n0', hub, 'available', g): 2000
                 for g in ('computing_equipment', 'communication_equipment')}
        state.initial.inventory.transform({}, stock, reason='test_backup_stock',
                                          origins={a: Origin(a.owner, a.location) for a in stock})

    def test_backup_hub_needs_the_real_equipment_and_protects_theory(self):
        state = self.fixture().state
        handler = ContinuityCommands(state)
        order = {'id': '1', 'kind': 'research', 'op': 'set_backup_hub',
                 'primary_hub': 'n0-h0', 'backup_hub': 'n0-h1'}
        with self.assertRaises(RuleViolation) as err:
            handler.prepare('n0', order, ctx(0))
        self.assertEqual(err.exception.code, 'ERR_INSUFFICIENT_STOCK')
        self.backup_stock(state)
        handler.prepare('n0', order, ctx(0)).commit()
        self.assertTrue(state.continuity.protects('n0', 'n0-h0'))
        lab = 'n0/P075/0/line'
        project = state.research['n0'].projects[state.research['n0'].focus[lab]]
        project.theory_mU = 1000
        self.assertTrue(state.continuity.lab_lost(lab))
        self.assertEqual(project.theory_mU, 900)
        state.audit()

    def test_without_a_backup_a_lost_lab_keeps_only_30_percent(self):
        state = self.fixture().state
        lab = 'n0/P075/0/line'
        project = state.research['n0'].projects[state.research['n0'].focus[lab]]
        project.theory_mU = 1000
        project.validation_progress_mU = 500
        self.assertFalse(state.continuity.lab_lost(lab))
        self.assertEqual((project.theory_mU, project.validation_progress_mU), (300, 0))

    def test_cycle_end_scan_skips_idle_slots_and_loses_a_destroyed_lab_once(self):
        import dataclasses
        state = self.fixture().state
        lab = 'n0/P075/0/line'
        template = state.initial.lines[lab]
        idle = dataclasses.replace(template, id='n0/idle_fixture/line',
                                   recipe=dataclasses.replace(template.recipe, id='__idle__'))
        state.initial.lines[idle.id] = idle  # unconfigured slot: no catalog recipe
        project = state.research['n0'].projects[state.research['n0'].focus[lab]]
        project.theory_mU = 1000
        end = NS(tick=9, cycle=0, clock=ClockConfig())
        state.continuity.scan_labs(end)  # must not raise on '__idle__'
        self.assertEqual(project.theory_mU, 1000)
        state.initial.assets[lab.rsplit('/line', 1)[0]].health_mU = 0  # the facility shell is destroyed
        state.continuity.scan_labs(end)
        self.assertEqual(project.theory_mU, 300)
        self.assertNotIn(lab, state.research['n0'].focus)
        state.continuity.scan_labs(NS(tick=19, cycle=1, clock=ClockConfig()))
        self.assertEqual(project.theory_mU, 300)  # once per loss

    def test_conditional_plan_requires_c10_and_an_ai_core(self):
        state = self.fixture().state
        handler = ContinuityCommands(state)
        order = {'id': '1', 'kind': 'governance', 'op': 'conditional_plan', 'hub_id': 'n0-h0',
                 'metric': 'hub_stock', 'ref_id': 'food', 'comparator': '<=', 'threshold_mU': 10**9,
                 'action': {'kind': 'logistics', 'op': 'market_bid', 'region_market': 'n0-h0', 'good': 'food',
                            'qty_mU': 1000, 'limit_price_mU': 100, 'expire_cycles': 1},
                 'cooldown_ticks': 5}
        with self.assertRaises(RuleViolation) as err:
            handler.prepare('n0', {**order, 'cooldown_ticks': 4}, ctx(0))
        self.assertEqual(err.exception.code, 'ERR_SCHEMA')
        with self.assertRaises(RuleViolation) as err:
            handler.prepare('n0', order, ctx(0))
        self.assertEqual(err.exception.code, 'ERR_SITE_INCOMPATIBLE')
        state.line_options.setdefault('n0/P070/0/line', {})['addons'] = ('ai_core',)
        handler.prepare('n0', order, ctx(0)).commit()
        plan, = state.continuity.plans.values()
        self.assertEqual(plan.hub, 'n0-h0')
        self.assertIn('n0', state.continuity.due(ctx(5)))
        self.assertNotIn('n0', state.continuity.due(ctx(6)))  # only the window's first tick
        state.cyber.plan_freeze['n0'] = 20
        self.assertFalse(state.continuity.due(ctx(10)))
        state.audit()

    def test_plan_fires_once_per_window_and_respects_its_cooldown(self):
        game = self.fixture()
        state = game.state
        state.line_options.setdefault('n0/P070/0/line', {})['addons'] = ('ai_core',)
        order = {'id': '1', 'kind': 'governance', 'op': 'conditional_plan', 'hub_id': 'n0-h0',
                 'metric': 'hub_stock', 'ref_id': 'food', 'comparator': '<=', 'threshold_mU': 10**9,
                 'action': {'kind': 'logistics', 'op': 'market_ask', 'hub_id': 'n0-h0', 'good': 'food',
                            'qty_mU': 1000, 'limit_price_mU': 10**6, 'expire_cycles': 1},
                 'cooldown_ticks': 15}
        game.step({'n0': [order]})
        plan, = state.continuity.plans.values()
        self.assertEqual(plan.fired, 0)
        game.step()
        game.step()
        game.step()
        game.step()
        game.step()  # tick 5: window start
        self.assertEqual(plan.fired, 1)
        fired_tick = plan.last_fired_tick
        for _ in range(5):
            game.step()
        self.assertEqual(plan.fired, 1)  # cooldown of 15 ticks still running
        self.assertEqual(plan.last_fired_tick, fired_tick)
        self.assertEqual(state.continuity.visible('n0')['reports'][-1]['status'], 'EXECUTED')
        state.audit()


if __name__ == '__main__':
    unittest.main()
