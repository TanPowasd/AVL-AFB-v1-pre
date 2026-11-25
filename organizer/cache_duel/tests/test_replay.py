# -*- coding: utf-8 -*-
"""cache_duel 回放系统、播放器、导出纯净性、冻结校验与 ELO 自动化测试。"""
from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parent.parent
ORGANIZER_ROOT = ROOT.parent

sys.path.insert(0, str(ROOT / "engine"))
sys.path.insert(0, str(ROOT / "score"))
sys.path.insert(0, str(ORGANIZER_ROOT))

import elo  # noqa: E402
import engine as engine_mod  # noqa: E402
import replay as replay_mod  # noqa: E402
import strategy_scoring  # noqa: E402


def _load_local_pack():
    spec = importlib.util.spec_from_file_location(
        "cache_duel_pack_contestant", ROOT / "scripts" / "pack_contestant.py"
    )
    mod = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(mod)
    return mod


local_pack = _load_local_pack()

FORBIDDEN_WORDS = re.compile(
    r"organizer|组委会|OPEN_ISSUES|HEAVY_TESTS|TanPowasd|Powasd|/data/ws|/data/home"
)
FORBIDDEN_NAMES = re.compile(
    r"(^|/)(\.git|__pycache__|hidden|official|reference)(/|$)|\.(pyc|so|log)$|problem_mod\.py$"
)
TEXT_SUFFIXES = {".py", ".md", ".json", ".txt", ".html", ".js"}


class TestReplayEngine(unittest.TestCase):
    def test_play_with_replay_matches_engine_across_configs(self):
        lru = str(ROOT / "agents" / "baselines" / "lru.py")
        fifo = str(ROOT / "agents" / "baselines" / "fifo.py")
        marking = str(ROOT / "agents" / "baselines" / "marking.py")
        cases = [
            (lru, fifo, 8, 64, 300, 11),
            (fifo, marking, 32, 256, 350, 7),
            (marking, lru, 64, 4096, 350, 19),
        ]
        for att, dfd, c, k, n, seed in cases:
            with self.subTest(c=c, k=k, n=n, seed=seed):
                ref = engine_mod.play(att, dfd, c, k, n, seed)
                res, data = replay_mod.play_with_replay(att, dfd, c, k, n, seed)
                self.assertEqual(res.def_misses, ref.def_misses)
                self.assertEqual(res.opt_misses, ref.opt_misses)
                self.assertAlmostEqual(res.def_score, ref.def_score, places=12)
                self.assertEqual(res.faults, ref.faults)
                self.assertEqual(data["format"], replay_mod.FORMAT)
                self.assertEqual(len(data["keys"]), n)
                self.assertEqual(len(data["opt_hits"]), n)
                self.assertEqual(len(data["opt_evicts"]), n)
                self.assertTrue(len(data["checkpoints"]) >= 2)
                ok, msg = replay_mod.verify_against_engine(data, ref)
                self.assertTrue(ok, msg)

    def test_verify_detects_tampering(self):
        lru = str(ROOT / "agents" / "baselines" / "lru.py")
        fifo = str(ROOT / "agents" / "baselines" / "fifo.py")
        res, data = replay_mod.play_with_replay(lru, fifo, 8, 64, 120, 5)
        ok, _ = replay_mod.verify_against_engine(data, res)
        self.assertTrue(ok)
        # 篡改一步命中标记必须被检出
        bad = json.loads(json.dumps(data))
        bad["hits"][0] = 1
        ok2, _ = replay_mod.verify_against_engine(bad, res)
        self.assertFalse(ok2)

    def test_sample_replays_exist_and_verify(self):
        replays_dir = ROOT / "replays"
        self.assertTrue((replays_dir / ".gitkeep").is_file())
        fw = replays_dir / "replay_cfg0_seed11_fw.json"
        sw = replays_dir / "replay_cfg0_seed11_sw.json"
        self.assertTrue(fw.is_file(), f"Missing sample replay: {fw}")
        self.assertTrue(sw.is_file(), f"Missing sample replay: {sw}")
        for p in (fw, sw):
            data = json.loads(p.read_text(encoding="utf-8"))
            ok, msg = replay_mod.verify_against_engine(data)
            self.assertTrue(ok, f"{p.name}: {msg}")


