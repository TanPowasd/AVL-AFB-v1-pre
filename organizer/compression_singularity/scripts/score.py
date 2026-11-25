# -*- coding: utf-8 -*-
"""《压缩奇点》高性能并行正式动态评测脚本（阶段二：暗箱泛化压测）。

特性：
  - 使用 OS CSPRNG 动态生成 512 个隐藏测试点（8 家族 × [32×10^5 + 32×10^6]），多进程并行生成并计算 B_ref
  - 基于 CPU/内存余量自适应并行调度多测试点（--jobs），实时输出完成进度
  - 支持直接线索化 32B HotOp + AVX-512/AVX2 虚拟机（fast，10~85 B steps/s）与纯 C99 寄存器驻留 AOT JIT（jit，15~130 B steps/s）
  - 自动挂载 AVL_SANDBOX_WRAP（Windows AppContainer + Job Object / Linux bubblewrap）
"""
from __future__ import annotations

import argparse
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
import json
import os
from pathlib import Path
import secrets
import sys
import tempfile
import threading
import time
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "engine"))
sys.path.insert(0, str(ROOT / "hidden"))
import generator  # noqa: E402
import runner  # noqa: E402
import vm  # noqa: E402

SCORING_VERSION = "compression_singularity_v1"


def _gen_one_case_worker(item: tuple[tuple[str, str, int], int]) -> dict[str, Any]:
    (name, fam, length), seed = item
    target_str, meta = generator.generate_case(fam, length, seed)
    b_ref = vm.compute_b_ref(target_str)
    return {
        "name": name,
        "family": fam,
        "length": length,
        "seed": int(seed),
        "b_ref_bits": b_ref,
        "target_str": target_str,
        "meta": meta,
    }


