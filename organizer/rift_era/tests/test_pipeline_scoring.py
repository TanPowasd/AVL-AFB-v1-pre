"""rift_era scoring_v2 hooks: formula, N-opponent seat/lineup rotation, shadow cache, freeze, aggregation."""

from fractions import Fraction
import json
import math
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock
import zipfile

from organizer.rift_era.pipeline import scoring
from organizer.rift_era.pipeline.match_runner import versions
from organizer.rift_era.scripts import freeze_profile

ORGANIZER = Path(__file__).resolve().parents[2]
if str(ORGANIZER) not in sys.path:
    sys.path.insert(0, str(ORGANIZER))
import strategy_scoring  # noqa: E402


def fake_match(seed, seats, ticks, values, sandbox='none'):
    return {'valid': True, 'seed': seed, 'ticks': ticks, 'sandbox': sandbox, 'sandbox_reason': 'test',
            'engine_version': 'e', 'engine_hash': 'h', 'rules_version': 'r', 'rules_hash': 'rh',
            'state_hash': f's{seed}', 'practice_clock': True, 'map_version': 'm', 'wall_seconds': 1.0,
            'nations': {f'n{i}': {'seat': Path(str(seat)).name, 'bot_sha256': 'x', 'faults': 0,
                                  'strategy_time': {'timeouts': 0, 'custodial': False},
                                  'V': {'float': float(v), 'exact': f'{Fraction(v).numerator}/{Fraction(v).denominator}'}}
                        for i, (seat, v) in enumerate(zip(seats, values))}}


class FormulaTest(unittest.TestCase):
    def test_vtilde_branches(self):
        self.assertEqual(scoring.vtilde(Fraction(5)), 15)
        self.assertEqual(scoring.vtilde(Fraction(0)), 10)
        self.assertEqual(scoring.vtilde(Fraction(-10)), 5)  # 100/(10+10)
        self.assertEqual(scoring.vtilde(Fraction(-1, 10**9)), Fraction(100) / (10 + Fraction(1, 10**9)))

    def test_design_formula(self):
        expected = 50 + 50 * math.tanh(0.6 * math.log(30 / 20))
        self.assertAlmostEqual(scoring.seat_score(20, 10), expected, places=12)
        self.assertEqual(scoring.seat_score(Fraction(7, 3), Fraction(7, 3)), 50.0)
        self.assertLess(scoring.seat_score(9, 10), 50.0)
        self.assertGreater(scoring.seat_score(Fraction(-1), Fraction(-2)), 50.0)

    def test_strictly_monotone_and_bounded(self):
        previous = -1.0
        for v in (-1000, -5, -1, 0, 1, 10, 100, 10**6):
            s = scoring.seat_score(v, 10)
            self.assertTrue(0 <= s <= 100)
            self.assertGreater(s, previous)
            previous = s

    def test_huge_rationals_do_not_overflow(self):
        big = Fraction(10**400 + 7, 10**399)
        self.assertTrue(50 < scoring.seat_score(big, Fraction(1)) < 100)

    def test_parse_exact(self):
        self.assertEqual(scoring.parse_exact({'exact': '7/3', 'float': 2.3}), Fraction(7, 3))


class JobsTest(unittest.TestCase):
    def cfg(self, opponents=None, **extra):
        v = versions()
        opps = opponents if opponents is not None else [{'id': 'super', 'path': 'bots/super/main.py'}]
        return {'opponents': opps, 'ticks': 28800,
                'game_timeout_s': 10, 'match': {'sandbox': 'auto'}, 'engine_hash': v['engine_hash'],
                'rules_hash': v['rules_hash']} | extra

    def test_seat_rotation_k_mod_8_single_opponent(self):
        jobs = scoring.make_jobs('cand.zip', self.cfg(), list(range(100, 117)), ticks=200)
        self.assertEqual([j['seat'] for j in jobs], [k % 8 for k in range(17)])
        self.assertTrue(all(j['ticks'] == 200 and j['opponent'] == 'super' for j in jobs))
        self.assertTrue(jobs[0]['opponent_path'].endswith('super'))
        self.assertEqual(jobs[0]['shadow_seat_ids'], ['super'] * 8)

    def test_arbitrary_n_opponent_mixed_lineup_and_rotation(self):
        for n in (1, 2, 3, 4, 5, 8, 10):
            with self.subTest(n=n):
                opps = [{'id': f'opp{i}', 'path': f'bots/opp{i}/main.py'} for i in range(n)]
                jobs = scoring.make_jobs('cand.zip', self.cfg(opponents=opps), list(range(1, 65)), ticks=200)
                self.assertEqual(len(jobs), 64)
                self.assertEqual([j['seat'] for j in jobs], [k % 8 for k in range(64)])
                # Designated shadow opponent at seat matches shadow_seat_ids[seat]
                for job in jobs:
                    self.assertEqual(len(job['shadow_seats']), 8)
                    self.assertEqual(len(job['shadow_seat_ids']), 8)
                    self.assertEqual(job['shadow_seat_ids'][job['seat']], job['opponent'])
                    # Table contains min(8, n) distinct opponents in balanced counts
                    distinct = set(job['shadow_seat_ids'])
                    self.assertEqual(len(distinct), min(8, n))
                # Across 64 seeds, every opponent appears nearly equally as designated shadow
                counts = {f'opp{i}': sum(j['opponent'] == f'opp{i}' for j in jobs) for i in range(n)}
                self.assertLessEqual(max(counts.values()) - min(counts.values()), 1)
                # Even when N shares a factor with 8 (e.g. N=2,4,8), every opponent covers multiple seats
                if n in (2, 4, 8):
                    for i in range(n):
                        seats_seen = {j['seat'] for j in jobs if j['opponent'] == f'opp{i}'}
                        self.assertEqual(seats_seen, set(range(8)))

    def test_profile_frozen_to_other_engine_is_refused(self):
        with self.assertRaises(ValueError):
            scoring.make_jobs('cand.zip', self.cfg(engine_hash='0' * 64), [1])

    def test_profile_path_uses_bots_root_override(self):
        with tempfile.TemporaryDirectory() as root, mock.patch.dict(os.environ, {'RIFT_ERA_BOTS_ROOT': root}):
            self.assertEqual(scoring.profile_path('bots/super/main.py'), Path(root).resolve() / 'super/main.py')
            self.assertEqual(scoring.profile_path('pipeline/scoring.py'), Path(scoring.__file__).resolve())
            with self.assertRaises(ValueError):
                scoring.profile_path('../../README.md')
            with self.assertRaises(ValueError):
                scoring.profile_path('bots/../../README.md')


class ShadowAndGameTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.cache = Path(self.temp.name) / 'cache'
        self.calls = []
        self.shadow_values = [Fraction(10 + i) for i in range(8)]

        def run(seed, seats, ticks, match, out, timeout, official=False):
            self.calls.append(list(seats))
            if 'cand.zip' not in seats:
                return fake_match(seed, seats, ticks, self.shadow_values)
            values = list(self.shadow_values)
            values[seats.index('cand.zip')] = Fraction(20)
            return fake_match(seed, seats, ticks, values)

        self.patches = [mock.patch.object(scoring, '_run_match', side_effect=run),
                        mock.patch.object(scoring, 'SHADOW_CACHE', self.cache)]
        for p in self.patches:
            p.start()
        self.bot_dirs = {}
        for name in ('hegemon', 'arbiter', 'verdant'):
            d = Path(self.temp.name) / name
            d.mkdir()
            (d / 'main.py').write_text(f'# {name}\n', encoding='utf-8')
            self.bot_dirs[name] = d

    def tearDown(self):
        for p in self.patches:
            p.stop()
        self.temp.cleanup()

    def spec(self, seed, seat, *, mixed=False):
        if mixed:
            names = ['hegemon', 'arbiter', 'verdant']
            lineup_ids = [names[(seat + i) % 3] for i in range(8)]
            lineup = [str(self.bot_dirs[n]) for n in lineup_ids]
            return {'seat': seat, 'seed': seed, 'ticks': 200, 'match': {}, 'game_timeout_s': 5,
                    'opponent': lineup_ids[seat], 'opponent_path': lineup[seat],
                    'shadow_seats': lineup, 'shadow_seat_ids': lineup_ids, 'candidate': 'cand.zip'}
        return {'seat': seat, 'seed': seed, 'ticks': 200, 'match': {}, 'game_timeout_s': 5,
                'opponent': 'hegemon', 'opponent_path': str(self.bot_dirs['hegemon']), 'candidate': 'cand.zip'}

    def test_score_compares_same_seat_against_shadow_and_caches_shadow(self):
        game = scoring.run_scored_game(self.spec(7, 3))
        self.assertAlmostEqual(game['score'], scoring.seat_score(20, 13))
        self.assertEqual(game['raw']['V_shadow'], 13.0)
        game2 = scoring.run_scored_game(self.spec(7, 5))
        self.assertAlmostEqual(game2['score'], scoring.seat_score(20, 15))
        shadows = [c for c in self.calls if 'cand.zip' not in c]
        self.assertEqual(len(shadows), 1)
        self.assertEqual(self.calls[1][3], 'cand.zip')
        self.assertEqual(self.calls[1].count(str(self.bot_dirs['hegemon'])), 7)

    def test_mixed_lineup_cache_invalidates_when_any_opponent_changes(self):
        spec = self.spec(11, 2, mixed=True)
        game1 = scoring.run_scored_game(spec)
        self.assertEqual(game1['result']['shadow_lineup'], spec['shadow_seat_ids'])
        # Same lineup + seed hits cache
        scoring.run_scored_game(spec)
        shadows = [c for c in self.calls if 'cand.zip' not in c]
        self.assertEqual(len(shadows), 1)
        # Mutate ONE opponent in the 8-seat lineup -> cache key MUST change and recompute shadow!
        (self.bot_dirs['verdant'] / 'main.py').write_text('# verdant v2\n', encoding='utf-8')
        scoring.run_scored_game(spec)
        shadows = [c for c in self.calls if 'cand.zip' not in c]
        self.assertEqual(len(shadows), 2)

    def test_concurrent_shadow_match_computes_once(self):
        entered = threading.Event()
        original = scoring._run_match.side_effect

        def slow_shadow(seed, seats, ticks, match, out, timeout, official=False):
            if 'cand.zip' not in seats:
                entered.set()
                time.sleep(0.1)
            return original(seed, seats, ticks, match, out, timeout, official)
        scoring._run_match.side_effect = slow_shadow
        results = []
        errors = []

        def worker(seat):
            try:
                results.append(scoring.run_scored_game(self.spec(77, seat)))
            except Exception as exc:  # pragma: no cover
                errors.append(exc)
        t1 = threading.Thread(target=worker, args=(0,))
        t2 = threading.Thread(target=worker, args=(1,))
        t1.start()
        entered.wait(timeout=2.0)
        t2.start()
        t1.join(timeout=5.0)
        t2.join(timeout=5.0)
        self.assertEqual(errors, [])
        self.assertEqual(len(results), 2)
        shadows = [c for c in self.calls if 'cand.zip' not in c]
        self.assertEqual(len(shadows), 1)

    def test_sandbox_mismatch_is_refused(self):
        original = scoring._run_match.side_effect

        def run(seed, seats, ticks, match, out, timeout, official=False):
            data = original(seed, seats, ticks, match, out, timeout)
            if 'cand.zip' in seats:
                data['sandbox'] = 'bwrap'
            return data
        scoring._run_match.side_effect = run
        with self.assertRaises(RuntimeError):
            scoring.run_scored_game(self.spec(9, 0))

    def test_aggregate_gives_0_100_by_opponent_and_bootstrap_ci(self):
        cfg = {'component_weights': {'adv': 1.0}}
        jobs, games = [], []
        for k, seed in enumerate((1, 2, 3)):
            spec = self.spec(seed, k % 8, mixed=True) | {'component': 'adv', 'config_id': '0'}
            jobs.append(spec)
            games.append(scoring.run_scored_game(spec) | {key: spec[key] for key in
                                                          ('seed', 'component', 'opponent', 'config_id', 'seat')})
        row = strategy_scoring.aggregate('cand.zip', games, cfg, jobs)
        self.assertTrue(50 < row['score'] < 100)
        self.assertEqual(row['n_games'], 3)
        self.assertEqual(set(row['by_opponent']), {'hegemon', 'arbiter', 'verdant'})
        low, high = row['ci95']
        self.assertTrue(0 <= low <= row['score'] <= high <= 100)
        with self.assertRaises(ValueError):
            strategy_scoring.aggregate('cand.zip', games[:2], cfg, jobs)


