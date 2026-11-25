"""Complete-match assembly, five-pillar collection and finalizer (P0-1/P0-2)."""
import os
import subprocess
import sys
import unittest
from fractions import Fraction
from pathlib import Path

from organizer.rift_era.engine.catalog import Catalog
from organizer.rift_era.engine.economy import opening_economy
from organizer.rift_era.engine.errors import InfrastructureFault, InvariantViolation
from organizer.rift_era.engine.initialization import initialize
from organizer.rift_era.engine.match import finalize_match, match_economy
from organizer.rift_era.engine.national_power import action_sources
from organizer.rift_era.engine.scoring import Trajectory
from organizer.rift_era.engine.world import generate_candidate

ROOT = Path(__file__).resolve().parents[3]


class PracticeTrajectoryTest(unittest.TestCase):
    def test_official_trajectory_unchanged(self):
        trajectory = Trajectory()
        self.assertTrue(trajectory.official)
        self.assertEqual(trajectory.era_length, 720)
        self.assertEqual(trajectory.last40.maxlen, 40)

    def test_practice_trajectory_weights(self):
        trajectory = Trajectory(8)
        for cycle in range(8):
            trajectory.commit(cycle, Fraction(cycle))
        self.assertEqual(trajectory.era_means(), (Fraction(1, 2), Fraction(5, 2), Fraction(9, 2), Fraction(13, 2)))
        self.assertEqual(trajectory.end_mean(), Fraction(13, 2))
        expected = (Fraction(8, 100) / 2 + Fraction(14, 100) * 5 / 2 + Fraction(24, 100) * 9 / 2
                    + Fraction(29, 100) * 13 / 2 + Fraction(13, 2) / 4)
        self.assertEqual(trajectory.value(Fraction(1)), expected)
        with self.assertRaises(InvariantViolation):
            trajectory.commit(8, Fraction(0))


class MatchAssemblyTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.catalog = Catalog()
        cls.engine = match_economy(cls.catalog, initialize(cls.catalog, generate_candidate(1000)),
                                   total_ticks=40, fingerprint_each_tick=False)
        cls.engine.state.export_controls.action_cycles.setdefault(3, {})['n1'] = Fraction(7, 1000)
        while cls.engine.tick < 40:
            if cls.engine.tick == 35:
                # Inject a real minimum action event into the corridor table
                # of the current cycle to check unique ownership end to end.
                cls.engine.state.cargo.action_cycles.setdefault(3, {})['n2'] = Fraction(1, 1000)
            cls.engine.step()
        cls.summary = finalize_match(cls.engine)

    def test_opening_profile_still_bounded(self):
        with self.assertRaises(InfrastructureFault):
            opening_economy(self.catalog, initialize(self.catalog, generate_candidate(1000)), total_ticks=160)

    def test_every_cycle_collected_and_finalized(self):
        collector = self.engine.state.national_power
        for owner, rows in collector.rows.items():
            self.assertEqual([r.cycle for r in rows], [0, 1, 2, 3])
            self.assertEqual(collector.trajectories[owner].cycles, 4)
            self.assertIsInstance(rows[-1].total, Fraction)
        self.assertEqual(set(self.summary['nations']), set(self.engine.state.initial.known))
        for row in self.summary['nations'].values():
            self.assertIsInstance(row['V'], Fraction)
            self.assertTrue(Fraction(35, 100) <= row['phi'] <= 1)
        # Every ActiveScale meter has an engine count (active_scale_entities_v1);
        # only the practice clock prevents certification here.
        self.assertEqual(self.summary['pending_rule_inputs'], [])
        self.assertFalse(self.summary['full_rules_accepted'])
        self.assertFalse(self.summary['official_clock'])

    def test_loss_action_unique_ownership(self):
        collector = self.engine.state.national_power
        names = [name for name, _, _ in action_sources(self.engine.state)]
        self.assertEqual(len(names), len(set(names)))
        self.assertIn('corridor_policy', names)
        self.assertIn('export_controls', names)
        last = {p: rows[-1] for p, rows in collector.rows.items()}
        self.assertGreaterEqual(last['n1'].loss_action, Fraction(7, 1000))
        self.assertGreaterEqual(last['n2'].loss_action, Fraction(1, 1000))
        # Each source counted exactly once in the committed sum.
        for owner, row in last.items():
            self.assertEqual(row.loss_action, sum((v.get(owner, Fraction(0)) for v in collector.loss_by_source.values()), Fraction(0)))

    def test_fx_revaluation_invariance(self):
        state = self.engine.state
        collector = state.national_power
        before = {p: collector.nfa_ru(p, 3) for p in state.initial.known}
        saved = dict(state.rates)
        try:
            for p in state.rates:
                state.rates[p] = Fraction(1, 2)
            after = {p: collector.nfa_ru(p, 3) for p in state.initial.known}
            for p in state.rates:
                state.rates[p] = Fraction(1, 20)
            far = {p: collector.nfa_ru(p, 3) for p in state.initial.known}
        finally:
            state.rates.update(saved)
        self.assertEqual(before, after)
        self.assertEqual(before, far)

    def test_finalizer_requires_complete_clock(self):
        engine = match_economy(self.catalog, initialize(self.catalog, generate_candidate(1001)),
                               total_ticks=40, fingerprint_each_tick=False)
        with self.assertRaises(InfrastructureFault):
            finalize_match(engine)

    def test_scoring_query_is_read_only(self):
        from organizer.rift_era.engine.determinism import fingerprint
        state = self.engine.state
        before = fingerprint(state.snapshot())
        for _ in range(3):
            state.national_power.nfa_ru('n0', 3)
            state.national_power.connected_regions('n0')
        self.assertEqual(before, fingerprint(state.snapshot()))

    def test_industrial_va_attributed_by_penetrated_equity_and_direct_facility_shares(self):
        state = self.engine.state
        ia = state.industry_activity
        base_n0 = ia.penetrated_va('n0')
        base_n1 = ia.penetrated_va('n1')
        base_n2 = ia.penetrated_va('n2')
        self.assertEqual(state.national_power.rows['n0'][-1].va, base_n0)
        cid = 'n0-manufacturing'
        comp = state.initial.companies[cid]
        va_mfg = ia.last_company_va[cid]
        self.assertNotEqual(va_mfg, 0)
        saved_shares = dict(comp.shares)
        lid = 'n0/P033/0/line'
        rights = state.authority.assets[lid]
        saved_rights = dict(rights.owner_shares)
        saved_corp_va = ia.last_company_va[cid]
        try:
            # 25% sold to n1, 15% sold to npc:world -> n0 keeps 60%.
            comp.shares = {'n0': 600_000, 'n1': 250_000, 'npc:world': 150_000}
            self.assertEqual(ia.penetrated_va('n0'), base_n0 - Fraction(4, 10) * va_mfg)
            self.assertEqual(ia.penetrated_va('n1'), base_n1 + Fraction(1, 4) * va_mfg)
            # Direct 30% facility share held by n2 on n0/P033/0/line:
            line_va = ia.last_asset_va[lid]
            rights.owner_shares = {'n0': 700, 'n2': 300}
            ia.last_company_va[cid] = va_mfg - Fraction(3, 10) * line_va
            self.assertEqual(ia.penetrated_va('n2'), base_n2 + Fraction(3, 10) * line_va)
            self.assertEqual(
                ia.penetrated_va('n0') + ia.penetrated_va('n1') + ia.penetrated_va('n2')
                + Fraction(15, 100) * ia.last_company_va[cid],
                base_n0 + base_n1 + base_n2,
            )
        finally:
            comp.shares = saved_shares
            rights.owner_shares = saved_rights
            ia.last_company_va[cid] = saved_corp_va

    def test_connected_regions_uses_backup_command_hubs_and_authorized_undamaged_corridors(self):
        from organizer.rift_era.engine.ledger import Account
        from organizer.rift_era.engine.materials import Origin
        from organizer.rift_era.engine.national_power import command_hubs
        state = self.engine.state
        world = state.initial.map
        auth = state.authority.regions
        inv = state.initial.inventory
        cap_region = world.hubs['n0-h0'].region
        backup_hub = 'n0-h1'
        backup_region = world.hubs[backup_hub].region
        self.assertEqual(command_hubs(state, 'n0'), ('n0-h0',))
        self.assertIn(backup_region, state.national_power.connected_regions('n0'))
        # 1. Destroying all corridors incident to n0-h0 isolates the capital even though L2 regions are adjacent.
        incident = [edge for edge in state.cargo.corridors.values() if 'n0-h0' in (edge.a, edge.b)]
        saved_health = {e.id: e.health_mU for e in incident}
        try:
            for e in incident:
                e.health_mU = 0
            self.assertEqual(state.national_power.connected_regions('n0'), {cap_region})
        finally:
            for e in incident:
                e.health_mU = saved_health[e.id]
        # 2. Capital lost: disconnected without a stocked backup core hub, reconnected when n0-h1 has comm + food >= 1.
        food_acct = Account('n0', backup_hub, 'available', 'food')
        comm_acct = Account('n0', backup_hub, 'available', 'communication_equipment')
        auth[cap_region].controller = 'n1'
        try:
            self.assertEqual(command_hubs(state, 'n0'), ())
            self.assertEqual(state.national_power.connected_regions('n0'), set())
            inv.transform({}, {food_acct: 1000, comm_acct: 1000}, reason='test_backup_hub',
                          origins={food_acct: Origin('n0', backup_hub), comm_acct: Origin('n0', backup_hub)})
            try:
                self.assertEqual(command_hubs(state, 'n0'), (backup_hub,))
                reconnected = state.national_power.connected_regions('n0')
                self.assertIn(backup_region, reconnected)
                self.assertNotIn(cap_region, reconnected)
            finally:
                inv.transform({food_acct: 1000, comm_acct: 1000}, {}, reason='test_backup_hub_cleanup')
        finally:
            auth[cap_region].controller = 'n0'


