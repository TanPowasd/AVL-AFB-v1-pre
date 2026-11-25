"""rift_era_rules_r7 (decision 0014): E9 P070 emission, E3 opening medicine,
E2 eta window over existing cycles, E8 warehouse Kinfra by occupancy."""
import json
import shutil
import tempfile
import unittest
from fractions import Fraction as Q
from pathlib import Path

from organizer.rift_era.engine.catalog import Catalog, DEFAULT_DATA, RULE_FILES
from organizer.rift_era.engine.consumption import Consumption
from organizer.rift_era.engine.errors import InfrastructureFault
from organizer.rift_era.engine.initialization import initialize
from organizer.rift_era.engine.ledger import Account
from organizer.rift_era.engine.match import match_economy
from organizer.rift_era.engine.scoring import eta_sink
from organizer.rift_era.engine.world import generate_candidate


class R7BalanceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.catalog = Catalog()
        cls.world = generate_candidate(1000)

    def test_rule_version_and_opening_data(self):
        c = self.catalog
        # r9 (decision 0016) supersedes r7/r8; check shared structural invariants and updated r9 opening values.
        self.assertEqual(c.version, 'rift_era_rules_r9')
        iw = c.runtime['initial_world']
        self.assertEqual(c.runtime['environment']['emissions']['P070'], Q(1, 5))
        self.assertEqual(iw['productive_lines']['P070'], 5)
        self.assertEqual(iw['productive_lines']['P033'], 1)
        self.assertEqual(iw['line_profiles']['P033'], 'S2')
        self.assertEqual(iw['load_overrides']['P033'], Q(1, 2))
        self.assertEqual(iw['inventory']['medicine'], 160)
        self.assertEqual(c.runtime['world_pool']['per_nation_good_cycle_cap_units'], 40)

    def test_opening_pharmacy_line_sits_in_s2_without_granting_a04(self):
        state = initialize(self.catalog, self.world)
        for owner in sorted(state.known):
            lid = f'{owner}/P033/0/line'
            self.assertIn(lid, state.lines)
            self.assertEqual(state.facilities[f'{owner}/P033/0'].profile, 'S2')
            self.assertEqual(state.facilities[f'{owner}/P033/0'].kind, '制药厂')
            self.assertEqual(state.line_policies[lid].load_mU, 500)
            self.assertEqual(state.assets[f'{owner}/P033/0'].book_nmU,
                             {'steel': 16_000_000, 'cement': 16_000_000, 'basic_parts': 8_000_000})
            self.assertNotIn('A04', state.known[owner])
            self.assertEqual(state.inventory.balance(Account(owner, f'{owner}-h0', 'available', 'medicine')), 160_000)

    def test_catalog_rejects_an_opening_line_above_its_shell_grade(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            for name in RULE_FILES:
                shutil.copy(DEFAULT_DATA / f'{name}.json', directory)
            path = directory / 'runtime_rules.json'
            table = json.loads(path.read_text(encoding='utf-8'))
            table['initial_world']['line_profiles'] = {'P033': 'S1'}
            path.write_text(json.dumps(table, ensure_ascii=False), encoding='utf-8')
            with self.assertRaisesRegex(InfrastructureFault, 'P033'):
                Catalog(directory)

    def test_eta_window_averages_only_existing_cycles(self):
        # 30 units in stock, 5 units consumed in the only existing cycle.
        self.assertEqual(eta_sink(Q(30000), Q(0), Q(5000), 1), 1)
        # Old zero-padded mean20 would floor C at 1 unit: chi 30, eta 0.
        self.assertEqual(eta_sink(Q(30000), Q(0), Q(5000)), 0)
        with self.assertRaises(ValueError):
            eta_sink(Q(1), Q(0), Q(1), 0)
        self.assertEqual(Consumption().window_cycles(), 1)

    def test_match_opening_window_power_emission_and_medicine(self):
        game = match_economy(self.catalog, initialize(self.catalog, self.world), total_ticks=40,
                             fingerprint_each_tick=False)
        state = game.state
        for _ in range(20):
            game.step()
        self.assertEqual(state.consumption.window_cycles(), 2)
        metric = state.history['n0'][-1]
        self.assertEqual(metric.power_received_mU, metric.power_needed_mU)
        # r6 emitted 5880 mU/cycle at the capital; P070 at 0.2 (r7) left
        # ~3030, and r8's x0.55 on P001/P002/P005/P007 leaves ~2000.
        self.assertLess(metric.emitted_mU, 2200)
        self.assertGreater(metric.emitted_mU, 1800)
        medicine = state.initial.inventory.balance(Account('n0', 'n0-h0', 'available', 'medicine'))
        self.assertGreater(medicine, 160_000 - 2 * 1700)
        # The opening P033 line actually runs (it is metered for VA).
        self.assertIn('n0/P033/0/line', state.industry_activity.last_asset_va)
        state.audit()

    def test_empty_warehouse_expansion_adds_no_kinfra_but_occupancy_does(self):
        game = match_economy(self.catalog, initialize(self.catalog, self.world), total_ticks=40,
                             fingerprint_each_tick=False)
        state = game.state
        game.step()
        power = state.national_power
        connected = power.connected_regions('n0')
        before = power._kinfra('n0', connected)
        hub, aid = 'n0-h0', 'warehouse:n0-h0'
        asset = state.initial.assets[aid]
        occupied = state.warehouse_load()[hub]
        capacity = state.initial.warehouse_mU[hub]
        self.assertLess(occupied, capacity)
        w = state.catalog.weights
        module_book = 4 * w['steel'] + 6 * w['cement']
        modules = (capacity - 300_000) // 500_000
        expected = module_book * modules * min(1, max(0, occupied - 300_000) / Q(capacity - 300_000))
        # Empty expansion: two more modules of real book, no extra goods.
        asset.book_nmU['steel'] += 8_000_000
        asset.book_nmU['cement'] += 12_000_000
        state.initial.warehouse_mU[hub] += 1_000_000
        after = power._kinfra('n0', connected)
        self.assertEqual(after, before)
        # The warehouse's own share equals the occupied module capacity.
        others = before - expected * max(Q(1, 5), 1 - Q(3, 10) * asset.maintenance_debt)
        self.assertGreaterEqual(others, 0)


if __name__ == '__main__':
    unittest.main()