class FreezeExtensibilityTest(unittest.TestCase):
    def test_freeze_supports_arbitrary_n_dirs_and_zips(self):
        with tempfile.TemporaryDirectory() as tmp:
            broot = Path(tmp) / 'bots'
            for name in ('alpha', 'beta'):
                (broot / name).mkdir(parents=True)
                (broot / name / 'main.py').write_text('pass\n', encoding='utf-8')
                (broot / name / 'bot.py').write_text(f'NAME = {name!r}\n', encoding='utf-8')
            zpath = broot / 'gamma.zip'
            with zipfile.ZipFile(zpath, 'w') as zf:
                zf.writestr('main.py', 'pass\n')
            with mock.patch.dict(os.environ, {'RIFT_ERA_BOTS_ROOT': str(broot)}):
                opps = freeze_profile.parse_opponent_specs(['alpha,beta,gamma'])
                profile = freeze_profile.build('test-rev-n3', opps)
                self.assertEqual([o['id'] for o in profile['opponents']], ['alpha', 'beta', 'gamma'])
                self.assertEqual(profile['opponents'][2]['path'], 'bots/gamma.zip')
                for rel in ('bots/alpha/main.py', 'bots/alpha/bot.py', 'bots/beta/main.py',
                            'bots/beta/bot.py', 'bots/gamma.zip', 'pipeline/scoring.py'):
                    self.assertIn(rel, profile['frozen_files'])


class ProfileTest(unittest.TestCase):
    def test_rift_profile_is_registered_and_private(self):
        self.assertIn('rift_era', strategy_scoring.PROBLEMS)
        cfg = json.loads((ORGANIZER / 'rift_era/config/scoring_v2.json').read_text(encoding='utf-8'))
        self.assertEqual(cfg['submission_ext'], '.zip')
        self.assertGreaterEqual(len(cfg['opponents']), 1)
        self.assertEqual([o['id'] for o in cfg['opponents']], ['hegemon', 'arbiter', 'verdant'])
        for opp in cfg['opponents']:
            self.assertIn(opp['path'], cfg['frozen_files'])
        self.assertEqual(cfg['ticks'], 28800)
        self.assertEqual(cfg['seed_count'], 64)
        self.assertEqual(cfg['seed_source'], 'os_csprng')


if __name__ == '__main__':
    unittest.main()