class FxRevaluationScenarioTest(unittest.TestCase):
    """round6 fx_revaluation_invariance: one real exchange, then only e moves."""
    def test_nfa_minus_200_through_rate_changes(self):
        from organizer.rift_era.engine.ledger import CashAccount
        catalog = Catalog()
        engine = match_economy(catalog, initialize(catalog, generate_candidate(1000)), total_ticks=40,
                               fingerprint_each_tick=False)
        state = engine.state
        collector = state.national_power
        base = collector.nfa_ru('n0', 0)
        state.initial.cash.exchange(CashAccount('n0', 'treasury', 'n0'), CashAccount('world', 'pool', 'n0'),
                                    CashAccount('world', 'pool', 'RU'), CashAccount('n0', 'reserves', 'RU'),
                                    100_000_000, 99_800_000, executed_ru_mU=Fraction(99_800_000))
        values = []
        for rate in (Fraction(1), Fraction(1, 2), Fraction(1, 20)):
            state.rates['n0'] = rate
            values.append(collector.nfa_ru('n0', 0) - base)
        self.assertEqual(values, [Fraction(-200)] * 3)


class MatchDeterminismTest(unittest.TestCase):
    def test_hash_seed_independent(self):
        code = ('from organizer.rift_era.engine.catalog import Catalog\n'
                'from organizer.rift_era.engine.initialization import initialize\n'
                'from organizer.rift_era.engine.match import match_economy\n'
                'from organizer.rift_era.engine.world import generate_candidate\n'
                'from organizer.rift_era.engine.determinism import fingerprint\n'
                'c=Catalog();e=match_economy(c,initialize(c,generate_candidate(1000)),total_ticks=40,fingerprint_each_tick=False)\n'
                'for _ in range(12): e.step()\n'
                'print(fingerprint(e.state.snapshot()))\n')
        hashes = set()
        for seed in ('0', '12345'):
            env = dict(os.environ, PYTHONHASHSEED=seed, PYTHONPATH=str(ROOT))
            out = subprocess.run([sys.executable, '-c', code], env=env, capture_output=True, text=True, check=True)
            hashes.add(out.stdout.strip())
        self.assertEqual(len(hashes), 1)


if __name__ == '__main__':
    unittest.main()