def build_dynamic_suite(
    suite_spec: tuple[tuple[str, str, int], ...],
    seeds: list[int],
    workers: int = 1,
) -> list[dict[str, Any]]:
    """多进程并行动态生成测试串并计算 liblzma -9 基准 B_ref。"""
    pairs = list(zip(suite_spec, seeds))
    eff_w = max(1, min(workers, len(pairs)))
    if eff_w == 1:
        return [_gen_one_case_worker(p) for p in pairs]
    if len(pairs) >= 16 and os.path.isfile(sys.argv[0]):
        chunksize = max(1, len(pairs) // (eff_w * 4))
        with ProcessPoolExecutor(max_workers=eff_w) as pool:
            return list(pool.map(_gen_one_case_worker, pairs, chunksize=chunksize))
    with ThreadPoolExecutor(max_workers=eff_w) as pool:
        return list(pool.map(_gen_one_case_worker, pairs))


def evaluate_submission(
    src_path: str,
    dynamic_cases: list[dict[str, Any]],
    *,
    solver_tl_ms: int = runner.SOLVER_TIME_LIMIT_MS,
    workers: int = 1,
    vm_mode: str = "fast",
    stream_progress: bool = False,
) -> dict[str, Any]:
    sub_name = Path(src_path).stem
    sub_sha = runner.digest_file(Path(src_path))
    t0 = time.perf_counter()
    with tempfile.TemporaryDirectory(prefix="afb_cs_judge_") as build_dir:
        comp = runner.compile_submission(src_path, build_dir, str(ROOT))
        if not comp.ok:
            return {
                "problem": runner.PROBLEM_ID,
                "title": runner.TITLE,
                "submission": sub_name,
                "submission_sha256": sub_sha,
                "src": os.path.abspath(src_path),
                "status": "CE",
                "compile_ms": round(comp.elapsed_ms, 1),
                "compile_log": comp.log,
                "score": 0.0,
                "max_score": 100.0,
                "raw_score": 0.0,
                "ac_count": 0,
                "total_cases": len(dynamic_cases),
                "max_time_ms": 0.0,
                "max_mem_mb": 0.0,
                "wall_s": round(time.perf_counter() - t0, 3),
                "by_family": {},
                "cases": [],
            }

        print_lock = threading.Lock()

        def _on_done(done_i: int, total_n: int, res: dict[str, Any]) -> None:
            if not stream_progress:
                return
            with print_lock:
                print(
                    f"  [{done_i:>3d}/{total_n:<3d}] {res['name']:<12s} {res['verdict']:<18s} "
                    f"|P|={res['prog_len']:<9d} Raw={res['raw_score']:9.2f} Norm={res['norm_score']:6.2f} "
                    f"(solve {res['wall_ms']:.0f}ms, vm {res['vm_ms']:.1f}ms)",
                    file=sys.stderr,
                    flush=True,
                )

        case_results = runner.evaluate_cases_parallel(
            comp.exe_path,
            dynamic_cases,
            workers=workers,
            solver_tl_ms=solver_tl_ms,
            vm_mode=vm_mode,
            on_case_done=_on_done,
        )

        if runner.digest_file(Path(src_path)) != sub_sha:
            raise RuntimeError(f"Submission changed during evaluation: {src_path}")
        agg = runner.aggregate_cases(case_results)
        total_steps = sum(int(r.get("steps", 0)) for r in case_results)
        total_vm_ms = sum(float(r.get("vm_ms", 0.0)) for r in case_results)
        return {
            "problem": runner.PROBLEM_ID,
            "title": runner.TITLE,
            "submission": sub_name,
            "submission_sha256": sub_sha,
            "src": os.path.abspath(src_path),
            "status": agg["status"],
            "compile_ms": round(comp.elapsed_ms, 1),
            "compile_log": comp.log,
            "score": agg["score"],
            "max_score": 100.0,
            "raw_score": agg["raw_score"],
            "ac_count": agg["ac_count"],
            "total_cases": agg["total_cases"],
            "max_time_ms": round(max((r.get("wall_ms", 0.0) for r in case_results), default=0.0), 2),
            "max_mem_mb": round(max((r.get("mem_mb", 0.0) for r in case_results), default=0.0), 2),
            "total_steps": total_steps,
            "total_vm_ms": round(total_vm_ms, 2),
            "wall_s": round(time.perf_counter() - t0, 3),
            "by_family": agg["by_family"],
            "cases": case_results,
        }


def print_report(rep: dict[str, Any], official: bool, verbose: bool = True) -> None:
    tag = "正式动态评测" if official else "练习/重放评测"
    print(f"=== {rep['title']} ({rep['problem']}) · {tag} ===")
    print(f"Submission : {rep['submission']} ({rep['src']})")
    if rep["status"] == "CE":
        print(f"Status     : CE ({rep['compile_ms']} ms)\n{rep['compile_log']}")
        return
    print(
        f"Status     : {rep['status']} ({rep['ac_count']}/{rep['total_cases']} AC)  |  "
        f"Score: {rep['score']:.2f} / 100  |  RawScore: {rep['raw_score']:.2f} (不封顶)"
    )
    print(
        f"Resources  : Wall {rep.get('wall_s', 0.0):.2f} s  |  Max Case {rep['max_time_ms']:.1f} ms  |  "
        f"Max Mem {rep['max_mem_mb']:.1f} MB  |  VM Total {rep.get('total_vm_ms', 0.0):.1f} ms ({rep.get('total_steps', 0):,} steps)"
    )
    for fam, info in rep.get("by_family", {}).items():
        print(f"  {fam:<4s}  AC {info['ac']}/{info['cases']}  |  RawScore {info['raw_score']:10.2f}  |  Norm {info['norm_score']:6.2f}/100")
    if verbose:
        print()
        print(f"  {'Case':<10s} {'Verdict':<18s} {'|P|':>9s} {'B_ref':>9s} {'RawScore':>10s} {'Norm':>7s} {'Steps':>12s} {'WallMs':>8s} {'VmMs':>7s}")
        for c in rep.get("cases", []):
            print(
                f"  {c['name']:<10s} {c['verdict']:<18s} {c['prog_len']:>9d} {c['b_ref_bits']:>9d} "
                f"{c['raw_score']:>10.2f} {c['norm_score']:>7.2f} {c['steps']:>12d} {c['wall_ms']:>8.1f} {c['vm_ms']:>7.1f}"
            )


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="《压缩奇点》C++20 高性能并行正式动态评测器")
    ap.add_argument("--src", default="", help="单个待测 .cpp 文件路径")
    ap.add_argument("--submissions", default=str(ROOT / "agents" / "submissions"), help="批量评测目录")
    ap.add_argument("--seeds", type=int, nargs="+", default=None, help="显式指定测试种子（非正式重放）")
    ap.add_argument("--quick", action="store_true", help="快速冒烟档（4×10^4，非正式）")
    ap.add_argument("--tl", type=int, default=0, help="自定义单点求解时限（毫秒，非默认时标为非正式）")
    ap.add_argument("--jobs", type=int, default=None, help="并发评测工作线程数（默认按 CPU/内存余量自动选择）")
    ap.add_argument("--vm-mode", choices=["fast", "jit", "auto"], default="auto", help="虚拟机校验模式（默认 auto：紧凑长串程序自动启用 C99 AOT JIT，其余走零开销 fast）")
    ap.add_argument("--quiet-cases", action="store_true")
    ap.add_argument("--out", default="", help="输出 JSON 路径")
    ns = ap.parse_args()

    profile_cfg = runner.verify_frozen_profile(ROOT)
    profile_path = ROOT / "config" / "scoring_v2.json"
    profile_sha = runner.digest_file(profile_path)
    suite_spec = generator.QUICK_SUITE_SPEC if ns.quick else generator.OFFICIAL_SUITE_SPEC
    n_cases = len(suite_spec)

    if ns.seeds is not None:
        if len(ns.seeds) == 1:
            base_s = ns.seeds[0]
            seeds = [base_s + i * 1000003 for i in range(n_cases)]
        elif len(ns.seeds) == n_cases:
            seeds = list(ns.seeds)
        else:
            ap.error(f"--seeds 需提供 1 个主种子或恰好 {n_cases} 个种子")
        sampling_mode = "replay"
    else:
        seeds = []
        used = set(generator.PUBLIC_SPECIMEN_SEEDS)
        while len(seeds) < n_cases:
            s_val = secrets.randbits(63) + 1
            if s_val not in used:
                used.add(s_val)
                seeds.append(s_val)
        sampling_mode = "os_csprng"

    tl_ms = ns.tl if ns.tl > 0 else runner.SOLVER_TIME_LIMIT_MS
    official = (not ns.quick) and (ns.seeds is None) and (tl_ms == runner.SOLVER_TIME_LIMIT_MS)

    official_lb = (ROOT / "results" / "leaderboard.json").resolve()
    out_path = Path(ns.out).resolve() if ns.out else (official_lb if official else (ROOT / "results" / "practice.json").resolve())
    if not official and out_path == official_lb:
        ap.error("practice results must not overwrite the official leaderboard")

    exec_info = runner.detect_parallel_workers(ns.jobs, n_cases)
    workers = exec_info["workers"]

    sys.path.insert(0, str(ROOT.parent))
    import submission_sandbox
    if official:
        if submission_sandbox.official_ready():
            submission_sandbox.enable_cpp()
    elif submission_sandbox.available():
        submission_sandbox.enable_cpp()

    vm.ensure_vm_binary()
    t_gen = time.perf_counter()
    dynamic_cases = build_dynamic_suite(suite_spec, seeds, workers=workers)
    gen_ms = round((time.perf_counter() - t_gen) * 1000.0, 1)
    print(
        f"[Judge] 已生成 {n_cases} 个动态测试点 ({gen_ms:.1f} ms) · "
        f"并发 workers={workers} ({exec_info['mode']}) · VM={ns.vm_mode}",
        file=sys.stderr,
    )

    if ns.src:
        rep = evaluate_submission(
            ns.src,
            dynamic_cases,
            solver_tl_ms=tl_ms,
            workers=workers,
            vm_mode=ns.vm_mode,
            stream_progress=(not ns.quiet_cases),
        )
        rep["official"] = official
        rep["scoring_version"] = SCORING_VERSION
        rep["profile_revision"] = profile_cfg["profile_revision"]
        rep["profile_sha256"] = profile_sha
        rep["sampling"] = {"mode": sampling_mode, "seeds": seeds}
        rep["execution"] = {**exec_info, "gen_ms": gen_ms, "vm_mode": ns.vm_mode}
        if runner.digest_file(profile_path) != profile_sha:
            raise RuntimeError("Scoring profile changed during evaluation")
        print_report(rep, official=official, verbose=not ns.quiet_cases)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(rep, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
        return

    sub_dir = Path(ns.submissions)
    cpp_files = sorted(
        str(p) for p in sub_dir.glob("*.cpp")
        if not p.name.startswith("_")
    ) if sub_dir.is_dir() else []
    if not cpp_files:
        print(f"未找到待测提交：请将模型产出的 .cpp 放入 {sub_dir}/<模型名>.cpp 或用 --src 指定。")
        return

    reports = [
        evaluate_submission(
            p,
            dynamic_cases,
            solver_tl_ms=tl_ms,
            workers=workers,
            vm_mode=ns.vm_mode,
            stream_progress=(not ns.quiet_cases),
        )
        for p in cpp_files
    ]
    if runner.digest_file(profile_path) != profile_sha:
        raise RuntimeError("Scoring profile changed during evaluation")
    reports.sort(key=lambda r: (-r["score"], -r["raw_score"], r["max_time_ms"]))

    print(f"=== Leaderboard: {runner.TITLE} ({'正式' if official else '练习'}) ===")
    print(f"{'Model/Submission':<24s}{'Status':>8s}{'Score/100':>11s}{'RawScore':>12s}{'AC':>8s}{'Wall(s)':>10s}")
    for r in reports:
        ac_str = f"{r['ac_count']}/{r['total_cases']}"
        print(f"{r['submission']:<24s}{r['status']:>8s}{r['score']:>11.2f}{r['raw_score']:>12.2f}{ac_str:>8s}{r.get('wall_s', 0.0):>10.2f}")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "problem": runner.PROBLEM_ID,
        "official": official,
        "scoring_version": SCORING_VERSION,
        "profile_revision": profile_cfg["profile_revision"],
        "profile_sha256": profile_sha,
        "sampling": {"mode": sampling_mode, "seeds": seeds},
        "execution": {**exec_info, "gen_ms": gen_ms, "vm_mode": ns.vm_mode},
        "cases_meta": [
            {"name": c["name"], "family": c["family"], "length": c["length"], "seed": c["seed"], "b_ref_bits": c["b_ref_bits"]}
            for c in dynamic_cases
        ],
        "leaderboard": reports,
    }
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")


if __name__ == "__main__":
    main()
