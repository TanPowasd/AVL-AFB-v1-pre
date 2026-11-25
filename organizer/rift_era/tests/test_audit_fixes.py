"""Regression tests for P0, P1, and P2 forensic audit fixes in rift_era."""

from __future__ import annotations

from decimal import Decimal
from fractions import Fraction
import importlib.util
import json
import os
from pathlib import Path
import re
import sys
import tempfile
from types import SimpleNamespace as NS
import unittest
from unittest import mock
import zipfile

from organizer.rift_era.engine.archive import extracted_submission
from organizer.rift_era.engine.assets import Asset
from organizer.rift_era.engine.catalog import Catalog
from organizer.rift_era.engine.clock import ClockConfig
from organizer.rift_era.engine.commands import EngineCommands
from organizer.rift_era.engine.continuity import ContinuityCommands
from organizer.rift_era.engine.contracts import ContractCommands
from organizer.rift_era.engine.driver import (
    MatchDriver,
    economic_development_init,
    economic_development_messages,
    replay_decisions,
)
from organizer.rift_era.engine.economy import Economy, opening_economy
from organizer.rift_era.engine.errors import RuleViolation
from organizer.rift_era.engine.initialization import initialize
from organizer.rift_era.engine.ipc import StrategySession
from organizer.rift_era.engine.ledger import Account, CashAccount, CashTransfer
from organizer.rift_era.engine.match import match_economy
from organizer.rift_era.engine.materials import Origin
from organizer.rift_era.engine.numeric import Q
from organizer.rift_era.engine.protocol import MAX_INT_ABS, decode_json, validate_shape
from organizer.rift_era.engine.replay import verify_replay
from organizer.rift_era.engine.world import generate_candidate
from organizer.rift_era.pipeline import hosting, scoring as pipeline_scoring
from organizer.rift_era.tests import test_contracts as contracts_base
from organizer.rift_era.tests import test_equity_finance as eq_base
from organizer.rift_era.tests import test_strike_operations as strike_base

RIFT_ROOT = Path(__file__).resolve().parents[1]


class AuditFixesTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.catalog = Catalog()
        cls.world = generate_candidate(1)

    def _fresh_match(self, total_ticks=40, fingerprint_each_tick=False):
        return match_economy(
            self.catalog,
            initialize(self.catalog, self.world),
            total_ticks=total_ticks,
            fingerprint_each_tick=fingerprint_each_tick,
        )

    # ---- P0-1: Duplicate same-window finance/fx_cover ----
    def test_p0_1_duplicate_fx_cover_same_window_no_crash(self):
        eng = self._fresh_match()
        state = eng.state
        state.research['n0'].known.add('F03')
        state.initial.cash.transfer(
            CashAccount('world', 'pool', 'RU'),
            CashAccount('n0', 'reserves', 'RU'),
            50_000_000,
        )
        # Window 0: open an FX short on n1
        eng.step({
            'n0': ({'id': '1', 'kind': 'finance', 'op': 'fx_short', 'currency': 'n1', 'amount_mU': 100_000},),
        })
        pos = state.forward_finance.shorts.get(('n0', 'n1'))
        self.assertIsNotNone(pos)
        owed = pos.owed_mU
        self.assertGreater(owed, 0)

        # Advance to window 1 (tick 5) and send TWO full fx_cover orders in the SAME window
        for _ in range(4):
            eng.step({})
        eng.step({
            'n0': (
                {'id': '2', 'kind': 'finance', 'op': 'fx_cover', 'currency': 'n1', 'amount_mU': owed},
                {'id': '3', 'kind': 'finance', 'op': 'fx_cover', 'currency': 'n1', 'amount_mU': owed},
            ),
        })
        obs = economic_development_messages(Economy(state), 'n0', eng.tick, eng.clock)
        receipts = {r['id']: r for r in obs['command_receipts']}
        self.assertEqual(receipts['2']['status'], 'ACCEPTED')
        self.assertEqual(receipts['3']['status'], 'REJECTED')
        self.assertEqual(receipts['3']['code'], 'ERR_STATE')
        self.assertNotIn(('n0', 'n1'), state.forward_finance.shorts)
        state.audit()

    # ---- P0-2: Windows AppContainer per-seat isolation, clean env, fail-closed, disk quota ----
    @unittest.skipUnless(os.name == 'nt', 'Windows AppContainer test')
    def test_p0_2_windows_appcontainer_isolation_env_failclosed_and_quota(self):
        launcher = hosting.select_launcher('windows')
        if launcher.sandbox != 'windows_appcontainer_job':
            self.skipTest(f'AppContainer unavailable on this host ({launcher.sandbox})')

        probe_bot = (
            'import json, os, pathlib, sys\n'
            'while True:\n'
            '    raw = sys.stdin.readline()\n'
            '    if not raw:\n'
            '        break\n'
            '    req = json.loads(raw)\n'
            '    act = req["act"]\n'
            '    out = {}\n'
            '    if act == "info":\n'
            '        work = pathlib.Path(os.environ["USERPROFILE"])\n'
            '        (work / "secret.txt").write_text("seat-a-secret", encoding="utf-8")\n'
            '        bot_ro = False\n'
            '        try:\n'
            '            (pathlib.Path(sys.path[0]) / "tamper.txt").write_text("x", encoding="utf-8")\n'
            '        except PermissionError:\n'
            '            bot_ro = True\n'
            '        out = {\n'
            '            "work": str(work),\n'
            '            "bot": sys.path[0],\n'
            '            "bot_ro": bot_ro,\n'
            '            "hashseed": os.environ.get("PYTHONHASHSEED"),\n'
            '            "secret_leaked": "AUDIT_HOST_SECRET" in os.environ,\n'
            '        }\n'
            '    elif act == "probe_other":\n'
            '        target = pathlib.Path(req["path"])\n'
            '        read_blocked = False\n'
            '        write_blocked = False\n'
            '        try:\n'
            '            (target / "secret.txt").read_text(encoding="utf-8")\n'
            '        except PermissionError:\n'
            '            read_blocked = True\n'
            '        try:\n'
            '            (target / "pwn.txt").write_text("pwn", encoding="utf-8")\n'
            '        except PermissionError:\n'
            '            write_blocked = True\n'
            '        out = {"read_blocked": read_blocked, "write_blocked": write_blocked}\n'
            '    elif act == "bloat":\n'
            '        work = pathlib.Path(os.environ["USERPROFILE"])\n'
            '        (work / "bloat.bin").write_bytes(b"X" * req["size"])\n'
            '        out = {"wrote": req["size"]}\n'
            '    print(json.dumps(out), flush=True)\n'
        )

        with tempfile.TemporaryDirectory() as tmp:
            bot_a = Path(tmp) / 'bot_a'
            bot_b = Path(tmp) / 'bot_b'
            bot_a.mkdir()
            bot_b.mkdir()
            (bot_a / 'main.py').write_text(probe_bot, encoding='utf-8')
            (bot_b / 'main.py').write_text(probe_bot, encoding='utf-8')

            old_secret = os.environ.get('AUDIT_HOST_SECRET')
            os.environ['AUDIT_HOST_SECRET'] = 'host-super-secret-value'
            seat_a = seat_b = seat_q = None
            try:
                seat_a = hosting.WindowsSeatProcess(bot_a, require_appcontainer=True)
                seat_b = hosting.WindowsSeatProcess(bot_b, require_appcontainer=True)
                self.assertEqual(seat_a.sandbox, 'windows_appcontainer_job')
                self.assertEqual(seat_b.sandbox, 'windows_appcontainer_job')

                seat_a.process.stdin.write(b'{"act":"info"}\n')
                seat_a.process.stdin.flush()
                info_a = json.loads(seat_a.process.stdout.readline().decode('utf-8'))
                self.assertEqual(info_a['hashseed'], '0')
                self.assertFalse(info_a['secret_leaked'])
                self.assertTrue(info_a['bot_ro'])
                self.assertNotEqual(Path(info_a['bot']).resolve(), bot_a.resolve())

                seat_b.process.stdin.write(json.dumps({'act': 'probe_other', 'path': info_a['work']}).encode('utf-8') + b'\n')
                seat_b.process.stdin.flush()
                res_b = json.loads(seat_b.process.stdout.readline().decode('utf-8'))
                self.assertTrue(res_b['read_blocked'])
                self.assertTrue(res_b['write_blocked'])

                # Fail-closed when require_appcontainer=True and AppContainer setup fails
                with mock.patch.object(hosting._userenv, 'CreateAppContainerProfile', return_value=-2147024891):
                    with self.assertRaises(RuntimeError):
                        hosting.WindowsSeatProcess(bot_a, require_appcontainer=True)

                # 64 MiB (parameterized to 4 KiB) disk quota guard kills offending seat cleanly
                seat_q = hosting.WindowsSeatProcess(bot_a, require_appcontainer=True, max_work_bytes=4096)
                seat_q.process.stdin.write(b'{"act":"bloat","size":8192}\n')
                seat_q.process.stdin.flush()
                json.loads(seat_q.process.stdout.readline().decode('utf-8'))
                self.assertFalse(seat_q.check_disk_quota())
                self.assertTrue(seat_q._quota_exceeded)
                # Verify pause() also enforces disk quota
                seat_q2 = hosting.WindowsSeatProcess(bot_a, require_appcontainer=True, max_work_bytes=4096)
                try:
                    seat_q2.process.stdin.write(b'{"act":"bloat","size":8192}\n')
                    seat_q2.process.stdin.flush()
                    json.loads(seat_q2.process.stdout.readline().decode('utf-8'))
                    seat_q2.pause()
                    self.assertTrue(seat_q2._quota_exceeded)
                finally:
                    seat_q2.close()
            finally:
                if seat_a is not None:
                    seat_a.close()
                if seat_b is not None:
                    seat_b.close()
                if seat_q is not None:
                    seat_q.close()
                if old_secret is None:
                    os.environ.pop('AUDIT_HOST_SECRET', None)
                else:
                    os.environ['AUDIT_HOST_SECRET'] = old_secret

    # ---- P0-3: Archive Windows reserved names, illegal chars, and OSError conversion ----
    def test_p0_3_archive_rejects_windows_reserved_and_illegal_paths(self):
        bad_names = ['NUL', 'sub/con.py', 'COM1.txt', 'lpt9.dat', 'bad?name.py', 'bad*name.py', 'trailing ', 'dot.']
        with tempfile.TemporaryDirectory() as tmp:
            for idx, bad in enumerate(bad_names):
                zpath = Path(tmp) / f'bad_{idx}.zip'
                with zipfile.ZipFile(zpath, 'w') as zf:
                    zf.writestr('main.py', 'print(1)\n')
                    zf.writestr(bad, 'x')
                with self.assertRaises(RuleViolation) as cm:
                    with extracted_submission(zpath):
                        pass
                self.assertEqual(cm.exception.code, 'ERR_ARCHIVE_SECURITY', f'failed for {bad!r}')

            # Verify raw OSError inside extraction converts into ERR_ARCHIVE_SECURITY
            good_zip = Path(tmp) / 'good.zip'
            with zipfile.ZipFile(good_zip, 'w') as zf:
                zf.writestr('main.py', 'print(1)\n')
            with mock.patch.object(zipfile.ZipFile, 'open', side_effect=OSError('fs rejected')):
                with self.assertRaises(RuleViolation) as cm:
                    with extracted_submission(good_zip):
                        pass
                self.assertEqual(cm.exception.code, 'ERR_ARCHIVE_SECURITY')

    # ---- P0-4: player-guide.md JSON examples & obs keys + task.txt purity ----
    def test_p0_4_player_guide_examples_obs_keys_and_prompt(self):
        guide = (RIFT_ROOT / 'kit' / 'handbook' / 'player-guide.md').read_text(encoding='utf-8')
        blocks = re.findall(r'```json\s*(.*?)```', guide, re.S)
        self.assertTrue(blocks, 'expected json examples block in player-guide.md')
        examples = [json.loads(line.strip()) for block in blocks for line in block.splitlines() if line.strip()]
        self.assertGreaterEqual(len(examples), 9)

        eng = self._fresh_match()
        eng.step({'n0': tuple(examples)})
        obs = economic_development_messages(Economy(eng.state), 'n0', eng.tick, eng.clock)
        receipts = {r['id']: r for r in obs['command_receipts']}
        for ex in examples:
            rcpt = receipts[ex['id']]
            self.assertNotEqual(rcpt['code'], 'ERR_SCHEMA', f'example {ex} hit ERR_SCHEMA: {rcpt}')

        # Verify all documented key names exist in real obs
        for real_key in (
            'cash_accounts', 'treasury_mU', 'population_mU', 'hubs', 'lines', 'facilities', 'mines',
            'research', 'markets', 'world_pool', 'macroeconomy', 'sovereign_finance', 'corporate_finance',
            'bond_market', 'stock_market', 'forward_finance', 'fx_fills', 'diplomacy',
            'military_contacts', 'orbital_contacts', 'shared_intelligence', 'city_states',
            'command_receipts', 'strikes',
        ):
            self.assertIn(real_key, obs)
            self.assertIn(f'`{real_key}`', guide)

        # Verify task.txt includes COMMANDS.md and obeys purity
        task_txt = (RIFT_ROOT / 'kit' / 'prompts' / 'task.txt').read_text(encoding='utf-8')
        self.assertIn('COMMANDS.md', task_txt)
        self.assertIn('`ai/license_offer`', task_txt)
        self.assertIsNone(re.search(r'organizer|组委会|OPEN_ISSUES|HEAVY_TESTS|TanPowasd|Powasd|/data/ws|/data/home', task_txt))

    # ---- P0-5: Protocol integer magnitude cap ----
    def test_p0_5_protocol_rejects_oversized_integers(self):
        validate_shape({'id': '1', 'kind': 'x', 'op': 'y', 'n': MAX_INT_ABS})
        validate_shape({'id': '1', 'kind': 'x', 'op': 'y', 'n': -MAX_INT_ABS})
        with self.assertRaises(RuleViolation) as cm:
            validate_shape({'id': '1', 'kind': 'x', 'op': 'y', 'n': MAX_INT_ABS + 1})
        self.assertEqual(cm.exception.code, 'ERR_SCHEMA')
        with self.assertRaises(RuleViolation) as cm2:
            decode_json(b'{"n": ' + b'9' * 4000 + b'}')
        self.assertEqual(cm2.exception.code, 'ERR_SCHEMA')

    # ---- P1-1: COMMANDS.md completeness & accuracy ----
    def test_p1_1_commands_md_complete_and_accurate(self):
        text = (RIFT_ROOT / 'kit' / 'COMMANDS.md').read_text(encoding='utf-8')
        self.assertNotIn('| — |', text)
        self.assertIn('license_kind', text)
        self.assertIn('strikes.shipment_contacts', text)
        self.assertNotIn('military.shipment_contacts', text)
        self.assertIn('align_engineers_mU', text)
        for cmd in (
            'diplomacy/city_state_aid', 'diplomacy/leave_joint_venture', 'diplomacy/military_aid',
            'finance/fx_cover', 'finance/fx_short', 'industry/install_storage',
            'industry/refill_storage', 'military/reinforce', 'trade/city_reexport',
        ):
            self.assertIn(f'`{cmd}`', text)

    # ---- P1-2a: world_pool checks active controller and enforces warehouse capacity ----
    def test_p1_2_world_pool_controller_and_warehouse_capacity(self):
        eng = self._fresh_match()
        state = eng.state
        state.initial.cash.transfer(
            CashAccount('world', 'pool', 'RU'), CashAccount('n0', 'reserves', 'RU'), 50_000_000
        )
        state.initial.cash.transfer(
            CashAccount('world', 'pool', 'RU'), CashAccount('n1', 'reserves', 'RU'), 50_000_000
        )
        # Occupy n0-h0 by n1 -> n0 must be denied, n1 permitted
        reg = state.initial.map.hubs['n0-h0'].region
        state.authority.regions[reg].controller = 'n1'
        ctx = NS(tick=0, cycle=0, seed=1)
        with self.assertRaises(RuleViolation) as cm:
            state.world_pool.prepare('n0', {'id': '1', 'kind': 'trade', 'op': 'pool_buy',
                                            'hub_id': 'n0-h0', 'good': 'food', 'qty_mU': 1000}, ctx)
        self.assertEqual(cm.exception.code, 'ERR_NO_PERMISSION')
        # n1 controls n0-h0 now, so prepare succeeds
        state.world_pool.prepare('n1', {'id': '1', 'kind': 'trade', 'op': 'pool_buy',
                                        'hub_id': 'n0-h0', 'good': 'food', 'qty_mU': 1000}, ctx)

        # Restore controller to n0 and fill warehouse near cap
        state.authority.regions[reg].controller = 'n0'
        cap = state.initial.warehouse_mU['n0-h0']
        load = state.warehouse_load().get('n0-h0', Q(0))
        good = 'food'
        unit_w = state.cargo_weights[good]
        free_target = unit_w * 5000
        filler_good = 'steel'
        filler_w = state.cargo_weights[filler_good]
        fill_qty = max(0, int((Q(cap) - load - free_target) // filler_w))
        if fill_qty > 0:
            acc = Account('n0', 'n0-h0', 'available', filler_good)
            state.initial.inventory.transform({}, {acc: fill_qty}, reason='test_fill_wh',
                                              origins={acc: Origin('n0', 'n0-h0')})
        before_free = max(Q(0), Q(state.initial.warehouse_mU['n0-h0']) - state.warehouse_load().get('n0-h0', Q(0)))
        max_units_mU = int(before_free // unit_w)
        req_qty = max_units_mU + 10_000
        res_before = state.initial.cash.balance(CashAccount('n0', 'reserves', 'RU')).native_mU
        state.world_pool.prepare('n0', {'id': '2', 'kind': 'trade', 'op': 'pool_buy',
                                        'hub_id': 'n0-h0', 'good': good, 'qty_mU': req_qty}, ctx).commit()
        clear_ctx = NS(tick=4, cycle=0, seed=1, clock=eng.clock)
        state.world_pool.clear(state, clear_ctx)
        delivered = state.initial.inventory.balance(Account('n0', 'n0-h0', 'next_available', good))
        self.assertLessEqual(delivered, max_units_mU)
        self.assertGreater(delivered, 0)
        # Excess cash refunded so warehouse is not overflowed
        self.assertLessEqual(state.warehouse_load().get('n0-h0', Q(0)), Q(state.initial.warehouse_mU['n0-h0']))
        self.assertGreater(state.initial.cash.balance(CashAccount('n0', 'reserves', 'RU')).native_mU,
                           res_before - req_qty * state.world_pool.buy_price_mU(good) // 1000)

    # ---- P1-2b: equity_finance fixed debt_book and short_liability_book max(cur, entry) ----
    def test_p1_2_equity_finance_fixed_book_and_short_max(self):
        state, finance = eq_base.EquityFinanceFundingTests().fixture()
        from organizer.rift_era.engine.equity_finance import Position
        pos = Position('n0', 'c', 'margin', debt_mU=500, debt_book_ru_mU=Q(500),
                       fees={'world': 100}, fee_books={'world': Q(100)}, debt_lots={Q(1): (500, Q(500))})
        finance.positions[('n0', 'c', 'margin')] = pos
        book_before = finance.debt_book('n0')
        state.rates['n1'] = Q(3)  # live FX triples; fixed historical book MUST NOT change
        self.assertEqual(finance.debt_book('n0'), book_before)

        # Short liability book must use max(cur_price, entry_price)
        short = Position('n0', 'c', 'short', shares=10, short_lots={(Q(1), 100): 10})
        finance.positions[('n0', 'c', 'short')] = short
        state.stock_market.exchange.prices['c'] = 60  # fell below entry 100
        self.assertEqual(finance.short_liability_book('n0', 0), Q(10 * 100))
        state.stock_market.exchange.prices['c'] = 150  # rose above entry 100
        self.assertEqual(finance.short_liability_book('n0', 0), Q(10 * 150))

    # ---- P1-2c: sovereign_loans book_ru_mU comes from CashLedger movement ----
    def test_p1_2_sovereign_loans_book_ru_from_cash_ledger(self):
        contracts_base.LicenseContractTests.setUpClass()
        state = contracts_base.LicenseContractTests().fixture().state
        state.initial.assets['collateral'] = Asset('collateral', 'n1', 'n1-h0', {})
        state.authority.sync_assets(state.initial.assets)
        # n0 holds n1 currency at historical book rate 1, while live rate is changed to 2
        state.initial.cash.transfer(CashAccount('world', 'pool', 'n1'), CashAccount('n0', 'treasury', 'n1'), 1000)
        state.rates['n1'] = Q(2)
        h = ContractCommands(state)
        ctx = NS(tick=0, cycle=0)
        offer = {
            'id': '1', 'kind': 'diplomacy', 'op': 'propose_contract', 'counterparty': 'n1',
            'clause_type': 2, 'duration_cycles': 10,
            'params': {'currency': 'n1', 'principal_mU': 1000, 'annual_rate_bp': 500, 'due_cycle': 5,
                       'collateral': [{'asset_id': 'collateral', 'shares_mU': 1000}]},
        }
        before_lender_book = state.initial.cash.balance(CashAccount('n0', 'treasury', 'n1')).book_ru_mU
        h.prepare('n0', offer, ctx).commit()
        state.contracts.publish()
        alias = state.contracts.visible('n1')['proposals'][0]['id']
        h.prepare('n1', {'id': '1', 'kind': 'diplomacy', 'op': 'accept_contract', 'proposal_id': alias}, ctx).commit()
        after_lender_book = state.initial.cash.balance(CashAccount('n0', 'treasury', 'n1')).book_ru_mU
        expected_book = before_lender_book - after_lender_book
        state.contracts.settle(ctx)
        loan = next(iter(state.contracts.loans.rows.values()))
        # Must equal actual CashLedger book movement (1000), NOT 1000 * live FX (2000)
        self.assertEqual(loan.book_ru_mU, expected_book)
        self.assertNotEqual(loan.book_ru_mU, Q(1000) * state.rates['n1'])

    # ---- P1-2d: strike_operations blockade persistent + no ammo teleport when hub is None ----
    def test_p1_2_strike_operations_persistent_and_no_ammo_teleport(self):
        strike_base.StrikeTests.setUpClass()
        state = strike_base.StrikeTests().fixture(war=True).state
        f = state.formations.rows['n0/garrison/0/0']
        prepared = state.strikes.blockade_prepare(
            'n0', {'id': '1', 'kind': 'military', 'op': 'blockade',
                   'formation_id': f.id, 'edge_id': 'road:n0-h0:n0-h1', 'target_nations': ['n1']},
            NS(tick=0, cycle=0, seed=1),
        )
        self.assertTrue(prepared.persistent)
        prepared.commit()

        # Move formation off-hub (naval domain so blockade runs off-hub) with empty onboard supplies; home_hub still has supplies
        f.domain = 'Sea-Surface'
        f.hub = None
        home_fuel_before = state.initial.inventory.balance(Account('n0', f.home_hub, 'available', 'fuel'))
        home_ammo_before = state.initial.inventory.balance(Account('n0', f.home_hub, 'available', 'ammunition'))
        state.strikes.advance(NS(tick=1, cycle=0, seed=1, clock=ClockConfig()))
        self.assertEqual(state.initial.inventory.balance(Account('n0', f.home_hub, 'available', 'fuel')), home_fuel_before)
        self.assertEqual(state.initial.inventory.balance(Account('n0', f.home_hub, 'available', 'ammunition')), home_ammo_before)
        # Unsupplied naval blockade must NOT teleport ammo from home_hub and must produce zero blockade ratio
        self.assertNotIn('n1', state.cargo.corridors['road:n0-h0:n0-h1'].blocked)

    # ---- P1-2e: continuity per-window limit & 256-command cap ----
    def test_p1_2_continuity_forbids_declare_war_and_caps_commands(self):
        game = opening_economy(self.catalog, initialize(self.catalog, self.world), total_ticks=20, debug=False, commerce=True)
        state = game.state
        state.research['n0'].known.add('C10')
        # Give n0-h0 an installed ai_core addon on a line
        lid = next(k for k, l in state.initial.lines.items() if l.owner == 'n0' and l.hub == 'n0-h0')
        state.line_options.setdefault(lid, {})['addons'] = ('ai_core',)
        handler = ContinuityCommands(state)
        ctx = NS(seed=1, tick=0, cycle=0, window=0, clock=ClockConfig())
        with self.assertRaises(RuleViolation) as cm:
            handler.prepare('n0', {
                'id': '1', 'kind': 'governance', 'op': 'conditional_plan', 'hub_id': 'n0-h0',
                'metric': 'hub_stock', 'ref_id': 'food', 'comparator': '>=', 'threshold_mU': 0,
                'cooldown_ticks': 5,
                'action': {'kind': 'diplomacy', 'op': 'declare_war', 'target_nation': 'n1'},
            }, ctx)
        self.assertEqual(cm.exception.code, 'ERR_SCHEMA')

        # Register a valid plan and verify it cannot fire when ctx.orders already uses 256 slots
        handler.prepare('n0', {
            'id': '2', 'kind': 'governance', 'op': 'conditional_plan', 'hub_id': 'n0-h0',
            'metric': 'hub_stock', 'ref_id': 'food', 'comparator': '>=', 'threshold_mU': 0,
            'cooldown_ticks': 5,
            'action': {'kind': 'governance', 'op': 'fiscal_policy',
                       'tax_rate_bp': 1000, 'welfare_budget_mU': 0, 'maint_budget_mU': 0},
        }, ctx).commit()
        cmds = EngineCommands(state)
        full_ctx = NS(seed=1, tick=0, cycle=0, window=0, clock=ClockConfig(),
                      orders={'n0': tuple({'id': str(i)} for i in range(256))})
        state.continuity.run(full_ctx, lambda o, order: cmds.prepare(o, order, full_ctx))
        reports = state.continuity.visible('n0')['reports']
        self.assertEqual(reports[-1]['status'], 'REJECTED')
        self.assertEqual(reports[-1]['code'], 'ERR_WINDOW_ORDER_CAP')

    # ---- P1-3: driver skips per-tick full snapshot when fingerprint_each_tick=False ----
    def test_p1_3_driver_skips_per_tick_snapshot_and_replays_cleanly(self):
        from organizer.rift_era.engine.ipc import Decision

        class _NoopSession:
            def __init__(self):
                self.clock = NS(spent_seconds=0.0, pool_seconds=60.0, timeouts=0, custodial=False)
                self.managed = NS(sandbox='none', input_capacity_bytes=0)
            def initialize(self, init_msg): return Decision(-1, (), 'ready', (), 0, 0, False)
            def decide(self, obs_msg): return Decision(obs_msg['window'], (), 'orders', (), 0, 0, False)
            def finish(self, end_msg): pass
            def close(self): pass

        eng = self._fresh_match(total_ticks=40, fingerprint_each_tick=False)
        econ = Economy(eng.state)
        calls = 0
        orig_snapshot = eng.state.snapshot

        def counted_snapshot():
            nonlocal calls
            calls += 1
            return orig_snapshot()

        eng.state.snapshot = counted_snapshot
        with tempfile.TemporaryDirectory() as tmp:
            rpath = Path(tmp) / 'replay.jsonl'
            driver = MatchDriver(
                eng, {'n0': _NoopSession()},
                init_messages={'n0': economic_development_init(econ, 'n0', eng.clock)},
                observe=lambda owner, tick: economic_development_messages(econ, owner, tick, eng.clock),
                finalize=lambda: {'done': True},
                replay=rpath,
                manifest={'test': 'audit'},
            )
            report = driver.run()
            self.assertTrue(report['valid'])
            # Must NOT call snapshot() 40+ times per tick when fingerprint_each_tick=False
            self.assertLessEqual(calls, 4)
            verify_replay(rpath)
            eng2 = self._fresh_match(total_ticks=40, fingerprint_each_tick=False)
            final_hash = replay_decisions(rpath, eng2)
            self.assertEqual(final_hash, report['state_hash'])

    # ---- P1-4: pipeline/scoring.py uses outward-rounded score_interval ----
    def test_p1_4_scoring_uses_score_interval_precision(self):
        vp = Fraction(10**20 + 1, 10**20)
        vs = Fraction(1)
        interval = pipeline_scoring.seat_score_interval(vp, vs)
        self.assertGreater(interval.lower, Decimal(50))
        self.assertLessEqual(interval.lower, interval.upper)
        score = pipeline_scoring.seat_score(vp, vs)
        self.assertGreaterEqual(score, 50.0)
        self.assertLessEqual(score, 100.0)

    # ---- P2: make_submission.py checks sdk.py first and validates archive rules ----
    def test_p2_make_submission_sdk_check_and_archive_validation(self):
        spec = importlib.util.spec_from_file_location('rift_make_sub', RIFT_ROOT / 'kit' / 'scripts' / 'make_submission.py')
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / 'mybot'
            src.mkdir()
            (src / 'main.py').write_text('from sdk import Client\n', encoding='utf-8')
            out = Path(tmp) / 'out.zip'
            with self.assertRaises(SystemExit) as cm:
                mod.build(src, out)
            self.assertIn('sdk.py', str(cm.exception))

            # Validate archive path rejection for reserved Windows names / illegal chars
            with self.assertRaises(SystemExit):
                mod._validate_archive_paths(['main.py', 'sub/NUL.py'])
            with self.assertRaises(SystemExit):
                mod._validate_archive_paths(['main.py', 'a/b.py', 'A/B.py'])

            # Validate >100x compression ratio rejection and cleanup
            (src / 'sdk.py').write_text('class Client: pass\n', encoding='utf-8')
            (src / 'zeros.dat').write_bytes(b'\x00' * (256 * 1024))
            with self.assertRaises(SystemExit) as cm2:
                mod.build(src, out)
            self.assertIn('100', str(cm2.exception))
            self.assertFalse(out.exists())


if __name__ == '__main__':
    unittest.main()
