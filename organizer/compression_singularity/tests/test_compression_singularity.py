# -*- coding: utf-8 -*-
"""《压缩奇点》（compression_singularity）全量单元测试、VM 对拍、沙箱冒烟与零剧透纯净检查。"""
from __future__ import annotations

import json
import math
import os
from pathlib import Path
import random
import re
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "engine"))
sys.path.insert(0, str(ROOT / "hidden"))
import generator  # noqa: E402
import replay  # noqa: E402
import runner  # noqa: E402
import vm  # noqa: E402
import vm_ref  # noqa: E402


def _random_balanced_program(rng: random.Random, max_len: int = 40) -> str:
    ops = [">", "<", "+", "."]
    out: list[str] = []
    depth = 0
    while len(out) < max_len:
        r = rng.random()
        if r < 0.14 and len(out) + depth + 2 < max_len:
            out.append("[")
            depth += 1
        elif r < 0.28 and depth > 0:
            out.append("]")
            depth -= 1
        else:
            ch = rng.choice(ops)
            rep = rng.choice([1, 1, 1, 2, 3])
            for _ in range(rep):
                if len(out) + depth < max_len:
                    out.append(ch)
    out.extend("]" * depth)
    return "".join(out) or "."


class VMCorrectnessAndFuzzTest(unittest.TestCase):
    def test_section_iv_formal_example(self):
        target = "11110111101111011110"
        prog = "+>+>+>+[....+.<]"
        r_cpp = vm.verify_program(target, prog)
        r_py = vm_ref.run_reference_vm(target, prog)
        self.assertEqual(r_cpp["verdict"], "AC")
        self.assertEqual(r_py["verdict"], "AC")
        self.assertEqual(r_cpp["steps"], 40)
        self.assertEqual(r_py["steps"], 40)
        self.assertEqual((r_cpp["p_final"], r_cpp["p_min"], r_cpp["p_max"]), (-1, -1, 3))
        self.assertEqual((r_py["p_final"], r_py["p_min"], r_py["p_max"]), (-1, -1, 3))

    def test_syntax_whitespace_and_length_limits(self):
        # Single trailing \n or \r\n allowed; double newline / space / bad char / unmatched brackets rejected.
        self.assertEqual(vm.verify_program("0", ".\n")["verdict"], "AC")
        self.assertEqual(vm.verify_program("0", ".\r\n")["verdict"], "AC")
        self.assertEqual(vm.verify_program("0", ".\n\n")["verdict"], "SYNTAX_ERROR")
        self.assertEqual(vm.verify_program("0", ". ")["verdict"], "SYNTAX_ERROR")
        self.assertEqual(vm.verify_program("0", ".a")["verdict"], "SYNTAX_ERROR")
        self.assertEqual(vm.verify_program("0", "")["verdict"], "SYNTAX_ERROR")
        self.assertEqual(vm.verify_program("0", "[")["verdict"], "SYNTAX_ERROR")
        self.assertEqual(vm.verify_program("0", "]")["verdict"], "SYNTAX_ERROR")
        self.assertEqual(vm.verify_program("0", "][")["verdict"], "SYNTAX_ERROR")
        # Length limit: max_prog_len = 2*L + 100
        too_long = "+" * 103
        self.assertEqual(vm.verify_program("0", too_long)["verdict"], "PROGRAM_TOO_LONG")
        # Exact max_prog_len = 102 for L=1 is allowed (100 '+' + 1 '+' + 1 '.' = 102 -> flips 101 times = '1')
        exact_ok = "+" * 101 + "."
        self.assertEqual(len(exact_ok), 102)
        self.assertEqual(vm.verify_program("1", exact_ok)["verdict"], "AC")

    def test_differential_fuzz_against_pure_python_vm(self):
        rng = random.Random(20261121)
        tricky = [
            ("0", "+[]", 50, 10),
            ("0", "[]", 50, 10),
            ("0", "+[+].", 3, 10),
            ("0", "+[+].", 4, 10),
            ("0", "+[+].", 5, 10),
            ("0", "+>+>+>[>].", 20, 2),
            ("0", "+>+>+>[>].", 7, 2),
            ("0", "+>+>+>[>>].", 20, 3),
            ("0", "+>+>+>[>>].", 10, 10),
            ("0", "+<+<+<[<].", 20, 2),
            ("0", "+<+<+<[<<].", 10, 10),
            ("000", "....", 3, 5),
            ("000", "....", 4, 5),
            ("010", "....", 1, 5),
            ("010", "....", 2, 5),
            ("0", ">>><<<.", 20, 2),
            ("0", "<<<>>>.", 20, 2),
            ("0", ">>><<<.", 2, 2),
            ("1", "+++.", 4, 5),
            ("1", "+++.", 3, 5),
        ]
        for _ in range(180):
            L = rng.randint(1, 20)
            target = "".join(rng.choice("01") for _ in range(L))
            prog = _random_balanced_program(rng, max_len=rng.randint(4, 36))
            sl = rng.choice([5, 15, 40, 120, 500])
            tb = rng.choice([2, 4, 8, 20])
            tricky.append((target, prog, sl, tb))

        for target, prog, sl, tb in tricky:
            a = vm.verify_program(target, prog, step_limit=sl, tape_bound=tb)
            b = vm_ref.run_reference_vm(target, prog, step_limit=sl, tape_bound=tb)
            for k in ("verdict", "steps", "out_len", "p_final", "p_min", "p_max"):
                self.assertEqual(a[k], b[k], f"Mismatch on {(target, prog, sl, tb)} key={k}: cpp={a} py={b}")

    def test_vm_macro_speed_high_steps(self):
        # 1) Infinite balanced loop anti-DoS: O(1) fast-forward up to full 3e12 step limit (64-bit exact, zero truncation!)
        self.assertEqual(vm.DEFAULT_STEP_LIMIT, 3 * 10**12)
        self.assertEqual(runner.VM_STEP_LIMIT, 3 * 10**12)
        t0 = time.perf_counter()
        res_inf = vm.verify_program("0", "+[><]", step_limit=vm.DEFAULT_STEP_LIMIT, tape_bound=100, mode="fast")
        self.assertEqual(res_inf["verdict"], "STEP_LIMIT_EXCEEDED")
        self.assertEqual(res_inf["steps"], 3 * 10**12)
        self.assertLess(time.perf_counter() - t0, 1.0)
        res_inf_jit = vm.verify_program("0", "+[><]", step_limit=vm.DEFAULT_STEP_LIMIT, tape_bound=100, mode="jit")
        self.assertEqual(res_inf_jit["verdict"], "STEP_LIMIT_EXCEEDED")
        self.assertEqual(res_inf_jit["steps"], 3 * 10**12)

        # 2) Real terminating 2^18 two-track binary counter (~36M raw steps) verified identically on fast_vm2 & jit_aot
        prog = ">+" + ">>+" * 18 + ">+[<[<<]>>>[+>>]+<[>>]<]"
        t1 = time.perf_counter()
        r_fast = vm.simulate_program(prog, mode="fast", profile=True)
        dt_fast = time.perf_counter() - t1
        self.assertEqual(r_fast["verdict"], "AC")
        self.assertGreater(r_fast["steps"], 30_000_000)
        self.assertLess(dt_fast, 2.0)
        self.assertTrue(r_fast.get("hotspots"))

        r_jit = vm.simulate_program(prog, mode="jit")
        self.assertEqual(r_jit["verdict"], "AC")
        self.assertEqual(r_jit["steps"], r_fast["steps"])
        self.assertEqual(r_jit["p_final"], r_fast["p_final"])
        self.assertEqual(r_jit["p_min"], r_fast["p_min"])
        self.assertEqual(r_jit["p_max"], r_fast["p_max"])

    def test_jit_and_simulate_cli_match_reference(self):
        cases = [
            ("11110111101111011110", "+>+>+>+[....+.<]", 1000, 100),
            ("0", "+[+>+<<+].", 100, 10),
            ("0", "+>+>+>[+>].", 100, 10),
            ("0", "+<+<+<[+<].", 100, 10),
            ("0", "+[><]", 9, 10),
            ("0", "+[>+<]", 2, 0),
        ]
        for target, prog, sl, tb in cases:
            j = vm.verify_program(target, prog, step_limit=sl, tape_bound=tb, mode="jit")
            r = vm_ref.run_reference_vm(target, prog, step_limit=sl, tape_bound=tb)
            for k in ("verdict", "steps", "out_len", "p_final", "p_min", "p_max"):
                self.assertEqual(j[k], r[k], f"JIT vs ref mismatch on {(target, prog, sl, tb)} key={k}")

        sim = subprocess.run(
            [sys.executable, str(ROOT / "scripts/simulate.py"), "-e", "+>+>+>+[....+.<]", "--profile", "--json"],
            cwd=ROOT, capture_output=True, text=True, encoding="utf-8",
        )
        self.assertEqual(sim.returncode, 0, sim.stderr)
        data = json.loads(sim.stdout)
        self.assertEqual(data["verdict"], "AC")
        self.assertEqual(data["steps"], 40)
        self.assertEqual(data["out_bits"], "11110111101111011110")
        self.assertTrue(data.get("hotspots"))

    def test_simd_strides_and_lazy_tape_growth(self):
        # Verify AVX-512/AVX2 boundary widths (15, 16, 31, 32, 63, 64, 65) on stride-1/2/4 seek & clear-seek
        for n_ones in (15, 16, 31, 32, 63, 64, 65):
            for prog in (
                "+>" * n_ones + "<[<]>[>].",
                "+>" * n_ones + "<[+<]>[+>].",
                "+>+>" * n_ones + "<<[<<]>>[>>].",
                "+>+>" * n_ones + "<<[+<<]>>[+>>].",
                "+>>>>" * n_ones + "<<<<[<<<<]>>>>[>>>>].",
            ):
                rf = vm.verify_program("0", prog, step_limit=100_000, tape_bound=512, max_prog_len=2000, mode="fast")
                rr = vm_ref.run_reference_vm("0", prog, step_limit=100_000, tape_bound=512, max_prog_len=2000)
                for k in ("verdict", "steps", "out_len", "p_final", "p_min", "p_max"):
                    self.assertEqual(rf[k], rr[k], f"SIMD boundary mismatch n={n_ones} key={k}")

        # Verify dynamic lazy-tape expansion beyond 32,768 cells on BOTH fast_vm4 and jit_c99
        prog_grow = (
            "<+" * 190 + "[>]<"
            + "[[<]>+>[>]>[>]" + "+>" * 200 + "<[<]<]"
            + ">[+>]<[+<]."
        )
        rf = vm.verify_program("0", prog_grow, step_limit=10**9, tape_bound=100_000, max_prog_len=2000, mode="fast")
        rj = vm.verify_program("0", prog_grow, step_limit=10**9, tape_bound=100_000, max_prog_len=2000, mode="jit")
        self.assertEqual(rf["verdict"], "AC")
        self.assertGreaterEqual(rf["p_max"], 38_000)
        for k in ("verdict", "steps", "out_len", "p_final", "p_min", "p_max"):
            self.assertEqual(rf[k], rj[k], f"Lazy-tape-growth fast vs jit mismatch key={k}")