class TestCliAndVisualizer(unittest.TestCase):
    def test_record_replay_cli_and_verify(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            cmd = [
                sys.executable, "-X", "utf8",
                str(ROOT / "scripts" / "record_replay.py"),
                "--a", "lru", "--b", "marking",
                "--seed", "17", "--config", "0", "--length", "250",
                "--swap", "--out", tmp,
            ]
            run = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, encoding="utf-8")
            self.assertEqual(run.returncode, 0, f"{run.stdout}\n{run.stderr}")
            files = sorted(Path(tmp).glob("*.json"))
            self.assertEqual(len(files), 2)
            for f in files:
                data = json.loads(f.read_text(encoding="utf-8"))
                ok, msg = replay_mod.verify_against_engine(data)
                self.assertTrue(ok, msg)

    def test_run_match_cli_swap(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            cmd = [
                sys.executable, "-X", "utf8",
                str(ROOT / "scripts" / "run_match.py"),
                "--a", "lru", "--b", "fifo",
                "--swap", "--config-idx", "0", "--seeds", "11",
                "--out", tmp,
            ]
            run = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, encoding="utf-8")
            self.assertEqual(run.returncode, 0, f"{run.stdout}\n{run.stderr}")
            report = Path(tmp) / "match.json"
            self.assertTrue(report.is_file())
            payload = json.loads(report.read_text(encoding="utf-8"))
            self.assertEqual(payload["mode"], "swap")
            self.assertEqual(len(payload["games"]), 2)

    def test_visualizer_offline_and_clean(self):
        html_path = ROOT / "viz" / "player.html"
        self.assertTrue(html_path.is_file())
        text = html_path.read_text(encoding="utf-8")
        self.assertIn(replay_mod.FORMAT, text)
        # 零外链 / 零 CDN 检查
        self.assertNotRegex(text, r"https?://")
        self.assertNotRegex(text, r"<script[^>]+src\s*=")
        self.assertNotRegex(text, r"<link[^>]+href\s*=")
        self.assertIsNone(FORBIDDEN_WORDS.search(text))


