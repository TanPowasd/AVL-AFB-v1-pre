"""rift_era_rules_r9 (decision 0016): A1 world-pool spread, A2 warehouse priority
& mine toggle, A3 permanent opening endowment exemption, A4 opening headroom &
empty-shell K_infra discount, and A5.1-A5.3 rule/engine parity fixes."""
from types import SimpleNamespace as NS
import unittest
from fractions import Fraction as Q

from organizer.rift_era.engine.assets import Asset
from organizer.rift_era.engine.catalog import Catalog
from organizer.rift_era.engine.consumption import Consumption
from organizer.rift_era.engine.diplomacy import Relation
from organizer.rift_era.engine.economy import opening_economy
from organizer.rift_era.engine.errors import RuleViolation
from organizer.rift_era.engine.industry_commands import IndustryCommands
from organizer.rift_era.engine.initialization import Facility, initialize
from organizer.rift_era.engine.ledger import Account, CashAccount
from organizer.rift_era.engine.logistics import Corridor, Shipment, TransitMode, Vehicle
from organizer.rift_era.engine.match import match_economy
from organizer.rift_era.engine.numeric import ceil, floor
from organizer.rift_era.engine.own_observation import owned_entities
from organizer.rift_era.engine.periodic_contracts import PeriodicSwap, SwapProposal
from organizer.rift_era.engine.prisoners import settle_cession_assets
from organizer.rift_era.engine.production import Line, Production, Recipe
from organizer.rift_era.engine.strike_operations import StrikeCommands
from organizer.rift_era.engine.world import generate_candidate