class ScoringAndGeneratorTest(unittest.TestCase):
    def test_scoring_formula_invariants(self):
        self.assertEqual(vm.compute_norm_score(0.0), 0.0)
        self.assertEqual(vm.compute_norm_score(-5.0), 0.0)
        self.assertAlmostEqual(vm.compute_norm_score(1000.0), 50.0, places=9)
        self.assertAlmostEqual(vm.compute_norm_score(100.0), 15.09796, places=4)
        vals = [vm.compute_norm_score(r) for r in (0, 1, 10, 25, 100, 500, 1000, 10000, 100000)]
        self.assertEqual(vals, sorted(vals))
        self.assertTrue(all(vals[i] < vals[i + 1] for i in range(len(vals) - 1)))
        self.assertTrue(all(0.0 <= v < 100.0 for v in vals))

    def test_eight_families_determinism_and_quality(self):
        for fam in ("F1", "F2", "F3", "F4", "F5", "F6", "F7", "F8"):
            s1, m1 = generator.generate_case(fam, 10_000, seed=77777)
            s2, m2 = generator.generate_case(fam, 10_000, seed=77777)
            self.assertEqual(s1, s2)
            self.assertEqual(m1, m2)
            self.assertEqual(len(s1), 10_000)
            self.assertTrue(set(s1) <= {"0", "1"})
            b_ref = vm.compute_b_ref(s1)
            self.assertGreater(b_ref, 100)

    def test_frozen_profile_validates(self):
        cfg = runner.verify_frozen_profile(ROOT)
        self.assertEqual(cfg["problem"], "compression_singularity")
        self.assertEqual(cfg["official_cases"], 512)
        self.assertEqual(cfg["vm_step_limit"], 3 * 10**12)
        fmt = json.loads((ROOT / "config" / "format.json").read_text(encoding="utf-8"))
        self.assertEqual(fmt["vm"]["step_limit"], 3 * 10**12)


