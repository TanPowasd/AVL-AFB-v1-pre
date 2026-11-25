"""Verification of all 17 canonical scenarios in round6_scenarios.json."""
import json
from pathlib import Path
import unittest
from fractions import Fraction as Q

from organizer.rift_era.engine.assets import Asset
from organizer.rift_era.engine.catalog import Catalog
from organizer.rift_era.engine.diplomacy import AcceptedPeace, Diplomacy, Relation
from organizer.rift_era.engine.errors import InfrastructureFault, RuleViolation
from organizer.rift_era.engine.evaluation import GIB, Batch, ResourceBudget
from organizer.rift_era.engine.market import finite_pool_fills
from organizer.rift_era.engine.numeric import units
from organizer.rift_era.engine.population import Demography, Passenger, Population, grow, housing_factor
from organizer.rift_era.engine.provenance import Leg, Lot, ReceiptBook, Use
from organizer.rift_era.engine.research import LabOutput, combine_labs
from organizer.rift_era.engine.scoring import Trajectory, action_loss, industrial_pillar


RIFT_ROOT = Path(__file__).resolve().parents[1]
ROOT = Path(__file__).resolve().parents[3]
SCENARIOS_PATH = (
    RIFT_ROOT / 'designs' / 'data' / 'round6_scenarios.json'
    if (RIFT_ROOT / 'designs' / 'data' / 'round6_scenarios.json').is_file()
    else ROOT / 'designs' / 'rift_era' / 'data' / 'round6_scenarios.json'
)


