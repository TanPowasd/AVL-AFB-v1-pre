# -*- coding: utf-8 -*-
"""《双符奇点》（sk_singularity）全面单元测试与集成校验。"""
from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path
import random
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "engine"))
sys.path.insert(0, str(ROOT / "hidden"))
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "luogu"))

import common  # noqa: E402
import export_luogu  # noqa: E402
import pack_contestant  # noqa: E402
import runner  # noqa: E402
import schedule  # noqa: E402
import score as official_score  # noqa: E402


def _rand_sexp(n: int, rng: random.Random) -> str:
    if n == 1:
        return "S" if rng.random() < 0.56 else "K"
    l = rng.randint(1, n - 1)
    return f"({_rand_sexp(l, rng)} {_rand_sexp(n - l, rng)})"


class TestSkSingularity(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.judge_exe = common.ensure_judge_binary(ROOT)
        cls.practice_cases = json.loads((ROOT / "config" / "practice_suite.json").read_text(encoding="utf-8"))["cases"]
        cls.official_cases = schedule.load_raw_official_cases()

    def _eval_expr(self, case: dict, expr: str, *, l_ref: float = 10.0, vmax: int | None = None, trace: int = 0) -> dict:
        stdin_text = runner.format_case_input(case, vmax=vmax)
        return common.run_judge_cli(
            self.judge_exe,
            stdin_text,
            (expr + "\n").encode("utf-8"),
            l_ref=l_ref,
            trace_steps=trace,
        )

    def _brute_check(self, case: dict, expr: str, limit: int = 2000) -> dict:
        stdin_text = runner.format_case_input(case)
        with tempfile.TemporaryDirectory(prefix="afb_sk_bf_") as td:
            cp = os.path.join(td, "case.in")
            ep = os.path.join(td, "expr.out")
            Path(cp).write_text(stdin_text, encoding="utf-8", newline="\n")
            Path(ep).write_text(expr + "\n", encoding="utf-8", newline="\n")
            proc = subprocess.run(
                [self.judge_exe, "--brute-check", cp, "--expr", ep, "--limit", str(limit)],
                capture_output=True,
                text=True,
                encoding="utf-8",
                check=True,
            )
            return json.loads(proc.stdout.strip())

    def test_known_sk_anchors_and_edge_semantics(self) -> None:
        std_case = {
            "N": 24, "a_S": 3, "rhs_S": "((0 2) (1 2))", "a_K": 2, "rhs_K": "0",
            "req": "-", "max_nodes": 10000000, "time_limit_ms": 5000,
        }
        # 1. Sample N=5: (((S (S K)) K) K) -> 3 steps
        r1 = self._eval_expr(std_case, "(((S (S K)) K) K)")
        self.assertEqual(r1["verdict"], "AC")
        self.assertEqual(r1["exact_steps"], 3)
        self.assertEqual(r1["leaves"], 5)

        # 2. Sample N=8 (App-Left / App-Right order): (((S ((S K) K)) ((S K) K)) K) -> 5 steps
        r2 = self._eval_expr(std_case, "(((S ((S K) K)) ((S K) K)) K)")
        self.assertEqual(r2["verdict"], "AC")
        self.assertEqual(r2["exact_steps"], 5)

        # 3. Exact global champion at N=8: ((((S (S ((S S) S))) S) S) K) -> 1958 steps
        r3 = self._eval_expr(std_case, "((((S (S ((S S) S))) S) S) K)", l_ref=8.0, trace=10)
        self.assertEqual(r3["verdict"], "AC")
        self.assertEqual(r3["exact_steps"], 1958)
        self.assertEqual(r3["leaves"], 8)
        self.assertGreaterEqual(len(r3["trace"]), 10)
        self.assertGreater(len(r3["dag_growth"]), 10)

        # 4a. Standard SK Omega = (S I I)(S I I) grows I^k(S I I): WHNF-congruence prover certifies DIV!
        I_comb = "((S K) K)"
        M_comb = f"((S {I_comb}) {I_comb})"
        omega = f"({M_comb} {M_comb})"
        r_omega = self._eval_expr(std_case, omega, vmax=500)
        self.assertEqual(r_omega["verdict"], "DIV")
        self.assertIn("CYCLE_WHNF", r_omega["message"])
        self.assertLess(r_omega["dag_nodes"], 200)
        self.assertEqual(r_omega["norm_score"], 0.0)

        # 4a-2. Shifted Omega M(I M), M(I(I M)) and Y-combinator (WHNF & NF divergence) -> DIV!
        std_big = dict(std_case, N=64)
        for div_expr, exp_cyc in [
            (f"({M_comb} ({I_comb} {M_comb}))", "CYCLE_WHNF"),
            (f"({M_comb} ({I_comb} ({I_comb} {M_comb})))", "CYCLE_WHNF"),
            (f"(((S (K {M_comb})) ((S ((S (K S)) K)) (K {M_comb}))) {I_comb})", "CYCLE_WHNF"),
            (f"(((S (K {M_comb})) ((S ((S (K S)) K)) (K {M_comb}))) K)", "CYCLE_NF"),
            (f"(((S (K {M_comb})) ((S ((S (K S)) K)) (K {M_comb}))) (S K))", "CYCLE_NF"),
        ]:
            rd = self._eval_expr(std_big, div_expr)
            self.assertEqual(rd["verdict"], "DIV", f"Expected DIV for {div_expr}, got {rd}")
            self.assertIn(exp_cyc, rd["message"])
            self.assertLess(rd["dag_nodes"], 300)

        # 4a-3. Soundness check: nested I-chains & Church exponentiation must still halt identically (0 false positives)
        C2 = f"((S ((S (K S)) K)) {I_comb})"
        for halt_expr, exp_steps in [
            (f"({I_comb} ({I_comb} K))", 4),
            (f"({I_comb} ({I_comb} ({I_comb} ({I_comb} ({I_comb} ({I_comb} K))))))", 12),
            (f"(((({C2} {C2}) {C2}) {I_comb}) K)", 210),
        ]:
            rh = self._eval_expr(std_big, halt_expr)
            self.assertEqual(rh["verdict"], "AC", f"False positive on halting term: {rh}")
            self.assertEqual(rh["exact_steps"], exp_steps)

        # 4b. Exact WHNF cycle under P6_Ar23Dup: S x y -> (x y) y, so ((S S) S) -> ((S S) S) -> DIV!
        p6_case = {
            "N": 12, "a_S": 2, "rhs_S": "((0 1) 1)", "a_K": 3, "rhs_K": "(0 2)",
            "req": "-", "max_nodes": 10000000, "time_limit_ms": 5000,
        }
        r_cyc_w = self._eval_expr(p6_case, "((S S) S)")
        self.assertEqual(r_cyc_w["verdict"], "DIV")
        self.assertIn("CYCLE_WHNF", r_cyc_w["message"])
        self.assertEqual(r_cyc_w["norm_score"], 0.0)

        # 4c. Exact NF cycle: S x -> (K (S x)) reaches WHNF (K (S K)) in 1 step, then nf(S K) loops -> DIV!
        nf_cyc_case = {
            "N": 12, "a_S": 1, "rhs_S": "(K (S 0))", "a_K": 2, "rhs_K": "0",
            "req": "-", "max_nodes": 10000000, "time_limit_ms": 5000,
        }
        r_cyc_n = self._eval_expr(nf_cyc_case, "(S K)")
        self.assertEqual(r_cyc_n["verdict"], "DIV")
        self.assertIn("CYCLE_NF", r_cyc_n["message"])
        self.assertEqual(r_cyc_n["norm_score"], 0.0)

        # 5. Discarded divergent branch: ((K S) Omega) -> AC in exactly 1 step!
        r_disc = self._eval_expr(std_case, f"((K S) {omega})")
        self.assertEqual(r_disc["verdict"], "AC")
        self.assertEqual(r_disc["exact_steps"], 1)

        # 6. Shared duplicated subterm (no call-by-need undercounting!)
        dup_expr = "(((S (S K)) (S K)) ((K S) K))"
        r_dup = self._eval_expr(std_case, dup_expr)
        bf_dup = self._brute_check(std_case, dup_expr, limit=500)
        self.assertEqual(r_dup["verdict"], "AC")
        self.assertTrue(bf_dup["halted"])
        self.assertEqual(r_dup["exact_steps"], bf_dup["steps"])

    def test_brute_force_cross_verification_across_all_families(self) -> None:
        rng = random.Random(20261122)
        seen_rules: set[tuple] = set()
        unique_cases: list[dict] = []
        for c in self.practice_cases + self.official_cases:
            key = (c["a_S"], c["rhs_S"], c["a_K"], c["rhs_K"])
            if key not in seen_rules:
                seen_rules.add(key)
                c_copy = dict(c)
                c_copy["N"] = 12
                c_copy["req"] = "-"
                unique_cases.append(c_copy)

        self.assertGreaterEqual(len(unique_cases), 12)
        checked = 0
        for c in unique_cases:
            for _ in range(12):
                expr = _rand_sexp(rng.randint(4, 10), rng)
                mr = self._eval_expr(c, expr)
                bf = self._brute_check(c, expr, limit=1200)
                if bf["halted"]:
                    self.assertEqual(mr["verdict"], "AC", f"Rule {c['family']} expr={expr}")
                    self.assertTrue(mr["is_exact"])
                    self.assertEqual(mr["exact_steps"], bf["steps"], f"Mismatch on {c['family']} {expr}")
                    checked += 1
        self.assertGreater(checked, 60)

    def test_exact_sk_flip_isomorphism_invariance(self) -> None:
        rng = random.Random(9991)
        for c in self.practice_cases[:6] + self.official_cases[::4]:
            req = c.get("req", "-")
            if req == "-":
                expr = _rand_sexp(min(c["N"], 10), rng)
            else:
                expr = f"({req} S)" if (req.count("S") + req.count("K") + 1) <= c["N"] else req
            c_flip = runner.apply_sk_flip(c, True)
            expr_flip = runner.swap_sk_str(expr)

            r0 = self._eval_expr(c, expr, l_ref=float(c["l_ref"]))
            r1 = self._eval_expr(c_flip, expr_flip, l_ref=float(c_flip["l_ref"]))
            self.assertEqual(r0["verdict"], r1["verdict"], f"Flip mismatch on {c['name']}")
            self.assertEqual(r0["exact_steps"], r1["exact_steps"])
            self.assertAlmostEqual(r0["log2_steps"], r1["log2_steps"], places=6)
            self.assertEqual(r0["dag_nodes"], r1["dag_nodes"])
            self.assertAlmostEqual(r0["norm_score"], r1["norm_score"], places=4)

    def test_syntax_size_remnant_and_node_limit_guards(self) -> None:
        c_req = {
            "N": 8, "a_S": 3, "rhs_S": "((0 2) (1 2))", "a_K": 2, "rhs_K": "0",
            "req": "((K S) K)", "max_nodes": 10000000, "time_limit_ms": 5000,
        }
        # Illegal syntax / chars / omitted parens
        for bad in ["", "S K K", "(S)", "(S K K)", "((S K) I)", "((S K)\nK)", "S" * 4100]:
            r = self._eval_expr(c_req, bad)
            self.assertEqual(r["verdict"], "PE", f"Expected PE for {bad!r}, got {r}")

        # Exceeds leaf budget N=8
        r_big = self._eval_expr(c_req, "((((S S) S) S) (((K S) K) (S S)))")
        self.assertEqual(r_big["verdict"], "WA")

        # Missing required remnant E_req
        r_noreq = self._eval_expr(c_req, "(((S K) K) S)")
        self.assertEqual(r_noreq["verdict"], "WA")

        # Valid expression containing E_req
        r_ok = self._eval_expr(c_req, "((S ((K S) K)) S)")
        self.assertEqual(r_ok["verdict"], "AC")

        # Tight node limit -> NL
        c_nl = {
            "N": 8, "a_S": 3, "rhs_S": "((0 2) (1 2))", "a_K": 2, "rhs_K": "0",
            "req": "-", "max_nodes": 25, "time_limit_ms": 5000,
        }
        r_nl = self._eval_expr(c_nl, "((((S (S ((S S) S))) S) S) K)", vmax=25)
        self.assertEqual(r_nl["verdict"], "NL")

    def test_norm_score_and_frozen_profile(self) -> None:
        cfg = official_score.verify_frozen_profile()
        self.assertEqual(cfg["problem"], "sk_singularity")
        self.assertEqual(cfg["official_cases"], 512)
        self.assertEqual(cfg["official_families"], 8)
        self.assertEqual(cfg["cases_per_family"], 64)
        self.assertEqual(cfg["max_dag_nodes"], 10000000)
        self.assertEqual(cfg["max_stack"], 40000000)
        self.assertEqual(cfg["solver_memory_limit_mb"], 1024)
        self.assertEqual(len(self.official_cases), 32)  # static legacy Luogu snapshot retained
        self.assertEqual(len(self.practice_cases), 14)

        self.assertEqual(runner.compute_norm_score(0.0, 50.0), 0.0)
        self.assertAlmostEqual(runner.compute_norm_score(50.0, 50.0), 50.0, places=6)
        self.assertGreater(runner.compute_norm_score(200.0, 50.0), 75.0)
        self.assertLess(runner.compute_norm_score(200.0, 50.0), 100.0)

    def test_dynamic_512_seed_generator_invariants(self) -> None:
        """验证 512 种子动态生成器去重、严格排除 14 个公开练习规则、非退化残骸停机与同构不变性。"""
        samp, dyn_cases = schedule.build_official_schedule()
        self.assertEqual(samp["mode"], "os_csprng")
        self.assertEqual(len(dyn_cases), 512)
        self.assertEqual(len(set(samp["seeds"])), 512)
        self.assertTrue(all(s > 0 for s in samp["seeds"]))

        sigs = {schedule.canonical_case_signature(c) for c in dyn_cases}
        self.assertEqual(len(sigs), 512, "512 dynamic cases must have 512 distinct canonical signatures")

        # 8 families x 64 cases, evenly 16 per budget N in {14, 18, 21, 24}
        for fam in schedule.OFFICIAL_FAMILIES:
            sub = [c for c in dyn_cases if c["family"] == fam]
            self.assertEqual(len(sub), 64)
            for bud in schedule.OFFICIAL_BUDGETS:
                self.assertEqual(sum(1 for x in sub if x["N"] == bud), 16)

        # Zero overlap with 14 public practice rules (both orientations) & valid halting remnants
        checked_reqs = 0
        for c in dyn_cases:
            rk = schedule._rule_key(c["a_S"], c["rhs_S"], c["a_K"], c["rhs_K"])
            self.assertNotIn(rk, schedule.PUBLIC_RULE_KEYS, f"Dynamic case leaked public rule: {c}")
            req = c.get("req", "-")
            if c["family"] == "F1_RemnantSK":
                self.assertNotEqual(req, "-")
            if req != "-":
                self.assertNotIn(req, schedule.PUBLIC_REQ_KEYS)
                lv = req.count("S") + req.count("K")
                self.assertGreaterEqual(lv, 4)
                self.assertLessEqual(lv, min(7, c["N"] - 6))
                self.assertIn("S", req)
                self.assertIn("K", req)
                if checked_reqs < 24:
                    ev = self._eval_expr(c, req)
                    self.assertEqual(ev["verdict"], "AC", f"Generated E_req did not halt cleanly: {c}")
                    checked_reqs += 1
        self.assertGreaterEqual(checked_reqs, 24)

        # Deterministic master seed expansion & quick (32 cases) & explicit seeds
        s1, c1 = schedule.build_official_schedule(seed=20261124, quick=True)
        s2, c2 = schedule.build_official_schedule(seed=20261124, quick=True)
        self.assertEqual(len(c1), 32)
        self.assertEqual(s1["seeds"], s2["seeds"])
        self.assertEqual(c1, c2)
        s3, c3 = schedule.build_official_schedule(explicit_seeds=s1["seeds"], quick=True)
        self.assertEqual(s3["mode"], "explicit_seeds")
        self.assertEqual(c3, c1)

        # Verify S <-> K isomorphism invariance on dynamically generated cases
        rng = random.Random(4242)
        for c in c1[::4]:
            req = c.get("req", "-")
            expr = req if req != "-" else _rand_sexp(min(c["N"], 9), rng)
            cf = runner.apply_sk_flip(c, True)
            ef = runner.swap_sk_str(expr)
            r0 = self._eval_expr(c, expr, l_ref=50.0)
            r1 = self._eval_expr(cf, ef, l_ref=50.0)
            self.assertEqual(r0["verdict"], r1["verdict"])
            self.assertEqual(r0["exact_steps"], r1["exact_steps"])
            self.assertAlmostEqual(r0["log2_steps"], r1["log2_steps"], places=6)

    def test_contestant_pack_purity_and_spar_list(self) -> None:
        with tempfile.TemporaryDirectory(prefix="afb_sk_pack_") as td:
            dest = Path(td) / "sk_singularity"
            pack_contestant.export_kit(dest, root=ROOT, force=True)
            manifest = json.loads((dest / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["unified_submission"], "../submissions/sk_singularity.cpp")
            for rel, expected_sha in manifest["files"].items():
                p = dest / rel
                self.assertTrue(p.is_file(), f"Missing manifest file: {rel}")
                self.assertEqual(hashlib.sha256(p.read_bytes()).hexdigest(), expected_sha)

            # Verify hidden files are absent
            self.assertFalse((dest / "hidden").exists())
            self.assertFalse((dest / "agents" / "reference").exists())

            # Run spar.py --list inside exported contestant workspace
            proc = subprocess.run(
                [sys.executable, str(dest / "scripts" / "spar.py"), "--list"],
                cwd=dest,
                capture_output=True,
                text=True,
                encoding="utf-8",
            )
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertIn("P1_01", proc.stdout)
            self.assertIn("P7_01", proc.stdout)

    def test_luogu_checker_cpp14_compile_and_export(self) -> None:
        mock_testlib = r"""
#pragma once
#include <cstdarg>
#include <cstdio>
#include <cstdlib>
#include <fstream>
#include <sstream>
#include <string>
enum TResult { _ok = 0, _wa = 1, _pe = 2, _fail = 3 };
struct InStream {
    std::ifstream ifs;
    void open(const char* p) { ifs.open(p, std::ios::binary); }
    int readInt(int lo, int hi, const char*) { int x = 0; ifs >> x; if (x < lo || x > hi) std::exit(3); return x; }
    double readDouble(double lo, double hi, const char*) { double x = 0; ifs >> x; if (x < lo || x > hi) std::exit(3); return x; }
    void readEoln() { int c = ifs.get(); if (c == '\r') c = ifs.get(); (void)c; }
    std::string readLine() { std::string s; std::getline(ifs, s); return s; }
    bool eof() { return ifs.peek() == EOF; }
    char readChar() { return static_cast<char>(ifs.get()); }
};
static InStream inf, ouf, ans;
static inline void registerTestlibCmd(int argc, char* argv[]) {
    if (argc < 4) std::exit(3);
    inf.open(argv[1]); ouf.open(argv[2]); ans.open(argv[3]);
}
static inline void quitf(TResult r, const char* fmt, ...) {
    va_list ap; va_start(ap, fmt); std::vfprintf(stderr, fmt, ap); va_end(ap); std::fprintf(stderr, "\n");
    std::exit(r == _ok ? 0 : 1);
}
static inline void quitp(double ratio, const char* fmt, ...) {
    std::printf("POINTS %.6f ", ratio);
    va_list ap; va_start(ap, fmt); std::vprintf(fmt, ap); va_end(ap); std::printf("\n");
    std::exit(0);
}
"""
        with tempfile.TemporaryDirectory(prefix="afb_sk_lg_") as td:
            td_p = Path(td)
            (td_p / "testlib.h").write_text(mock_testlib, encoding="utf-8", newline="\n")
            chk_exe = td_p / ("checker.exe" if os.name == "nt" else "checker")
            cxx = common.find_cxx()
            comp = subprocess.run(
                [cxx, "-std=gnu++14", "-O2", "-Wall", "-Wextra", "-I", str(td_p),
                 str(ROOT / "luogu" / "checker.cpp"), "-o", str(chk_exe)],
                capture_output=True, text=True, encoding="utf-8",
            )
            self.assertEqual(comp.returncode, 0, f"Luogu checker C++14 compile failed:\n{comp.stderr}")

            # Export practice bundle and test checker on P1_01 champion
            bundle = td_p / "bundle"
            export_luogu.export_luogu_bundle(bundle, suite="practice", make_zip=False)
            out_file = td_p / "champ.out"
            out_file.write_text("((((S (S ((S S) S))) S) S) K)\n", encoding="utf-8", newline="\n")
            run = subprocess.run(
                [str(chk_exe), str(bundle / "data" / "sk_01.in"), str(out_file), str(bundle / "data" / "sk_01.ans")],
                capture_output=True, text=True, encoding="utf-8",
            )
            self.assertEqual(run.returncode, 0, run.stderr)
            self.assertIn("T=1958", run.stdout)

    def test_baselines_end_to_end_smoke(self) -> None:
        sample = [self.practice_cases[0], self.practice_cases[5], self.practice_cases[7]]
        with tempfile.TemporaryDirectory(prefix="afb_sk_smoke_") as td:
            c_st = runner.compile_submission(str(ROOT / "agents" / "starter.cpp"), os.path.join(td, "st"), str(ROOT))
            self.assertTrue(c_st.ok, c_st.log)
            c_bm = runner.compile_submission(str(ROOT / "agents" / "baselines" / "beam_evo.cpp"), os.path.join(td, "bm"), str(ROOT))
            self.assertTrue(c_bm.ok, c_bm.log)

            r_st = runner.evaluate_cases_parallel(self.judge_exe, c_st.exe_path, sample, workers=3, solver_tl_ms=2000)
            r_bm = runner.evaluate_cases_parallel(self.judge_exe, c_bm.exe_path, sample, workers=3, solver_tl_ms=2000)
            agg_st = runner.aggregate_cases(r_st)
            agg_bm = runner.aggregate_cases(r_bm)
            self.assertEqual(agg_st["ac_count"], len(sample))
            self.assertEqual(agg_bm["ac_count"], len(sample))
            self.assertGreater(agg_bm["score"], agg_st["score"])

    def test_stepcount_promote_rounding_and_deep_probe_spine(self) -> None:
        """验证 StepCount::promote 不与 EXACT_EXP 哨兵冲突、10.000e+ 进位、24-arg 探针脊不越界、深树步进不爆栈。"""
        harness = r'''
#include "sk_core.hpp"
#include <cassert>
#include <cmath>
#include <iostream>
#include <string>

using namespace sk_singularity;

int main() {
    // 1. Promote mant = 2^61 (shift=1 => exp2=-1): must NOT be treated as exact!
    StepCount a = {1ULL << 61, StepCount::EXACT_EXP};
    StepCount b = {1ULL << 62, 0};
    StepCount c = StepCount::add(a, b);
    assert(!c.is_exact());
    double expected_l2 = std::log2(static_cast<double>(1ULL << 61) + static_cast<double>(1ULL << 62) + 1.0);
    assert(std::fabs(c.log2_plus1() - expected_l2) < 1e-6);

    StepCount p = a;
    p.promote();
    assert(!p.is_exact());
    assert(p.exp2 == -1);
    assert(p.mant == (1ULL << 62));

    // 2. Rounding boundary near 10^19 (m10 >= 9.9995 must normalize to 1.000e+...)
    // Find a StepCount whose m10 is >= 9.9995: 10^20 - 1 => log2 ~ 66.4385618977
    // Let's test values just below powers of 10 across a sweep
    for (int k = 19; k <= 30; ++k) {
        double l2_target = k / 0.30102999566398119521 - 1e-5;
        int64_t e = static_cast<int64_t>(std::floor(l2_target)) - 62;
        double frac = std::pow(2.0, l2_target - static_cast<double>(e + 62));
        uint64_t m = static_cast<uint64_t>(frac * static_cast<double>(1ULL << 62));
        if (m >= (1ULL << 63)) { m >>= 1; e++; }
        if (m < (1ULL << 62)) m = (1ULL << 62);
        StepCount sc{m, e};
        std::string ds = sc.display_string();
        assert(ds.rfind("10.000e+", 0) != 0);
    }

    // 3. MemoEngine with num_probes=1 and 24-argument spine (previously smashed int args[16])
    RuleSpec rule;
    rule.arity[0] = 3; rule.arity[1] = 2;
    std::string err;
    assert(parse_rhs_strict("((0 2) (1 2))", 3, rule.rhs[0], rule.root[0], err));
    assert(parse_rhs_strict("0", 2, rule.rhs[1], rule.root[1], err));
    MemoEngine eng(rule, 10000, 1);
    int cur = 2; // probe atom v0
    for (int i = 0; i < 24; ++i) cur = eng.app(cur, (i & 1) ? 1 : 0);
    EvalResult er = eng.eval_nf(cur);
    assert(er.status == EvalStatus::HALTED);
    assert(er.steps.is_zero());

    // 4. BruteTracer::step_one deep-tree guard (> 1024 depth returns -1 safely)
    BruteTracer bt(rule, 20000);
    int deep = 0; // S
    for (int i = 0; i < 1100; ++i) deep = bt.mk_app(deep, 1); // (deep K)
    std::string path;
    int comb = -1;
    int next_u = bt.step_one(deep, path, comb);
    assert(next_u == -1);
    return 0;
}
'''
        with tempfile.TemporaryDirectory(prefix="afb_sk_core_") as td:
            src = Path(td) / "core_test.cpp"
            src.write_text(harness, encoding="utf-8", newline="\n")
            comp = common.compile_cpp(
                [str(src)],
                os.path.join(td, "core_test"),
                include_dirs=[str(ROOT / "engine")],
                use_sandbox_wrap=False,
            )
            self.assertTrue(comp.ok, comp.log)
            proc = subprocess.run([comp.exe_path], capture_output=True, text=True, timeout=15)
            self.assertEqual(proc.returncode, 0, proc.stderr)

    def test_luogu_checker_malformed_rhs_guard(self) -> None:
        """验证洛谷检查器遇到非法 RHS (9 0) 时优雅 _fail 退出而不越界崩溃。"""
        mock_testlib = r"""
#pragma once
#include <cstdarg>
#include <cstdio>
#include <cstdlib>
#include <fstream>
#include <string>
enum TResult { _ok = 0, _wa = 1, _pe = 2, _fail = 3 };
struct InStream {
    std::ifstream ifs;
    void open(const char* p) { ifs.open(p, std::ios::binary); }
    int readInt(int lo, int hi, const char*) { int x = 0; ifs >> x; if (x < lo || x > hi) std::exit(3); return x; }
    double readDouble(double lo, double hi, const char*) { double x = 0; ifs >> x; if (x < lo || x > hi) std::exit(3); return x; }
    void readEoln() { int c = ifs.get(); if (c == '\r') c = ifs.get(); (void)c; }
    std::string readLine() { std::string s; std::getline(ifs, s); return s; }
    bool eof() { return ifs.peek() == EOF; }
    char readChar() { return static_cast<char>(ifs.get()); }
};
static InStream inf, ouf, ans;
static inline void registerTestlibCmd(int argc, char* argv[]) {
    if (argc < 4) std::exit(3);
    inf.open(argv[1]); ouf.open(argv[2]); ans.open(argv[3]);
}
static inline void quitf(TResult r, const char* fmt, ...) {
    va_list ap; va_start(ap, fmt); std::vfprintf(stderr, fmt, ap); va_end(ap); std::fprintf(stderr, "\n");
    std::exit(int(r));
}
static inline void quitp(double, const char*, ...) { std::exit(0); }
"""
        with tempfile.TemporaryDirectory(prefix="afb_sk_lgbad_") as td:
            td_p = Path(td)
            (td_p / "testlib.h").write_text(mock_testlib, encoding="utf-8", newline="\n")
            chk_exe = td_p / ("checker.exe" if os.name == "nt" else "checker")
            comp = subprocess.run(
                [common.find_cxx(), "-std=c++14", "-fno-asm", "-O2", "-Wall", "-Wextra",
                 "-I", str(td_p), str(ROOT / "luogu" / "checker.cpp"), "-o", str(chk_exe)],
                capture_output=True, text=True, encoding="utf-8",
            )
            self.assertEqual(comp.returncode, 0, comp.stderr)
            (td_p / "bad.in").write_text("8 3 2 1000 5000\n(9 0)\n0\n-\n", encoding="utf-8", newline="\n")
            (td_p / "bad.out").write_text("S\n", encoding="utf-8", newline="\n")
            (td_p / "bad.ans").write_text("10.0\n", encoding="utf-8", newline="\n")
            run = subprocess.run(
                [str(chk_exe), str(td_p / "bad.in"), str(td_p / "bad.out"), str(td_p / "bad.ans")],
                capture_output=True, text=True, encoding="utf-8", timeout=15,
            )
            self.assertEqual(run.returncode, 3, f"Expected _fail (3), got rc={run.returncode} stderr={run.stderr}")

    def test_score_leaderboard_guard_and_sandbox_defense(self) -> None:
        """验证 score.py 非正式参数禁止覆盖官方榜、编译/运行沙盒拦截隐藏集泄露、多线程 TLE 与超内存 MLE。"""
        org_dir = ROOT.parent
        sys.path.insert(0, str(org_dir))
        import submission_sandbox  # noqa: E402

        with tempfile.TemporaryDirectory(prefix="afb_sk_sbx_") as td:
            td_p = Path(td)
            # 1. --seed 固定种子属于非正式评测，试图写入 results/leaderboard.json 必须被拒绝
            lb_path = ROOT / "results" / "leaderboard.json"
            p_guard = subprocess.run(
                [sys.executable, str(ROOT / "scripts" / "score.py"),
                 str(ROOT / "agents" / "starter.cpp"),
                 "--seed", "12345", "--families", "F1_RemnantSK", "--cases-per-family", "2",
                 "--out", str(lb_path)],
                capture_output=True, text=True, encoding="utf-8", timeout=30,
            )
            self.assertNotEqual(p_guard.returncode, 0, "Non-official run must not overwrite leaderboard.json")

            # 2. 编译期 #include 隐藏 official_suite.json 在沙盒下必须 CE；运行期读取隐藏文件必须失败
            hidden_path = str((ROOT / "hidden" / "official_suite.json").resolve()).replace("\\", "\\\\")
            evil_rt = td_p / "evil_rt.cpp"
            evil_rt.write_text(
                f'''
#include <fstream>
#include <iostream>
int main() {{
    std::ifstream f("{hidden_path}");
    if (f.good()) std::cerr << "LEAKED_OFFICIAL\\n";
    std::cout << "S\\n";
    return 0;
}}
''',
                encoding="utf-8", newline="\n",
            )
            out_json = td_p / "evil_rt.json"
            p_rt = subprocess.run(
                [sys.executable, str(ROOT / "scripts" / "score.py"), str(evil_rt),
                 "--seed", "20261123", "--families", "F1_RemnantSK", "--cases-per-family", "2",
                 "--ref-tl", "300", "--jobs", "2", "--out", str(out_json)],
                capture_output=True, text=True, encoding="utf-8", timeout=90,
            )
            self.assertEqual(p_rt.returncode, 0, p_rt.stderr)
            payload = json.loads(out_json.read_text(encoding="utf-8"))
            self.assertEqual(payload["scoring_version"], "sk_singularity_v2")
            self.assertFalse(payload["official"])
            self.assertEqual(len(payload["leaderboard"][0]["cases"]), 2)
            for cs in payload["leaderboard"][0]["cases"]:
                self.assertNotIn("LEAKED_OFFICIAL", cs.get("stderr", ""), "Sandbox leaked hidden official_suite.json!")

            # 3. 多线程烧 CPU -> TLE；分配 560 MiB -> MLE
            code_mt = r'''
#include <atomic>
#include <iostream>
#include <thread>
#include <vector>
int main() {
    std::atomic<uint64_t> sink{0};
    std::vector<std::thread> ts;
    for (int i = 0; i < 4; ++i) {
        ts.emplace_back([&]() {
            uint64_t x = 1;
            for (int k = 0; k < 400000000; ++k) x = x * 6364136223846793005ULL + 1;
            sink.fetch_add(x);
        });
    }
    for (auto& t : ts) t.join();
    std::cout << (sink.load() ? "S\n" : "K\n");
    return 0;
}
'''
            code_mle = r'''
#include <cstring>
#include <iostream>
#include <vector>
int main() {
    std::vector<char*> blocks;
    for (int i = 0; i < 560; ++i) {
        char* p = new char[1024 * 1024];
        std::memset(p, i & 255, 1024 * 1024);
        blocks.push_back(p);
    }
    std::cout << "S\n";
    return 0;
}
'''
            old_wrap = os.environ.get("AVL_SANDBOX_WRAP")
            old_mem = os.environ.get("AVL_SANDBOX_MEM_MB")
            try:
                if submission_sandbox.official_ready():
                    submission_sandbox.enable_cpp()
                    os.environ["AVL_SANDBOX_MEM_MB"] = "512"
                mt_src = td_p / "mt_tle.cpp"
                mt_src.write_text(code_mt, encoding="utf-8", newline="\n")
                c_mt = common.compile_cpp([str(mt_src)], str(td_p / "mt_tle"), use_sandbox_wrap=True)
                self.assertTrue(c_mt.ok, c_mt.log)
                r_mt = runner.evaluate_case(
                    self.judge_exe, c_mt.exe_path, self.practice_cases[0],
                    solver_tl_ms=600, memory_limit_mb=512,
                )
                self.assertEqual(r_mt["verdict"], "TLE", f"Expected TLE for 4-thread burn, got {r_mt}")

                mle_src = td_p / "oom_mle.cpp"
                mle_src.write_text(code_mle, encoding="utf-8", newline="\n")
                c_mle = common.compile_cpp([str(mle_src)], str(td_p / "oom_mle"), use_sandbox_wrap=True)
                self.assertTrue(c_mle.ok, c_mle.log)
                r_mle = runner.evaluate_case(
                    self.judge_exe, c_mle.exe_path, self.practice_cases[0],
                    solver_tl_ms=5000, memory_limit_mb=512,
                )
                self.assertEqual(r_mle["verdict"], "MLE", f"Expected MLE for 560 MiB alloc, got {r_mle}")
            finally:
                if old_wrap is None:
                    os.environ.pop("AVL_SANDBOX_WRAP", None)
                else:
                    os.environ["AVL_SANDBOX_WRAP"] = old_wrap
                if old_mem is None:
                    os.environ.pop("AVL_SANDBOX_MEM_MB", None)
                else:
                    os.environ["AVL_SANDBOX_MEM_MB"] = old_mem

    def test_batch_judge_and_adaptive_workers(self) -> None:
        """验证常驻 --batch 裁判模式与单次 CLI 零漂移一致、宽哈希表回退、以及自适应并发计算。"""
        # 1. Batch vs oneshot equivalence across AC + trace + DIV + PE + wide HT (> 2^21 nodes)
        std_case = self.practice_cases[0]
        stdin_std = runner.format_case_input(std_case)
        champ = b"((((S (S ((S S) S))) S) S) K)\n"
        r_batch = common.run_judge_cli(self.judge_exe, stdin_std, champ, l_ref=8.0, trace_steps=12)
        r_one = common._run_judge_cli_oneshot(self.judge_exe, stdin_std, champ, l_ref=8.0, trace_steps=12)
        for k in (
            "verdict", "halted", "leaves", "expr_canonical", "is_exact", "exact_steps",
            "steps_display", "log2_steps", "norm_score", "singularity", "dag_nodes",
            "redex_fires", "whnf_hits", "nf_hits", "peak_stack", "nf_preview",
            "trace", "dag_growth", "hot_dag_nodes",
        ):
            self.assertEqual(r_batch[k], r_one[k], f"Batch/oneshot mismatch on key {k}")

        # Default wide-HT path (V_max = 10,000,000 > 2^21 - 1), switching down to packed HT,
        # triggering post-eval oversized-buffer trim (> 262k nodes), and reusing the same worker cleanly
        packed_case = dict(std_case)
        packed_case["max_nodes"] = 500000
        r_packed = common.run_judge_cli(self.judge_exe, runner.format_case_input(packed_case), champ, l_ref=8.0)
        self.assertEqual(r_packed["verdict"], "AC")
        self.assertEqual(r_packed["exact_steps"], 1958)

        grow_case = {
            "N": 8, "a_S": 1, "rhs_S": "(S (K 0))", "a_K": 2, "rhs_K": "0",
            "req": "-", "max_nodes": 300000, "time_limit_ms": 5000,
        }
        r_grow = common.run_judge_cli(self.judge_exe, runner.format_case_input(grow_case), b"(S K)\n", l_ref=10.0)
        self.assertEqual(r_grow["verdict"], "NL")
        self.assertEqual(r_grow["dag_nodes"], 300000)

        wide_case = dict(std_case)
        wide_case["max_nodes"] = 10000000
        r_wide = common.run_judge_cli(self.judge_exe, runner.format_case_input(wide_case), champ, l_ref=8.0)
        self.assertEqual(r_wide["verdict"], "AC")
        self.assertEqual(r_wide["exact_steps"], 1958)

        r_back = common.run_judge_cli(self.judge_exe, stdin_std, champ, l_ref=8.0)
        self.assertEqual(r_back["verdict"], "AC")
        self.assertEqual(r_back["exact_steps"], 1958)

        # 2. Adaptive parallel worker detection
        auto_32 = runner.detect_parallel_workers(None, 32)
        self.assertEqual(auto_32["mode"], "auto")
        self.assertGreaterEqual(auto_32["workers"], 1)
        self.assertLessEqual(auto_32["workers"], min(24, auto_32["logical_cpus"], 32))
        self.assertEqual(auto_32["workers"], min(auto_32["safe_cap"], 32))

        auto_small = runner.detect_parallel_workers(None, 2)
        self.assertLessEqual(auto_small["workers"], 2)

        manual_ok = runner.detect_parallel_workers(3, 32)
        self.assertEqual(manual_ok["mode"], "manual")
        self.assertEqual(manual_ok["workers"], min(3, manual_ok["logical_cpus"]))

        with self.assertRaises(ValueError):
            runner.detect_parallel_workers(0, 32)

        # Cleanly close idle workers and verify subsequent call transparently respawns
        common.shutdown_judge_pools()
        r_respawn = common.run_judge_cli(self.judge_exe, stdin_std, champ, l_ref=8.0)
        self.assertEqual(r_respawn["exact_steps"], 1958)


if __name__ == "__main__":
    unittest.main()