class EndToEndAndPurityTest(unittest.TestCase):
    def test_starter_and_rle_baseline_on_samples_and_sandbox(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            exe_start = runner.compile_submission(str(ROOT / "agents/starter.cpp"), str(Path(tmp) / "starter"))
            self.assertTrue(exe_start.ok, exe_start.log)
            exe_rle = runner.compile_submission(str(ROOT / "agents/baselines/rle_loop.cpp"), str(Path(tmp) / "rle"))
            self.assertTrue(exe_rle.ok, exe_rle.log)

            # Test on 00_example and a long-run string where rle_loop strictly beats starter
            r_s = runner.run_sample_file(exe_start.exe_path, str(ROOT / "data/samples/00_example.in"))
            r_r = runner.run_sample_file(exe_rle.exe_path, str(ROOT / "data/samples/00_example.in"))
            self.assertEqual(r_s["verdict"], "AC")
            self.assertEqual(r_r["verdict"], "AC")

            long_runs = ("1" * 400 + "0" * 400) * 5
            rs_long = runner.evaluate_target_case(exe_start.exe_path, "runs", long_runs)
            rr_long = runner.evaluate_target_case(exe_rle.exe_path, "runs", long_runs)
            self.assertEqual(rs_long["verdict"], "AC")
            self.assertEqual(rr_long["verdict"], "AC")
            self.assertLess(rr_long["prog_len"], rs_long["prog_len"] // 5)
            self.assertGreater(rr_long["raw_score"], rs_long["raw_score"] * 5)

    def test_score_quick_with_sandbox_and_practice_guard(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            out_json = Path(tmp) / "quick.json"
            cmd = [
                sys.executable, "-X", "utf8", str(ROOT / "scripts/score.py"),
                "--src", str(ROOT / "agents/starter.cpp"),
                "--quick", "--seeds", "12345",
                "--out", str(out_json),
            ]
            run = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, encoding="utf-8", timeout=120)
            self.assertEqual(run.returncode, 0, f"score.py failed:\n{run.stdout}\n{run.stderr}")
            data = json.loads(out_json.read_text(encoding="utf-8"))
            self.assertFalse(data["official"])
            self.assertEqual(data["status"], "AC")
            self.assertEqual(data["ac_count"], 8)
            self.assertGreater(data["raw_score"], 0.0)
            self.assertTrue(0.0 < data["score"] < 100.0)

            # Practice must refuse to overwrite official leaderboard.json
            bad_cmd = [
                sys.executable, "-X", "utf8", str(ROOT / "scripts/score.py"),
                "--src", str(ROOT / "agents/starter.cpp"),
                "--quick", "--out", str(ROOT / "results/leaderboard.json"),
            ]
            bad = subprocess.run(bad_cmd, cwd=ROOT, capture_output=True, text=True, encoding="utf-8", timeout=30)
            self.assertEqual(bad.returncode, 2)
            self.assertIn("practice results must not overwrite", bad.stderr)

    def test_replay_and_visualizer_offline_and_valid_js(self):
        sample_rep = ROOT / "replays" / "replay_sample_00.json"
        self.assertTrue(sample_rep.is_file())
        data = json.loads(sample_rep.read_text(encoding="utf-8"))
        self.assertEqual(data["format"], replay.REPLAY_FORMAT)
        self.assertEqual(data["cases"][0]["verdict"], "AC")
        self.assertEqual(data["cases"][0]["steps"], 40)
        self.assertGreater(len(data["cases"][0]["trace"]), 1)

        html = (ROOT / "viz" / "player.html").read_text(encoding="utf-8")
        self.assertNotIn("http://", html)
        self.assertNotIn("https://", html)
        scripts = re.findall(r"<script[^>]*>(.*?)</script>", html, flags=re.S | re.I)
        self.assertTrue(scripts)
        if shutil.which("node"):
            with tempfile.NamedTemporaryFile("w", suffix=".js", encoding="utf-8", delete=False) as f:
                f.write("\n".join(scripts))
                js_path = Path(f.name)
            try:
                chk = subprocess.run(["node", "--check", str(js_path)], capture_output=True, text=True)
                self.assertEqual(chk.returncode, 0, chk.stderr)
            finally:
                js_path.unlink(missing_ok=True)

    def test_export_purity_and_zero_spoiler_leakage(self):
        forbidden_repo = re.compile(r"organizer|组委会|OPEN_ISSUES|HEAVY_TESTS|TanPowasd|Powasd|/data/ws|/data/home")
        forbidden_spoiler = re.compile(
            r"\bLFSR\b|Berlekamp|Sequitur|L-System|L_System|元胞自动机|元胞|\bECA\b|\bEller\b|迷宫|"
            r"\bFCSR\b|2-进|Weyl|\bNLFSR\b|Shrinking|Kolakoski|Cyclic Tag|循环标签|Myhill|自动序列|"
            r"递归语法|黑盒协议|空间演化|可解耦混淆|"
            r"F[1-8]_100k|f[1-8]_sample",
            re.IGNORECASE,
        )
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            dest = Path(tmp) / "cs_kit"
            cmd = [
                sys.executable, "-X", "utf8",
                str(ROOT / "scripts/pack_contestant.py"),
                "--out", str(dest), "--force",
            ]
            run = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, encoding="utf-8")
            self.assertEqual(run.returncode, 0, f"Export failed:\n{run.stdout}\n{run.stderr}")

            # Protected organizer-only files must NEVER be exported
            self.assertFalse((dest / "hidden").exists())
            self.assertFalse((dest / "scripts/score.py").exists())
            self.assertFalse((dest / "config/scoring_v2.json").exists())
            self.assertFalse((dest / "tests").exists())

            # All 8 anonymous specimens + example must exist
            for fn in (
                "00_example.in", "00_example.out",
                "specimen_1.in", "specimen_2.in", "specimen_3.in", "specimen_4.in",
                "specimen_5.in", "specimen_6.in", "specimen_7.in", "specimen_8.in",
                "manifest.json",
            ):
                self.assertTrue((dest / "data/samples" / fn).is_file(), f"Missing {fn}")

            # Scan EVERY exported text file for both repo-leak and spoiler-leak!
            for p in dest.rglob("*"):
                if p.is_file() and p.suffix.lower() in {".md", ".txt", ".json", ".py", ".cpp", ".h", ".html", ".js"}:
                    txt = p.read_text(encoding="utf-8", errors="replace")
                    rel = p.relative_to(dest).as_posix()
                    m1 = forbidden_repo.search(txt)
                    self.assertIsNone(m1, f"Repo marker {m1.group(0) if m1 else ''} leaked in {rel}")
                    m2 = forbidden_spoiler.search(txt)
                    self.assertIsNone(m2, f"Family spoiler {m2.group(0) if m2 else ''} leaked in {rel}")

            # Smoke test spar.py --list & verify_program.py inside exported contestant workspace!
            r_list = subprocess.run([sys.executable, "scripts/spar.py", "--list"], cwd=dest, capture_output=True, text=True, encoding="utf-8")
            self.assertEqual(r_list.returncode, 0, r_list.stderr)
            self.assertIn("specimen_1", r_list.stdout)

            r_ver = subprocess.run(
                [sys.executable, "scripts/verify_program.py", "data/samples/00_example.in", "data/samples/00_example.out"],
                cwd=dest, capture_output=True, text=True, encoding="utf-8",
            )
            self.assertEqual(r_ver.returncode, 0, r_ver.stderr)


if __name__ == "__main__":
    unittest.main()