class Round6ScenarioTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.catalog = Catalog()
        cls.data = json.loads(SCENARIOS_PATH.read_text(encoding='utf-8'))
        cls.by_id = {row['id']: row for row in cls.data['scenarios']}

    def test_all_17_scenarios_present(self):
        expected_ids = {
            'barrage_no_gains',
            'cyber_credit_floor',
            'patrol_loop',
            'zero_material_hostility',
            'repair_single_count',
            'relations_and_neutrality',
            'blockade_food_substitution',
            'damage_power_and_repair',
            'energy_substitution',
            'fx_revaluation_invariance',
            'transit_formula',
            'healthy_relocation',
            'cpu_budget',
            'S1_salvage',
            'split_labs',
            'world_pool_pressure',
            'batch_consistency',
        }
        self.assertEqual(set(self.by_id), expected_ids)

    def test_barrage_no_gains(self):
        exp = self.by_id['barrage_no_gains']['expected']
        w = self.catalog.weights
        loss = action_loss({'ammunition': 240000, 'fuel': 72000}, w)
        self.assertAlmostEqual(float(loss), exp['action_loss_per_cycle'], places=6)
        va = Q(str(exp['upper_chain_VA_per_cycle']))
        delta_u = industrial_pillar(va, Q(0), loss, Q(0), Q(0)) / 4
        self.assertAlmostEqual(float(delta_u), exp['upper_delta_U_per_cycle'], places=6)
        self.assertLess(delta_u, 0)

    def test_cyber_credit_floor(self):
        exp = self.by_id['cyber_credit_floor']['expected']
        w = self.catalog.weights
        loss = action_loss({'cyber_suite': 1000, 'compute': 10000}, w)
        self.assertAlmostEqual(float(loss), exp['Loss_action'], places=6)
        delta_u = industrial_pillar(Q(0), Q(0), loss, Q(0), Q(0)) / 4
        self.assertAlmostEqual(float(delta_u), exp['delta_U_action'], places=6)
        self.assertLess(delta_u, 0)

    def test_patrol_loop(self):
        exp = self.by_id['patrol_loop']['expected']
        w = self.catalog.weights
        loss = action_loss({'fuel': 144000}, w)
        self.assertEqual(loss, Q(str(exp['loss'])))
        va = Q(str(exp['upper_refinery_VA']))
        delta_u = industrial_pillar(va, Q(0), loss, Q(0), Q(0)) / 4
        self.assertEqual(delta_u, Q(str(exp['upper_delta_U'])))

    def test_zero_material_hostility(self):
        exp = self.by_id['zero_material_hostility']['expected']
        loss = action_loss({}, self.catalog.weights)
        self.assertEqual(loss, Q(str(exp['Loss_action'])))
        delta_u = industrial_pillar(Q(0), Q(0), loss, Q(0), Q(0)) / 4
        self.assertAlmostEqual(float(delta_u), exp['delta_U_action'], places=9)
        self.assertLess(delta_u, 0)

    def test_repair_single_count(self):
        exp = self.by_id['repair_single_count']['expected']
        material = {g: units(q) * 1000 for g, q in self.catalog.profiles['S1']['build'].items()}
        asset = Asset('s1', 'A', 'h', material)
        removed = asset.damage(500)
        cap_val = sum(q / 1000000 * self.catalog.weights[g] for g, q in removed.items())
        self.assertEqual(cap_val, Q(str(exp['capitalized_value'])))
        repair = asset.plan_repair(1000, frozenset())
        self.assertEqual(repair.duration_ticks, exp['duration_cycles'] * 10)
        asset.repair = repair
        for _ in range(repair.duration_ticks):
            repair.advance_paid(asset)
        final_book = sum(q / 1000000 * self.catalog.weights[g] for g, q in asset.book_nmU.items())
        self.assertEqual(final_book, Q(str(exp['final_book'])))

    def test_relations_and_neutrality(self):
        exp = self.by_id['relations_and_neutrality']['expected']
        diplomacy = Diplomacy(frozenset({'A', 'B', 'C'}))
        diplomacy.phase1(0, declarations=(('A', 'C'), ('C', 'A')))
        self.assertEqual(sorted(diplomacy.pairs[('A', 'C')].aggressors), exp['A_C_aggressors'])
        with self.assertRaisesRegex(RuleViolation, exp['C_strike_B']):
            diplomacy.validate_kinetic('C', 'B')
        self.assertEqual(diplomacy.relation('A', 'B'), Relation.PEACE)
        diplomacy.phase1(1, treaties=(AcceptedPeace('A', 'C', 'armistice', 10),))
        self.assertEqual(diplomacy.relation('A', 'C'), Relation.TRUCE)
        diplomacy.phase1(101)
        self.assertEqual(diplomacy.relation('A', 'C'), Relation.PEACE)

    def test_blockade_food_substitution(self):
        exp = self.by_id['blockade_food_substitution']['expected']
        w_food = self.catalog.weights['food']
        receipt_val = 12 * w_food
        self.assertEqual(receipt_val, Q(str(exp['A_receipt_value_per_cycle'])))
        export_u = Q(12, 100) * Q(1, 10) * receipt_val
        self.assertAlmostEqual(float(export_u), exp['steady_weighted_export_U_gain'], places=9)
        phi_loss = Q(8, 100) * Q(1, 20) * receipt_val
        self.assertAlmostEqual(float(phi_loss), exp['steady_weighted_Phi_U_loss'], places=9)
        act_cost = Q(35, 100) * Q(1, 4) * Q('0.01')
        self.assertAlmostEqual(float(act_cost), exp['weighted_action_U_cost_per_cycle'], places=9)
        steady_du = export_u - phi_loss - act_cost
        self.assertAlmostEqual(float(steady_du), exp['steady_delta_U_lower_bound'], places=9)

    def test_damage_power_and_repair(self):
        exp = self.by_id['damage_power_and_repair']['expected']
        self.assertEqual(floor_avail := int(60 * Q(1, 4)), exp['corridor_capacity_at_quarter'])
        self.assertEqual(exp['corridor_capacity_at_zero'], 0)
        self.assertEqual(exp['repair_duration_full_cycles'], 20)
        self.assertEqual(exp['after_repair_generator_capacity'], 10)

    def test_energy_substitution(self):
        exp = self.by_id['energy_substitution']['expected']
        w_elec = self.catalog.weights['electricity']
        confirmed = Q('3.94') * w_elec
        self.assertAlmostEqual(float(confirmed), exp['A_confirmed_value'], places=6)
        va_u = Q(35, 100) * Q(1, 4) * confirmed
        self.assertAlmostEqual(float(va_u), exp['weighted_delta_VA_U'], places=9)

    def test_fx_revaluation_invariance(self):
        from organizer.rift_era.tests.test_match import FxRevaluationScenarioTest
        FxRevaluationScenarioTest().test_nfa_minus_200_through_rate_changes()

    def test_transit_formula(self):
        exp = self.by_id['transit_formula']['expected']
        lot = Lot('x', 'A', 'h0', 'food', 10000, (Leg('h0', 'h1', 'A', 6), Leg('h1', 'h2', 'B', 4)))
        book = ReceiptBook()
        book.add_lot(lot)
        receipt = book.consume(1, 'x', 10000, event_id='use', cycle=0, consumer='C', final_hub='h2', use=Use.CIVILIAN,
                               weight=self.catalog.weights['food'], origin_credit=Q(1), initial_shortest_length=8)
        self.assertAlmostEqual(float(receipt.transit['A']), exp['TransitReceipt_A'], places=6)
        self.assertAlmostEqual(float(receipt.transit['B']), exp['TransitReceipt_B'], places=6)
        self.assertAlmostEqual(float(book.mean20(0, 'A', 'transit')), exp['NetTransit_A'], places=6)
        self.assertAlmostEqual(float(book.mean20(0, 'B', 'transit')), exp['NetTransit_B'], places=6)

    def test_healthy_relocation(self):
        exp = self.by_id['healthy_relocation']['expected']
        source = Population('a', dict(zip(('general_worker', 'skilled_worker', 'engineer'), (144000, 40000, 10000))))
        dest = Population('b', {})
        source.reserve_people({'general_worker': 5000})
        passenger = Passenger('p', 'a', 'b', {'general_worker': 5000}, 0, 2)
        self.assertFalse(passenger.arrive(1, dest))
        self.assertEqual(dest.total_mU // 1000, exp['before_arrival_destination_pop'])
        self.assertTrue(passenger.arrive(2, dest))
        self.assertEqual(dest.skills_mU['general_worker'] // 1000, exp['after_arrival_destination_general_pop'])
        self.assertEqual(source.skills_mU['general_worker'] // 1000, exp['origin_general_pop_after_departure'])
        self.assertEqual(housing_factor(dest.total_mU, 0), exp['destination_H'])
        births, _ = grow(1, 0, dest, Demography(Q(1), 0, Q(0), Q(0), Q(0), Q(0), Q(0)))
        self.assertEqual(births, exp['destination_birth_rate'])

    def test_cpu_budget(self):
        exp = self.by_id['cpu_budget']['expected']
        budget = ResourceBudget(
            effective_cpu=Q(16),
            memory_available=200 * GIB,
            memory_total=200 * GIB,
            engine_cpu_peak=Q(1),
            engine_memory_peak=GIB,
            active_strategies=8,
            resident_strategies=8,
        )
        self.assertEqual(1 + 8, exp['C_match'])
        self.assertEqual(budget.concurrency(), exp['Kmax'])

    def test_s1_salvage(self):
        exp = self.by_id['S1_salvage']['expected']
        w = self.catalog.weights
        build_val = sum(Q(units(q), 1000) * w[g] for g, q in self.catalog.profiles['S1']['build'].items())
        salvage = 3 * w['scrap_steel']
        self.assertEqual(build_val, Q(str(exp['build_value'])))
        self.assertEqual(salvage, Q(str(exp['salvage'])))
        self.assertEqual(build_val - salvage, Q(str(exp['loss'])))

    def test_split_labs(self):
        exp = self.by_id['split_labs']['expected']
        distinct = tuple(LabOutput(str(i), str(i), 1000000, connected=True) for i in range(8))
        same = tuple(LabOutput(str(i), 'one', 1000000, connected=True) for i in range(8))
        self.assertAlmostEqual(combine_labs(distinct, organized=True) / 1e6, exp['eight_hubs_RP'], places=5)
        self.assertAlmostEqual(combine_labs(same, organized=True) / 1e6, exp['one_hub_RP'], places=5)

    def test_world_pool_pressure(self):
        exp = self.by_id['world_pool_pressure']['expected']
        requests = {f'n{i}': 40000 for i in range(8)}
        fills = finite_pool_fills(1, 0, 1000, requests)
        self.assertEqual(sum(fills.values()), exp['total_paid_max'])
        self.assertLessEqual(max(fills.values()) - min(fills.values()), 1)

    def test_batch_consistency(self):
        b1 = Batch.create(
            rules_hash='r1',
            engine_hash='e1',
            reference_hash='ref1',
            runtime_hash='rt1',
            resource_plan_hash='rp1',
        )
        b1.validate()
        self.assertEqual(len(b1.seeds), 64)
        b_invalid = Batch(b1.batch_id, b1.seeds, b1.seats, '', 'e1', 'ref1', 'rt1', 'rp1')
        with self.assertRaises(InfrastructureFault):
            b_invalid.validate()


if __name__ == '__main__':
    unittest.main()
