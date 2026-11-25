"""03 E12 cross-border power sub-item of a periodic swap (e12_power_contract_v1).

The contract is settled on the dispatch's actual flow over the named line;
at period end the seller refunds what the buyer paid above price_cap. A seller
that cannot refund defaults (Credit -0.15). C28: Credit < 0.3 blocks new ones.
"""
import unittest
from types import SimpleNamespace as NS

from organizer.rift_era.tests import test_contracts as license_tests
from organizer.rift_era.engine.contracts import ContractCommands
from organizer.rift_era.engine.errors import RuleViolation
from organizer.rift_era.engine.ledger import CashAccount
from organizer.rift_era.engine.numeric import Q
from organizer.rift_era.engine.utility_network import UtilityLink

LINK = 'power:e12test'


class PowerContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):license_tests.LicenseContractTests.setUpClass()

    def fixture(self, link=('n0-h0', 'n1-h0')):
        game = license_tests.LicenseContractTests().fixture()
        state = game.state
        if link:
            state.utilities.links[LINK] = UtilityLink(LINK, 'n0', link[0], link[1], 'power', 1, tuple(range(41)), 'asset:none')
        return game, state, ContractCommands(state)

    @staticmethod
    def offer(seq=1, **params):
        p = {'corridor_id': LINK, 'qty_mU': 1000, 'price_cap_ru_mU': 2000, 'period_cycles': 2}
        p.update(params)
        return {'id': str(seq), 'kind': 'diplomacy', 'op': 'propose_contract', 'counterparty': 'n1', 'clause_type': 1,
                'duration_cycles': 10, 'params': p}

    def signed(self, **params):
        game, state, h = self.fixture()
        ctx = NS(tick=0, cycle=0)
        h.prepare('n0', self.offer(**params), ctx).commit()
        state.contracts.publish()
        alias = state.contracts.visible('n1')['proposals'][0]['id']
        h.prepare('n1', {'id': '2', 'kind': 'diplomacy', 'op': 'accept_contract', 'proposal_id': alias}, ctx).commit()
        row, = state.contracts.swaps.visible('n0')
        return game, state, row

    @staticmethod
    def reserves(state, owner):
        return state.initial.cash.balance(CashAccount(owner, 'reserves', 'RU')).native_mU

    def test_offer_is_visible_with_power_params_and_accept_escrows_nothing(self):
        game, state, h = self.fixture()
        ctx = NS(tick=0, cycle=0)
        h.prepare('n0', self.offer(), ctx).commit()
        state.contracts.publish()
        proposal, = state.contracts.visible('n1')['proposals']
        self.assertEqual(proposal['params'], {'corridor_id': LINK, 'qty_mU': 1000, 'price_cap_ru_mU': 2000, 'period_cycles': 2})
        before = state.initial.cash.snapshot() if hasattr(state.initial.cash, 'snapshot') else None
        h.prepare('n1', {'id': '2', 'kind': 'diplomacy', 'op': 'accept_contract', 'proposal_id': proposal['id']}, ctx).commit()
        row, = state.contracts.swaps.visible('n1')
        self.assertEqual((row.status, row.next_cycle, row.end_cycle), ('ACTIVE', 2, 10))
        self.assertNotIn(row.proposal.id, state.contracts.swaps.pending)
        if before is not None:
            self.assertEqual(before, state.initial.cash.snapshot())
        state.contracts.settle(ctx)  # no physical legs: settling is a no-op for it
        self.assertEqual(row.delivered_periods, 0)
        state.audit()

    def test_rejects_missing_domestic_and_wrong_schema(self):
        game, state, h = self.fixture(link=None)
        ctx = NS(tick=0, cycle=0)
        with self.assertRaises(RuleViolation):
            h.prepare('n0', self.offer(), ctx)
        game, state, h = self.fixture(link=('n0-h0', 'n0-h1'))
        with self.assertRaises(RuleViolation):
            h.prepare('n0', self.offer(), ctx)
        game, state, h = self.fixture()
        for bad in ({'qty_mU': 0}, {'price_cap_ru_mU': -1}, {'period_cycles': 0}, {'qty_mU': '5'}, {'extra': 1}):
            with self.assertRaises(RuleViolation):
                h.prepare('n0', self.offer(**bad), ctx)

    def test_c28_credit_below_point_three_blocks_new_power_contracts(self):
        game, state, h = self.fixture()
        ctx = NS(tick=0, cycle=0)
        state.credit['n1'] = Q(29, 100)
        with self.assertRaises(RuleViolation):
            h.prepare('n0', self.offer(), ctx)
        state.credit['n1'] = Q(3, 10)
        h.prepare('n0', self.offer(), ctx).commit()
        state.contracts.publish()
        alias = state.contracts.visible('n1')['proposals'][0]['id']
        state.credit['n0'] = Q(2, 10)  # falls below before acceptance: accept re-validates
        with self.assertRaises(RuleViolation):
            h.prepare('n1', {'id': '2', 'kind': 'diplomacy', 'op': 'accept_contract', 'proposal_id': alias}, ctx)

    def test_metering_counts_only_seller_to_buyer_flow_on_the_line_up_to_qty(self):
        game, state, row = self.signed(qty_mU=1000)
        swaps = state.contracts.swaps
        swaps.record_power([
            ('n0', 'n1', {LINK: 700}, 600, Q(1800)),      # counted: 600 mU for 1800
            ('n1', 'n0', {LINK: 500}, 500, Q(500)),       # wrong direction
            ('n0', 'n1', {'power:other': 10}, 10, Q(10)),  # other line
            ('n0', 'n2', {LINK: 50}, 50, Q(50)),          # other buyer
            ('n0', 'n1', {LINK: 900}, 800, Q(800)),       # only 400 left under qty
        ])
        self.assertEqual(row.power_delivered_mU, 1000)
        self.assertEqual(row.power_paid_ru_mU, Q(1800) + Q(800) * 400 / 800)

    def test_period_end_refunds_excess_over_cap(self):
        game, state, row = self.signed(qty_mU=1000, price_cap_ru_mU=2000)
        swaps = state.contracts.swaps
        state.initial.cash.transfer(CashAccount('world', 'pool', 'RU'), CashAccount('n0', 'reserves', 'RU'), 5000)
        swaps.record_power([('n0', 'n1', {LINK: 600}, 500, Q(3000))])  # cap allows 500*2 = 1000
        a, b = self.reserves(state, 'n0'), self.reserves(state, 'n1')
        swaps.power_period(row, NS(cycle=1))  # before period end: nothing
        self.assertEqual(row.power_delivered_mU, 500)
        swaps.power_period(row, NS(cycle=2))
        self.assertEqual((self.reserves(state, 'n0'), self.reserves(state, 'n1')), (a - 2000, b + 2000))
        self.assertEqual((row.power_refunded_ru_mU, row.delivered_periods, row.next_cycle, row.status), (2000, 1, 4, 'ACTIVE'))
        self.assertEqual((row.power_delivered_mU, row.power_paid_ru_mU), (0, 0))
        state.audit()

    def test_under_cap_period_refunds_nothing_and_last_period_expires(self):
        game, state, row = self.signed()
        swaps = state.contracts.swaps
        swaps.record_power([('n0', 'n1', {LINK: 600}, 500, Q(500))])
        a = self.reserves(state, 'n0')
        swaps.power_period(row, NS(cycle=10))
        self.assertEqual((self.reserves(state, 'n0'), row.power_refunded_ru_mU, row.status), (a, 0, 'EXPIRED'))

    def test_seller_that_cannot_refund_defaults_with_credit_penalty_once(self):
        game, state, row = self.signed(qty_mU=1000, price_cap_ru_mU=1000)
        swaps = state.contracts.swaps
        seller = CashAccount('n0', 'reserves', 'RU')
        held = self.reserves(state, 'n0')
        state.initial.cash.transfer(seller, CashAccount('world', 'pool', 'RU'), held)
        state.initial.cash.transfer(CashAccount('world', 'pool', 'RU'), seller, 300)
        credit = state.credit['n0']
        swaps.record_power([('n0', 'n1', {LINK: 1000}, 1000, Q(2000))])  # owes 1000 back, holds 300
        b = self.reserves(state, 'n1')
        swaps.power_period(row, NS(cycle=2))
        self.assertEqual(self.reserves(state, 'n0'), 0)
        self.assertEqual(self.reserves(state, 'n1'), b + 300)
        self.assertEqual((row.status, row.power_refunded_ru_mU, row.defaulted_cycle), ('DEFAULTED', 300, 2))
        self.assertEqual(state.credit['n0'], max(Q(1, 10), credit - Q(15, 100)))
        self.assertEqual(row.delivered_periods, 0)
        swaps.begin_tick(NS(tick=0, cycle=4, clock=NS(cycle_ticks=1)))  # defaulted rows are inert
        self.assertEqual(state.credit['n0'], max(Q(1, 10), credit - Q(15, 100)))
        swaps.record_power([('n0', 'n1', {LINK: 1000}, 1000, Q(2000))])
        self.assertEqual(row.power_delivered_mU, 0)
        state.audit()

    def test_terminated_power_row_settles_running_period_and_stops_metering(self):
        game, state, row = self.signed(qty_mU=1000, price_cap_ru_mU=1000)
        state.initial.cash.transfer(CashAccount('world', 'pool', 'RU'), CashAccount('n0', 'reserves', 'RU'), 5000)
        state.contracts.swaps.record_power([('n0', 'n1', {LINK: 400}, 400, Q(1000))])  # 600 above cap
        b = self.reserves(state, 'n1')
        h = ContractCommands(state)
        h.prepare('n0', {'id': '3', 'kind': 'diplomacy', 'op': 'terminate_contract', 'contract_id': row.proposal.id},
                  NS(tick=0, cycle=0)).commit()
        self.assertEqual(row.status, 'TERMINATED')
        self.assertEqual((self.reserves(state, 'n1'), row.power_refunded_ru_mU), (b + 600, 600))
        state.contracts.swaps.record_power([('n0', 'n1', {LINK: 10}, 10, Q(10))])
        self.assertEqual(row.power_delivered_mU, 0)


if __name__ == '__main__':
    unittest.main()
