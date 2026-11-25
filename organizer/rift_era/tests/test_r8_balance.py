"""rift_era_rules_r8/r9 (decisions 0015 & 0016): industrial emissions x0.55 and
permanent non-refillable opening endowment exemption in eta's stock count."""
import unittest
from fractions import Fraction as Q

from organizer.rift_era.engine.catalog import Catalog
from organizer.rift_era.engine.consumption import Consumption
from organizer.rift_era.engine.errors import InvariantViolation
from organizer.rift_era.engine.initialization import initialize
from organizer.rift_era.engine.match import match_economy
from organizer.rift_era.engine.world import generate_candidate


def _consumption(cycle, endowment):
    c = Consumption()
    c.set_endowment(endowment)
    c.cycle = cycle
    return c


class R8BalanceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.catalog = Catalog()
        cls.world = generate_candidate(1000)

    def test_rule_version_and_emission_factors(self):
        c = self.catalog
        self.assertEqual(c.version, 'rift_era_rules_r9')
        e = c.runtime['environment']['emissions']
        self.assertEqual(e['P001'], Q(1, 2) * Q(55, 100))
        self.assertEqual(e['P002'], Q(3, 10) * Q(55, 100))
        self.assertEqual(e['P005'], Q(2, 5) * Q(55, 100))
        self.assertEqual(e['P007'], Q(3, 10) * Q(55, 100))
        self.assertEqual(e['P070'], Q(1, 5))
        self.assertEqual(e['P006'], Q(3, 10))  # not part of the approved change
        self.assertIn('opening_endowment_exemption', c.tables['scoring']['pillars']['ind']['eta_sink'])

    def test_endowment_is_permanently_exempt_and_new_stock_counts_in_full(self):
        stock = {'n0': {'food': Q(210_000)}}
        c = _consumption(0, stock)
        self.assertEqual(c.eta_physical('n0', 'food', Q(210_000)), Q(0))
        # 30 units produced on top of the untouched endowment count in full.
        self.assertEqual(c.eta_physical('n0', 'food', Q(240_000)), Q(30_000))
        # Later cycles (including beyond cycle 20) still exempt remaining opening endowment.
        c.cycle = 9
        self.assertEqual(c.eta_physical('n0', 'food', Q(210_000)), Q(0))
        c.cycle = 25
        self.assertEqual(c.eta_physical('n0', 'food', Q(240_000)), Q(30_000))
        # A good the nation did not start with is never discounted.
        c.cycle = 0
        self.assertEqual(c.eta_physical('n0', 'steel', Q(50_000)), Q(50_000))
        self.assertEqual(c.eta_physical('n1', 'food', Q(50_000)), Q(50_000))

    def test_endowment_is_drawn_first_and_never_refilled(self):
        c = _consumption(0, {'n0': {'food': Q(210_000), 'steel': Q(48_000)}})
        c.observe_stock(0, {'n0': {'food': Q(100_000), 'steel': Q(48_000)}})
        self.assertEqual(c.endowment[('n0', 'food')], Q(100_000))
        # Restocking (production or purchase) does not refill the endowment.
        c.observe_stock(1, {'n0': {'food': Q(400_000)}})
        self.assertEqual(c.endowment[('n0', 'food')], Q(100_000))
        self.assertNotIn(('n0', 'steel'), c.endowment)  # sold out entirely
        c.cycle = 25
        self.assertEqual(c.eta_physical('n0', 'food', Q(400_000)), Q(300_000))
        c.observe_stock(25, {'n0': {'food': Q(400_000)}})
        self.assertEqual(c.endowment[('n0', 'food')], Q(100_000))
        c.audit()
        c.tick = 200  # the match has started: the endowment cannot be reset
        with self.assertRaises(InvariantViolation):
            c.set_endowment({'n0': {'food': Q(1)}})

    def test_opening_lines_earn_positive_value_added_and_beat_idling(self):
        game = match_economy(self.catalog, initialize(self.catalog, self.world), total_ticks=40,
                             fingerprint_each_tick=False)
        state = game.state
        self.assertEqual(state.consumption.endowment[('n0', 'food')], Q(210_000))
        for _ in range(10):
            game.step()
        row = state.national_power.rows['n0'][-1]
        # r7 tick 10: VA <= 0 on every line, industry about -51.8.
        self.assertGreater(row.va, 0)
        self.assertGreater(row.industry, 0)
        va = state.industry_activity.last_asset_va
        for line in ('n0/P031/0/line', 'n0/P001/0/line', 'n0/P033/0/line'):
            self.assertGreater(va[line], 0, line)
        state.audit()


if __name__ == '__main__':
    unittest.main()
