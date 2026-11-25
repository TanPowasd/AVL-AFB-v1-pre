"""Real IPC through the pipeline launcher (fallback path, sandbox=none): one short match.

Kept to a 40-tick legacy-map match with one template seat so the module stays well under 110 s.
"""

import json
from pathlib import Path
import tempfile
import unittest
import zipfile

from organizer.rift_era.pipeline import match_runner

RIFT = Path(__file__).resolve().parents[1]


class PipelineMatchTest(unittest.TestCase):
    def test_template_zip_plays_through_fallback_launcher(self):
        with tempfile.TemporaryDirectory() as temp:
            archive = Path(temp) / 'cand.zip'
            with zipfile.ZipFile(archive, 'w') as zf:
                zf.write(RIFT / 'kit/template/main.py', 'main.py')
                zf.write(RIFT / 'kit/template/bot.py', 'bot.py')
                zf.write(RIFT / 'sdk.py', 'sdk.py')
            seats = [archive.resolve()] + ['none'] * 7
            result = match_runner.run_match(seed=5, seats=seats, ticks=40, legacy_map=True, sandbox='none')
        self.assertTrue(result['valid'])
        self.assertEqual(result['sandbox'], 'none')
        self.assertFalse(result['official'])
        self.assertTrue(result['practice_clock'])
        self.assertEqual(result['ticks'], 40)
        for key in ('engine_version', 'engine_hash', 'rules_version', 'rules_hash', 'pipeline_version', 'runtime'):
            self.assertTrue(result[key])
        me = result['nations']['n0']
        self.assertEqual(me['faults'], 0)
        self.assertEqual(len(me['bot_sha256']), 64)
        self.assertEqual(me['strategy_time']['timeouts'], 0)
        self.assertFalse(me['strategy_time']['custodial'])
        self.assertLessEqual(me['strategy_time']['pool_remaining_ns'], 450 * 10**9)
        self.assertIsNone(result['nations']['n1']['bot_sha256'])
        n, d = me['V']['exact'].split('/')
        self.assertAlmostEqual(int(n) / int(d), me['V']['float'])
        json.dumps(result)

    def test_official_requires_cgroup_and_full_clock(self):
        with self.assertRaises(RuntimeError):
            match_runner.run_match(seed=5, seats=['simple'] * 8, ticks=40, legacy_map=True, sandbox='none',
                                   official=True)

    def test_seat_parsing(self):
        self.assertEqual(match_runner.parse_seats('none'), ['none'] * 8)
        with self.assertRaises(ValueError):
            match_runner.parse_seats('none,none')
        with self.assertRaises(ValueError):
            match_runner.resolve_seat('/definitely/not/here.zip')


if __name__ == '__main__':
    unittest.main()
