# -*- coding: utf-8 -*-
"""《天翼幽灵兔：四重奇点追截战》（ghost_rabbit）全套单元、交互、对抗一致性与防御测试。"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import zipfile

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "engine"))
sys.path.insert(0, str(ROOT / "hidden"))
sys.path.insert(0, str(ROOT / "scripts"))

import common  # noqa: E402
import pack_contestant  # noqa: E402
import runner  # noqa: E402
import schedule  # noqa: E402


def _delta3(a: int, b: int) -> int:
    return (a + b) % 3


def _to_c3(a: int, b: int) -> tuple[int, int, int]:
    d = _delta3(a, b)
    return ((2 * a - b + d) // 3, (2 * b - a + d) // 3, (-a - b + d) // 3)


def _hex_dist(u: tuple[int, int], v: tuple[int, int]) -> int:
    cu = _to_c3(*u)
    cv = _to_c3(*v)
    return abs(cu[0] - cv[0]) + abs(cu[1] - cv[1]) + abs(cu[2] - cv[2])


def _is_prime_py(n: int) -> bool:
    if n < 2:
        return False
    if n % 2 == 0:
        return n == 2
    d = 3
    while d * d <= n:
        if n % d == 0:
            return False
        d += 2
    return True


def _is_ep_py(a: int, b: int) -> bool:
    if _delta3(a, b) == 0:
        return False
    if a == 0 or b == 0 or a == b:
        r = max(abs(a), abs(b))
        return r % 3 == 2 and _is_prime_py(r)
    norm = a * a - a * b + b * b
    return norm % 3 == 1 and _is_prime_py(norm)


def _legal_moves_py(u: tuple[int, int], traps: set[tuple[int, int]]) -> list[tuple[int, int]]:
    a, b = u
    dirs = [(1, 0), (0, 1), (-1, -1)] if _delta3(a, b) == 1 else [(-1, 0), (0, -1), (1, 1)]
    out = [(a + da, b + db) for da, db in dirs if (a + da, b + db) not in traps]
    if _is_ep_py(a, b):
        for da, db in ((1, -1), (-1, 1), (2, 1), (-2, -1), (1, 2), (-1, -2)):
            v = (a + da, b + db)
            if v not in traps:
                out.append(v)
    return out


class TestGhostRabbit(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.practice_exe = runner.ensure_practice_interactor(ROOT)
        cls.official_exe = schedule.ensure_official_interactor(ROOT)

    def _compile_snippet(self, tmp: str, name: str, code: str) -> common.CompileResult:
        src = Path(tmp) / f"{name}.cpp"
        src.write_text(code, encoding="utf-8", newline="\n")
        return common.compile_cpp([str(src)], os.path.join(tmp, name), use_sandbox_wrap=False)

    def test_01_judge_core_math_and_3ap_invariants(self) -> None:
        """核验 O(1) 三维嵌入距离、艾森斯坦素数表、Local 3-AP-Free 等价性与双轨计分公式。"""
        harness = r'''
#include "judge_core.hpp"
#include <cassert>
#include <cmath>
#include <queue>

using namespace ghost_rabbit;

static int bfs_dist(Pt s, Pt t) {
    if (s == t) return 0;
    std::unordered_map<uint64_t, int> d;
    std::queue<Pt> q;
    d[pack_pt(s)] = 0;
    q.push(s);
    while (!q.empty()) {
        Pt u = q.front(); q.pop();
        int du = d[pack_pt(u)];
        for (Pt v : get_n1(u)) {
            uint64_t kv = pack_pt(v);
            if (!d.count(kv)) {
                if (v == t) return du + 1;
                d[kv] = du + 1;
                q.push(v);
            }
        }
    }
    return -1;
}

static bool brute_prime(uint64_t n) {
    if (n < 2) return false;
    for (uint64_t i = 2; i * i <= n; ++i) if (n % i == 0) return false;
    return true;
}

int main() {
    // 1. to_c3 / from_c3 / hex_dist vs BFS
    std::vector<Pt> pts;
    for (int a = -7; a <= 7; ++a)
        for (int b = -7; b <= 7; ++b)
            if (valid_grid({a, b})) {
                C3 c = to_c3({a, b});
                assert(c.c1 + c.c2 + c.c3 == delta3(a, b));
                Pt back = from_c3(c.c1, c.c2, c.c3);
                assert(back.a == a && back.b == b);
                pts.push_back({a, b});
            }
    for (size_t i = 0; i < pts.size(); i += 5) {
        for (size_t j = 0; j < pts.size(); j += 7) {
            assert(hex_dist(pts[i], pts[j]) == bfs_dist(pts[i], pts[j]));
        }
    }

    // 2. Eisenstein primes (small + > 2e6 Miller-Rabin)
    assert(!is_eisenstein_prime({1, 0}));
    assert(is_eisenstein_prime({2, 0}));
    assert(is_eisenstein_prime({2, 2}));
    assert(is_eisenstein_prime({0, 2}));
    assert(is_eisenstein_prime({3, 1}));   // norm = 7
    assert(!is_eisenstein_prime({4, 0}));  // r = 4 composite
    assert(!is_eisenstein_prime({3, 0}));  // not in H_omega
    for (int a = -20; a <= 20; ++a) {
        for (int b = -20; b <= 20; ++b) {
            if (!valid_grid({a, b})) continue;
            bool expect = false;
            if (a == 0 || b == 0 || a == b) {
                int r = std::max(std::abs(a), std::abs(b));
                expect = (r % 3 == 2) && brute_prime(uint64_t(r));
            } else {
                uint64_t norm = uint64_t(1LL * a * a - 1LL * a * b + 1LL * b * b);
                expect = (norm % 3 == 1) && brute_prime(norm);
            }
            assert(is_eisenstein_prime({a, b}) == expect);
        }
    }
    // Large splitting prime: (2001, 1) -> norm = 4004001 - 2001 + 1 = 4002001? Test (1600, 1): 2558401
    for (int a = 1500; a <= 1560; ++a) {
        Pt p{a, 1};
        if (!valid_grid(p)) continue;
        uint64_t norm = uint64_t(1LL * a * a - a + 1);
        assert(is_eisenstein_prime(p) == ((norm % 3 == 1) && brute_prime(norm)));
    }

    // 3. Local 3-AP-Free equivalence with brute-force (u+w=2v && dist(u,w)<=6)
    TrapSet ts;
    std::string err;
    // Straight wall along d=(1,-1): (1,0), (2,-1), (3,-2) must fail on 3rd!
    assert(ts.validate_round_public({{1, 0}, {2, -1}}, err));
    ts.add({1, 0}); ts.add({2, -1});
    assert(!ts.validate_round_public({{3, -2}}, err));
    assert(!ts.validate_round_public({{0, 1}}, err));
    // Two traps in same round forming 3-AP with existing (1,0) along d=(2,1): (3,1) and (5,2)
    assert(!ts.validate_round_public({{3, 1}, {5, 2}}, err));
    // Staggered neighbor (1,1) does NOT form 3-AP with (1,0),(2,-1)
    assert(ts.validate_round_public({{1, 1}}, err));
    // Verify over 80 valid grid points in [-6, 6]^2 that violates_local_3ap <=> exists u,v,w in V(H_omega) with u+w=2v and hex_dist(u,w)<=6
    TrapSet ts2;
    std::vector<Pt> pool;
    for (int a = -6; a <= 6; ++a)
        for (int b = -6; b <= 6; ++b)
            if (valid_grid({a, b})) pool.push_back({a, b});
    // Deterministic shuffle
    for (size_t i = 0; i < pool.size(); ++i) {
        size_t j = (i * 37 + 13) % pool.size();
        std::swap(pool[i], pool[j]);
    }
    for (Pt p : pool) {
        bool fast_bad = ts2.violates_local_3ap(p, false, {0,0});
        std::vector<Pt> all = ts2.pts;
        all.push_back(p);
        bool brute_bad = false;
        for (size_t i = 0; i < all.size() && !brute_bad; ++i)
            for (size_t j = i + 1; j < all.size() && !brute_bad; ++j)
                for (size_t k = 0; k < all.size(); ++k) {
                    if (k == i || k == j) continue;
                    if (all[i].a + all[j].a == 2 * all[k].a && all[i].b + all[j].b == 2 * all[k].b
                        && hex_dist(all[i], all[j]) <= 6) {
                        brute_bad = true;
                        break;
                    }
                }
        assert(fast_bad == brute_bad);
        if (!fast_bad) ts2.add(p);
    }

    // 4. Dual-track scoring exact values
    auto s200 = compute_score(2, 1, true);
    assert(s200.raw_score == 0.0 && s200.norm_score == 0.0 && !s200.singularity);
    auto s175 = compute_score(7, 4, true);
    assert(std::fabs(s175.raw_score - 20.0) < 1e-9 && std::fabs(s175.norm_score - 100.0/11.0) < 1e-9);
    auto s150 = compute_score(3, 2, true);
    assert(std::fabs(s150.raw_score - 50.0) < 1e-9 && std::fabs(s150.norm_score - 20.0) < 1e-9);
    auto s125 = compute_score(5, 4, true);
    assert(std::fabs(s125.raw_score - 100.0) < 1e-9 && std::fabs(s125.norm_score - 100.0/3.0) < 1e-9);
    auto s100 = compute_score(1, 1, true);
    assert(std::fabs(s100.raw_score - 200.0) < 1e-9 && std::fabs(s100.norm_score - 50.0) < 1e-9);
    auto s060 = compute_score(3, 5, true);
    assert(std::fabs(s060.raw_score - 1400.0) < 1e-9 && std::fabs(s060.norm_score - 87.5) < 1e-9);
    auto s050 = compute_score(1, 2, true);
    assert(s050.singularity && s050.norm_score == 100.0);
    auto s040 = compute_score(2, 5, true);
    assert(s040.singularity && s040.norm_score == 100.0);
    return 0;
}
'''
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "math_test.cpp"
            src.write_text(harness, encoding="utf-8", newline="\n")
            comp = common.compile_cpp(
                [str(src)],
                os.path.join(tmp, "math_test"),
                include_dirs=[str(ROOT / "engine")],
                use_sandbox_wrap=False,
            )
            self.assertTrue(comp.ok, comp.log)
            proc = subprocess.run([comp.exe_path], capture_output=True, text=True, timeout=15)
            self.assertEqual(proc.returncode, 0, proc.stderr)

    def test_02_readme_four_turn_sample(self) -> None:
        """精确复现题面中的 4 回合 q=3/2 样例交互（适配任意 r_1=(1,1) 或 r_1=(0,-1) 分支）。"""
        sample_solver = r'''
#include <iostream>
int main() {
    std::cout << "3 2\n" << std::flush;
    int st, d, fl, bn;
    // Turn 1: block (2,0), far-field probe (0, 100000)
    std::cout << "1 2 0 0 100000\n" << std::flush;
    if (!(std::cin >> st >> d >> fl >> bn) || st == 1) return 0;
    if (d == 133333) {
        // r_1 = (1, 1)
        std::cout << "2 2 2 0 1 1 0\n" << std::flush;
        if (!(std::cin >> st >> d >> fl >> bn) || st == 1) return 0;
        std::cout << "1 0 -1 1 1\n" << std::flush;
        if (!(std::cin >> st >> d >> fl >> bn) || st == 1) return 0;
        std::cout << "1 1 0 1 0\n" << std::flush;
        std::cin >> st >> d >> fl >> bn;
    } else {
        // r_1 = (0, -1)
        std::cout << "2 0 -2 -1 -1 1 0\n" << std::flush;
        if (!(std::cin >> st >> d >> fl >> bn) || st == 1) return 0;
        std::cout << "1 1 1 0 -1\n" << std::flush;
        if (!(std::cin >> st >> d >> fl >> bn) || st == 1) return 0;
        std::cout << "1 1 0 0 -1\n" << std::flush;
        std::cin >> st >> d >> fl >> bn;
    }
    return 0;
}
'''
        with tempfile.TemporaryDirectory() as tmp:
            comp = self._compile_snippet(tmp, "sample4", sample_solver)
            self.assertTrue(comp.ok, comp.log)
            for strat in ("P1_BasicEscape", "P2_PrimeGreedy", "P3_LiteBelief"):
                rr = common.run_interactive_match(self.practice_exe, comp.exe_path, strategy=strat, seed=20261101)
                self.assertEqual(rr.verdict, "AC", rr.message)
                self.assertEqual(rr.turns, 4)
                self.assertEqual(rr.traps_placed, 5)
                self.assertAlmostEqual(rr.raw_score, 50.0, places=4)
                self.assertAlmostEqual(rr.norm_score, 20.0, places=3)

    def test_03_quantum_witness_trajectory_consistency(self) -> None:
        """验证 F3_QuantumAdv 无论捕获还是多轮对抗，回放导出的实体见证轨迹与全程所有观测 100% 严格自洽。"""
        with tempfile.TemporaryDirectory() as tmp:
            for src_path, max_t in (
                (ROOT / "agents" / "baselines" / "blitz_150.cpp", 20),
                (ROOT / "agents" / "baselines" / "staggered_125.cpp", 120),
            ):
                comp = runner.compile_submission(str(src_path), os.path.join(tmp, src_path.stem))
                self.assertTrue(comp.ok, comp.log)
                rp = os.path.join(tmp, f"rep_{src_path.stem}.json")
                rr = common.run_interactive_match(
                    self.official_exe,
                    comp.exe_path,
                    strategy="F3_QuantumAdv",
                    seed=998244353,
                    max_turns=max_t,
                    replay_path=rp,
                )
                self.assertIn(rr.verdict, ("AC", "WA"), rr.message)
                data = json.loads(Path(rp).read_text(encoding="utf-8"))
                wpath = [tuple(p) for p in data["witness_path"]]
                frames = data["frames"]
                self.assertGreater(len(frames), 0)
                self.assertEqual(wpath[0], (1, 0))

                traps: set[tuple[int, int]] = set()
                for idx, fr in enumerate(frames):
                    r0 = tuple(fr["r0"])
                    r1 = tuple(fr["r1"])
                    self.assertEqual(r0, wpath[idx])
                    self.assertNotIn(r0, traps)
                    att = [tuple(p) for p in fr["att"]]
                    land = [tuple(p) for p in fr["land"]]
                    bn = int(fr["bn"])
                    fl = int(fr["fl"])
                    st = int(fr["st"])
                    sonar = tuple(fr["sonar"])
                    dist = int(fr["dist"])

                    # 核验幽灵弹开与落地陷阱
                    exp_bn = 0
                    exp_land = []
                    for j, p in enumerate(att):
                        if p == r0:
                            exp_bn = j + 1
                        else:
                            exp_land.append(p)
                    self.assertEqual(bn, exp_bn)
                    self.assertEqual(land, exp_land)
                    for p in exp_land:
                        traps.add(p)

                    # 核验素数闪光
                    self.assertEqual(fl, 1 if _is_ep_py(*r0) else 0)

                    # 核验合法移动与声呐测距
                    legal = _legal_moves_py(r0, traps)
                    if st == 1:
                        self.assertEqual(len(legal), 0, f"Expected capture on turn {idx+1}, legal={legal}")
                    else:
                        self.assertIn(r1, legal, f"Witness move {r0}->{r1} illegal on turn {idx+1}")
                        self.assertEqual(_hex_dist(sonar, r1), dist)
                        self.assertEqual(r1, wpath[idx + 1])

    def test_04_baselines_and_dual_track_aggregation(self) -> None:
        """核验公开模板/基线成绩阶梯及双轨计分汇总语义。"""
        with tempfile.TemporaryDirectory() as tmp:
            for src_rel, exp_raw, exp_norm in (
                ("agents/starter.cpp", 20.0, 9.09),
                ("agents/baselines/blitz_150.cpp", 50.0, 20.0),
            ):
                comp = runner.compile_submission(str(ROOT / src_rel), os.path.join(tmp, Path(src_rel).stem))
                self.assertTrue(comp.ok, comp.log)
                ocases = [
                    {"name": n, "family": f, "strategy": f, "seed": 42000 + i}
                    for i, (n, f) in enumerate(schedule.QUICK_SUITE_SPEC)
                ]
                rows = runner.evaluate_cases_parallel(self.official_exe, comp.exe_path, ocases, workers=4)
                agg = runner.aggregate_cases(rows)
                self.assertEqual(agg["ac_count"], len(ocases))
                self.assertEqual(agg["status"], "AC")
                self.assertAlmostEqual(agg["raw_score"], exp_raw, places=2)
                self.assertAlmostEqual(agg["score"], exp_norm, places=2)

            # 双轨汇总边缘测试：部分通过、奇点全通
            partial = runner.aggregate_cases([
                {"family": "A", "verdict": "AC", "raw_score": 100.0, "norm_score": 50.0, "singularity": False, "turns": 10, "traps_placed": 10},
                {"family": "B", "verdict": "WA", "raw_score": 0.0, "norm_score": 0.0, "singularity": False, "turns": 5000, "traps_placed": 100},
            ])
            self.assertEqual(partial["status"], "PC")
            self.assertEqual(partial["score"], 25.0)
            self.assertEqual(partial["raw_score"], 0.0)
            self.assertFalse(partial["singularity"])

            sing = runner.aggregate_cases([
                {"family": "A", "verdict": "AC", "raw_score": 0.0, "norm_score": 100.0, "singularity": True, "turns": 20, "traps_placed": 10},
                {"family": "B", "verdict": "AC", "raw_score": 0.0, "norm_score": 100.0, "singularity": True, "turns": 30, "traps_placed": 15},
            ])
            self.assertEqual(sing["status"], "AC")
            self.assertEqual(sing["score"], 100.0)
            self.assertTrue(sing["singularity"])

    def test_05_bad_submissions_defense(self) -> None:
        """验证各类违规/异常提交（CE/WA/PE/OLE/RE/TLE 以及公开校验先于幽灵弹开）均被精准拦截。"""
        with tempfile.TemporaryDirectory() as tmp:
            # 1. CE
            ce = self._compile_snippet(tmp, "bad_ce", "int main( { return 0; }")
            self.assertFalse(ce.ok)

            # 2. Bad rate (P > 2Q)
            c = self._compile_snippet(tmp, "bad_rate", '#include <iostream>\nint main(){std::cout<<"5 2\\n"<<std::flush;}\n')
            rr = common.run_interactive_match(self.practice_exe, c.exe_path, strategy="P1_BasicEscape", seed=1)
            self.assertEqual(rr.verdict, "WA")

            # 3. Insufficient tokens (declare 1/2, try k=1 on Turn 1)
            c = self._compile_snippet(tmp, "bad_tok", '#include <iostream>\nint main(){std::cout<<"1 2\\n1 2 0 1 0\\n"<<std::flush;}\n')
            rr = common.run_interactive_match(self.practice_exe, c.exe_path, strategy="P1_BasicEscape", seed=1)
            self.assertEqual(rr.verdict, "WA")

            # 4. Not in H_omega ((0, 0) has a+b=0 mod 3)
            c = self._compile_snippet(tmp, "bad_grid", '#include <iostream>\nint main(){std::cout<<"3 2\\n1 0 0 1 0\\n"<<std::flush;}\n')
            rr = common.run_interactive_match(self.practice_exe, c.exe_path, strategy="P1_BasicEscape", seed=1)
            self.assertEqual(rr.verdict, "WA")

            # 5. Public-first Local 3-AP-Free BEFORE ghost bounce:
            # Turn 1 places (2,-1) and (3,-2) with P=2,Q=1; Wait: r_0=(1,0) might move.
            # Instead: place (-1, 2) and (3, -2) -> no, place (2,-1) and (3,-2) in Turn 1 (2 traps),
            # then in Turn 2 try (1,0) which forms 3-AP with (2,-1),(3,-2) along d=(1,-1) — MUST be WA!
            # Even sharper: Turn 1 (P=2,Q=1) place (2,-1) and (0,1), then even if (1,0) were r_0,
            # Turn 1 tries placing (2,-1) and (0,1) — wait, test directly:
            # Turn 1: P=2 Q=1, place 2 traps: (0,1) and (2,-1) [legal!], rabbit moves.
            # Turn 2: try placing (1,0) -> (0,1), (1,0), (2,-1) is 3-AP -> WA!
            # Even better: Turn 1 tries 2 traps (2,-1) & (3,-2), Turn 2 tries (1,0) -> WA!
            code_3ap = r'''
#include <iostream>
int main() {
    std::cout << "2 1\n" << std::flush;
    std::cout << "2 2 -1 3 -2 1 0\n" << std::flush;
    int s,d,f,b;
    if (std::cin >> s >> d >> f >> b && s == 0) {
        std::cout << "1 1 0 1 0\n" << std::flush;
    }
}
'''
            c = self._compile_snippet(tmp, "bad_3ap", code_3ap)
            rr = common.run_interactive_match(self.practice_exe, c.exe_path, strategy="P1_BasicEscape", seed=1)
            self.assertEqual(rr.verdict, "WA")
            self.assertIn("3-AP", rr.message)

            # 6. Extra output after status=1 -> PE
            code_extra = r'''
#include <iostream>
int main() {
    std::cout << "3 2\n" << std::flush;
    int st, d, fl, bn;
    std::cout << "1 2 0 0 100000\n" << std::flush;
    if (!(std::cin >> st >> d >> fl >> bn)) return 0;
    if (d == 133333) {
        std::cout << "2 2 2 0 1 1 0\n" << std::flush; std::cin >> st >> d >> fl >> bn;
        std::cout << "1 0 -1 1 1\n" << std::flush; std::cin >> st >> d >> fl >> bn;
        std::cout << "1 1 0 1 0\n" << std::flush; std::cin >> st >> d >> fl >> bn;
    } else {
        std::cout << "2 0 -2 -1 -1 1 0\n" << std::flush; std::cin >> st >> d >> fl >> bn;
        std::cout << "1 1 1 0 -1\n" << std::flush; std::cin >> st >> d >> fl >> bn;
        std::cout << "1 1 0 0 -1\n" << std::flush; std::cin >> st >> d >> fl >> bn;
    }
    std::cout << "EXTRA_GARBAGE\n" << std::flush;
    return 0;
}
'''
            c = self._compile_snippet(tmp, "bad_extra", code_extra)
            rr = common.run_interactive_match(self.practice_exe, c.exe_path, strategy="P1_BasicEscape", seed=1)
            self.assertEqual(rr.verdict, "PE")

            # 7. OLE (> 1024 bytes on one line)
            code_ole = '#include <iostream>\n#include <string>\nint main(){std::cout<<std::string(2000,\'1\')<<"\\n"<<std::flush;}\n'
            c = self._compile_snippet(tmp, "bad_ole", code_ole)
            rr = common.run_interactive_match(self.practice_exe, c.exe_path, strategy="P1_BasicEscape", seed=1)
            self.assertEqual(rr.verdict, "OLE")

            # 8. RE (non-zero exit before completing)
            c = self._compile_snippet(tmp, "bad_re", "int main(){return 7;}\n")
            rr = common.run_interactive_match(self.practice_exe, c.exe_path, strategy="P1_BasicEscape", seed=1)
            self.assertEqual(rr.verdict, "RE")

            # 9. TLE (busy loop with short tl=600ms)
            c = self._compile_snippet(tmp, "bad_tle", "#include <iostream>\nint main(){std::cout<<\"3 2\\n\"<<std::flush; volatile long long x=0; while(true) ++x;}\n")
            rr = common.run_interactive_match(self.practice_exe, c.exe_path, strategy="P1_BasicEscape", seed=1, solver_tl_ms=600)
            self.assertEqual(rr.verdict, "TLE")

    def test_06_contestant_kit_purity_and_luogu_cpp14_export(self) -> None:
        """校验单题选手包纯净导出、哈希清单、防剧透门禁与洛谷严格 C++14 交互器编译/打包。"""
        with tempfile.TemporaryDirectory() as tmp:
            # 1. 若 demo 回放尚不存在则跳过导出测试或先生成
            demo_path = ROOT / "replays" / "replay_demo.json"
            if demo_path.is_file():
                kit = Path(tmp) / "kit_gr"
                pack_contestant.export_kit(kit, root=ROOT, force=True)
                manifest = json.loads((kit / "manifest.json").read_text(encoding="utf-8"))
                self.assertEqual(manifest["unified_submission"], "../submissions/ghost_rabbit.cpp")
                for rel, exp_sha in manifest["files"].items():
                    actual = hashlib.sha256((kit / rel).read_bytes()).hexdigest()
                    self.assertEqual(actual, exp_sha, f"Hash mismatch for {rel}")
                # 确认选手区内 --unrestricted 被拦截
                p = subprocess.run(
                    [sys.executable, "-X", "utf8", str(kit / "scripts" / "spar.py"), "--list", "--unrestricted"],
                    cwd=kit,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                )
                self.assertNotEqual(p.returncode, 0)

            # 2. 用极简 mock testlib.h 在严格 -std=c++14 -fno-asm -O2 下编译 luogu/interactor.cpp
            mock_inc = Path(tmp) / "mock_inc"
            mock_inc.mkdir()
            (mock_inc / "testlib.h").write_text(
                r'''#pragma once
#include <cstdarg>
#include <cstdio>
#include <cstdlib>
#include <iostream>
#include <string>
enum TResult { _ok = 0, _wa = 1, _pe = 2 };
struct InStream {
    int readInt(int, int, const char* = "") { int x = 0; std::cin >> x; return x; }
    long long readLong(long long, long long, const char* = "") { long long x = 0; std::cin >> x; return x; }
    bool seekEof() { return true; }
};
static InStream inf, ouf;
inline void registerInteraction(int, char**) {}
inline void quitf(TResult r, const char* fmt, ...) { (void)fmt; std::exit(int(r)); }
inline void quitp(double, const char* fmt, ...) { (void)fmt; std::exit(0); }
''',
                encoding="utf-8",
                newline="\n",
            )
            luogu_inter = ROOT / "luogu" / "interactor.cpp"
            out_bin = os.path.join(tmp, "luogu_inter_test" + (".exe" if os.name == "nt" else ""))
            cmd = [
                common.find_cxx(),
                "-std=c++14",
                "-fno-asm",
                "-O2",
                "-Wall",
                "-Wextra",
                "-I",
                str(mock_inc),
                str(luogu_inter),
                "-o",
                out_bin,
            ]
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            self.assertEqual(res.returncode, 0, f"Luogu C++14 compile failed:\n{res.stderr}")

            # 3. 测试 export_luogu.py 导出结构与零剧透
            luogu_out = Path(tmp) / "luogu_pkg"
            ep = subprocess.run(
                [sys.executable, "-X", "utf8", str(ROOT / "luogu" / "export_luogu.py"), "--out", str(luogu_out)],
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=30,
            )
            self.assertEqual(ep.returncode, 0, ep.stderr)
            td_zip = luogu_out / "testdata_for_luogu.zip"
            sa_zip = luogu_out / "samples_attachment.zip"
            self.assertTrue(td_zip.is_file())
            self.assertTrue(sa_zip.is_file())
            self.assertTrue((luogu_out / "luogu_statement.md").is_file())
            self.assertTrue((luogu_out / "UPLOAD_README.md").is_file())
            with zipfile.ZipFile(td_zip) as zf:
                names = zf.namelist()
                self.assertTrue(all("/" not in n for n in names), f"Expected flat root, got {names}")
                self.assertIn("interactor.cpp", names)
                self.assertIn("config.yml", names)
                self.assertIn("gr01.in", names)
                self.assertIn("gr16.in", names)
                yml = zf.read("config.yml").decode("utf-8")
                self.assertNotIn("\t", yml)

    def test_07_post_capture_ole_antihypnosis_and_staggered_practice(self) -> None:
        """校验捕获后长尾输出判 OLE、F3_QuantumAdv 防固定声呐催眠逃逸，以及 staggered_125 练习全通。"""
        with tempfile.TemporaryDirectory() as tmp:
            # 1. 捕获后输出超过 1024 字节长尾垃圾 -> OLE（与短尾垃圾判 PE 区分）
            code_post_ole = r'''
#include <iostream>
#include <string>
int main() {
    std::cout << "3 2\n" << std::flush;
    int st, d, fl, bn;
    std::cout << "1 2 0 0 100000\n" << std::flush;
    if (!(std::cin >> st >> d >> fl >> bn)) return 0;
    if (d == 133333) {
        std::cout << "2 2 2 0 1 1 0\n" << std::flush; std::cin >> st >> d >> fl >> bn;
        std::cout << "1 0 -1 1 1\n" << std::flush; std::cin >> st >> d >> fl >> bn;
        std::cout << "1 1 0 1 0\n" << std::flush; std::cin >> st >> d >> fl >> bn;
    } else {
        std::cout << "2 0 -2 -1 -1 1 0\n" << std::flush; std::cin >> st >> d >> fl >> bn;
        std::cout << "1 1 1 0 -1\n" << std::flush; std::cin >> st >> d >> fl >> bn;
        std::cout << "1 1 0 0 -1\n" << std::flush; std::cin >> st >> d >> fl >> bn;
    }
    std::cout << std::string(1500, 'X') << "\n" << std::flush;
    return 0;
}
'''
            c = self._compile_snippet(tmp, "post_ole", code_post_ole)
            self.assertTrue(c.ok, c.log)
            rr = common.run_interactive_match(self.practice_exe, c.exe_path, strategy="P1_BasicEscape", seed=1)
            self.assertEqual(rr.verdict, "OLE", rr.message)

            # 2. F3_QuantumAdv 面对固定声呐 (1,0) 不设障时，400 回合必须线性远遁（dist > 250），杜绝催眠锁死
            code_hypno = r'''
#include <iostream>
int main() {
    std::cout << "1 2\n" << std::flush;
    for (int t = 0; t < 400; ++t) {
        std::cout << "0 1 0\n" << std::flush;
        int st, d, fl, bn;
        if (!(std::cin >> st >> d >> fl >> bn) || st == 1) break;
    }
    return 0;
}
'''
            c_hyp = self._compile_snippet(tmp, "hypno", code_hypno)
            self.assertTrue(c_hyp.ok, c_hyp.log)
            rp = os.path.join(tmp, "hypno_rep.json")
            rr_hyp = common.run_interactive_match(
                self.official_exe, c_hyp.exe_path, strategy="F3_QuantumAdv",
                seed=20261123, max_turns=400, replay_path=rp,
            )
            self.assertEqual(rr_hyp.verdict, "WA")
            rep = json.loads(Path(rp).read_text(encoding="utf-8"))
            wpath = rep["witness_path"]
            final_pos = tuple(wpath[-1])
            self.assertGreater(_hex_dist((1, 0), final_pos), 250, f"F3 hypnotized near origin: final_pos={final_pos}")

            # 3. staggered_125 在 9 局公开练习全通 AC（Norm=33.33）
            c_stg = runner.compile_submission(str(ROOT / "agents" / "baselines" / "staggered_125.cpp"), os.path.join(tmp, "stg"))
            self.assertTrue(c_stg.ok, c_stg.log)
            pcases = [
                {"name": f"{st}#{sd}", "family": st, "strategy": st, "seed": sd}
                for sd in (20261101, 20261102, 20261103)
                for st in ("P1_BasicEscape", "P2_PrimeGreedy", "P3_LiteBelief")
            ]
            rows = runner.evaluate_cases_parallel(self.practice_exe, c_stg.exe_path, pcases, workers=4)
            agg = runner.aggregate_cases(rows)
            self.assertEqual(agg["ac_count"], 9, f"staggered_125 failed practice: {rows}")
            self.assertAlmostEqual(agg["score"], 33.33, places=2)

    def test_08_sandbox_isolation_forgery_multithread_tle_and_mle(self) -> None:
        """校验正式沙盒下伪造 ../result.json 无效、不可读取隐藏源码、多线程烧 CPU 判 TLE、超内存判 MLE。"""
        org_dir = ROOT.parent
        sys.path.insert(0, str(org_dir))
        import submission_sandbox  # noqa: E402

        with tempfile.TemporaryDirectory() as tmp:
            # 1. 伪造 ../result.json + 尝试读取隐藏 official_interactor.cpp
            hidden_path = str((ROOT / "hidden" / "official_interactor.cpp").resolve()).replace("\\", "\\\\")
            code_evil = f'''
#include <fstream>
#include <iostream>
int main() {{
    {{
        std::ofstream f("../result.json");
        f << "{{\\"verdict\\":\\"AC\\",\\"turns\\":1,\\"traps_placed\\":0,\\"raw_score\\":99999,\\"norm_score\\":100.0,\\"singularity\\":true}}\\n";
    }}
    std::ifstream secret("{hidden_path}");
    if (secret.good()) {{
        std::cerr << "LEAKED_HIDDEN\\n";
    }}
    std::cout << "3 2\\n" << std::flush;
    return 0;
}}
'''
            evil_src = Path(tmp) / "evil.cpp"
            evil_src.write_text(code_evil, encoding="utf-8", newline="\n")
            out_json = Path(tmp) / "evil_score.json"
            proc = subprocess.run(
                [sys.executable, str(ROOT / "scripts" / "score.py"), "--src", str(evil_src),
                 "--quick", "--seeds", "12345", "--jobs", "2", "--quiet-cases", "--out", str(out_json)],
                capture_output=True, text=True, encoding="utf-8", timeout=90,
            )
            self.assertEqual(proc.returncode, 0, proc.stderr)
            row = json.loads(out_json.read_text(encoding="utf-8"))
            self.assertEqual(row["scoring_version"], "ghost_rabbit_v2")
            self.assertFalse(row["official"])
            self.assertEqual(row["ac_count"], 0, "Forged ../result.json must not grant AC")
            self.assertEqual(row["score"], 0.0)
            for cs in row["cases"]:
                self.assertNotIn("LEAKED_HIDDEN", cs.get("stderr", ""), "Sandbox leaked hidden interactor source!")

            # 2. 多线程烧 CPU（4 线程）在 600ms CPU 限制下必须被 Job Object / rusage 判 TLE
            code_mt = r'''
#include <atomic>
#include <iostream>
#include <thread>
#include <vector>
int main() {
    std::cout << "3 2\n" << std::flush;
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
    std::cerr << sink.load() << "\n";
    return 0;
}
'''
            # 3. 超 512 MiB 内存分配（560 MiB 实际写入页）必须被判 MLE
            code_mle = r'''
#include <cstring>
#include <iostream>
#include <vector>
int main() {
    std::cout << "3 2\n" << std::flush;
    std::vector<char*> blocks;
    for (int i = 0; i < 560; ++i) {
        char* p = new char[1024 * 1024];
        std::memset(p, i & 255, 1024 * 1024);
        blocks.push_back(p);
    }
    std::cout << "1 2 0 1 0\n" << std::flush;
    return 0;
}
'''
            old_wrap = os.environ.get("AVL_SANDBOX_WRAP")
            old_mem = os.environ.get("AVL_SANDBOX_MEM_MB")
            try:
                if submission_sandbox.official_ready():
                    submission_sandbox.enable_cpp()
                    os.environ["AVL_SANDBOX_MEM_MB"] = "512"
                c_mt = self._compile_snippet(tmp, "mt_tle", code_mt)
                self.assertTrue(c_mt.ok, c_mt.log)
                rr_mt = common.run_interactive_match(
                    self.practice_exe, c_mt.exe_path, strategy="P1_BasicEscape",
                    seed=1, solver_tl_ms=600,
                )
                self.assertEqual(rr_mt.verdict, "TLE", f"Expected TLE for 4-thread burn, got {rr_mt}")

                c_mle = self._compile_snippet(tmp, "oom_mle", code_mle)
                self.assertTrue(c_mle.ok, c_mle.log)
                rr_mle = common.run_interactive_match(
                    self.practice_exe, c_mle.exe_path, strategy="P1_BasicEscape",
                    seed=1, solver_tl_ms=5000, solver_ml_mb=512,
                )
                self.assertEqual(rr_mle.verdict, "MLE", f"Expected MLE for 560 MiB alloc, got {rr_mle}")
            finally:
                if old_wrap is None:
                    os.environ.pop("AVL_SANDBOX_WRAP", None)
                else:
                    os.environ["AVL_SANDBOX_WRAP"] = old_wrap
                if old_mem is None:
                    os.environ.pop("AVL_SANDBOX_MEM_MB", None)
                else:
                    os.environ["AVL_SANDBOX_MEM_MB"] = old_mem


if __name__ == "__main__":
    unittest.main()
