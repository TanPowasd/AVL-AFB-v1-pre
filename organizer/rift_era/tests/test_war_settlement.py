"""Constructed scenarios: sovereign insolvency auction, surrender/prisoners, occupation taxes."""
import unittest
from collections import defaultdict
from types import SimpleNamespace as NS

from organizer.rift_era.tests import test_contracts as base
from organizer.rift_era.tests import test_sovereign_loans as loans_base
from organizer.rift_era.engine.contracts import ContractCommands
from organizer.rift_era.engine.diplomacy import Relation
from organizer.rift_era.engine.ledger import Account, CashAccount
from organizer.rift_era.engine.military_movement import MarchPlan
from organizer.rift_era.engine.numeric import Q, units


def cycle_ctx(cycle, seed=1000):
    return NS(tick=cycle * 10 + 9, cycle=cycle, seed=seed, clock=NS(cycle_ticks=10, is_cycle_end=lambda t: True))


def drain(state, owner):
    """Move the nation's free cash to a parked account of its own (conserved)."""
    cash = state.initial.cash
    for account in (state.native(owner, 'treasury'), CashAccount(owner, 'reserves', 'RU')):
        amount = cash.balance(account).native_mU
        if amount:
            cash.transfer(account, CashAccount(owner, 'test_parked', account.currency), amount)


class InsolvencyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        base.LicenseContractTests.setUpClass()

    def game(self):
        return base.LicenseContractTests().fixture()

    def test_no_trigger_with_liquidity_or_without_arrears(self):
        state = self.game().state
        for cycle in range(5):
            state.insolvency.settle(cycle_ctx(cycle))
        self.assertFalse(any(r.reorganizing for r in state.insolvency.rows.values()))
        drain(state, 'n0')
        for cycle in range(5, 10):   # illiquid but no arrears: no reorganisation
            state.insolvency.settle(cycle_ctx(cycle))
        self.assertFalse(state.insolvency.rows['n0'].reorganizing)
        state.audit()

    def test_three_illiquid_cycles_with_arrears_trigger_one_credit_cut_and_auction(self):
        state = self.game().state
        drain(state, 'n0')
        payroll = state.native('n0', 'payroll:n0-h0')
        key = (CashAccount('n0', 'wage_test_lock', 'n0'), payroll, 'wage')
        state.payments.arrears[key] = 5000
        credit = state.credit['n0']
        cash = state.initial.cash
        books = (cash.owner_book('n0'), cash.owner_book('world'))
        pool_stock = {g: state.initial.inventory.balance(Account('world', 'world_pool', 'available', g)) for g in state.catalog.goods}
        destroyed = state.cycles['n0'].destroyed_value
        for cycle in range(3):
            state.cycles['n0'].unpaid_wages_mU = 2000
            state.insolvency.settle(cycle_ctx(cycle))
            state.cycles['n0'].unpaid_wages_mU = 0
            self.assertEqual(state.insolvency.rows['n0'].reorganizing, cycle == 2)
        self.assertEqual(state.credit['n0'], credit - Q(25, 100))
        row = state.insolvency.rows['n0']
        self.assertTrue(row.auctions)
        for hub, good, quantity, _ in row.auctions:
            self.assertNotIn(good, ('food', 'grain', 'water'))
            self.assertEqual(state.initial.inventory.balance(Account('world', 'world_pool', 'available', good)),
                             pool_stock[good] + quantity)
        # proceeds first clear the wage arrears into the real payroll account
        self.assertNotIn(key, state.payments.arrears)
        self.assertEqual(state.initial.cash.balance(payroll).native_mU, 5000)
        self.assertEqual(row.wage_arrears_paid_mU, 5000)
        # scoring attribution: the sale is a real cash-for-goods exchange, never Loss_destroy;
        # the nation's historical cash book gains exactly what the world pool's book lost.
        gain = cash.owner_book('n0') - books[0]
        self.assertGreater(gain, 0)
        self.assertEqual(gain, books[1] - cash.owner_book('world'))
        self.assertEqual(state.cycles['n0'].destroyed_value, destroyed)
        # a later reorganised cycle does not charge credit again
        state.insolvency.settle(cycle_ctx(3))
        self.assertEqual(state.credit['n0'], credit - Q(25, 100))
        state.audit()

    def test_prior_default_in_window_suppresses_extra_credit_penalty(self):
        state = self.game().state
        drain(state, 'n0')
        state.sovereign_finance.default_cycles['n0'].append(1)
        credit = state.credit['n0']
        for cycle in range(3):
            state.cycles['n0'].unpaid_wages_mU = 1000
            state.insolvency.settle(cycle_ctx(cycle))
            state.cycles['n0'].unpaid_wages_mU = 0
        self.assertTrue(state.insolvency.rows['n0'].reorganizing)
        self.assertEqual(state.credit['n0'], credit)

    def test_reorganisation_forecloses_pledged_collateral_to_creditor(self):
        loans_base.SovereignLoanTests.setUpClass()
        case = loans_base.SovereignLoanTests()
        state, handler = case.fixture()
        loan = case.originate(state, handler)
        asset = state.initial.assets['collateral']
        asset.book_nmU = {'steel': Q(1000000)}
        asset.original_nmU = {'steel': 1000000}
        drain(state, 'n1')
        for cycle in range(3):
            state.cycles['n1'].unpaid_wages_mU = 1000
            state.insolvency.settle(cycle_ctx(cycle))
            state.cycles['n1'].unpaid_wages_mU = 0
        self.assertTrue(state.insolvency.rows['n1'].reorganizing)
        rights = state.authority.assets['collateral']
        self.assertGreater(rights.owner_shares.get('n0', 0), 0)
        self.assertFalse(rights.pledged)
        self.assertFalse(loan.collateral)
        self.assertEqual(state.insolvency.events[0]['foreclosed_loans'], [loan.proposal.id])
        state.authority.audit(); state.contracts.loans.audit(); state.insolvency.audit()  # book forged as in loan tests


class SurrenderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        base.LicenseContractTests.setUpClass()

    def cornered(self):
        game = base.LicenseContractTests().fixture()
        state = game.state
        state.initial.diplomacy.phase1(0, declarations=(('n0', 'n1'),))
        loser = next(f for f in state.formations.rows.values() if f.owner == 'n1' and f.domain == 'Land')
        winner = next(f for f in state.formations.rows.values() if f.owner == 'n0' and f.domain == 'Land')
        hp = sum(state.initial.units[u].hp_mU for u in loser.unit_ids)
        state.movement.plans[loser.id] = MarchPlan('n1', '', loser.id, 'retreat', loser.region, 'n1-h0', 500, hp, (), (),
                                                   auto_retreat=True, retreat_status='blocked')
        winner.region = loser.region          # an enemy force stands in the pocket
        return game, state, loser

    def test_blocked_retreat_surrenders_crew_as_prisoners_and_equipment_as_trophies(self):
        self.surrender()

    def surrender(self):
        game, state, loser = self.cornered()
        crew = sum(state.initial.units[u].crew_mU for u in loser.unit_ids)
        uids = loser.unit_ids
        specs = {u: state.catalog.units[state.initial.units[u].unit_type] for u in uids}
        destroyed = defaultdict(lambda: Q(0))
        state.prisoners.process(NS(tick=5, cycle=0, seed=1), destroyed)
        self.assertNotIn(loser.id, state.formations.rows)
        self.assertTrue(destroyed['n1'] > 0)
        self.assertEqual(destroyed['n0'], 0)          # Loss_destroy only on the surrendering side
        group = next(iter(state.prisoners.groups.values()))
        self.assertEqual((group.captor, group.origin, group.total_mU), ('n0', 'n1', crew))
        for uid in uids:
            stored = state.formations.stored_units[uid]
            self.assertEqual(stored.unit.owner, 'n0')
            self.assertEqual(stored.unit.crew_mU, 0)
            self.assertLessEqual(stored.unit.hp_mU, units(specs[uid]['hp']) * 2 // 5)
            for good, quantity in specs[uid]['installed'].items():
                account = state.formations.stored_account('n0', stored.hub, uid, good)
                self.assertEqual(state.initial.inventory.balance(account), units(quantity))
        state.audit()
        return state, crew

    def test_no_enemy_present_means_no_surrender(self):
        game, state, loser = self.cornered()
        state.initial.diplomacy.pairs['n0', 'n1'].state = Relation.PEACE
        state.prisoners.process(NS(tick=5, cycle=0, seed=1), defaultdict(lambda: Q(0)))
        self.assertIn(loser.id, state.formations.rows)
        self.assertFalse(state.prisoners.groups)

    def test_prisoners_return_home_only_when_war_ends(self):
        state, crew = self.surrender()
        population = state.initial.populations['n1-h0']
        before = population.total_mU
        state.prisoners.release(NS(tick=6))
        self.assertEqual(population.total_mU, before)
        state.initial.diplomacy.pairs['n0', 'n1'].state = Relation.TRUCE
        state.prisoners.release(NS(tick=7))
        self.assertEqual(population.total_mU, before + crew)
        self.assertFalse(state.prisoners.groups)
        self.assertEqual(state.prisoners.repatriated_mU, crew)
        state.audit()


class CessionAssetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        base.LicenseContractTests.setUpClass()

    def test_pledged_shares_stay_with_giver_and_keep_operator(self):
        from organizer.rift_era.engine.prisoners import settle_cession_assets
        state = base.LicenseContractTests().fixture().state
        authority = state.authority
        region = state.initial.map.hubs['n0-h0'].region
        aid = sorted(a for a, h in authority.asset_hubs.items()
                     if state.initial.map.hubs[h].region == region and authority.assets[a].owner_shares == {'n0': 1000})[0]
        authority.pledge(aid, 'loan-x', 'n0', 400)
        authority.cede('n0', 'n1', [region])
        moved = dict(settle_cession_assets(state, 'n0', 'n1', [region]))
        self.assertEqual(moved[aid], 600)
        self.assertEqual(authority.assets[aid].owner_shares, {'n0': 400, 'n1': 600})
        self.assertEqual(authority.assets[aid].operator, 'n0')
        authority.audit()


class OccupationTaxTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        base.LicenseContractTests.setUpClass()

    def test_occupied_hub_wage_and_consumption_tax_go_to_controller_in_resident_currency(self):
        state = base.LicenseContractTests().fixture().state
        state.initial.diplomacy.phase1(0, declarations=(('n0', 'n1'),))
        region = state.initial.map.hubs['n1-h0'].region
        state.authority.regions[region].controller = 'n0'
        household = state.native('n1', 'household:n1-h0')
        foreign = CashAccount('n0', 'treasury', 'n1')
        before = state.initial.cash.balance(foreign).native_mU
        taxes = state.cycles['n0'].taxes_mU
        own = state.cycles['n1'].taxes_mU
        self.assertEqual(state.governance.resident_tax('n1-h0', 'n1'), state.tax['n0'])
        receipt = state.governance.collect_tax(household, 'n1-h0', Q(1000), kind='wage_tax', resident=True)
        self.assertEqual(state.initial.cash.balance(foreign).native_mU, before + receipt.paid_mU)
        self.assertEqual(state.cycles['n1'].taxes_mU, own)
        self.assertGreater(state.cycles['n0'].taxes_mU, taxes)
        # non-resident taxes (corporate) keep the issuer as collector
        receipt = state.governance.collect_tax(household, 'n1-h0', Q(1000), kind='other')
        self.assertEqual(state.initial.cash.balance(foreign).native_mU, before + 1000)
        state.audit()

    def test_foreign_employer_payroll_reaches_residents_and_controller_taxes_it(self):
        game = base.LicenseContractTests().fixture()
        state = game.state
        region = state.initial.map.hubs['n1-h0'].region
        state.initial.diplomacy.phase1(0, declarations=(('n0', 'n1'),))
        state.authority.regions[region].controller = 'n0'
        payroll = state.native('n0', 'payroll:n1-h0')
        state.initial.cash.transfer(state.native('n0', 'treasury'), payroll, 10000)
        for _ in range(10):
            game.step({})
        self.assertEqual(state.initial.cash.balance(payroll).native_mU, 0)
        self.assertGreater(state.initial.cash.balance(state.native('n0', 'household:n1-h0')).native_mU, 0)
        self.assertGreater(state.governance.occupied_tax_mU.get('n0', 0), 0)
        state.audit()


if __name__ == '__main__':
    unittest.main()
