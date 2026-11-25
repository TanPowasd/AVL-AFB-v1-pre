"""Check contestant data isolation, full-parity doc/prompt/replay kit, purity, and zero leakage."""
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
import zipfile

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
from pack_contestant import PUBLIC_MAP, export_kit  # noqa: E402
from pack_prompt import build_prompt  # noqa: E402

TEXT_SUFFIXES = {".py", ".md", ".json", ".txt", ".html", ".js"}
FORBIDDEN_WORDS = re.compile(
    r"organizer|组委会|OPEN_ISSUES|HEAVY_TESTS|TanPowasd|Powasd|/data/ws|/data/home",
    re.IGNORECASE,
)


class ContestantKitTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.source = self.base / "source_kit"
        for src_rel, _ in PUBLIC_MAP:
            target = self.source / src_rel
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / src_rel, target)

    def export(self, name="player", make_zip=False, force=False):
        return export_kit(self.base / name, root=self.source, make_zip=make_zip, force=force)

    def test_full_parity_kit_purity_and_zero_reference_leakage(self):
        private = (
            "agents/submissions/other.py",
            "agents/reference/strong.py",
            "agents/reference/hist_carbon.py",
            "agents/reference/crop_rotation_strong.py",
            "agents/reference/hist_opus.py",
            "agents/reference/livestock_compound.py",
            "agents/reference/agronomy_arbitrage.py",
            "agents/reference/ranch_compounder.py",
            "agents/reference/tactical_counterplay.py",
            "agents/reference/README.md",
            "engine/PROVENANCE.md",
            "engine/champion/README.md",
            "engine/champion/RSIdemo/references/notes.json",
            "replays/private.json",
            "scripts/duel.py",
            "scripts/run_league.py",
            "scripts/pack_prompt.py",
            "config/scoring_v2.json",
        )
        for name in private:
            path = self.source / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("PRIVATE_SENTINEL", encoding="utf-8")

        (self.source / "engine/champion/main.py").parent.mkdir(parents=True, exist_ok=True)
        (self.source / "engine/champion/main.py").write_text("PUBLIC", encoding="utf-8")
        kit = self.export(make_zip=True)
        for name in private:
            self.assertFalse((kit / name).exists(), f"Private file leaked: {name}")

        expected_docs = (
            "README.md",
            "AGENTS.md",
            "AI_README.md",
            "FORMAT.md",
            "handbook/contest-rules.md",
            "handbook/player-guide.md",
            "handbook/game-facts.md",
            "prompts/task.txt",
            "agents/submissions/README.md",
            "agents/template/main.py",
            "agents/template/planner.py",
            "agents/template/params.json",
            "scripts/make_submission.py",
            "scripts/spar.py",
            "scripts/run_match.py",
            "scripts/record_replay.py",
            "viz/player.html",
            "agents/reference/champion.py",
            "engine/champion/main.py",
            "config/workspace.json",
        )
        for rel in expected_docs:
            self.assertTrue((kit / rel).is_file(), f"Missing expected public file: {rel}")

        self.assertEqual(
            (kit / "FORMAT.md").read_bytes(),
            (kit / "handbook/contest-rules.md").read_bytes(),
        )
        md_files = sorted(p.relative_to(kit).as_posix() for p in kit.rglob("*.md"))
        self.assertEqual(
            md_files,
            [
                "AGENTS.md",
                "AI_README.md",
                "FORMAT.md",
                "README.md",
                "agents/submissions/README.md",
                "handbook/contest-rules.md",
                "handbook/game-facts.md",
                "handbook/player-guide.md",
            ],
        )
        self.assertEqual(sorted(p.name for p in (kit / "agents/submissions").iterdir()), [".gitkeep", "README.md"])
        self.assertEqual([p.name for p in (kit / "replays").iterdir()], [".gitkeep"])

        # AFB purity check across every exported text file (including prompts/task.txt & manifest.json)
        for path in sorted(p for p in kit.rglob("*") if p.is_file()):
            if path.suffix in TEXT_SUFFIXES:
                text = path.read_text(encoding="utf-8", errors="replace")
                match = FORBIDDEN_WORDS.search(text)
                if match is not None:
                    self.fail(
                        f"Forbidden word {match.group(0)!r} found in {path.relative_to(kit).as_posix()}"
                    )

        # Offline player must have zero external CDN links
        player_html = (kit / "viz/player.html").read_text(encoding="utf-8")
        self.assertIsNone(
            re.search(r"https?://", player_html, re.IGNORECASE),
            "viz/player.html must not contain external HTTP(S)/CDN links",
        )

        # Prompt bundle matches live build_prompt()
        self.assertEqual((kit / "prompts/task.txt").read_text(encoding="utf-8"), build_prompt(str(ROOT)))

        inventory = json.loads((kit / "manifest.json").read_text(encoding="utf-8"))
        actual = {p.relative_to(kit).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                  for p in kit.rglob("*") if p.is_file() and p != kit / "manifest.json"}
        self.assertEqual(inventory["files"], actual)
        self.assertEqual(inventory["submission"], "agents/submissions/my_agent.zip（根目录 main.py）")
        self.assertEqual(inventory["unified_submission"], "../submissions/kaggriculture.zip")

        with zipfile.ZipFile(kit.with_suffix(".zip")) as zf:
            files = {name for name in zf.namelist() if not name.endswith("/")}
            self.assertEqual(files, set(actual) | {"manifest.json"})
            self.assertFalse(any(b"PRIVATE_SENTINEL" in zf.read(name) for name in files))

    def test_existing_submission_is_preserved_without_force(self):
        kit = self.export()
        submission = kit / "agents/submissions/my_agent.py"
        submission.write_text("MY_SUBMISSION", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "already exists"):
            self.export()
        self.assertEqual(submission.read_text(encoding="utf-8"), "MY_SUBMISSION")

    def test_standalone_baselines_and_isolation_guards(self):
        a, b = self.export("player-a"), self.export("player-b")
        listed = subprocess.run([sys.executable, "-X", "utf8", "scripts/spar.py", "--list"], cwd=a,
                                capture_output=True, encoding="utf-8", timeout=30)
        self.assertEqual(listed.returncode, 0, listed.stderr)
        for name in ("passive", "random", "greedy"):
            self.assertIn(name, listed.stdout)
        self.assertIn("champion", listed.stdout)

        played = subprocess.run(
            [sys.executable, "-X", "utf8", "scripts/run_match.py", "--a", "agents/starter.py",
             "--b", "agents/baselines/passive.py", "--seed-count", "1"],
            cwd=a, capture_output=True, encoding="utf-8", timeout=30,
        )
        self.assertEqual(played.returncode, 0, played.stderr)

        private = b / "logs/private.py"
        private.write_text("raise Exception('PRIVATE_SENTINEL')", encoding="utf-8")
        for cmd in (
            ("scripts/spar.py", str(private), "passive"),
            ("scripts/spar.py", "agents/starter.py", str(private)),
            ("scripts/spar.py", "--unrestricted"),
            ("scripts/run_match.py", "--a", "agents/starter.py", "--b", "agents/baselines/passive.py", "--unrestricted"),
            ("scripts/record_replay.py", "--a", "agents/starter.py", "--b", "passive", "--seed", "11", "--serve"),
        ):
            res = subprocess.run([sys.executable, "-X", "utf8", *cmd], cwd=a,
                                 capture_output=True, encoding="utf-8", timeout=30)
            self.assertEqual(res.returncode, 2, res.stdout + res.stderr)

    def test_zip_package_build_spar_replay_and_security(self):
        kit = self.export("player-zip")
        out_zip = kit / "agents/submissions/my_agent.zip"
        built = subprocess.run(
            [sys.executable, "-X", "utf8", "scripts/make_submission.py", "agents/template",
             "--out", str(out_zip)],
            cwd=kit, capture_output=True, encoding="utf-8", timeout=60,
        )
        self.assertEqual(built.returncode, 0, built.stdout + built.stderr)
        self.assertTrue(out_zip.is_file())

        # Spar against passive using packaged .zip
        sp = subprocess.run(
            [sys.executable, "-X", "utf8", "scripts/spar.py", str(out_zip), "passive", "--seeds", "11"],
            cwd=kit, capture_output=True, encoding="utf-8", timeout=60,
        )
        self.assertEqual(sp.returncode, 0, sp.stdout + sp.stderr)
        self.assertIn("vs passive", sp.stdout)

        # Record short replay using packaged .zip
        rec = subprocess.run(
            [sys.executable, "-X", "utf8", "scripts/record_replay.py", "--a", str(out_zip),
             "--b", "passive", "--seed", "11", "--steps", "48"],
            cwd=kit, capture_output=True, encoding="utf-8", timeout=60,
        )
        self.assertEqual(rec.returncode, 0, rec.stdout + rec.stderr)
        self.assertTrue((kit / "replays/replay_seed11_fw.json").is_file())

        # Archive security: reject missing main.py and path traversal
        sys.path.insert(0, str(ROOT / "engine"))
        import loader  # noqa: E402
        bad_nomain = self.base / "nomain.zip"
        with zipfile.ZipFile(bad_nomain, "w") as zf:
            zf.writestr("helper.py", "x = 1\n")
        self.assertTrue(any("main.py" in p for p in loader.check_archive(str(bad_nomain))))

        bad_trav = self.base / "trav.zip"
        with zipfile.ZipFile(bad_trav, "w") as zf:
            zf.writestr("../escape.py", "x = 1\n")
            zf.writestr("main.py", "def act(o): return {'farmer':['PASS'],'hands':[],'market':[]}\n")
        self.assertTrue(any("路径不安全" in p for p in loader.check_archive(str(bad_trav))))


if __name__ == "__main__":
    unittest.main()
