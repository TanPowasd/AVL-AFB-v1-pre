# -*- coding: utf-8 -*-
"""《天翼幽灵兔：四重奇点追截战》高性能并行正式动态评测脚本（scripts/score.py）。

特性：
  - 使用 OS CSPRNG 每轮抽取 32 个全新随机种子（4 类互补隐藏强交互器 × 8 局）
  - 原生 C++20 交互器 + 匿名管道直连 + 沙盒隔离（AVL_SANDBOX_WRAP + 512 MiB 内存限额）
  - 双轨计分：套题 0-100 折算分（全部测试点 Norm 均分，防买彩票） + 单题不封顶 RawScore（全通取最劣）
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "engine"))
sys.path.insert(0, str(ROOT / "hidden"))
import runner  # noqa: E402
import schedule  # noqa: E402

SCORING_VERSION = "ghost_rabbit_v2"


def _fmt_raw(raw_score: float, singularity: bool) -> str:
    return "INF(Sing)" if singularity else f"{raw_score:9.2f}"


def evaluate_submission(
    src_path: str,
    interactor_exe: str,
    cases: list[dict[str, Any]],
    *,
    solver_tl_ms: int = runner.SOLVER_TIME_LIMIT_MS,
    workers: int = 1,
    stream_progress: bool = False,
) -> dict[str, Any]:
    sub_name = Path(src_path).stem
    sub_sha = runner.digest_file(Path(src_path))
    t0 = time.perf_counter()
    with tempfile.TemporaryDirectory(prefix="afb_gr_judge_") as build_dir:
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
                "singularity": False,
                "ac_count": 0,
                "total_cases": len(cases),
                "max_time_ms": 0.0,
                "max_active_ms": 0.0,
                "max_mem_mb": 0.0,
                "wall_s": round(time.perf_counter() - t0, 3),
                "by_family": {},
                "cases": [],
            }

        print_lock = threading.Lock()

        def _on_done(done_i: int, total_n: int, res: dict[str, Any]) -> None:
            if not stream_progress:
                return
            pq = f"{res['P_red']}/{res['Q_red']}" if res["Q_red"] > 0 else "-"
            raw_s = _fmt_raw(res["raw_score"], res["singularity"]).strip()
            with print_lock:
                print(
                    f"  [{done_i:>2d}/{total_n:<2d}] {res['name']:<6s} ({res['family']:<13s}) {res['verdict']:<5s} "
                    f"q={pq:<8s} T={res['turns']:<4d} F={res['traps_placed']:<4d} "
                    f"Raw={raw_s:>9s} Norm={res['norm_score']:6.2f} "
                    f"(cpu {res['cpu_ms']:.0f}ms, int {res['interactor_ms']:.0f}ms)",
                    file=sys.stderr,
                    flush=True,
                )

        case_results = runner.evaluate_cases_parallel(
            interactor_exe,
            comp.exe_path,
            cases,
            workers=workers,
            solver_tl_ms=solver_tl_ms,
            on_case_done=_on_done,
        )

        agg = runner.aggregate_cases(case_results)
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
            "singularity": agg["singularity"],
            "ac_count": agg["ac_count"],
            "total_cases": agg["total_cases"],
            "max_time_ms": round(max((r.get("cpu_ms", 0.0) for r in case_results), default=0.0), 2),
            "max_active_ms": round(max((r.get("active_ms", 0.0) for r in case_results), default=0.0), 2),
            "max_mem_mb": round(max((r.get("mem_mb", 0.0) for r in case_results), default=0.0), 2),
            "total_interactor_ms": round(sum(float(r.get("interactor_ms", 0.0)) for r in case_results), 2),
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
    raw_s = _fmt_raw(rep["raw_score"], rep.get("singularity", False)).strip()
    print(
        f"Status     : {rep['status']} ({rep['ac_count']}/{rep['total_cases']} AC)  |  "
        f"Score: {rep['score']:.2f} / 100  |  RawScore: {raw_s} (全通最劣)"
    )
    print(
        f"Resources  : Wall {rep.get('wall_s', 0.0):.2f} s  |  Max CPU {rep['max_time_ms']:.1f} ms  |  "
        f"Max Active {rep.get('max_active_ms', 0.0):.1f} ms  |  Max Mem {rep['max_mem_mb']:.1f} MiB  |  "
        f"Interactor Total {rep.get('total_interactor_ms', 0.0):.1f} ms"
    )
    for fam, info in rep.get("by_family", {}).items():
        fr = _fmt_raw(info["raw_score"], info.get("singularity", False)).strip()
        print(
            f"  {fam:<15s}  AC {info['ac']:>2d}/{info['cases']:<2d}  |  "
            f"RawScore {fr:>9s}  |  Norm {info['norm_score']:6.2f}/100  |  "
            f"avgT {info['avg_turns']:6.1f}  avgF {info['avg_traps']:6.1f}"
        )
    if verbose:
        print()
        print(
            f"  {'Case':<7s} {'Family':<14s} {'Verd':<5s} {'P/Q':<10s} {'Turns':>6s} {'Traps':>6s} "
            f"{'Bnc':>4s} {'RawScore':>9s} {'Norm':>7s} {'CpuMs':>7s} {'IntMs':>7s}"
        )
        for c in rep.get("cases", []):
            pq = f"{c['P_red']}/{c['Q_red']}" if c["Q_red"] > 0 else "-"
            rs = _fmt_raw(c["raw_score"], c.get("singularity", False))
            print(
                f"  {c['name']:<7s} {c['family']:<14s} {c['verdict']:<5s} {pq:<10s} {c['turns']:>6d} "
                f"{c['traps_placed']:>6d} {c['bounces']:>4d} {rs:>9s} {c['norm_score']:>7.2f} "
                f"{c['cpu_ms']:>7.1f} {c['interactor_ms']:>7.1f}"
            )


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="《天翼幽灵兔》C++20 高性能并行正式动态评测器")
    ap.add_argument("--src", default="", help="单个待测 .cpp 文件路径")
    ap.add_argument("--submissions", default=str(ROOT / "agents" / "submissions"), help="批量评测目录")
    ap.add_argument("--seeds", type=int, nargs="+", default=None, help="显式指定测试种子（非正式重放）")
    ap.add_argument("--quick", action="store_true", help="快速冒烟档（8 局，非正式）")
    ap.add_argument("--tl", type=int, default=0, help="自定义单局求解器 CPU 时限（毫秒，非默认时标为非正式）")
    ap.add_argument("--jobs", type=int, default=None, help="并发评测工作线程数（默认按 CPU/内存余量自动选择）")
    ap.add_argument("--quiet-cases", action="store_true")
    ap.add_argument("--out", default="", help="输出 JSON 路径")
    ns = ap.parse_args()

    profile_cfg = runner.verify_frozen_profile(ROOT)
    profile_rev = str(profile_cfg.get("profile_revision", "unfrozen"))
    suite_spec = schedule.QUICK_SUITE_SPEC if ns.quick else schedule.OFFICIAL_SUITE_SPEC
    n_cases = len(suite_spec)

    try:
        seeds, sampling_mode = schedule.sample_official_seeds(n_cases, explicit_seeds=ns.seeds)
    except ValueError as exc:
        ap.error(str(exc))

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
    os.environ["AVL_SANDBOX_MEM_MB"] = str(runner.SOLVER_MEMORY_LIMIT_MB)

    interactor_exe = schedule.ensure_official_interactor(ROOT)
    cases = [
        {
            "name": name,
            "family": fam,
            "strategy": fam,
            "seed": int(seed),
            "max_turns": runner.DEFAULT_MAX_TURNS,
            "max_traps": runner.DEFAULT_MAX_TRAPS,
        }
        for (name, fam), seed in zip(suite_spec, seeds)
    ]

    print(
        f"[Judge] 已就绪 {n_cases} 局隐藏交互对抗（{sampling_mode}）· 并发 workers={workers} ({exec_info['mode']})",
        file=sys.stderr,
    )

    if ns.src:
        rep = evaluate_submission(
            ns.src,
            interactor_exe,
            cases,
            solver_tl_ms=tl_ms,
            workers=workers,
            stream_progress=(not ns.quiet_cases),
        )
        rep["official"] = official
        rep["scoring_version"] = SCORING_VERSION
        rep["profile_revision"] = profile_rev
        rep["sampling"] = {"mode": sampling_mode, "seeds": seeds}
        rep["execution"] = exec_info
        print_report(rep, official=official, verbose=not ns.quiet_cases)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(rep, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
        return

    sub_dir = Path(ns.submissions)
    cpp_files = sorted(str(p) for p in sub_dir.glob("*.cpp") if not p.name.startswith("_")) if sub_dir.is_dir() else []
    if not cpp_files:
        print(f"未找到待测提交：请将模型产出的 .cpp 放入 {sub_dir}/<模型名>.cpp 或用 --src 指定。")
        return

    reports = [
        evaluate_submission(
            p,
            interactor_exe,
            cases,
            solver_tl_ms=tl_ms,
            workers=workers,
            stream_progress=(not ns.quiet_cases),
        )
        for p in cpp_files
    ]
    reports.sort(key=lambda r: (-r["score"], -(1 if r.get("singularity") else 0), -r["raw_score"], r["max_time_ms"]))

    print(f"=== Leaderboard: {runner.TITLE} ({'正式' if official else '练习'}) ===")
    print(f"{'Model/Submission':<24s}{'Status':>8s}{'Score/100':>11s}{'RawScore':>12s}{'AC':>8s}{'Wall(s)':>10s}")
    for r in reports:
        ac_str = f"{r['ac_count']}/{r['total_cases']}"
        raw_s = _fmt_raw(r["raw_score"], r.get("singularity", False)).strip()
        print(f"{r['submission']:<24s}{r['status']:>8s}{r['score']:>11.2f}{raw_s:>12s}{ac_str:>8s}{r.get('wall_s', 0.0):>10.2f}")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "problem": runner.PROBLEM_ID,
        "official": official,
        "scoring_version": SCORING_VERSION,
        "profile_revision": profile_rev,
        "sampling": {"mode": sampling_mode, "seeds": seeds},
        "execution": exec_info,
        "cases_meta": [{"name": c["name"], "family": c["family"], "seed": c["seed"]} for c in cases],
        "leaderboard": reports,
    }
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")


if __name__ == "__main__":
    main()
