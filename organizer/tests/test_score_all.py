import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import score_all
import strategy_scoring
import submission_sandbox


class TestFinalScoring(unittest.TestCase):
    def test_rehearsal_reads_fresh_mem_report_and_preserves_official_summary(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source_dir = root / "input"
            results = root / "results"
            mem = root / "mem_recall"
            for path in (source_dir, results, mem / "config", mem / "results"):
                path.mkdir(parents=True)
            source = source_dir / "mem_recall.py"
            source.write_text("# fixture\n", encoding="utf-8")
            digest = hashlib.sha256(source.read_bytes()).hexdigest()
            profile = {"profile_revision": "fixture", "expected_official_sets": ["test"]}
            (mem / "config/scoring_v2.json").write_text(json.dumps(profile), encoding="utf-8")
            official_path = results / "summary.json"
            official_path.write_text("old summary", encoding="utf-8")

            def report(official, score):
                macro = "_macro_official" if official else "_macro_dev"
                mode = "hidden" if official else "dev"
                return {"_scoring": {"scoring_version": "mem_recall_v2", "official": official,
                                     "profile_revision": "fixture", "data_mode": mode},
                        "candidate": {"_submission_sha256": digest, macro: {"score": score, "n_sets": 1}}}

            leaderboard = mem / "results/leaderboard.json"
            old = json.dumps(report(True, 0.72))
            leaderboard.write_text(old, encoding="utf-8")

            def score(command, **_):
                self.assertIn("--allow-dev", command)
                Path(command[command.index("--out") + 1]).write_text(json.dumps(report(False, 0.25)), encoding="utf-8")
                return 0

            argv = ["score_all.py", str(source_dir), "--name", "candidate", "--problems", "mem_recall", "--mem-allow-dev"]
            with patch.object(score_all, "ORGANIZER_ROOT", root), patch.object(sys, "argv", argv), patch.object(subprocess, "call", score):
                score_all.main()
            fresh = json.loads((results / "practice_summary.json").read_text(encoding="utf-8"))
            self.assertFalse(fresh["official"])
            self.assertEqual(fresh["problems"]["mem_recall"]["split"], "dev")
            self.assertEqual(fresh["total_score"], round(25.0 / len(score_all.SCORED_PROBLEMS), 2))
            self.assertEqual(leaderboard.read_text(encoding="utf-8"), old)
            self.assertEqual(official_path.read_text(encoding="utf-8"), "old summary")

    def test_empty_input_does_not_delete_existing_submissions(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source_dir = root / "input"
            source_dir.mkdir()
            existing = root / "cache_duel/agents/submissions/alice.py"
            existing.parent.mkdir(parents=True)
            existing.write_text("# previously dispatched", encoding="utf-8")
            argv = ["score_all.py", str(source_dir), "--problems", "cache_duel"]
            with patch.object(score_all, "ORGANIZER_ROOT", root), patch.object(sys, "argv", argv):
                score_all.main()
            self.assertTrue(existing.is_file())

    def test_score_existing_runs_only_named_model_and_marks_missing_tasks(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for problem, model in [("cache_duel", "alice"), ("cache_duel", "bob"), ("mem_recall", "bob")]:
                sub = root / problem / "agents" / "submissions" / f"{model}.py"
                sub.parent.mkdir(parents=True, exist_ok=True)
                sub.write_text(model, encoding="utf-8")
            config = root / "cache_duel" / "config"
            config.mkdir()
            (config / "scoring_v2.json").write_text('{"seed_count": 64}', encoding="utf-8")

            def score(command, **_):
                files = list(Path(command[command.index("--submissions") + 1]).iterdir())
                self.assertEqual([p.name for p in files], ["alice.py"])
                self.assertEqual(files[0].read_text(encoding="utf-8"), "alice")
                self.assertEqual(command[command.index("--seed-count") + 1], "1")
                Path(command[command.index("--out") + 1]).write_text("{}", encoding="utf-8")
                return 0

            argv = ["score_all.py", "--score-existing", "--name", "alice", "--problems", "cache_duel", "mem_recall",
                    "--strategy-seed-count", "1"]
            with (patch.object(score_all, "ORGANIZER_ROOT", root), patch.object(sys, "argv", argv),
                  patch.object(subprocess, "call", side_effect=score) as runner,
                  patch.object(score_all, "extract_problem_metric", return_value={"status": "OK", "score": 50}) as metric):
                score_all.main()
            runner.assert_called_once()
            self.assertTrue(metric.call_args.kwargs["practice"])
            result = json.loads((root / "results/practice_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(result["problems"]["mem_recall"]["status"], "unsubmitted")
            self.assertEqual(result["total_score"], round(50.0 / len(score_all.SCORED_PROBLEMS), 2))

    def test_strategy_seed_counts_match_contest_rules(self):
        for problem in ("cache_duel", "kaggriculture", "ai_training", "rift_era"):
            with self.subTest(problem=problem):
                self.assertFalse(strategy_scoring.official_seed_count(problem, 63, 64))
                self.assertTrue(strategy_scoring.official_seed_count(problem, 64, 64))
                self.assertEqual(strategy_scoring.official_seed_count(problem, 65, 64), problem != "rift_era")

    def test_compression_leaderboard_validates_provenance_and_range(self):
        problem = "compression_singularity"
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            base = root / problem
            (base / "config").mkdir(parents=True)
            (base / "agents/submissions").mkdir(parents=True)
            source = base / "agents/submissions/candidate.cpp"
            source.touch()
            profile = base / "config/scoring_v2.json"
            profile.write_text('{"profile_revision": "fixture"}', encoding="utf-8")
            row = {"submission": "candidate", "status": "AC", "submission_sha256": score_all._digest(source),
                   "score": 75.0, "raw_score": 300.0}
            path = root / "report.json"
            report = {"official": True, "scoring_version": "compression_singularity_v1", "profile_revision": "fixture",
                      "profile_sha256": score_all._digest(profile), "leaderboard": [row]}

            def extract():
                path.write_text(json.dumps(report), encoding="utf-8")
                return score_all.extract_problem_metric(problem, "candidate", path)

            with patch.object(score_all, "ORGANIZER_ROOT", root):
                self.assertEqual(extract()["score"], 75)
                report["official"] = False
                self.assertEqual(extract()["status"], "incompatible_scoring")
                report["official"] = True
                row["score"] = 1100
                self.assertEqual(extract()["status"], "invalid_result")
                row["score"] = 75
                row["submission_sha256"] = "stale"
                self.assertEqual(extract()["status"], "stale_submission")
                report["profile_sha256"] = "stale"
                self.assertEqual(extract()["status"], "stale_profile")
            self.assertEqual(score_all.overall_score([problem, problem], {problem: {"score": 100}}), round(100.0 / len(score_all.SCORED_PROBLEMS), 2))

    def test_cache_duel_technical_forfeits(self):
        @dataclass
        class Game:
            faults: dict
            def_score: float = 0.5
            def_misses: int = 1
            opt_misses: int = 1

        spec = {"problem": "cache_duel", "candidate": "candidate.py", "seat": 0, "opponent": "fixture",
                "seed": 1, "cache_config": {}}
        for faults, score in [({"defender_dead": True}, 100), ({"attacker_dead": True}, 0),
                              ({"attacker_dead": True, "defender_dead": True}, 0)]:
            with self.subTest(faults=faults), patch.dict(sys.modules, {"engine": SimpleNamespace(play=lambda *_, **__: Game(faults))}), patch.object(sys, "path", sys.path.copy()):
                self.assertEqual(strategy_scoring.run_game(spec)["score"], score)

    def test_official_windows_rejects_job_without_appcontainer(self):
        with patch.object(submission_sandbox.sys, "platform", "win32"), patch.object(submission_sandbox, "probe_windows_sandbox", return_value="windows_job_isolated"):
            with self.assertRaises(SystemExit):
                submission_sandbox.official_ready()


if __name__ == "__main__":
    unittest.main()
