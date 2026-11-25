"""P2: every stateful subsystem audits itself and is part of OperatingWorld.audit()."""
import unittest

from organizer.rift_era.engine.catalog import Catalog
from organizer.rift_era.engine.economy import opening_economy
from organizer.rift_era.engine.errors import InvariantViolation
from organizer.rift_era.engine.initialization import initialize
from organizer.rift_era.engine.ledger import CashAccount
from organizer.rift_era.engine.numeric import Q
from organizer.rift_era.engine.world import generate_candidate

NEW = ('payments', 'fx', 'macro', 'battles', 'relations', 'debt_funding', 'orbital_observation',
       'sensors', 'vision', 'forecasts', 'military_governance', 'intelligence_sharing')


class SubsystemAuditTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        catalog = Catalog()
        cls.game = opening_economy(catalog, initialize(catalog, generate_candidate(1000)), total_ticks=20, debug=False)
        for _ in range(3):
            cls.game.step()

    def test_every_subsystem_with_snapshot_has_audit(self):
        state = self.game.state
        for name in NEW:
            self.assertTrue(callable(getattr(getattr(state, name), 'audit', None)), name)
        state.audit()

    def test_corruptions_are_detected_by_the_global_audit(self):
        state = self.game.state
        spread = state.relations.spread_pp['n0']
        imports = state.macro.cycles['n0'].imports_ru_mU
        cases = [
            (lambda: state.macro.cycles['n0'].__setattr__('imports_ru_mU', Q(-1)),
             lambda: state.macro.cycles['n0'].__setattr__('imports_ru_mU', imports)),
            (lambda: state.relations.spread_pp.__setitem__('n0', Q(-1)),
             lambda: state.relations.spread_pp.__setitem__('n0', spread)),
            (lambda: state.battles.combat_regions.add('no-such-region'),
             lambda: state.battles.combat_regions.discard('no-such-region')),
            (lambda: state.fx.controls.__setitem__('n0', 20000), lambda: state.fx.controls.pop('n0')),
        ]
        a, b = CashAccount('n0', 'treasury', 'n0'), CashAccount('n1', 'treasury', 'n0')
        cases.append((lambda: state.payments.residuals.__setitem__((a, b, 'x'), Q(3, 2)),
                      lambda: state.payments.residuals.pop((a, b, 'x'))))
        sequence = state.vision.sequences['n0']
        cases.append((lambda: state.vision.sequences.__setitem__('n0', -1),
                      lambda: state.vision.sequences.__setitem__('n0', sequence)))
        for corrupt, restore in cases:
            corrupt()
            try:
                with self.assertRaises(InvariantViolation):
                    state.audit()
            finally:
                restore()
        state.audit()

    def test_military_governance_events_are_control_change_tuples(self):
        """advance_control yields (region, previous, claimant); a war capture must pass the audit."""
        state = self.game.state
        governance = state.military_governance
        saved = governance.events
        region = next(iter(state.authority.regions))
        try:
            governance.events = [(region, 'n1', 'n0')]
            state.audit()
            for bad in ({'region': region}, (region, 'n0', 'n0'), (region, 'n0')):
                governance.events = [bad]
                with self.assertRaises(InvariantViolation):
                    state.audit()
        finally:
            governance.events = saved
        state.audit()


if __name__ == '__main__':
    unittest.main()
