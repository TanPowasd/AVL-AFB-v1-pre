"""10 coupling gaps: C06/C07 import parity, C15 grid loss, C16 NPC efficiency, C18 fusion,
C19 drones, C24 reserve life, C25 war-zone outages, C28 credit-limited cross-border lines."""
import unittest
from collections import deque
from dataclasses import replace
from types import SimpleNamespace

from organizer.rift_era.engine import ai_military
from organizer.rift_era.engine.catalog import Catalog
from organizer.rift_era.engine.economy import opening_economy
from organizer.rift_era.engine.economy_state import CycleEconomy
from organizer.rift_era.engine.initialization import initialize
from organizer.rift_era.engine.numeric import Q
from organizer.rift_era.engine.utility_network import UtilityLink
from organizer.rift_era.engine.world import generate_candidate


class CouplingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.catalog, cls.world = Catalog(), generate_candidate(1000)

    def setUp(self):
        self.game = opening_economy(self.catalog, initialize(self.catalog, self.world), total_ticks=20, debug=False)
        self.state = self.game.state

    # ---- C06 / C07 ----
    def test_import_parity_floors_reference_price_only_for_recent_net_importers(self):
        state, macro = self.state, self.state.macro
        hub = 'n0-h0'
        weight = state.catalog.weights['coal']
        self.assertEqual(macro.reference_price('n0', 'coal', hub), state.price('coal', hub))
        state.frame.prices[f'spot:{hub}:coal'] = 1  # 0.001 local units: below parity
        macro.net_import_history['n0'].append({'coal': Q(5000)})
        state.rates['n0'] = Q(1, 2)  # weaker currency: RU parity costs more local units
        expected = 10 * weight / Q(1, 2) * Q(1002, 1000)
        self.assertEqual(macro.reference_price('n0', 'coal', hub), expected)
        # C07 uses the same parity for accelerators.
        macro.net_import_history['n0'].append({'accelerator': Q(1)})
        self.assertEqual(macro.import_parity('n0', 'accelerator', hub),
                         10 * state.catalog.weights['accelerator'] * 2 * Q(1002, 1000))
        # A net exporter over the five-cycle window has no parity floor.
        macro.net_import_history['n0'].append({'coal': Q(-6000)})
        self.assertIsNone(macro.import_parity('n0', 'coal', hub))

    def test_net_import_window_rolls_with_macro_settlement(self):
        macro = self.state.macro
        macro.good_trade('n0', 'n1', 'coal', Q(700))
        macro.good_trade('n0', 'n0', 'coal', Q(900))  # domestic trade is not an import
        self.assertEqual(macro.net_imports['n0'], {'coal': Q(700)})
        self.assertEqual(macro.net_imports['n1'], {'coal': Q(-700)})
        for _ in range(7):
            macro.net_import_history['n0'].append({})
        self.assertEqual(len(macro.net_import_history['n0']), 5)
        self.state.audit()

    # ---- C15 / C28 ----
    def link(self, a, b):
        return UtilityLink('power:test', 'n0', a, b, 'power', 1, tuple(range(41)), 'asset:none')

    def test_ai_grid_scheduling_scales_line_loss(self):
        state = self.state
        link = self.link('n0-h0', 'n0-h1')
        base = 1 - state.utilities.power_rating(link)[1]
        self.assertEqual(base, Q(15, 1000) * 4)
        state.ai_deployment.grid_multiplier = lambda owner, hub: Q(7, 10) if hub == 'n0-h1' else Q(1)
        self.assertEqual(1 - state.utilities.power_rating(link)[1], base * Q(7, 10))

    def test_credit_limits_cross_border_line_capacity(self):
        state = self.state
        domestic, foreign = self.link('n0-h0', 'n0-h1'), self.link('n0-h0', 'n1-h0')
        self.assertEqual(state.utilities.credit_factor(domestic), 1)
        self.assertEqual(state.utilities.credit_factor(foreign), 1)
        state.credit['n1'] = Q(3, 10)
        self.assertEqual(state.utilities.credit_factor(foreign), Q(1, 2))
        self.assertEqual(state.utilities.credit_factor(domestic), 1)
        state.credit['n0'] = Q(1, 10)
        self.assertEqual(state.utilities.credit_factor(foreign), Q(1, 6))

    # ---- C16 ----
    def test_finance_ai_tightens_npc_threshold_and_momentum(self):
        state, market = self.state, self.state.stock_market
        self.assertEqual(market._npc_policy(), (Q(1, 20), Q(1)))
        state.research['n0'].known.add('AI07')
        state.industry_activity.completed_finance = {'n0': (Q(20000), Q(20)), 'n1': (Q(19999), Q(60))}
        self.assertEqual(market._npc_policy(), (Q(3, 100), 1 - Q(20, 70)))
        state.industry_activity.completed_finance = {'n0': (Q(20000), Q(60))}
        self.assertEqual(market._npc_policy(), (Q(1, 50), Q(1, 2)))

    # ---- C18 / C19 ----
    def test_fusion_and_drone_factors_follow_cyber_strength(self):
        state = self.state
        self.assertEqual(ai_military.fusion_factor(state, 'n0'), 1)
        state.research['n0'].known.add('C08')
        self.assertEqual(ai_military.fusion_factor(state, 'n0'), 1)  # no online fusion sensor
        state.systems.instances['fs'] = SimpleNamespace(owner='n0', kind='fusion_sensor', online=True)
        state.systems.availability = lambda s: Q(1)
        self.assertEqual(ai_military.fusion_factor(state, 'n0'), Q(45, 100))
        state.research['n0'].known.add('AI09')
        state.ai_deployment.previous_strength = lambda owner, hub, task, threshold: Q(30)
        self.assertEqual(ai_military.fusion_factor(state, 'n0'), Q(45, 100) * Q(85, 100))
        state.ai_deployment.previous_strength = lambda owner, hub, task, threshold: Q(100)
        self.assertEqual(ai_military.fusion_factor(state, 'n0'), Q(45, 100) * Q(4, 5))
        self.assertEqual(ai_military.drone_multiplier(state, 'n0'), Q(115, 100))
        state.ai_deployment.previous_strength = lambda owner, hub, task, threshold: Q(25)
        self.assertEqual(ai_military.drone_multiplier(state, 'n0'), Q(11, 10))
        formation = next(f for f in state.formations.rows.values() if f.owner == 'n0')
        uid = formation.unit_ids[0]
        connected = lambda owner: {formation.region}
        self.assertEqual(ai_military.drone_fire(state, connected), {})  # no drone units
        state.initial.units[uid] = replace(state.initial.units[uid], unit_type='ground_drone')
        self.assertEqual(ai_military.drone_fire(state, connected), {uid: Q(11, 10)})
        self.assertEqual(ai_military.drone_fire(state, lambda owner: set()), {})  # off-network
        state.ai.suspended_until['n0'] = 10 ** 6
        self.assertEqual(ai_military.drone_fire(state, connected), {})

    # ---- C24 ----
    def test_reserve_life_discounts_mining_company_value(self):
        state, market = self.state, self.state.stock_market
        self.assertEqual(market.reserve_life_factor('n0-materials'), 1)
        self.assertEqual(market.reserve_life_factor('n0-mining'), 1)  # no extraction history
        remaining = sum(state.deposits[state.mine_deposit[m]].remaining_mU for m, mine in state.initial.mines.items()
                        if mine.owner == 'n0' and not state.deposits[state.mine_deposit[m]].renewable)
        state.history['n0'].clear()
        # Life L = remaining / mean20 extraction = 50 cycles -> sqrt(50/200) = .5
        per_cycle = remaining // 50
        state.history['n0'].append(CycleEconomy(extracted={'coal': per_cycle * 20}))
        self.assertEqual(market.reserve_life_factor('n0-mining'), Q(1, 2))
        state.history['n0'].clear()
        state.history['n0'].append(CycleEconomy(extracted={'coal': remaining * 1000}))
        self.assertEqual(market.reserve_life_factor('n0-mining'), Q(3, 10))

    # ---- C25 ----
    def test_war_zone_outage_multiplier(self):
        state, battles = self.state, self.state.battles
        hub = 'n0-h0'
        region = state.initial.map.hubs[hub].region
        battles.zone_cycle = 4
        battles.zone_combat = {region: 5}
        self.assertEqual(battles.outage_multiplier('n0', hub, 5), 1)  # not at war
        state.initial.diplomacy.phase1(0, declarations=(('n1', 'n0'),))
        state.pollution[hub] = 0
        # Qsec = 1 - .4*.5 = .8 -> x(1 + 2*.2) = 1.4
        self.assertEqual(battles.outage_multiplier('n0', hub, 5), Q(14, 10))
        battles.zone_blockade = {region: Q(10)}
        self.assertEqual(battles.outage_multiplier('n0', hub, 5), 1 + 2 * (Q(1, 5) + Q(3, 10)))
        self.assertEqual(battles.outage_multiplier('n0', hub, 7), 1)  # stale record
        battles.zone_combat, battles.zone_blockade = {}, {}
        self.assertEqual(battles.outage_multiplier('n0', hub, 5), 1)

    def test_zone_record_rolls_each_cycle(self):
        battles = self.state.battles
        battles.combat_regions = {'r0'} if 'r0' in self.state.initial.map.regions else set(list(self.state.initial.map.regions)[:1])
        region = next(iter(battles.combat_regions))
        battles.record_zone(SimpleNamespace(cycle=3))
        battles.record_zone(SimpleNamespace(cycle=3))
        self.assertEqual(battles.zone_combat, {region: 2})
        battles.combat_regions = set()
        battles.record_zone(SimpleNamespace(cycle=4))
        self.assertEqual(battles.previous_zone, {region: (2, Q(0))})
        self.assertEqual(battles.zone_combat, {})


if __name__ == '__main__':
    unittest.main()
