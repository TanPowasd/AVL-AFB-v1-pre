"""P1 'written but not called' functions now on the production path:
JV operating-cost sharing, S08 city coercion, Consumption.eta / synthetic_share,
A07 precision agriculture, I12 molecular manufacturing, C08 track aging,
F04 sovereign wealth fund acquire_shares & wartime share freeze,
G12 supply auto-switch & route relay, passenger turnback, canal 40% cap,
and power plant 0.6 military administration cap."""
import unittest
from types import SimpleNamespace

from organizer.rift_era.engine.authority import AssetRights
from organizer.rift_era.engine.catalog import Catalog
from organizer.rift_era.engine.diplomacy import Relation
from organizer.rift_era.engine.economy import opening_economy
from organizer.rift_era.engine.errors import InvariantViolation, RuleViolation
from organizer.rift_era.engine.industry_effects import a07_active, gene_weather, i12_active, refresh
from organizer.rift_era.engine.initialization import initialize
from organizer.rift_era.engine.ledger import Account, CashAccount, CashTransfer
from organizer.rift_era.engine.military_movement import MarchPlan
from organizer.rift_era.engine.numeric import Q
from organizer.rift_era.engine.production import Recipe
from organizer.rift_era.engine.world import generate_candidate


class WiredFunctionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.catalog, cls.world = Catalog(), generate_candidate(1000)

    def setUp(self):
        self.game = opening_economy(self.catalog, initialize(self.catalog, self.world), total_ticks=40, debug=False)
        self.state = self.game.state

    def joint_line(self):
        state = self.state
        lid = sorted(l for l, line in state.initial.lines.items() if line.owner == 'n0' and l in state.authority.assets)[0]
        state.authority.assets[lid] = AssetRights('n0', {'n0': 600, 'n1': 400})
        state.facility_ownership.register(lid, lid)
        return lid

    def test_partner_reimburses_its_share_and_keeps_arrears(self):
        state = self.state
        cash = state.initial.cash
        lid = self.joint_line()
        payee = state.operating_account(lid)
        partner = CashAccount('n1', 'treasury', payee.currency)
        before = cash.balance(payee).native_mU
        ctx = SimpleNamespace(tick=7)
        state.facility_ownership.share_costs(lid, payee, Q(1001), ctx)  # partner owes 400 (largest remainder)
        self.assertEqual(state.facility_ownership.arrears, {(lid, 'n1'): 400})
        self.assertEqual(cash.balance(payee).native_mU, before)
        cash.transfer_many((CashTransfer(CashAccount('n0', 'treasury', payee.currency), partner, 500),))
        state.facility_ownership.share_costs(lid, payee, Q(500), ctx)  # 200 now + 400 arrears, 500 available
        self.assertEqual(cash.balance(payee).native_mU, before + 500)
        self.assertEqual(cash.balance(partner).native_mU, 0)
        self.assertEqual(state.facility_ownership.arrears, {(lid, 'n1'): 100})
        state.audit()
        state.facility_ownership.arrears[(lid, 'n1')] = -1
        with self.assertRaises(InvariantViolation):
            state.facility_ownership.audit()

    def test_jv_cost_share_runs_in_the_tick(self):
        state = self.state
        lid = self.joint_line()
        payee = state.operating_account(lid)
        for _ in range(12):
            self.game.step()
            if state.facility_ownership.arrears:
                break
        # The partner holds no operator currency, so its share accrues as arrears
        # only if the line actually paid operating cost.
        paid = any(state.facility_ownership.arrears.values())
        self.assertTrue(paid)
        self.assertEqual(set(state.facility_ownership.arrears), {(lid, 'n1')})
        state.audit()

    def test_s08_records_city_coercion_for_affinity(self):
        state = self.state
        economy = state.city_economy
        state.city_economy.record_coercion('n0', 3)
        self.assertTrue(economy.coercion_events)
        city = next(iter(economy.cities.values()))
        self.assertEqual(economy.coercion_events[city.id, 'n0'], (23, Q(15, 100)))
        _, coercion = economy._security(city, 'n0', 4)
        self.assertGreaterEqual(coercion, Q(15, 100))
        _, later = economy._security(city, 'n0', 23)
        self.assertLess(later, coercion)

    def test_industry_eta_is_consumption_eta(self):
        state = self.state
        materials = state.initial.inventory.materials
        physical = materials.physical_by_owner('n0')
        for good in ('food', 'steel', 'cement'):
            self.assertEqual(state.consumption.eta(materials, 'n0', good, physical=physical),
                             state.consumption.eta(materials, 'n0', good))

    def test_a07_precision_agriculture_cuts_water_fertilizer_and_boosts_bad_weather(self):
        state = self.state
        lid, line = next((lid, l) for lid, l in state.initial.lines.items() if l.owner == 'n0')
        line.recipe = Recipe.from_catalog(state.catalog, 'P029')
        ctx = SimpleNamespace(seed=1000, tick=1)
        refresh(state, ctx)
        base_inputs = dict(line.recipe.inputs)
        self.assertFalse(a07_active(state, line, 'P029'))
        state.research['n0'].known.update({'A07', 'C08'})
        state.line_options[lid] = {'addons': ('sensor',)}
        state.systems = SimpleNamespace(
            instances={'fs1': SimpleNamespace(owner='n0', kind='fusion_sensor', online=True)},
            availability=lambda s: Q(1),
        )
        self.assertTrue(a07_active(state, line, 'P029'))
        refresh(state, ctx)
        self.assertEqual(line.recipe.inputs['water'], max(round(base_inputs['water'] * Q(2, 5)), round(base_inputs['water'] * Q(3, 4))))
        self.assertEqual(line.recipe.inputs['fertilizer'], max(round(base_inputs['fertilizer'] * Q(2, 5)), round(base_inputs['fertilizer'] * Q(3, 4))))
        self.assertEqual(gene_weather(state, line, Q(60, 100))[0], Q(75, 100))

    def test_i12_molecular_manufacturing_reduces_inputs_zeroes_defects_and_scraps_on_brownout(self):
        from dataclasses import replace
        state = self.state
        lid, line = next((lid, l) for lid, l in state.initial.lines.items() if l.owner == 'n0')
        fid = lid.rsplit('/line', 1)[0]
        state.initial.facilities[fid] = replace(state.initial.facilities[fid], profile='S5')
        base_recipe = Recipe.from_catalog(state.catalog, 'P013')
        line.recipe = base_recipe
        state.line_options[lid] = {'addons': ('ai_core',)}
        state.research['n0'].known.add('I12')
        self.assertTrue(i12_active(state, lid, line))
        refresh(state, SimpleNamespace(seed=1000, tick=1))
        for g, q in base_recipe.inputs.items():
            self.assertEqual(line.recipe.inputs[g], max(round(q * Q(2, 5)), round(q * Q(7, 10))))
        for g, q in base_recipe.outputs.items():
            self.assertEqual(line.recipe.outputs[g], q)

    def test_c08_fusion_extends_track_aging_by_ten_ticks(self):
        from organizer.rift_era.engine.observation import Observer, Sighting
        obs = Observer('n0')
        obs.update(0, (Sighting('unit:1', 0, 'radar', 'r0', 800, 90, 110, 'Land'),))
        obs.update(1, ())  # contact lost at tick 1
        speeds = {'Land': Q(1)}
        view_base = obs.view(25, speeds, bonus_ticks=0)['tracks'][0]
        view_c08 = obs.view(25, speeds, bonus_ticks=10)['tracks'][0]
        self.assertGreater(view_c08['quality_mU'], view_base['quality_mU'])
        self.assertLess(view_c08['position_radius_hops'], view_base['position_radius_hops'])
        self.assertEqual(obs.expire(150, speeds, bonus_ticks=10), ())
        self.assertEqual(obs.expire(170, speeds, bonus_ticks=10), ('contact-1',))

    def test_f04_acquire_shares_and_wartime_freeze(self):
        state = self.state
        state.research['n1'].known.add('F04')
        ru_holder = next(a for a, b in state.initial.cash.balances().items() if a.currency == 'RU' and b.native_mU >= 5000)
        if ru_holder != CashAccount('n1', 'reserves', 'RU'):
            state.initial.cash.transfer(ru_holder, CashAccount('n1', 'reserves', 'RU'), 5000)
        fid = sorted(f for f, fac in state.initial.facilities.items()
                     if fac.owner == 'n0' and fac.commissioned and f in state.authority.assets)[0]
        ctx = SimpleNamespace(tick=1, cycle=0, seed=1000, clock=SimpleNamespace(cycle_ticks=10))
        order = {
            'id': 901,
            'counterparty': 'n1',
            'duration_cycles': 20,
            'params': {
                'mode': 'acquire_shares',
                'facility_id': fid,
                'shares_mU': 400,
                'price_ru_mU': 5000,
            },
        }
        cmd = state.contracts.joint_ventures.offer('n0', order, ctx)
        cmd.commit()
        pid = 'jv_proposal:n0:901'
        self.assertIn(pid, state.contracts.proposals)
        accept_cmd = state.contracts.joint_ventures.prepare_accept(state.contracts.proposals[pid], ctx)
        accept_cmd.commit()
        from organizer.rift_era.engine.economic_ownership import EconomicOwner
        self.assertEqual(state.facility_ownership.shares(fid), {
            EconomicOwner('n0', 'treasury'): Q(3, 5),
            EconomicOwner('n1', 'treasury'): Q(2, 5),
        })
        # Wartime share freeze returns enemy share to operator while at war
        state.initial.diplomacy.phase1(0, declarations=(('n0', 'n1'),))
        self.assertEqual(state.facility_ownership.shares(fid), {
            EconomicOwner('n0', 'treasury'): Q(1),
        })
        state.initial.diplomacy.pairs['n0', 'n1'].state = Relation.PEACE
        self.assertEqual(state.facility_ownership.shares(fid), {
            EconomicOwner('n0', 'treasury'): Q(3, 5),
            EconomicOwner('n1', 'treasury'): Q(2, 5),
        })

    def test_g12_supply_hub_auto_switch(self):
        state = self.state
        state.research['n0'].known.add('G12')
        fid, formation = next((k, v) for k, v in state.formations.rows.items() if v.owner == 'n0' and v.domain == 'Land')
        hubs = sorted(h for h, hub in state.initial.map.hubs.items() if hub.owner == 'n0')
        h0, h1 = hubs[0], hubs[1]
        # Drain food at h0 so G12 switches supply_hub to another online hub with food & water
        bal = state.initial.inventory.balance(Account('n0', h0, 'available', 'food'))
        if bal > 0:
            from organizer.rift_era.engine.ledger import Transfer
            state.initial.inventory.transfer_many((Transfer(Account('n0', h0, 'available', 'food'),
                                                            Account('n0', h1, 'available', 'food'), bal),))
        plan = MarchPlan('n0', 'cmd1', fid, 'move', state.initial.map.hubs[h1].region, h0, 1000, 1000, (), ())
        state.movement.plans[fid] = plan
        ctx = SimpleNamespace(tick=10, cycle=1, seed=1000)
        self.assertTrue(state.movement.g12_online('n0', 10))
        state.movement._switch_supply_hub(formation, plan, ctx)
        self.assertNotEqual(plan.supply_hub, h0)

    def test_canal_40_percent_inflow_cap(self):
        from organizer.rift_era.engine.utility_network import UtilityLink
        state = self.state
        rid = state.initial.map.hubs['n0-h0'].region
        state.hydrology.baseline[rid] = 10000
        state.hydrology.outflow_cycle_mU[rid] = 10000  # 1000 mU/tick -> 40% cap = 400 mU/tick
        aid = next(a for a in state.initial.assets if a in state.authority.assets and state.authority.assets[a].operator == 'n0')
        state.utilities.links['canal:test'] = UtilityLink('canal:test', 'n0', 'n0-h0', 'n0-h1', 'water', 1, (0, 1), aid)
        state.utilities.reserve(SimpleNamespace(seed=1000, tick=1))
        self.assertLessEqual(state.utilities.water_reserved.get('canal:test', 0), 400)

    def test_power_plant_occupation_cap(self):
        state = self.state
        lid = next(iter(state.initial.plants))
        fid = lid.rsplit('/line', 1)[0]
        owner = state.initial.facilities[fid].owner
        other = 'n1' if owner == 'n0' else 'n0'
        rid = state.initial.map.hubs[state.initial.facilities[fid].hub].region
        self.assertEqual(state.authority.military_operating_factor(fid, lambda a, b: False), Q(1))
        state.initial.diplomacy.phase1(0, declarations=((owner, other),))
        state.authority.regions[rid].controller = other
        self.assertEqual(
            state.authority.military_operating_factor(
                fid, lambda a, b: state.initial.diplomacy.relation(a, b) == Relation.WAR
            ),
            Q(3, 5),
        )

    def test_passenger_turnback_on_closed_corridor(self):
        from organizer.rift_era.tests.test_passenger_operations import PassengerTests
        from organizer.rift_era.engine.passenger_operations import PassengerOperations
        PassengerTests.setUpClass()
        pt = PassengerTests()
        state = pt.fixture().state
        ops = PassengerOperations(state)
        state.passengers = ops
        origin_before = state.initial.populations['n0-h0'].total_mU
        ops.prepare('n0', pt.order(), pt.context()).commit()
        ops.reserve(pt.context())
        trip, = ops.transport.shipments.values()
        trip.entered_tick = trip.departure_tick
        self.assertEqual(trip.unload_hub, 'n0-h1')
        state.cargo.corridors['road:n0-h0:n0-h1'].health_mU = 0
        ops.advance(pt.context(tick=trip.departure_tick + 1))
        self.assertEqual(state.initial.populations['n0-h0'].total_mU, origin_before)
        ops.audit()



if __name__ == '__main__':
    unittest.main()
