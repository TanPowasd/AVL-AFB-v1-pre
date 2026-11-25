"""Contestant kit export: content, byte-identical sdk/engine, no private material."""

import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest import mock

RIFT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RIFT / 'scripts'))
import pack_contestant  # noqa: E402
from organizer.rift_era.engine.determinism import engine_source_fingerprint  # noqa: E402


class PackTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        base = Path(self.temp.name)
        self.bots = base / 'bots'
        for tier in ('medium', 'advanced', 'super', 'hegemon', 'arbiter', 'verdant'):
            (self.bots / tier).mkdir(parents=True)
            shutil.copyfile(RIFT / 'kit/template/main.py', self.bots / tier / 'main.py')
            (self.bots / tier / 'bot.py').write_text(f'NAME = {tier!r}\n')
            shutil.copyfile(RIFT / 'sdk.py', self.bots / tier / 'sdk.py')
        self.out = base / 'kit'

    def tearDown(self):
        self.temp.cleanup()

    def test_export_contents_and_manifest(self):
        dest = pack_contestant.export_kit(self.out, bots=self.bots, make_zip=True)
        manifest = json.loads((dest / 'manifest.json').read_text())
        files = manifest['files']
        for required in ('README.md', 'GUIDE.md', 'COMMANDS.md', 'sdk.py', 'agents/template/main.py',
                         'agents/medium/bot.py', 'agents/advanced/bot.py', 'scripts/local_match.py',
                         'scripts/make_submission.py', 'rules/07_scoring_and_resources.md',
                         'runtime/pkg/rift_era/engine/match.py', 'runtime/designs/rift_era/data/runtime_rules.json'):
            self.assertIn(required, files)
        for rel, digest in files.items():
            self.assertEqual(hashlib.sha256((dest / rel).read_bytes()).hexdigest(), digest)
        for private in ('super', 'hegemon', 'arbiter', 'verdant'):
            self.assertFalse((dest / 'agents' / private).exists())
        self.assertFalse(any('scoring_v2' in rel or 'pipeline' in rel or rel.startswith('validation') for rel in files))
        self.assertEqual(manifest['engine_hash'], engine_source_fingerprint())
        self.assertEqual((dest / 'agents/template/sdk.py').read_bytes(), (RIFT / 'sdk.py').read_bytes())
        self.assertTrue(self.out.with_name('kit.zip').is_file())
        # the shipped engine fingerprints identically when imported as a stand-alone package
        code = ('import sys; sys.path.insert(0, sys.argv[1]); '
                'from rift_era.engine.determinism import engine_source_fingerprint as f; print(f())')
        import subprocess
        result = subprocess.run([sys.executable, '-c', code, str(dest / 'runtime/pkg')], capture_output=True,
                                text=True, timeout=60)
        self.assertEqual(result.stdout.strip(), engine_source_fingerprint(), result.stderr)

    def test_refuses_existing_output_and_private_leak(self):
        pack_contestant.export_kit(self.out, bots=self.bots)
        with self.assertRaises(ValueError):
            pack_contestant.export_kit(self.out, bots=self.bots)
        (self.bots / 'advanced' / 'bot.py').write_text('# see config/scoring_v2.json\n')
        with self.assertRaises(ValueError):
            pack_contestant.export_kit(self.out, bots=self.bots, force=True)

    def test_missing_public_bot_is_an_error(self):
        shutil.rmtree(self.bots / 'medium')
        with self.assertRaises(ValueError):
            pack_contestant.export_kit(self.out, bots=self.bots)


if __name__ == '__main__':
    unittest.main()
