# -*- coding: utf-8 -*-
"""《天翼幽灵兔：四重奇点追截战》本地交互练习与并列自测脚本（scripts/spar.py）。

用法：
  python scripts/spar.py --list
  python scripts/spar.py ../submissions/ghost_rabbit.cpp
  python scripts/spar.py agents/starter.cpp
  python scripts/spar.py agents/baselines/blitz_150.cpp --opponent P2_PrimeGreedy P3_LiteBelief --seed 101 102
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "engine"))
import common  # noqa: E402
import policy  # noqa: E402
import runner  # noqa: E402


def _fmt_raw(raw_score: float, singularity: bool) -> str:
    return "INF(Sing)" if singularity else f"{raw_score:9.2f}"


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="《天翼幽灵兔》公开练习交互器并行本地自测")
    ap.add_argument("src", nargs="?", default="", help="C++20 源文件路径（默认 ../submissions/ghost_rabbit.cpp）")
    ap.add_argument("--list", action="store_true", help="列出全部公开练习对手与内置基线")
    ap.add_argument("--opponent", nargs="*", default=None, help="只评测指定练习对手（如 P1_BasicEscape P3_LiteBelief）")
    ap.add_argument("--seed", type=int, nargs="*", default=None, help="自定义随机种子列表（默认 20261101 20261102 20261103）")
    ap.add_argument("--tl", type=int, default=runner.SOLVER_TIME_LIMIT_MS, help="单局求解器时限（毫秒，默认 5000）")
    ap.add_argument("--jobs", type=int, default=None, help="并行对局工作线程数（默认自动适配）")
    ap.add_argument("--unrestricted", action="store_true", help=argparse.SUPPRESS)
    ns = ap.parse_args()

    if ns.unrestricted and policy.is_contestant_workspace():
        print("选手工作区不支持 --unrestricted。", file=sys.stderr)
        sys.exit(2)

    if not ns.list and not ns.src:
        ns.src = policy.default_submission_path()
    if ns.list or not ns.src:
        print("公开练习对手（位于 engine/practice_interactor.cpp）：")
        for op in runner.PRACTICE_OPPONENTS:
            print(f"  {op['id']:<18s} [{op['tier']}]  {op['desc']}")
        print(f"\n默认练习种子：{list(runner.PRACTICE_DEFAULT_SEEDS)}（共 {len(runner.PRACTICE_OPPONENTS) * len(runner.PRACTICE_DEFAULT_SEEDS)} 局）")
        print("起步模板：agents/starter.cpp (q=7/4=1.75)")
        print("公开基线：agents/baselines/blitz_150.cpp (q=3/2=1.50) · agents/baselines/staggered_125.cpp (q=5/4=1.25)")
        print("录制回放：python scripts/record_replay.py agents/baselines/blitz_150.cpp --out replays/replay_blitz.json")
        return

    try:
        policy.check_source_path(ns.src)
    except policy.PolicyError as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(2)

    if not os.path.isfile(ns.src):
        print(f"找不到源文件：{ns.src}", file=sys.stderr)
        sys.exit(2)

    try:
        cases = runner.build_practice_suite(opponents=ns.opponent, seeds=ns.seed)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(2)

    inter_exe = runner.ensure_practice_interactor(ROOT)
    t_all = time.perf_counter()

    with tempfile.TemporaryDirectory(prefix="afb_gr_spar_", dir=str(common.writable_temp_root(ROOT))) as build_dir:
        comp = runner.compile_submission(ns.src, build_dir, ROOT)
        if not comp.ok:
            print(f"[CE] 编译失败 ({comp.elapsed_ms:.0f} ms):\n{comp.log}")
            sys.exit(1)

        exec_info = runner.detect_parallel_workers(ns.jobs, len(cases))
        workers = exec_info["workers"]
        print(
            f"编译成功 ({comp.elapsed_ms:.0f} ms) · 并发 {workers} 线程 · "
            f"求解时限 {ns.tl} ms · 内存 {runner.SOLVER_MEMORY_LIMIT_MB} MiB"
        )
        print("-" * 116)
        print(
            f"{'Case':<26s} {'Verdict':<8s} {'P/Q':<11s} {'Turns':>6s} {'Traps':>6s} {'Bnc':>4s} "
            f"{'RawScore':>9s} {'Norm':>7s} {'CpuMs':>7s} {'ActMs':>7s} {'IntMs':>7s}"
        )
        print("-" * 116)

        rows = runner.evaluate_cases_parallel(
            inter_exe,
            comp.exe_path,
            cases,
            workers=workers,
            solver_tl_ms=ns.tl,
        )

        for res in rows:
            pq = f"{res['P_red']}/{res['Q_red']}" if res["Q_red"] > 0 else "-"
            raw_s = _fmt_raw(res["raw_score"], res["singularity"])
            print(
                f"{res['name']:<26s} {res['verdict']:<8s} {pq:<11s} {res['turns']:>6d} {res['traps_placed']:>6d} "
                f"{res['bounces']:>4d} {raw_s:>9s} {res['norm_score']:>7.2f} "
                f"{res['cpu_ms']:>7.1f} {res['active_ms']:>7.1f} {res['interactor_ms']:>7.1f}"
            )
            if res["verdict"] != "AC":
                print(f"    -> {res['msg']}")

    summary = runner.aggregate_cases(rows)
    dt_s = time.perf_counter() - t_all
    print("-" * 116)
    raw_tot = _fmt_raw(summary["raw_score"], summary["singularity"]).strip()
    print(
        f"汇总：{summary['ac_count']}/{summary['total_cases']} AC  |  "
        f"套题百分制折算分（均分）= {summary['score']:.2f} / 100  |  "
        f"单题题面原分（全通最劣）= {raw_tot}  |  总耗时 {dt_s:.2f}s"
    )

    out_dir = os.path.join(ROOT, "results", time.strftime("%Y%m%d-%H%M%S") + f"-{time.time_ns() % 1000000:06d}")
    policy.check_output_dir(out_dir, "results")
    os.makedirs(out_dir, exist_ok=True)
    out_file = os.path.join(out_dir, "spar.json")
    with open(out_file, "w", encoding="utf-8", newline="\n") as f:
        json.dump(
            {
                "src": os.path.relpath(os.path.abspath(ns.src), ROOT),
                "execution": {**exec_info, "wall_s": round(dt_s, 3)},
                "summary": summary,
                "cases": rows,
            },
            f,
            ensure_ascii=False,
            indent=2,
        )
    print(f"自测记录已保存：{os.path.relpath(out_file, ROOT)}")


if __name__ == "__main__":
    main()
