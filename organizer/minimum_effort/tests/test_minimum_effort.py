# -*- coding: utf-8 -*-
"""《最小努力》（minimum_effort / U755456）完整单元测试与官方 36 点标答验证。"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "engine"))
sys.path.insert(0, str(ROOT / "scripts"))

import pack_contestant  # noqa: E402
import runner  # noqa: E402
import score as official_score  # noqa: E402


class TestMinimumEffort(unittest.TestCase):
    def test_official_sha256sums_and_frozen_profile(self) -> None:
        official_dir = ROOT / "data" / "official"
        sums_lines = (official_dir / "SHA256SUMS").read_text(encoding="utf-8").splitlines()
        checked = 0
        for ln in sums_lines:
            ln = ln.strip()
            if not ln:
                continue
            parts = ln.split()
            exp_hash = parts[0]
            fn = parts[-1].lstrip("*")
            act_hash = hashlib.sha256((official_dir / fn).read_bytes()).hexdigest()
            self.assertEqual(act_hash, exp_hash, f"SHA256SUMS mismatch on {fn}")
            checked += 1
        self.assertEqual(checked, 76)

        cfg = official_score.verify_frozen_profile()
        self.assertEqual(cfg["problem"], "minimum_effort")
        self.assertEqual(cfg["version"], "minimum_effort_v2")
        self.assertEqual(cfg["official_cases"], 36)
        self.assertEqual(cfg["memory_limit_mb"], 128)
        ew = cfg["efficiency_weights"]
        self.assertGreater(ew["time"], ew["mem"])

    def test_efficiency_weights_formula_and_monotonicity(self) -> None:
        self.assertGreater(runner.TIME_WEIGHT, runner.MEM_WEIGHT)
        self.assertAlmostEqual(runner.TIME_WEIGHT, 0.70, places=6)
        self.assertAlmostEqual(runner.MEM_WEIGHT, 0.30, places=6)

        # Free band: t <= 50 ms, m <= 16 MiB => E = 1.0
        self.assertAlmostEqual(
            runner.efficiency({"time_ms": 0.0, "mem_mb": 5.5, "time_limit_ms": 1000, "memory_limit_mb": 128}),
            1.0,
            places=6,
        )
        self.assertAlmostEqual(
            runner.efficiency({"time_ms": 50.0, "mem_mb": 16.0, "time_limit_ms": 2000, "memory_limit_mb": 128}),
            1.0,
            places=6,
        )

        # Time boundary (per-case TL = 1000 / 1500 / 2000 ms) => 1 - 0.70 = 0.30
        for tl in (1000, 1500, 2000):
            self.assertAlmostEqual(
                runner.efficiency({"time_ms": float(tl), "mem_mb": 8.0, "time_limit_ms": tl, "memory_limit_mb": 128}),
                0.30,
                places=6,
            )

        # Memory boundary (ML = 128 MiB) => 1 - 0.30 = 0.70
        self.assertAlmostEqual(
            runner.efficiency({"time_ms": 20.0, "mem_mb": 128.0, "time_limit_ms": 2000, "memory_limit_mb": 128}),
            0.70,
            places=6,
        )

        # Joint boundary => 0.30 * 0.70 = 0.21
        self.assertAlmostEqual(
            runner.efficiency({"time_ms": 2000.0, "mem_mb": 128.0, "time_limit_ms": 2000, "memory_limit_mb": 128}),
            0.21,
            places=6,
        )

        # Equal log-fraction (0.5): time penalty strictly stronger than space penalty
        t_mid = (runner.TIME_FREE_MS * 2000.0) ** 0.5  # ft = 0.5
        m_mid = (runner.MEM_FREE_MB * 128.0) ** 0.5    # fm = 0.5
        e_time = runner.efficiency({"time_ms": t_mid, "mem_mb": 8.0, "time_limit_ms": 2000, "memory_limit_mb": 128})
        e_mem = runner.efficiency({"time_ms": 10.0, "mem_mb": m_mid, "time_limit_ms": 2000, "memory_limit_mb": 128})
        self.assertAlmostEqual(e_time, 1.0 - 0.5 * runner.TIME_WEIGHT, places=6)
        self.assertAlmostEqual(e_mem, 1.0 - 0.5 * runner.MEM_WEIGHT, places=6)
        self.assertLess(e_time, e_mem)

        # score_results aggregates weighted score, raw_score, and avg_efficiency properly
        synth_manifest = {"subtasks": [{"id": 1, "name": "S1"}, {"id": 2, "name": "S2"}]}
        synth_cases = [
            {"subtask": 1, "points": 40.0, "verdict": "AC", "time_ms": 20.0, "mem_mb": 10.0, "time_limit_ms": 1000, "memory_limit_mb": 128},
            {"subtask": 1, "points": 40.0, "verdict": "AC", "time_ms": 1000.0, "mem_mb": 10.0, "time_limit_ms": 1000, "memory_limit_mb": 128},
            {"subtask": 2, "points": 20.0, "verdict": "WA", "time_ms": 10.0, "mem_mb": 10.0, "time_limit_ms": 1500, "memory_limit_mb": 128},
        ]
        sc = runner.score_results(synth_cases, synth_manifest)
        self.assertAlmostEqual(sc["raw_score"], 80.0, places=4)
        self.assertAlmostEqual(sc["score"], 52.0, places=4)  # 40*1.0 + 40*0.30
        self.assertAlmostEqual(sc["avg_efficiency"], 52.0 / 80.0, places=4)

    def test_manifest_scores_sum_to_100(self) -> None:
        manifest = json.loads((ROOT / "data" / "official" / "manifest.json").read_text(encoding="utf-8"))
        cases = manifest["cases"]
        self.assertEqual(len(cases), 36)
        self.assertAlmostEqual(sum(float(c["points"]) for c in cases), 100.0, places=6)
        self.assertAlmostEqual(sum(float(s["points"]) for s in manifest["subtasks"]), 100.0, places=6)

    def test_reference_solution_passes_samples_and_all_36_official(self) -> None:
        ref_src = ROOT / "agents" / "reference" / "leaderboard_profile_search.cpp"
        self.assertTrue(ref_src.is_file())

        # 1. Verify all 3 public samples
        with tempfile.TemporaryDirectory(prefix="afb_me_ref_samp_") as td:
            comp = runner.compile_submission(str(ref_src), td, str(ROOT))
            self.assertTrue(comp.ok, comp.log)
            samples_dir = str(ROOT / "data" / "samples")
            for idx in ("01", "02", "03"):
                c = {"name": f"sample_{idx}", "in": f"{idx}.in", "out": f"{idx}.out", "time_limit_ms": 2000, "memory_limit_mb": 128}
                res = runner.run_case(comp.exe_path, c, samples_dir)
                self.assertEqual(res["verdict"], "AC", f"Sample {idx} failed: {res}")

        # 2. Verify all 36 official hidden test cases (exact per-case TL and 128 MiB limit)
        rep = official_score.evaluate_submission(
            str(ref_src),
            str(ROOT / "data" / "official"),
            runs=1,
            stream_progress=False,
        )
        self.assertEqual(rep["status"], "AC", f"Reference failed official cases: {[c for c in rep['cases'] if c['verdict'] != 'AC']}")
        self.assertEqual(rep["ac_count"], 36)
        self.assertAlmostEqual(rep["raw_score"], 100.0, places=4)
        self.assertGreaterEqual(rep["score"], 80.0)
        self.assertLessEqual(rep["score"], 100.0)
        self.assertGreaterEqual(rep["avg_efficiency"], 0.80)
        self.assertLessEqual(rep["avg_efficiency"], 1.0)
        self.assertLessEqual(rep["max_mem_mb"], 128.0)

    def test_starter_compiles_and_fails_nonzero_samples(self) -> None:
        starter_src = ROOT / "agents" / "starter.cpp"
        with tempfile.TemporaryDirectory(prefix="afb_me_st_") as td:
            comp = runner.compile_submission(str(starter_src), td, str(ROOT))
            self.assertTrue(comp.ok, comp.log)
            res = runner.run_case(
                comp.exe_path,
                {"name": "sample_01", "in": "01.in", "out": "01.out", "time_limit_ms": 1000, "memory_limit_mb": 128},
                str(ROOT / "data" / "samples"),
            )
            self.assertEqual(res["verdict"], "WA")
            self.assertEqual(res["earned"], 0.0)

    def test_contestant_export_purity_and_spar(self) -> None:
        with tempfile.TemporaryDirectory(prefix="afb_me_pack_") as td:
            dest = Path(td) / "minimum_effort"
            pack_contestant.export_kit(dest, root=ROOT, force=True)
            manifest = json.loads((dest / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["unified_submission"], "../submissions/minimum_effort.cpp")
            for rel, exp_sha in manifest["files"].items():
                p = dest / rel
                self.assertTrue(p.is_file(), f"Missing {rel}")
                self.assertEqual(hashlib.sha256(p.read_bytes()).hexdigest(), exp_sha)

            self.assertFalse((dest / "data" / "official").exists())
            self.assertFalse((dest / "agents" / "reference").exists())
            self.assertFalse((dest / "tools").exists())

            proc = subprocess.run(
                [sys.executable, str(dest / "scripts" / "spar.py"), "--list"],
                cwd=dest,
                capture_output=True,
                text=True,
                encoding="utf-8",
            )
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertIn("sample_01", proc.stdout)
            self.assertIn("sample_03", proc.stdout)


if __name__ == "__main__":
    unittest.main()
