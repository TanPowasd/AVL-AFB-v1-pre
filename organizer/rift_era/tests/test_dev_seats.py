"""E1: per-seat external bots for the development match CLI."""

import shutil
import tempfile
import unittest
from pathlib import Path

from organizer.rift_era.engine.catalog import Catalog
from organizer.rift_era.engine.dev_seats import needs_driver, resolve_seats, run_seated_match
from organizer.rift_era.engine.initialization import initialize
from organizer.rift_era.engine.match import SdkBotOrders, match_economy
from organizer.rift_era.engine.world import generate_candidate

SDK = Path(__file__).resolve().parents[1] / 'sdk.py'


def simple_submission(root: Path) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(SDK, root / 'sdk.py')
    (root / 'main.py').write_text('import sdk\nsdk.run_simple_bot()\n', encoding='utf-8')
    return root


class SeatParsingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.bot = simple_submission(Path(self.temp.name) / 'bot')

    def tearDown(self):
        self.temp.cleanup()

    def test_builtin_bot_fills_every_seat(self):
        self.assertEqual(resolve_seats('none', None), ('none',) * 8)
        self.assertEqual(resolve_seats('simple', None), ('simple',) * 8)
        self.assertFalse(needs_driver(resolve_seats('simple', None)))

    def test_bot_directory_and_mixed_seats(self):
        seats = resolve_seats(str(self.bot), None)
        self.assertEqual(seats, (self.bot.resolve(),) * 8)
        self.assertTrue(needs_driver(seats))
        mixed = resolve_seats(None, [f'{self.bot},simple,none', 'none', 'none', 'none', 'simple', 'none'])
        self.assertEqual(mixed[0], self.bot.resolve())
        self.assertEqual(mixed[1:], ('simple', 'none', 'none', 'none', 'none', 'simple', 'none'))

    def test_invalid_seats_are_refused(self):
        with self.assertRaises(ValueError):
            resolve_seats(None, ['none'] * 7)
        with self.assertRaises(ValueError):
            resolve_seats('simple', ['none'] * 8)
        with self.assertRaises(ValueError):
            resolve_seats(str(Path(self.temp.name) / 'missing'), None)
        empty = Path(self.temp.name) / 'empty'
        empty.mkdir()
        with self.assertRaises(ValueError):
            resolve_seats(str(empty), None)

    def test_inprocess_seat_filter(self):
        orders = SdkBotOrders(('simple',) + ('none',) * 7)
        self.assertEqual(orders.seats[0], 'simple')


class ExternalSeatMatchTests(unittest.TestCase):
    def test_external_bot_runs_through_match_driver(self):
        """One external seat (n0) beside seven empty seats on a 40-tick clock."""
        with tempfile.TemporaryDirectory() as temp:
            bot = simple_submission(Path(temp) / 'bot')
            catalog = Catalog()
            engine = match_economy(catalog, initialize(catalog, generate_candidate(1000)), total_ticks=40,
                                   fingerprint_each_tick=False)
            report = run_seated_match(engine, (bot,) + ('none',) * 7, replay=Path(temp) / 'replay.jsonl')
            self.assertTrue((Path(temp) / 'replay.jsonl').is_file())
        self.assertTrue(report['valid'])
        self.assertEqual(report['ticks'], 40)
        self.assertEqual(report['transport'], 'unsandboxed_dev_pipe')
        self.assertEqual(report['seats']['n1'], 'none')
        self.assertEqual(sorted(report['faults']), ['n0'])
        self.assertEqual(report['faults']['n0'], [])
        self.assertFalse(report['strategy_time']['n0']['custodial'])
        # The external SimpleBot's orders really reached the engine command
        # streams; an empty seat submitted nothing.
        streams = engine.state.command_streams
        self.assertTrue(any(r.status == 'ACCEPTED' for r in streams['n0'].recent))
        self.assertFalse(streams['n1'].recent)
        self.assertIn('n0', report['nations'])


if __name__ == '__main__':
    unittest.main()
