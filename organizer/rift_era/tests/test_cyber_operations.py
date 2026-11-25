"""S03 / E84 / S08 cyber operations and their victim-side effects."""
import unittest
from types import SimpleNamespace as NS
from unittest.mock import patch

from organizer.rift_era.engine.catalog import Catalog
from organizer.rift_era.engine.clock import ClockConfig
from organizer.rift_era.engine.cyber_operations import CyberCommands
from organizer.rift_era.engine.economy import opening_economy
from organizer.rift_era.engine.errors import RuleViolation
from organizer.rift_era.engine.initialization import initialize
from organizer.rift_era.engine.ledger import Account
from organizer.rift_era.engine.materials import Origin
from organizer.rift_era.engine.numeric import Q
from organizer.rift_era.engine.world import generate_candidate


def ctx(tick):
    return NS(seed=1000, tick=tick, cycle=tick // 10, window=tick // 5, clock=ClockConfig())


class CyberTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.catalog, cls.world = Catalog(), generate_candidate(1000)

    def fixture(self):
        game = opening_economy(self.catalog, initialize(self.catalog, self.world), total_ticks=30,
                               debug=False, commerce=True)
        state = game.state
        state.research['n0'].known.update({'S01', 'S02', 'S03'})
        state.research['n1'].known.update({'E07', 'L07', 'L11', 'C10'})
        stock = {Account('n0', 'n0-h0', 'available', 'cyber_suite'): 5000,
                 Account('n0', 'n0-h0', 'available', 'ew_module'): 5000}
        state.initial.inventory.transform({}, stock, reason='test_cyber_stock',
                                          origins={a: Origin(a.owner, a.location) for a in stock})
        return game

    def op(self, seq=1, kind='S03_infiltrate', hub='n1-h0'):
        return {'id': str(seq), 'kind': 'intelligence', 'op': 'cyber_op', 'target_nation': 'n1',
                'target_hub': hub, 'op_kind': kind}

    def test_schema_tech_and_cooldown(self):
        state = self.fixture().state
        handler = CyberCommands(state)
        with self.assertRaises(RuleViolation) as err:
            handler.prepare('n0', self.op(kind='S07_zero_day'), ctx(0))
        self.assertEqual(err.exception.code, 'ERR_SCHEMA')
        with self.assertRaises(RuleViolation) as err:
            handler.prepare('n0', self.op(kind='S08_paralyze'), ctx(0))
        self.assertEqual(err.exception.code, 'ERR_TECH_LOCKED')
        with self.assertRaises(RuleViolation) as err:
            handler.prepare('n2', self.op(2), ctx(0))
        self.assertEqual(err.exception.code, 'ERR_TECH_LOCKED')
        handler.prepare('n0', self.op(3), ctx(0)).commit()
        with self.assertRaises(RuleViolation) as err:
            handler.prepare('n0', self.op(4), ctx(5))
        self.assertEqual(err.exception.code, 'ERR_CAPACITY_EXCEEDED')
        state.audit()

    def test_infiltration_spends_a_suite_rolls_back_a_real_system_and_logs_action_loss(self):
        state = self.fixture().state
        handler = CyberCommands(state)
        with patch('organizer.rift_era.engine.cyber_operations.uniform', return_value=0.0):
            handler.prepare('n0', self.op(1), ctx(0)).commit()
        suite = Account('n0', 'n0-h0', 'available', 'cyber_suite')
        self.assertEqual(state.initial.inventory.balance(suite), 4000)
        self.assertEqual(state.cyber.action_cycles[0]['n0'], self.catalog.weights['cyber_suite'])
        # Rollback hit the first victim technology the engine actually models.
        self.assertTrue(state.cyber.rolled_back('n1', 'C10'))
        self.assertFalse(state.cyber.rolled_back('n0', 'C10'))
        # A zero draw also attributes the operation: Credit -0.05.
        self.assertEqual(state.credit['n0'], Q(85, 100) - Q(5, 100))
        state.audit()

    def test_steal_tech_discounts_a_visible_project_but_not_a_classified_one(self):
        state = self.fixture().state
        handler = CyberCommands(state)
        victim = state.research['n1']
        lab = 'n1/P075/0/line'
        tech = victim.focus[lab]
        victim.security[lab] = 'classified'
        with patch('organizer.rift_era.engine.cyber_operations.uniform', return_value=0.0):
            handler.prepare('n0', self.op(1, kind='S03_steal_tech'), ctx(0)).commit()
        self.assertFalse(state.research['n0'].projects[tech].cost_modifiers.get('stolen'))
        before = state.research['n0'].projects[tech].target_mU
        victim.security[lab] = 'standard'
        with patch('organizer.rift_era.engine.cyber_operations.uniform', return_value=0.0):
            handler.prepare('n0', self.op(2, kind='S03_steal_tech'), ctx(100)).commit()
        project = state.research['n0'].projects[tech]
        self.assertTrue(project.cost_modifiers['stolen'])
        self.assertEqual(project.target_mU, before * 4 // 5)
        state.audit()

    def test_failed_operation_still_pays_and_changes_nothing_at_the_victim(self):
        state = self.fixture().state
        with patch('organizer.rift_era.engine.cyber_operations.uniform', return_value=0.999):
            CyberCommands(state).prepare('n0', self.op(1), ctx(0)).commit()
        self.assertEqual(state.initial.inventory.balance(Account('n0', 'n0-h0', 'available', 'cyber_suite')), 4000)
        self.assertFalse(state.cyber.rollbacks)
        self.assertEqual(state.credit['n0'], Q(85, 100))
        self.assertEqual(state.cyber.visible('n0')['reports'][-1]['status'], 'EXECUTED')
        state.audit()

    def test_s08_freezes_plans_and_is_always_attributed(self):
        state = self.fixture().state
        state.research['n0'].known.add('S08')
        with patch('organizer.rift_era.engine.cyber_operations.uniform', return_value=0.999):
            CyberCommands(state).prepare('n0', self.op(1, kind='S08_paralyze'), ctx(0)).commit()
        self.assertTrue(state.cyber.plans_frozen('n1', 3))
        self.assertFalse(state.cyber.plans_frozen('n1', 5))
        self.assertEqual(state.credit['n0'], Q(85, 100) - Q(1, 10))
        self.assertEqual(state.initial.inventory.balance(Account('n0', 'n0-h0', 'available', 'ew_module')), 4000)
        state.audit()

    def test_steal_model_needs_a_real_datacenter_at_the_hub(self):
        state = self.fixture().state
        order = {'id': '1', 'kind': 'intelligence', 'op': 'steal_model',
                 'target_nation': 'n1', 'target_hub': 'n1-h0'}
        with patch('organizer.rift_era.engine.cyber_operations.uniform', return_value=0.0):
            CyberCommands(state).prepare('n0', order, ctx(0)).commit()
        self.assertFalse(state.ai.models['n0'])  # no observed datacenter, no copy
        self.assertEqual(state.initial.inventory.balance(Account('n0', 'n0-h0', 'available', 'cyber_suite')), 4000)
        state.audit()


if __name__ == '__main__':
    unittest.main()
