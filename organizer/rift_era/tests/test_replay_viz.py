"""Tests for rift_era replay recording, offline visualizer, ELO scoring, kit purity & frozen profile."""
from __future__ import annotations

from fractions import Fraction
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

RIFT = Path(__file__).resolve().parents[1]       # organizer/rift_era
ORGANIZER = RIFT.parent                          # organizer
REPO = ORGANIZER.parent

for p in (str(REPO), str(ORGANIZER), str(RIFT / 'kit'), str(RIFT / 'scripts')):
    if p not in sys.path:
        sys.path.insert(0, p)

import pack_contestant  # noqa: E402
import replay_viz  # noqa: E402
import strategy_scoring  # noqa: E402
from organizer.rift_era.engine.catalog import Catalog  # noqa: E402
from organizer.rift_era.engine.determinism import engine_source_fingerprint  # noqa: E402
from organizer.rift_era.engine.dev_seats import run_seated_match  # noqa: E402
from organizer.rift_era.engine.initialization import initialize  # noqa: E402
from organizer.rift_era.engine.match import match_economy  # noqa: E402
from organizer.rift_era.engine.world import generate_candidate  # noqa: E402
from organizer.rift_era.score import elo  # noqa: E402

FORBIDDEN_WORDS = re.compile(r'organizer|组委会|OPEN_ISSUES|HEAVY_TESTS|TanPowasd|Powasd|/data/ws|/data/home')
TEXT_SUFFIXES = {'.py', '.md', '.json', '.txt', '.html', '.js', '.cpp', '.h'}