class R9BalanceAndParityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.catalog = Catalog()
        cls.world = generate_candidate(1000)

    def test_a1_world_pool_bid_ask_spread_kills_roundtrip_arbitrage(self):
        game = opening_economy(self.catalog, initialize(self.catalog, self.world),
                               total_ticks=20, debug=False, commerce=True)
        state = game.state
        wp = state.world_pool
        for good, row in self.catalog.goods.items():
            if row['kind'] != 'inventory':
                continue
            w = self.catalog.weights[good]
            self.assertEqual(wp.buy_price_mU(good), ceil(Q(23, 2) * w * 1000))
            self.assertEqual(wp.sell_price_mU(good), floor(Q(17, 2) * w * 1000))
            self.assertGreater(wp.buy_price_mU(good), wp.sell_price_mU(good))
        reserves = CashAccount('n0', 'reserves', 'RU')
        state.initial.cash.transfer(CashAccount('world', 'pool', 'RU'), reserves, 5_000_000)
        before_ru = state.initial.cash.balance(reserves).native_mU
        # Sell 10 steel and buy 10 steel in the same tick: round-trip loses 30% of mid price.
        game.step({'n0': (
            {'id': '1', 'kind': 'trade', 'op': 'pool_sell', 'hub_id': 'n0-h0', 'good': 'steel', 'qty_mU': 10_000},
            {'id': '2', 'kind': 'trade', 'op': 'pool_buy', 'hub_id': 'n0-h0', 'good': 'steel', 'qty_mU': 10_000},
        )})
        after_ru = state.initial.cash.balance(reserves).native_mU
        w_steel = self.catalog.weights['steel']
        expected_loss = 10 * (ceil(Q(23, 2) * w_steel * 1000) - floor(Q(17, 2) * w_steel * 1000))
        self.assertGreater(expected_loss, 0)
        self.assertEqual(before_ru - after_ru, expected_loss)
        vis = wp.visible('n0')
        self.assertIn('buy_price_ru_mU', vis)
        self.assertIn('sell_price_ru_mU', vis)
        state.audit()

    def test_a2_warehouse_priority_manufacturers_before_mines(self):
        state = initialize(self.catalog, self.world)
        state.inventory.enable_provenance(seed=1000)
        prod = Production(1000, state.inventory, {g: Q(1) for g in self.catalog.goods})
        hub = 'n0-h0'
        mfg_recipe = Recipe.from_catalog(self.catalog, 'P031')  # 2 grain + 1 water -> 3 food (3000 mU)
        ext_recipe = Recipe('extract:iron_ore', {'iron_ore': 6000}, {'iron_ore': 6000}, frozenset(), frozenset())
        mfg_line = Line('test/mfg', 'n0', hub, mfg_recipe)
        ext_line = Line('test/ext', 'n0', hub, ext_recipe)
        from organizer.rift_era.engine.materials import Origin
        dep = Account('nature', 'test/ext', 'deposit', 'iron_ore')
        state.inventory.transform(
            {},
            {Account('n0', hub, 'available', 'grain'): 10_000,
             Account('n0', hub, 'available', 'water'): 10_000,
             dep: 50_000},
            reason='test_setup',
            origins={Account('n0', hub, 'available', 'grain'): Origin('n0', hub),
                     Account('n0', hub, 'available', 'water'): Origin('n0', hub),
                     dep: Origin('nature', hub)},
        )
        # Only 2500 mU free warehouse space: P031 at 500 batch needs 1500 mU; mine wants up to 3000 mU.
        # Pass 1 gives P031 its full 1500 mU; Pass 2 clips the mine to the remaining 1000 mU
        # (max integer batch x with 6*x <= 1000 is x=166 -> 996 mU).
        results = {
            r.line_id: r for r in prod.advance(
                0,
                [mfg_line, ext_line],
                {'test/mfg': 500, 'test/ext': 500},
                free_capacity_mU={hub: 2500},
                input_sources={'test/ext': ('nature', 'test/ext', 'deposit')},
            )
        }
        self.assertEqual(results['test/mfg'].released_mU['food'], 1500)
        self.assertEqual(results['test/ext'].released_mU['iron_ore'], 996)

    def test_a2_mine_plant_toggle_pauses_and_resumes_extraction(self):
        game = opening_economy(self.catalog, initialize(self.catalog, self.world),
                               total_ticks=20, debug=False, commerce=True)
        state = game.state
        iron_mine = next(mid for mid, m in sorted(state.initial.mines.items())
                         if m.owner == 'n0' and m.good == 'iron_ore')
        water_mine = next(mid for mid, m in sorted(state.initial.mines.items())
                          if m.owner == 'n0' and m.good == 'water')
        obs0 = {m['id']: m for m in owned_entities(state, 'n0')['mines']}
        self.assertTrue(obs0[iron_mine]['online'])
        game.step({'n0': (
            {'id': '1', 'kind': 'industry', 'op': 'plant_toggle', 'facility_id': iron_mine, 'online': False},
            {'id': '2', 'kind': 'industry', 'op': 'plant_toggle', 'facility_id': water_mine, 'online': False},
        )})
        self.assertIn(iron_mine, state.paused_lines)
        self.assertIn(water_mine, state.paused_lines)
        obs1 = {m['id']: m for m in owned_entities(state, 'n0')['mines']}
        self.assertFalse(obs1[iron_mine]['online'])
        dep_before = state.deposits[state.mine_deposit[iron_mine]].remaining_mU
        while not game.clock.is_decision(game.tick):
            game.step({})
        self.assertEqual(state.deposits[state.mine_deposit[iron_mine]].remaining_mU, dep_before)
        game.step({'n0': (
            {'id': '3', 'kind': 'industry', 'op': 'plant_toggle', 'facility_id': iron_mine, 'online': True},
            {'id': '4', 'kind': 'industry', 'op': 'plant_toggle', 'facility_id': water_mine, 'online': True},
        )})
        self.assertNotIn(iron_mine, state.paused_lines)
        game.step({})
        self.assertLess(state.deposits[state.mine_deposit[iron_mine]].remaining_mU, dep_before)
        state.audit()

    def test_a3_permanent_endowment_exemption_across_late_cycles(self):
        c = Consumption()
        c.set_endowment({'n0': {'medicine': Q(160_000)}})
        c.cycle = 35
        self.assertEqual(c.eta_physical('n0', 'medicine', Q(160_000)), Q(0))
        c.observe_stock(35, {'n0': {'medicine': Q(60_000)}})
        self.assertEqual(c.endowment[('n0', 'medicine')], Q(60_000))
        c.observe_stock(36, {'n0': {'medicine': Q(150_000)}})
        self.assertEqual(c.endowment[('n0', 'medicine')], Q(60_000))
        self.assertEqual(c.eta_physical('n0', 'medicine', Q(150_000)), Q(90_000))
        c.observe_stock(37, {'n0': {'medicine': Q(0)}})
        self.assertNotIn(('n0', 'medicine'), c.endowment)
        self.assertEqual(c.eta_physical('n0', 'medicine', Q(50_000)), Q(50_000))
        c.audit()

    def test_a4_opening_headroom_and_empty_shell_kinfra_discount(self):
        iw = self.catalog.runtime['initial_world']
        self.assertEqual(iw['load_overrides']['P033'], Q(1, 2))
        self.assertEqual(iw['farm_fertility'], Q(5, 4))
        self.assertEqual(iw['inventory']['medicine'], 160)
        self.assertEqual(iw['inventory']['food'], 210)
        self.assertEqual(iw['inventory']['grain'], 120)

        game = match_economy(self.catalog, initialize(self.catalog, self.world),
                             total_ticks=40, fingerprint_each_tick=False)
        state = game.state
        game.step()
        np = state.national_power
        connected = np.connected_regions('n0')
        before = np._kinfra('n0', connected)

        # Register a commissioned unassigned idle S1 industrial shell.
        fid, lid = 'asset:test_shell', 'asset:test_shell/line/0'
        costs_nmU = {'steel': Q(6_000_000), 'cement': Q(6_000_000), 'basic_parts': Q(2_000_000)}
        from organizer.rift_era.engine.materials import Origin
        draft = state.initial.inventory.materials.draft()
        for g, amt in costs_nmU.items():
            draft.create(Account('n0', 'n0-h0', f'capital:{fid}', g), {Origin('n0', 'n0-h0'): amt / 1000}, virtual=True)
        state.initial.inventory.transform({}, {}, reason='test_shell', material_plan=draft.finish())
        state.initial.assets[fid] = Asset(fid, 'n0', 'n0-h0', {g: int(q) for g, q in costs_nmU.items()},
                                          book_nmU=dict(costs_nmU), under_construction=False)
        state.initial.assets[lid] = Asset(lid, 'n0', 'n0-h0', {}, book_nmU={}, under_construction=False)
        state.initial.facilities[fid] = Facility(fid, 'n0', 'n0-h0', 'S1', '机械厂', (lid,), commissioned=True)
        idle_rec = Recipe('__idle__', {}, {}, frozenset(), frozenset())
        state.initial.lines[lid] = Line(lid, 'n0', 'n0-h0', idle_rec)
        state.slot_use[lid] = 'unassigned'
        state.authority.sync_assets(state.initial.assets)

        w = self.catalog.weights
        full_book = 6 * w['steel'] + 6 * w['cement'] + 2 * w['basic_parts']
        idle_kinfra = np._kinfra('n0', connected)
        self.assertEqual(idle_kinfra - before, full_book * Q(1, 4))

        # Once a recipe is assigned to any slot, the shell counts at 100%.
        state.initial.lines[lid].recipe = Recipe.from_catalog(self.catalog, 'P011')
        state.slot_use[lid] = 'production'
        active_kinfra = np._kinfra('n0', connected)
        self.assertEqual(active_kinfra - before, full_book)
        state.audit()

    def test_a5_1_interdict_aliases_visibility_range_and_carrier_domain(self):
        game = opening_economy(self.catalog, initialize(self.catalog, self.world),
                               total_ticks=20, debug=False, commerce=True)
        state = game.state
        state.initial.diplomacy.phase1(0, declarations=(('n0', 'n1'),))
        handler = StrikeCommands(state)

        # Create a freighter shipment right on n0's road/corridor so n0 garrison can see and reach it,
        # and a distant shipment at n1-h0..n1-h1 that n0 cannot reach (and if unseen, cannot detect).
        near_edge = next(eid for eid, c in sorted(state.cargo.corridors.items()) if 'n0-h0' in (c.a, c.b))
        far_edge = next(eid for eid, c in sorted(state.cargo.corridors.items()) if 'n1-h0' in (c.a, c.b))

        def make_shipment(sid, vid, good_carrier, edge_id):
            edge = state.cargo.corridors[edge_id]
            v_acc = Account('n1', vid, 'deployed_vehicle', good_carrier)
            from organizer.rift_era.engine.materials import Origin
            state.initial.inventory.transform(
                {},
                {v_acc: 1000, Account('n1', sid, 'in_transit', 'food'): 10_000},
                reason='test_ship',
                origins={v_acc: Origin('n1', edge.a), Account('n1', sid, 'in_transit', 'food'): Origin('n1', edge.a)},
            )
            state.cargo.vehicles[vid] = Vehicle(vid, 'n1', None, good_carrier, 20_000, Q(8), Q(10),
                                                assigned=sid, physical_account=v_acc)
            ship = Shipment(sid, 'n1', vid, 'food', 10_000, edge.a, edge.b, (edge_id,),
                            0, 0, edge.a, index=0, mode=TransitMode.IN_TRANSIT)
            state.cargo.transport.shipments[sid] = ship
            return ship

        make_shipment('raw-near-1', 'fleet:n1:900', 'freighter', near_edge)
        make_shipment('raw-far-2', 'fleet:n1:901', 'vehicle', far_edge)
        state.strikes.publish()

        contacts = state.strikes.visible('n0')['shipment_contacts']
        self.assertEqual(len(contacts), 1)
        alias = contacts[0]['contact_id']
        self.assertEqual(alias, 'ship-contact-1')
        self.assertEqual(contacts[0]['carrier_kind'], 'freighter')

        # Raw internal shipment ID is never accepted.
        with self.assertRaises(RuleViolation) as err:
            handler.prepare('n0', {'id': '10', 'kind': 'military', 'op': 'interdict',
                                   'formation_id': 'n0/garrison/0/0', 'contact_id': 'raw-near-1'}, NS(tick=0))
        self.assertEqual(err.exception.code, 'ERR_NO_VISIBLE_TARGET')

        # Suppose orbital coverage makes far_edge visible, range_hops=0 garrison still cannot reach it.
        state.research['n0'].known.add('N06')
        from organizer.rift_era.engine.orbital_operations import Satellite
        state.orbit.satellites['sat:test'] = Satellite('sat:test', 'n0', 'n0-h0', (0, 0, 0), 0, 999)
        state.strikes.publish()
        far_contacts = [c for c in state.strikes.visible('n0')['shipment_contacts'] if c['edge_id'] == far_edge]
        if far_contacts:
            with self.assertRaises(RuleViolation) as err:
                handler.prepare('n0', {'id': '11', 'kind': 'military', 'op': 'interdict',
                                       'formation_id': 'n0/garrison/0/0',
                                       'contact_id': far_contacts[0]['contact_id']}, NS(tick=0))
            self.assertEqual(err.exception.code, 'ERR_SITE_INCOMPATIBLE')
        del state.orbit.satellites['sat:test']
        state.strikes.publish()

        # Interdict near freighter via contact_id: uses 'sea' firepower channel (infantry sea=2 -> 2/100 = 20 mU share).
        state.initial.inventory.transform(
            {}, {Account('n0', 'n0-h0', 'available', 'ammunition'): 5000},
            reason='test_ammo',
        )
        handler.prepare('n0', {'id': '12', 'kind': 'military', 'op': 'interdict',
                               'formation_id': 'n0/garrison/0/0', 'contact_id': alias}, NS(tick=0)).commit()
        state.strikes.advance(NS(tick=0, cycle=0, seed=1000))
        recent = state.strikes.visible('n0')['recent'][-1]
        self.assertEqual(recent['contact_id'], alias)
        # Garrison has 3 infantry (sea=2 each -> attack=6, share=6/100=60 mU; ground=54->250 mU cap, aa=18->180 mU).
        self.assertEqual(recent['damage_share_mU'], 60)

        # Air carrier uses 'aa' firepower channel: trim to 2 infantry (aa=8 each -> 16 -> 160 mU; sea=4->40 mU; ground=36->250 mU).
        make_shipment('raw-air-3', 'fleet:n1:902', 'transport_aircraft', near_edge)
        state.strikes.publish()
        air_alias = next(c['contact_id'] for c in state.strikes.visible('n0')['shipment_contacts']
                         if c['carrier_kind'] == 'transport_aircraft')
        formation = state.formations.rows['n0/garrison/0/0']
        kept = formation.unit_ids[:2]
        for uid in formation.unit_ids[2:]:
            spec = self.catalog.units[state.initial.units[uid].unit_type]
            state.initial.inventory.transform(
                {state.formations.equipment_account('n0', uid, g): state.initial.inventory.balance(
                    state.formations.equipment_account('n0', uid, g)) for g in spec['installed']},
                {}, reason='trim_unit',
            )
            del state.initial.units[uid]
            formation.crew_skills.pop(uid, None)
        formation.unit_ids = kept
        handler.prepare('n0', {'id': '13', 'kind': 'military', 'op': 'interdict',
                               'formation_id': 'n0/garrison/0/0', 'contact_id': air_alias}, NS(tick=1)).commit()
        state.strikes.advance(NS(tick=1, cycle=0, seed=1000))
        self.assertEqual(state.strikes.visible('n0')['recent'][-1]['damage_share_mU'], 160)
        state.audit()

    def test_a5_2_occupied_resident_foreign_cash_fx_and_cession_physical_handoff(self):
        game = opening_economy(self.catalog, initialize(self.catalog, self.world),
                               total_ticks=30, debug=False, commerce=True)
        state = game.state
        region = state.initial.map.hubs['n1-h0'].region
        state.initial.diplomacy.phase1(0, declarations=(('n0', 'n1'),))
        state.authority.regions[region].controller = 'n0'

        # Drain native n1 household cash at n1-h0 and deposit n0-currency resident wages there.
        native_hh = state.native('n1', 'household:n1-h0')
        foreign_hh = state.native('n0', 'household:n1-h0')
        bal_n1 = state.initial.cash.balance(native_hh).native_mU
        state.initial.cash.transfer(native_hh, state.native('n1', 'treasury'), bal_n1)
        state.initial.cash.transfer(state.native('n0', 'treasury'), foreign_hh, 5_000_000)
        food_before = state.initial.inventory.balance(Account('n1', 'n1-h0', 'available', 'food'))

        game.step({})
        food_after = state.initial.inventory.balance(Account('n1', 'n1-h0', 'available', 'food'))
        # Residents converted part of their n0-currency wages via FX to buy subsistence food!
        self.assertLess(food_after, food_before)
        self.assertLess(state.initial.cash.balance(foreign_hh).native_mU, 5_000_000)
        state.audit()

        # Peace cession outside tick hands off physical ownership of unpledged commissioned assets.
        target_fac = next(fid for fid, f in sorted(state.initial.facilities.items())
                          if f.hub == 'n1-h0' and f.owner == 'n1' and f.line_ids)
        target_line = state.initial.facilities[target_fac].line_ids[0]
        target_mine = next(mid for mid, m in sorted(state.initial.mines.items())
                           if m.hub == 'n1-h0' and m.owner == 'n1')
        pledged_fac = next(fid for fid, f in sorted(state.initial.facilities.items())
                           if f.hub == 'n1-h0' and f.owner == 'n1' and fid != target_fac)
        state.authority.pledge(pledged_fac, 'loan:keep', 'n1', 200)
        state.authority.cede('n1', 'n0', [region])
        settle_cession_assets(state, 'n1', 'n0', [region])

        self.assertEqual(state.initial.assets[target_fac].owner, 'n0')
        self.assertEqual(state.initial.facilities[target_fac].owner, 'n0')
        self.assertEqual(state.initial.lines[target_line].owner, 'n0')
        self.assertEqual(state.initial.mines[target_mine].owner, 'n0')
        self.assertEqual(state.maintenance.targets[target_fac].payer.owner, 'n0')
        # Pledged facility stays operated and physically owned by n1.
        self.assertEqual(state.initial.assets[pledged_fac].owner, 'n1')
        # New owner n0 can toggle its newly ceded mine directly!
        IndustryCommands(state).prepare(
            'n0',
            {'id': '99', 'kind': 'industry', 'op': 'plant_toggle', 'facility_id': target_mine, 'online': False},
            NS(tick=2, clock=NS(cycle_ticks=10)),
        ).commit()
        self.assertIn(target_mine, state.paused_lines)
        state.audit()

    def test_a5_3_cross_border_power_contract_refund_uses_reserves_fallback(self):
        game = opening_economy(self.catalog, initialize(self.catalog, self.world),
                               total_ticks=20, debug=False, commerce=True)
        state = game.state
        cash = state.initial.cash
        pool = CashAccount('world', 'pool', 'RU')
        corp_seller = CashAccount('n0', 'corp:energy', 'RU')
        reserves_seller = CashAccount('n0', 'reserves', 'RU')
        reserves_buyer = CashAccount('n1', 'reserves', 'RU')
        before_res_seller = cash.balance(reserves_seller).native_mU
        before_res_buyer = cash.balance(reserves_buyer).native_mU
        cash.transfer(pool, corp_seller, 2000)
        cash.transfer(pool, reserves_seller, 10_000)

        proposal = SwapProposal(
            'swap_proposal:n0:1', 'n0', 'n1', '1', '', '', 'electricity', '',
            10_000, 0, 1, 20, 20,
            power_link='power:test', power_qty_mU=10_000, price_cap_ru_mU=1000,
        )
        row = PeriodicSwap(proposal, start_cycle=0, end_cycle=20, next_cycle=1)
        row.power_delivered_mU = 10_000
        row.power_paid_ru_mU = Q(15_000)  # allowed = 10_000 -> excess = 5_000 RU mU
        credit_before = state.credit['n0']

        state.contracts.swaps.power_period(row, NS(cycle=1))
        self.assertEqual(row.status, 'ACTIVE')
        self.assertEqual(state.credit['n0'], credit_before)
        self.assertEqual(row.power_refunded_ru_mU, 5000)
        self.assertEqual(cash.balance(corp_seller).native_mU, 0)
        self.assertEqual(cash.balance(reserves_seller).native_mU, before_res_seller + 7000)
        self.assertEqual(cash.balance(reserves_buyer).native_mU, before_res_buyer + 5000)
        state.audit()


if __name__ == '__main__':
    unittest.main()