class TestExportPurityAndFrozenProfile(unittest.TestCase):
    def test_load_profile_frozen_hashes_pass(self):
        profile = strategy_scoring.load_profile("cache_duel")
        self.assertEqual(profile["problem"], "cache_duel")
        self.assertEqual(profile["version"], "scoring_v2")

    def test_pack_contestant_exports_all_public_files_cleanly(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            dest = Path(tmp) / "cache_duel_kit"
            local_pack.export_kit(dest, root=ROOT, force=True)

            expected_public = [
                "README.md",
                "AGENTS.md",
                "AI_README.md",
                "FORMAT.md",
                "handbook/contest-rules.md",
                "handbook/player-guide.md",
                "handbook/game-facts.md",
                "prompts/task.txt",
                "engine/engine.py",
                "engine/policy.py",
                "engine/replay.py",
                "scripts/spar.py",
                "scripts/run_match.py",
                "scripts/record_replay.py",
                "viz/player.html",
                "config/format.json",
                "config/workspace.json",
                "replays/.gitkeep",
                "results/.gitkeep",
                "logs/.gitkeep",
            ]
            for rel in expected_public:
                self.assertTrue((dest / rel).is_file(), f"Missing exported file: {rel}")

            forbidden_private = [
                "scripts/duel.py",
                "scripts/run_league.py",
                "scripts/score.py",
                "scripts/pack_contestant.py",
                "scripts/pack_prompt.py",
                "score/elo.py",
                "score/tournament.py",
                "config/scoring_v2.json",
                "agents/reference/strong.py",
                "agents/reference/hark_strong.py",
                "agents/reference/adaptive_strong.py",
            ]
            for rel in forbidden_private:
                self.assertFalse((dest / rel).exists(), f"Private file leaked: {rel}")

            for p in sorted(dest.rglob("*")):
                if not p.is_file():
                    continue
                rel_posix = p.relative_to(dest).as_posix()
                self.assertIsNone(
                    FORBIDDEN_NAMES.search(rel_posix),
                    f"Forbidden path exported: {rel_posix}",
                )
                if p.suffix in TEXT_SUFFIXES:
                    content = p.read_text(encoding="utf-8", errors="replace")
                    m = FORBIDDEN_WORDS.search(content)
                    self.assertIsNone(m, f"Forbidden word {m[0] if m else ''} in {rel_posix}")

            # 在导出的选手工作区中校验 --serve 与 --unrestricted 均返回退出码 2
            r_serve = subprocess.run(
                [sys.executable, "-X", "utf8", str(dest / "scripts" / "record_replay.py"),
                 "--a", "lru", "--b", "fifo", "--seed", "1", "--length", "50", "--serve"],
                cwd=dest, capture_output=True, text=True, encoding="utf-8",
            )
            self.assertEqual(r_serve.returncode, 2)

            r_unr = subprocess.run(
                [sys.executable, "-X", "utf8", str(dest / "scripts" / "run_match.py"),
                 "--a", "lru", "--b", "fifo", "--config-idx", "0", "--seeds", "1", "--unrestricted"],
                cwd=dest, capture_output=True, text=True, encoding="utf-8",
            )
            self.assertEqual(r_unr.returncode, 2)


class TestEloAndPvpTools(unittest.TestCase):
    def test_elo_math_and_table(self):
        sa = elo.pair_score(0.8, 0.2)
        sb = elo.pair_score(0.2, 0.8)
        self.assertAlmostEqual(sa, 0.8, places=12)
        self.assertAlmostEqual(sa + sb, 1.0, places=12)

        out_w, res_w, _ = elo.pair_outcome(0.7, 0.3)
        self.assertEqual((out_w, res_w), ("A", 1.0))
        out_d, res_d, _ = elo.pair_outcome(0.502, 0.500)
        self.assertEqual((out_d, res_d), ("draw", 0.5))
        out_l, res_l, _ = elo.pair_outcome(0.2, 0.8)
        self.assertEqual((out_l, res_l), ("B", 0.0))

        self.assertEqual(elo.margin_factor(0.8, 0.2, cap=2.0), 2.0)
        self.assertAlmostEqual(elo.margin_factor(0.55, 0.45, cap=2.0), 1.2, places=9)

        ra, rb = elo.update(1500.0, 1500.0, 1.0, margin=1.5, k=24.0)
        self.assertAlmostEqual(ra + rb, 3000.0, places=9)
        self.assertGreater(ra, 1500.0)
        self.assertLess(rb, 1500.0)

        self.assertTrue(elo.is_half_score(2, 0, 4))
        self.assertTrue(elo.is_half_score(1, 2, 4))
        self.assertFalse(elo.is_half_score(3, 0, 4))

        tb = elo.tiebreak_seeds({}, used_seeds=[173, 181], count=3)
        self.assertEqual(tb, [191, 193, 197])

        md = elo.render_table([
            {"name": "alpha", "elo": 1540.2, "w": 3, "d": 1, "l": 0,
             "avg_score": 68.5, "avg_defense": 72.1, "avg_attack": 64.9, "avg_margin": 37.0},
        ])
        self.assertIn("alpha", md)
        self.assertIn("1540", md)

    def test_duel_and_league_smoke(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            duel_out = Path(tmp) / "duel.json"
            r_duel = subprocess.run(
                [sys.executable, "-X", "utf8", str(ROOT / "scripts" / "duel.py"),
                 "lru", "fifo", "--seeds", "1", "--no-tiebreak", "--jobs", "2",
                 "--out", str(duel_out)],
                cwd=ROOT, capture_output=True, text=True, encoding="utf-8",
            )
            self.assertEqual(r_duel.returncode, 0, f"{r_duel.stdout}\n{r_duel.stderr}")
            duel_data = json.loads(duel_out.read_text(encoding="utf-8"))
            self.assertEqual(len(duel_data["pairs"]), 3)

            league_dir = Path(tmp) / "league"
            r_league = subprocess.run(
                [sys.executable, "-X", "utf8", str(ROOT / "scripts" / "run_league.py"),
                 str(ROOT / "agents" / "baselines" / "lru.py"),
                 str(ROOT / "agents" / "baselines" / "fifo.py"),
                 "--seed-count", "1", "--jobs", "2", "--out", str(league_dir)],
                cwd=ROOT, capture_output=True, text=True, encoding="utf-8",
            )
            self.assertEqual(r_league.returncode, 0, f"{r_league.stdout}\n{r_league.stderr}")
            self.assertTrue((league_dir / "leaderboard.md").is_file())
            self.assertTrue((league_dir / "league.json").is_file())


if __name__ == "__main__":
    unittest.main()
