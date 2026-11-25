"""Batch runner: 07 §4 concurrency plan, manifest freezing and resume (subprocess mocked)."""

import argparse
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from organizer.rift_era.pipeline import batch
from organizer.rift_era.pipeline.match_runner import versions

GIB = 1024**3


class PlanTest(unittest.TestCase):
    def test_small_host_is_oversubscribed_practice_only(self):
        plan = batch.concurrency_plan(cpus=2, memory_total=7 * GIB, memory_available=3 * GIB)
        self.assertEqual(plan['reserve_cpus'], 1)
        self.assertEqual(plan['k_cpu'], 0)
        self.assertEqual(plan['k'], 0)
        self.assertTrue(plan['oversubscribed'])
        self.assertEqual(plan['workers'], 1)
        with self.assertRaises(RuntimeError):
            batch.concurrency_plan(cpus=2, memory_total=7 * GIB, memory_available=3 * GIB, practice=False)

    def test_large_host_formula(self):
        plan = batch.concurrency_plan(cpus=32, memory_total=256 * GIB, memory_available=200 * GIB,
                                      engine_memory=2 * GIB, strategy_memory=GIB)
        # reserve=min(31, max(1, 8))=8 ; C_match=1+1=2 ; K_cpu=floor(24/2)=12
        self.assertEqual((plan['reserve_cpus'], plan['cpu_per_match'], plan['k_cpu']), (8, 2, 12))
        # margin=max(4GiB, 51.2GiB) ; M_match=2+8=10GiB ; K_mem=floor(148.8/10)=14
        self.assertEqual(plan['k_mem'], 14)
        self.assertEqual(plan['workers'], 12)
        self.assertFalse(plan['oversubscribed'])

    def test_memory_bound(self):
        plan = batch.concurrency_plan(cpus=64, memory_total=64 * GIB, memory_available=40 * GIB,
                                      engine_memory=GIB, strategy_memory=GIB)
        self.assertEqual(plan['k_mem'], (40 * GIB - plan['memory_margin']) // (9 * GIB))
        self.assertEqual(plan['k'], plan['k_mem'])

    def test_rotation(self):
        base = ['super'] * 8
        self.assertEqual(batch.seats_for(9, base, 'cand.zip')[1], 'cand.zip')
        self.assertEqual(batch.seats_for(9, base, None), base)


class ResumeTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.out = Path(self.temp.name) / 'run'
        self.launched = []
        self.fail_seed = None
        v = versions()

        test = self

        class FakePopen:
            def __init__(self, command, stdout=None, stderr=None, env=None):
                seed = int(command[command.index('--seed') + 1])
                ticks = int(command[command.index('--ticks') + 1])
                test.launched.append(seed)
                self.returncode = 1 if seed == test.fail_seed else 0
                if not self.returncode:
                    out = Path(command[command.index('--out') + 1])
                    seats = command[command.index('--seats') + 1].split(',')
                    out.write_text(json.dumps({
                        'valid': True, 'seed': seed, 'ticks': ticks, 'sandbox': 'none',
                        'engine_hash': v['engine_hash'], 'rules_hash': v['rules_hash'],
                        'nations': {f'n{i}': {'seat': Path(s).name, 'V': {'float': float(i), 'exact': f'{i}/1'}}
                                    for i, s in enumerate(seats)}}))

            def poll(self):
                return self.returncode

        self.patches = [mock.patch.object(batch.subprocess, 'Popen', FakePopen),
                        mock.patch.object(batch, 'host_resources', return_value=(2, 7 * GIB, 3 * GIB)),
                        mock.patch.object(batch.time, 'sleep', lambda s: None)]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        self.temp.cleanup()

    def args(self, **kw):
        base = dict(seeds=[11, 12, 13], seed_count=None, seats='simple', rotate=None, ticks=200, legacy_map=True,
                    sandbox='none', cgroup=None, jobs=None, engine_mem_mib=1536, strategy_mem_mib=1024,
                    out_dir=self.out)
        return argparse.Namespace(**(base | kw))

    def test_resume_skips_finished_and_reruns_failed(self):
        self.fail_seed = 12
        summary = batch.run_batch(self.args())
        self.assertEqual(sorted(self.launched), [11, 12, 13])
        self.assertFalse(summary['complete'])
        self.assertEqual(summary['failures'][0]['seed'], 12)
        manifest = json.loads((self.out / 'manifest.json').read_text())
        for key in ('engine_version', 'engine_hash', 'rules_version', 'rules_hash', 'bot_sha256'):
            self.assertIn(key, manifest)
        self.launched.clear()
        self.fail_seed = None
        summary = batch.run_batch(self.args(seeds=None))  # resume uses the frozen seed list
        self.assertEqual(self.launched, [12])
        self.assertTrue(summary['complete'])
        self.assertEqual(summary['matches_done'], 3)
        self.assertTrue(summary['concurrency']['oversubscribed'])
        self.launched.clear()
        batch.run_batch(self.args(seeds=None))
        self.assertEqual(self.launched, [])

    def test_changed_batch_is_refused(self):
        batch.run_batch(self.args())
        with self.assertRaises(SystemExit):
            batch.run_batch(self.args(ticks=240))

    def test_corrupt_result_is_rerun(self):
        batch.run_batch(self.args())
        (self.out / 'matches' / 'seed-13.json').write_text('{"valid": true, "seed": 13, "ticks": 200}')
        self.launched.clear()
        batch.run_batch(self.args(seeds=None))
        self.assertEqual(self.launched, [13])

    def test_rotate_places_candidate(self):
        with tempfile.TemporaryDirectory() as bot:
            (Path(bot) / 'main.py').write_text('pass\n')
            summary = batch.run_batch(self.args(rotate=bot))
        seats = [row['seats'] for row in summary['matches']]
        self.assertEqual(seats[1]['n1'], Path(bot).name)
        self.assertEqual(seats[2]['n2'], Path(bot).name)


if __name__ == '__main__':
    unittest.main()