class ReplayVizAndParityTest(unittest.TestCase):
    def test_record_match_replay_matches_engine_bit_for_bit(self):
        """Non-intrusive recording yields identical state_hash & exact Fraction V as dev_seats."""
        seed = 7
        ticks = 40
        seats = (
            (RIFT / 'kit' / 'template').resolve(),
            'simple',
            'none',
            'none',
            'none',
            'none',
            'none',
            'none',
        )
        labels = ('template', 'simple', 'none', 'none', 'none', 'none', 'none', 'none')

        data = replay_viz.record_match_replay(
            seed=seed,
            seats=seats,
            ticks=ticks,
            sample_every=10,
            legacy_map=True,
            seat_labels=labels,
        )

        # Independent reference run through dev_seats.run_seated_match
        catalog = Catalog()
        world = generate_candidate(seed)
        engine = match_economy(catalog, initialize(catalog, world), total_ticks=ticks, fingerprint_each_tick=False)
        ref_report = run_seated_match(engine, seats)

        ok, msg = replay_viz.verify_against_engine(data, ref_report)
        self.assertTrue(ok, msg)
        self.assertEqual(data['format'], 'afb-rift-era-replay/1')
        self.assertEqual(data['meta']['state_hash'], ref_report['state_hash'])
        self.assertEqual(data['meta']['engine_hash'], engine_source_fingerprint())
        self.assertEqual(data['map']['width'], 512)
        self.assertEqual(data['map']['height'], 512)
        self.assertEqual(len(data['map']['terrain']), 64 * 64)
        self.assertGreater(len(data['map']['cities']), 200)
        self.assertGreater(len(data['map']['corridors']), 1000)
        self.assertEqual([f['tick'] for f in data['frames']], [0, 10, 20, 30, 40])

        for owner in [f'n{i}' for i in range(8)]:
            rec_v = replay_viz._parse_fraction(data['meta']['nations'][owner]['V_exact'])
            ref_v = replay_viz._parse_fraction(ref_report['nations'][owner]['V'])
            self.assertEqual(rec_v, ref_v, f'V mismatch for {owner}')

        # Verify save_replay + reload round-trip
        with tempfile.TemporaryDirectory() as tmp:
            out = replay_viz.save_replay(Path(tmp) / 'sample.json', data)
            reloaded = json.loads(out.read_text(encoding='utf-8'))
            ok2, msg2 = replay_viz.verify_against_engine(reloaded, ref_report)
            self.assertTrue(ok2, msg2)

    def test_visualizer_offline_and_clean(self):
        player = RIFT / 'viz' / 'player.html'
        self.assertTrue(player.is_file(), 'viz/player.html missing')
        html = player.read_text(encoding='utf-8')
        # Zero external CDN or network URLs
        self.assertIsNone(re.search(r'https?://', html, re.IGNORECASE))
        self.assertIsNone(re.search(r'<script[^>]+src=', html, re.IGNORECASE))
        self.assertIsNone(re.search(r'<link[^>]+href=', html, re.IGNORECASE))
        self.assertIn('afb-rift-era-replay', html)
        for dom_id in ('cvMap', 'cvChart', 'lbBody', 'evList', 'seek', 'theme'):
            self.assertIn(f'id="{dom_id}"', html)
        self.assertIsNone(FORBIDDEN_WORDS.search(html))

    def test_pack_contestant_exports_new_files_purity_and_cli_guards(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            bots = base / 'bots'
            for tier in ('medium', 'advanced', 'super'):
                (bots / tier).mkdir(parents=True)
                shutil.copyfile(RIFT / 'kit' / 'template' / 'main.py', bots / tier / 'main.py')
                (bots / tier / 'bot.py').write_text(f'NAME = {tier!r}\n', encoding='utf-8')
                shutil.copyfile(RIFT / 'sdk.py', bots / tier / 'sdk.py')

            dest = pack_contestant.export_kit(base / 'kit', bots=bots, force=True)
            manifest = json.loads((dest / 'manifest.json').read_text(encoding='utf-8'))
            files = manifest['files']

            expected_new = (
                'AGENTS.md',
                'AI_README.md',
                'FORMAT.md',
                'handbook/contest-rules.md',
                'handbook/player-guide.md',
                'handbook/game-facts.md',
                'prompts/task.txt',
                'scripts/replay_viz.py',
                'scripts/record_replay.py',
                'viz/player.html',
            )
            for rel in expected_new:
                self.assertIn(rel, files, f'missing from exported kit: {rel}')
                self.assertTrue((dest / rel).is_file(), f'file not copied: {rel}')

            # Ensure private/organizer-only tools never enter the contestant kit
            for forbidden_rel in (
                'agents/super/bot.py',
                'config/scoring_v2.json',
                'score/elo.py',
                'scripts/duel.py',
                'scripts/run_league.py',
                'scripts/run_match.py',
                'scripts/pack_prompt.py',
            ):
                self.assertNotIn(forbidden_rel, files)
                self.assertFalse((dest / forbidden_rel).exists())

            # Full purity check across all exported text files
            for p in sorted(dest.rglob('*')):
                if not p.is_file() or p.suffix not in TEXT_SUFFIXES:
                    continue
                rel = p.relative_to(dest).as_posix()
                text = p.read_text(encoding='utf-8', errors='replace')
                m = FORBIDDEN_WORDS.search(text)
                self.assertIsNone(m, f'FORBIDDEN_WORDS hit ({m.group(0) if m else ""}) in exported {rel}')
                if not rel.startswith('rules/'):
                    for marker in pack_contestant.PRIVATE_MARKERS:
                        self.assertNotIn(marker, text, f'PRIVATE_MARKER {marker!r} in exported {rel}')

            # Contestant CLI guard 1: --serve is refused
            res_serve = subprocess.run(
                [sys.executable, str(dest / 'scripts' / 'record_replay.py'),
                 '--a', 'template', '--b', 'simple', '--ticks', '40', '--legacy-map', '--serve'],
                cwd=dest, capture_output=True, text=True, timeout=30,
            )
            self.assertNotEqual(res_serve.returncode, 0)

            # Contestant CLI guard 2: external bot path outside workspace is refused
            ext_bot = base / 'external_bot'
            ext_bot.mkdir()
            shutil.copyfile(RIFT / 'kit' / 'template' / 'main.py', ext_bot / 'main.py')
            res_ext = subprocess.run(
                [sys.executable, str(dest / 'scripts' / 'record_replay.py'),
                 '--a', str(ext_bot), '--b', 'simple', '--ticks', '40', '--legacy-map', '--no-verify'],
                cwd=dest, capture_output=True, text=True, timeout=30,
            )
            self.assertNotEqual(res_ext.returncode, 0)

            # Contestant CLI valid run: record 40-tick replay inside contestant kit
            res_ok = subprocess.run(
                [sys.executable, str(dest / 'scripts' / 'record_replay.py'),
                 '--seats', 'template,simple,none,none,none,none,none,none',
                 '--seed', '3', '--ticks', '40', '--every', '10', '--legacy-map', '--no-verify'],
                cwd=dest, capture_output=True, text=True, timeout=60,
            )
            self.assertEqual(res_ok.returncode, 0, res_ok.stdout + '\n' + res_ok.stderr)
            produced = list((dest / 'replays').glob('replay_seed3_40t_*.json'))
            self.assertEqual(len(produced), 1, f'expected 1 replay json, found {produced}')
            loaded = json.loads(produced[0].read_text(encoding='utf-8'))
            ok, msg = replay_viz.verify_against_engine(loaded)
            self.assertTrue(ok, msg)

    def test_load_profile_frozen_invariants(self):
        profile = strategy_scoring.load_profile('rift_era')
        self.assertEqual(profile['problem'], 'rift_era')
        self.assertEqual(profile['engine_hash'], engine_source_fingerprint())

    def test_elo_8seat_scoring_and_table(self):
        # Continuity and monotonicity of vtilde & log_ratio
        self.assertEqual(elo.vtilde(Fraction(0)), Fraction(10))
        self.assertEqual(elo.vtilde(Fraction(-10)), Fraction(5))
        self.assertEqual(elo.vtilde(Fraction(10)), Fraction(20))
        self.assertAlmostEqual(elo.log_ratio(Fraction(20), Fraction(10)), -elo.log_ratio(Fraction(10), Fraction(20)), places=12)

        # Exact zero-sum & seat-order invariance in 4v4
        init = {'A': 1500.0, 'B': 1500.0}
        seats_4v4 = [
            ('A', Fraction(50)), ('B', Fraction(30)),
            ('A', Fraction(48)), ('B', Fraction(32)),
            ('A', Fraction(52)), ('B', Fraction(29)),
            ('A', Fraction(49)), ('B', Fraction(31)),
        ]
        r1 = elo.update_8seat_match(init, seats_4v4)
        r2 = elo.update_8seat_match(init, list(reversed(seats_4v4)))
        self.assertAlmostEqual(r1['A'], r2['A'], places=12)
        self.assertAlmostEqual(r1['B'], r2['B'], places=12)
        self.assertAlmostEqual(r1['A'] + r1['B'], 3000.0, places=9)
        self.assertGreater(r1['A'], 1500.0)
        self.assertLess(r1['B'], 1500.0)

        # 8-way FFA zero-sum & ranking order
        ffa = [(f'bot{i}', Fraction(20 + i * 5)) for i in range(8)]
        r_ffa = elo.update_8seat_match({}, ffa)
        self.assertAlmostEqual(sum(r_ffa.values()), 8 * elo.DEFAULT_INIT_ELO, places=9)
        for i in range(7):
            self.assertLess(r_ffa[f'bot{i}'], r_ffa[f'bot{i + 1}'])

        # Draw within tolerance leaves equal ratings unchanged
        draw_res = elo.update_8seat_match(init, [('A', Fraction(40))] * 4 + [('B', Fraction(40))] * 4)
        self.assertAlmostEqual(draw_res['A'], 1500.0, places=9)
        self.assertAlmostEqual(draw_res['B'], 1500.0, places=9)

        # Markdown rendering
        md = elo.render_table([
            {'name': 'advanced', 'elo': 1532.4, 'w': 4, 'd': 0, 'l': 0, 'avg_v': 45.2, 'best_v': 47.1, 'faults': 0},
            {'name': 'medium', 'elo': 1467.6, 'w': 0, 'd': 0, 'l': 4, 'avg_v': 39.8, 'best_v': 41.0, 'faults': 0},
        ])
        self.assertIn('| 1 | advanced | 1532.4 |', md)
        self.assertIn('| 2 | medium | 1467.6 |', md)

    def test_sample_replay_present_and_valid(self):
        samples = list((RIFT / 'replays').glob('replay_seed7_80t_*.json'))
        self.assertTrue(samples, 'expected verified 80-tick sample replay under organizer/rift_era/replays/')
        data = json.loads(samples[0].read_text(encoding='utf-8'))
        ok, msg = replay_viz.verify_against_engine(data)
        self.assertTrue(ok, msg)
        self.assertEqual(data['meta']['seed'], 7)
        self.assertEqual(data['meta']['ticks'], 80)


if __name__ == '__main__':
    unittest.main()
