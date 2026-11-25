"""Short integrated game with a war: surrender, occupation taxes, insolvency auction, armistice repatriation."""
import unittest
from types import SimpleNamespace as NS

from organizer.rift_era.engine.catalog import Catalog
from organizer.rift_era.engine.contracts import ContractCommands
from organizer.rift_era.engine.determinism import fingerprint
from organizer.rift_era.engine.diplomacy import Relation
from organizer.rift_era.engine.economy import opening_economy
from organizer.rift_era.engine.initialization import initialize
from organizer.rift_era.engine.ledger import CashAccount
from organizer.rift_era.engine.military_movement import MarchPlan
from organizer.rift_era.engine.numeric import Q
from organizer.rift_era.engine.world import generate_candidate

TICKS = 30


def play(catalog, world, ticks=TICKS):
    game = opening_economy(catalog, initialize(catalog, world), total_ticks=ticks, commerce=True, debug=False)
    state = game.state
    trace = {}
    # n2 spends every unit of free cash each tick and leaves wages unpaid (insolvency path).
    def broke():
        for account in (state.native('n2', 'treasury'), CashAccount('n2', 'reserves', 'RU')):
            amount = state.initial.cash.balance(account).native_mU
            if amount:
                state.initial.cash.transfer(account, CashAccount('n2', 'test_parked', account.currency), amount)
    state.governance.policies['n2'].nominal_tax = Q(0)       # n2 has set a zero tax rate
    state.payments.arrears[(CashAccount('n2', 'wage_test_lock', 'n2'), state.native('n2', 'payroll:n2-h0'), 'wage')] = 3000
    review = state.insolvency.settle

    def reviewed(ctx):          # n2 has also spent this tick's receipts when P7 reviews it
        broke()
        return review(ctx)
    state.insolvency.settle = reviewed
    game.step({'n0': [{'id': '1', 'kind': 'diplomacy', 'op': 'declare_war', 'target_nation': 'n1'}]})
    trace['war'] = state.initial.diplomacy.relation('n0', 'n1')
    # an n1 land formation is cornered by an n0 force with no retreat path
    loser = next(f for f in sorted(state.formations.rows.values(), key=lambda f: f.id) if f.owner == 'n1' and f.domain == 'Land')
    winner = next(f for f in sorted(state.formations.rows.values(), key=lambda f: f.id) if f.owner == 'n0' and f.domain == 'Land')
    hp = sum(state.initial.units[u].hp_mU for u in loser.unit_ids)
    state.movement.plans[loser.id] = MarchPlan('n1', '', loser.id, 'retreat', loser.region, 'n1-h0', 500, hp, (), (),
                                               auto_retreat=True, retreat_status='blocked')
    winner.region = loser.region
    crew = sum(state.initial.units[u].crew_mU for u in loser.unit_ids)
    # an n1 border region is held by n0 (occupation economy path)
    occupied = state.initial.map.hubs['n1-h0'].region
    state.authority.regions[occupied].controller = 'n0'
    for _ in range(4):
        game.step({})
    trace['surrendered'] = loser.id not in state.formations.rows
    trace['prisoners'] = sum(g.total_mU for g in state.prisoners.groups.values())
    trace['crew'] = crew
    state.authority.regions[occupied].controller = 'n1'
    winner.region = state.initial.map.hubs['n0-h0'].region
    state.audit()
    # armistice proposed by n0, accepted by n1 at the next decision tick
    ContractCommands(state).prepare('n0', {'id': '2', 'kind': 'diplomacy', 'op': 'propose_contract', 'counterparty': 'n1',
                                           'clause_type': 6, 'duration_cycles': 10,
                                           'params': {'mode': 'armistice', 'region_ids': [], 'withdraw_cycles': 1}},
                                    NS(cycle=0, tick=game.tick)).commit()
    state.contracts.publish()
    alias = state.contracts.visible('n1')['proposals'][0]['id']
    while not game.clock.is_decision(game.tick):
        game.step({})
    before_home = state.initial.populations['n1-h0'].total_mU
    game.step({'n1': [{'id': '1', 'kind': 'diplomacy', 'op': 'accept_contract', 'proposal_id': alias}]})
    trace['truce'] = state.initial.diplomacy.relation('n0', 'n1')
    trace['repatriated'] = state.prisoners.repatriated_mU
    trace['home_delta'] = state.initial.populations['n1-h0'].total_mU - before_home
    while game.tick < ticks:
        game.step({})
        if game.tick % 10 == 0:
            state.audit()
    trace['occupied_tax'] = dict(state.governance.occupied_tax_mU)
    trace['insolvent'] = state.insolvency.rows['n2'].reorganizing
    trace['auction_proceeds'] = state.insolvency.rows['n2'].sold_native_mU
    trace['hash'] = fingerprint(state.snapshot())
    return trace


class WarMatchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.catalog, cls.world = Catalog(), generate_candidate(1000)

    def test_short_war_game_settles(self):
        first = play(self.catalog, self.world)
        self.assertEqual(first['war'], Relation.WAR)
        self.assertTrue(first['surrendered'])
        self.assertTrue(0 < first['prisoners'] <= first['crew'])   # surviving crew only
        self.assertEqual(first['truce'], Relation.TRUCE)
        self.assertEqual(first['repatriated'], first['prisoners'])
        self.assertGreaterEqual(first['home_delta'], 0)
        self.assertGreater(first['occupied_tax'].get('n0', 0), 0)
        self.assertTrue(first['insolvent'])
        self.assertGreater(first['auction_proceeds'], 0)


class WarMatchDeterminismTests(unittest.TestCase):
    def test_same_seed_same_state_hash(self):
        catalog, world = Catalog(), generate_candidate(1000)
        # 20 ticks: war, surrender, occupation tax, armistice and repatriation (both runs < 110 s);
        # the full 30-tick game is compared across PYTHONHASHSEED values by running this module's
        # ``__main__ hash`` entry in two processes.
        self.assertEqual(play(catalog, world, 20)['hash'], play(catalog, world, 20)['hash'])


if __name__ == '__main__':
    import sys
    if sys.argv[1:] == ['hash']:
        print(play(Catalog(), generate_candidate(1000))['hash'])
    else:
        unittest.main()
