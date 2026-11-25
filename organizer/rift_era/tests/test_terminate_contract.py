"""06 diplomacy/terminate_contract across every terminable agreement kind."""
import unittest
from types import SimpleNamespace as NS

from organizer.rift_era.tests import test_contracts as base
from organizer.rift_era.tests import test_forward_finance as forward_tests
from organizer.rift_era.tests import test_joint_ventures as jv_tests
from organizer.rift_era.tests import test_sovereign_loans as loan_tests
from organizer.rift_era.engine.clock import ClockConfig
from organizer.rift_era.engine.contracts import ContractCommands, treaty_id
from organizer.rift_era.engine.determinism import fingerprint
from organizer.rift_era.engine.errors import RuleViolation
from organizer.rift_era.engine.forward_finance import ForwardFinanceCommands
from organizer.rift_era.engine.ledger import Account, CashAccount
from organizer.rift_era.engine.numeric import Q

PENALTY = Q(15, 100)


def terminate(cid, seq=9):
    return {'id': str(seq), 'kind': 'diplomacy', 'op': 'terminate_contract', 'contract_id': cid}


def accept(alias):
    return {'id': '1', 'kind': 'diplomacy', 'op': 'accept_contract', 'proposal_id': alias}


class TerminateContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        base.LicenseContractTests.setUpClass()
        forward_tests.ForwardFinanceTests.setUpClass()

    def state(self):
        return base.LicenseContractTests().fixture().state

    def sign(self, state, order, ctx=None):
        h = ContractCommands(state); ctx = ctx or NS(tick=0, cycle=0, clock=ClockConfig())
        h.prepare('n0', order, ctx).commit(); state.contracts.publish()
        alias = state.contracts.visible('n1')['proposals'][0]['id']
        h.prepare('n1', accept(alias), ctx).commit()
        return h, ctx

    # ---- forward: only the terminator's margin is forfeited ----
    def test_forward_forfeits_only_terminator_margin_without_credit_penalty(self):
        game = forward_tests.ForwardFinanceTests().fixture(); state = game.state
        cash, ff = state.initial.cash, state.forward_finance
        fh = ForwardFinanceCommands(state)
        order = forward_tests.ForwardFinanceTests().forward
        fh.prepare('n0', order(1, 'n1'), forward_tests.ctx(0)).commit()
        fh.prepare('n1', order(1, 'n0'), forward_tests.ctx(0)).commit()
        row, = ff.forwards.values()
        credit = dict(state.credit)
        before_n0 = cash.balance(ff.reserves('n0')).native_mU
        before_n1 = cash.balance(ff.reserves('n1')).native_mU
        with self.assertRaises(RuleViolation):
            ContractCommands(state).prepare('n2', terminate(row.id), forward_tests.ctx(1))
        ContractCommands(state).prepare('n0', terminate(row.id), forward_tests.ctx(1)).commit()
        self.assertFalse(ff.forwards)
        self.assertEqual(cash.balance(ff.reserves('n1')).native_mU - before_n1, 40000)  # both margins to the victim
        self.assertEqual(cash.balance(ff.reserves('n0')).native_mU, before_n0)
        self.assertEqual(state.credit, credit)
        state.audit()

    # ---- periodic swap: escrowed obligations go back ----
    def swap_offer(self):
        return {'id': '1', 'kind': 'diplomacy', 'op': 'propose_contract', 'counterparty': 'n1', 'clause_type': 1, 'duration_cycles': 10,
                'params': {'hub_a': 'n0-h0', 'hub_b': 'n1-h0', 'good_a': 'food', 'qty_a_mU': 1000,
                           'good_b': 'water', 'qty_b_mU': 1000, 'period_cycles': 2}}

    def test_swap_returns_locked_supplies_and_pays_credit(self):
        state = self.state()
        inv = state.initial.inventory
        food, water = Account('n0', 'n0-h0', 'available', 'food'), Account('n1', 'n1-h0', 'available', 'water')
        before = (inv.balance(food), inv.balance(water))
        _, ctx = self.sign(state, self.swap_offer())
        row = next(iter(state.contracts.swaps.rows.values()))
        self.assertEqual(inv.balance(food), before[0] - 1000)  # escrowed
        credit = state.credit['n0']
        ContractCommands(state).prepare('n0', terminate(row.proposal.id), ctx).commit()
        self.assertEqual((inv.balance(food), inv.balance(water)), before)
        self.assertEqual(row.status, 'TERMINATED')
        self.assertFalse(state.contracts.swaps.pending)
        self.assertEqual(state.credit['n0'], credit - PENALTY)
        state.contracts.settle(ctx)  # nothing left to deliver
        self.assertEqual((inv.balance(food), inv.balance(water)), before)
        # a terminated swap never re-escrows and no longer counts against the cap
        state.contracts.swaps.begin_tick(NS(tick=20, cycle=2, clock=ClockConfig()))
        self.assertEqual(row.status, 'TERMINATED')
        self.assertEqual((inv.balance(food), inv.balance(water)), before)
        with self.assertRaises(RuleViolation):
            ContractCommands(state).prepare('n1', terminate(row.proposal.id), ctx)
        state.audit()

    def test_swap_suspended_by_counterparty_is_free(self):
        state = self.state()
        _, ctx = self.sign(state, self.swap_offer())
        state.contracts.settle(ctx)
        row = next(iter(state.contracts.swaps.rows.values()))
        row.status = 'SUSPENDED'; row.blocked_by = ('n1',)
        credit = state.credit['n0']
        ContractCommands(state).prepare('n0', terminate(row.proposal.id), ctx).commit()
        self.assertEqual(state.credit['n0'], credit)
        # the party that caused the block still pays
        state2 = self.state(); _, ctx2 = self.sign(state2, self.swap_offer()); state2.contracts.settle(ctx2)
        row2 = next(iter(state2.contracts.swaps.rows.values()))
        row2.status = 'SUSPENDED'; row2.blocked_by = ('n1',)
        c1 = state2.credit['n1']
        ContractCommands(state2).prepare('n1', terminate(row2.proposal.id), ctx2).commit()
        self.assertEqual(state2.credit['n1'], c1 - PENALTY)

    # ---- joint venture ----
    def test_joint_venture_exit_pays_credit_unless_at_war(self):
        for war in (False, True):
            with self.subTest(war=war):
                jv = jv_tests.JointVentureTests(); state, h = jv.fixture()
                ctx = NS(tick=0, cycle=0, clock=ClockConfig())
                h.prepare('n0', jv.offer(), ctx).commit(); state.contracts.publish()
                h.prepare('n1', accept(state.contracts.visible('n1')['proposals'][0]['id']), ctx).commit()
                vid, row = next(iter(state.contracts.joint_ventures.rows.items()))
                if war: state.initial.diplomacy.phase1(1, declarations=(('n0', 'n1'),))
                credit = state.credit['n1']
                h.prepare('n1', terminate(vid), ctx).commit()
                self.assertEqual(row.active_members, ('n0',))
                self.assertEqual(state.credit['n1'], credit if war else credit - PENALTY)
                with self.assertRaises(RuleViolation):
                    h.prepare('n1', terminate(vid), ctx)

    # ---- treaties ----
    def test_each_treaty_kind_is_removed_with_credit_cost(self):
        orders = {
            'intel': lambda s: {'clause_type': 8, 'params': {'region_ids': [s.initial.map.hubs['n0-h0'].region], 'min_quality_mU': 250}},
            'embargo': lambda s: {'clause_type': 9, 'params': {'target_nation': 'n2', 'good_ids': ['food']}},
            'defense': lambda s: {'clause_type': 7, 'params': {'response_cycles': 2, 'min_aid_ru_mU': 50000}},
        }
        for kind, make in orders.items():
            with self.subTest(kind=kind):
                state = self.state()
                order = {'id': '1', 'kind': 'diplomacy', 'op': 'propose_contract', 'counterparty': 'n1', 'duration_cycles': 10, **make(state)}
                h, ctx = self.sign(state, order)
                ids = state.contracts.visible('n0')['terminable_treaties']
                self.assertEqual(len(ids), 1); self.assertTrue(ids[0].startswith(kind + ':'))
                self.assertEqual(state.contracts.visible('n1')['terminable_treaties'], ids)
                self.assertEqual(state.contracts.visible('n2')['terminable_treaties'], [])
                with self.assertRaises(RuleViolation):
                    h.prepare('n2', terminate(ids[0]), ctx)
                credit = state.credit['n0']
                h.prepare('n0', terminate(ids[0]), ctx).commit()
                self.assertEqual(state.credit['n0'], credit - PENALTY)
                self.assertEqual(state.contracts.visible('n0')['terminable_treaties'], [])
                state.audit()

    def test_transit_treaty_terminates(self):
        from organizer.rift_era.tests import test_transit_contracts as transit_tests
        t = transit_tests.TransitContractTests(); state, eid = t.fixture(); t.sign(state, eid)
        tid = treaty_id(state.contracts.transit[0])
        credit = state.credit['n1']
        ContractCommands(state).prepare('n1', terminate(tid), NS(tick=0, cycle=0)).commit()
        self.assertFalse(state.contracts.transit)
        self.assertIsNone(state.contracts.transit_right('n1', eid))
        self.assertEqual(state.credit['n1'], credit - PENALTY)

    # ---- technology license ----
    def test_license_termination_and_credit_floor(self):
        state = self.state(); h = ContractCommands(state); ctx = NS(tick=0, cycle=0)
        h.prepare('n0', base.LicenseContractTests.offer(), ctx).commit(); state.contracts.publish()
        h.prepare('n1', accept(state.contracts.visible('n1')['proposals'][0]['id']), ctx).commit()
        self.assertIn('R01', state.contracts.licenses['n1'])
        with self.assertRaises(RuleViolation):
            h.prepare('n0', terminate('license:R01'), ctx)  # the licensor holds no license record
        state.credit['n1'] = Q(15, 100)
        h.prepare('n1', terminate('license:R01'), ctx).commit()
        self.assertNotIn('R01', state.contracts.licenses['n1'])
        self.assertEqual(state.credit['n1'], Q(1, 10))  # floored

    # ---- loans can never be terminated ----
    def test_loan_cannot_be_terminated_and_unknown_ids_have_no_mutation(self):
        s = loan_tests.SovereignLoanTests(); state, h = s.fixture(); row = s.originate(state, h)
        lid = next(iter(state.contracts.loans.rows))
        before = fingerprint(state.snapshot())
        for owner, cid in (('n0', lid), ('n1', lid), ('n0', 'nope'), ('n0', 'license:R99')):
            with self.assertRaises(RuleViolation):
                h.prepare(owner, terminate(cid), NS(tick=0, cycle=0))
        self.assertEqual(before, fingerprint(state.snapshot()))
        with self.assertRaises(RuleViolation):
            h.prepare('n0', {'id': '9', 'kind': 'diplomacy', 'op': 'terminate_contract'}, NS(tick=0, cycle=0))


if __name__ == '__main__':
    unittest.main()
