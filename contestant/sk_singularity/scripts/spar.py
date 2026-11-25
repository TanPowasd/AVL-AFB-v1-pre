# -*- coding: utf-8 -*-
"""《双符奇点》（sk_singularity）本地自测脚本（scripts/spar.py）。

用法：
  python scripts/spar.py --list
  python scripts/spar.py agents/starter.cpp
  python scripts/spar.py agents/baselines/beam_evo.cpp --workers 4
  python scripts/spar.py ../submissions/sk_singularity.cpp --case P1_01 P1_05 --tl 2000
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
import common  # noqa: E402
import policy  # noqa: E402
import runner  # noqa: E402

PRACTICE_SUITE_PATH = ROOT / "config" / "practice_suite.json"


def load_practice_cases() -> list[dict[str, Any]]:
    data = json.loads(PRACTICE_SUITE_PATH.read_text(encoding="utf-8"))
    return list(data["cases"])


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="《双符奇点》本地公开练习集自测（14 个公开样例）")
    ap.add_argument("source", nargs="?", default="", help="待测 C++20 源码路径")
    ap.add_argument("--list", action="store_true", help="列出全部公开练习测试点")
    ap.add_argument("--case", nargs="*", help="仅运行指定名称的测试点（如 P1_01 P3_02）")
    ap.add_argument("--family", nargs="*", help="仅运行指定家族（如 P1_StdSK P4_Pair33）")
    ap.add_argument("--tl", type=int, default=runner.SOLVER_TIME_LIMIT_MS, help="覆盖单测求解时限（ms，默认 5000）")
    ap.add_argument("--flip", action="store_true", help="对全部测试点施加 S<->K 同构翻转（检验对称鲁棒性）")
    ap.add_argument(
        "--workers", "--jobs", dest="workers", type=int,
        default=None,
        help="并发评测线程数（默认按 CPU/内存余量自动选择）",
    )
    ap.add_argument("--out", type=str, default="", help="保存 JSON 报告到指定目录（选手区须位于 results/ 内）")
    ap.add_argument("--json", action="store_true", help="仅输出机器可读 JSON")
    args = ap.parse_args()

    cases = load_practice_cases()
    if args.list:
        print(f"=== 《双符奇点》公开练习集（共 {len(cases)} 测） ===")
        print(f"{'Name':<7s} {'Family':<15s} {'N':>3s} {'Arity':>7s} {'Req':>5s} {'L_ref':>7s}  Rule / Description")
        print("-" * 88)
        for c in cases:
            has_req = "YES" if str(c.get("req", "-")).strip() not in ("", "-") else "-"
            ar = f"({c['a_S']},{c['a_K']})"
            print(
                f"{c['name']:<7s} {c['family']:<15s} {c['N']:>3d} {ar:>7s} {has_req:>5s} "
                f"{float(c['l_ref']):>7.2f}  S->{c['rhs_S']} | K->{c['rhs_K']}  [{c.get('description', '')}]"
            )
        print("\n公开参考基线：agents/starter.cpp, agents/baselines/beam_evo.cpp")
        return

    src = args.source or policy.default_submission_path()
    if not src:
        raise SystemExit("未指定待测 C++20 源码，且未找到默认提交文件。请传入路径，如：python scripts/spar.py agents/starter.cpp")

    try:
        policy.check_source_path(src)
    except policy.PolicyError as exc:
        raise SystemExit(f"策略拒绝：{exc}")
    if not os.path.isfile(src):
        raise SystemExit(f"找不到源码文件：{src}")

    if args.case:
        cset = set(args.case)
        cases = [c for c in cases if c["name"] in cset]
    if args.family:
        fset = set(args.family)
        cases = [c for c in cases if c["family"] in fset]
    if not cases:
        raise SystemExit("过滤后没有可运行的练习测试点。")

    if args.flip:
        cases = [runner.apply_sk_flip(c, True) for c in cases]

    out_dir = ""
    if args.out:
        try:
            policy.check_output_dir(args.out, "results")
        except policy.PolicyError as exc:
            raise SystemExit(f"策略拒绝：{exc}")
        out_dir = os.path.abspath(args.out)

    try:
        exec_info = runner.detect_parallel_workers(args.workers, len(cases))
    except ValueError as exc:
        ap.error(str(exc))
    workers = exec_info["workers"]

    judge_exe = common.ensure_judge_binary(ROOT)
    t0 = time.perf_counter()
    tmp_root = str(common.writable_temp_root(ROOT))
    with tempfile.TemporaryDirectory(prefix="afb_sk_spar_", dir=tmp_root, ignore_cleanup_errors=True) as build_dir:
        comp = runner.compile_submission(src, build_dir, str(ROOT))
        if not comp.ok:
            report = {
                "problem": runner.PROBLEM_ID,
                "suite": "practice",
                "submission": Path(src).stem,
                "status": "CE",
                "compile_ms": round(comp.elapsed_ms, 1),
                "compile_log": comp.log,
                "score": 0.0,
                "max_score": 100.0,
                "cases": [],
            }
            if args.json:
                print(json.dumps(report, ensure_ascii=False, indent=2))
            else:
                print(f"[CE] 编译失败（{comp.elapsed_ms:.0f} ms）：\n{comp.log}", file=sys.stderr)
            raise SystemExit(1)

        lock = threading.Lock()

        def _on_done(done_i: int, total_n: int, res: dict[str, Any]) -> None:
            if args.json:
                return
            sing_tag = " *SING*" if res.get("singularity") else ""
            flip_tag = " [FLIP]" if res.get("flipped") else ""
            with lock:
                print(
                    f"  [{done_i:>2d}/{total_n:<2d}] {res['name']:<6s} ({res['family']:<14s}, N={res['N']:>2d}){flip_tag} "
                    f"{res['verdict']:<4s} |E|={res['leaves']:<2d} log2T={res['log2_steps']:8.3f} "
                    f"T={res['steps_display']:>12s} DAG={res['dag_nodes']:<7d} "
                    f"Norm={res['norm_score']:6.2f} (cpu {res['cpu_ms']:.0f}ms){sing_tag}",
                    file=sys.stderr,
                    flush=True,
                )

        if not args.json:
            print(
                f"=== 《双符奇点》本地自测：{Path(src).name}（{len(cases)} 测，TL={args.tl}ms，workers={workers} [{exec_info['mode']}]） ===",
                file=sys.stderr,
                flush=True,
            )

        case_results = runner.evaluate_cases_parallel(
            judge_exe,
            comp.exe_path,
            cases,
            workers=workers,
            solver_tl_ms=args.tl,
            on_case_done=_on_done,
        )
        agg = runner.aggregate_cases(case_results)
        report = {
            "problem": runner.PROBLEM_ID,
            "title": runner.TITLE,
            "suite": "practice",
            "submission": Path(src).stem,
            "src": os.path.abspath(src),
            "status": agg["status"],
            "compile_ms": round(comp.elapsed_ms, 1),
            "score": agg["score"],
            "max_score": 100.0,
            "raw_log2_mean": agg["raw_log2_mean"],
            "raw_log2_max": agg["raw_log2_max"],
            "singularity_count": agg["singularity_count"],
            "ac_count": agg["ac_count"],
            "total_cases": agg["total_cases"],
            "max_time_ms": round(max((r.get("cpu_ms", 0.0) for r in case_results), default=0.0), 2),
            "max_mem_mb": round(max((r.get("mem_mb", 0.0) for r in case_results), default=0.0), 2),
            "wall_s": round(time.perf_counter() - t0, 3),
            "execution": exec_info,
            "by_family": agg["by_family"],
            "cases": case_results,
        }

    if out_dir:
        os.makedirs(out_dir, exist_ok=True)
        out_file = os.path.join(out_dir, "spar.json")
        with open(out_file, "w", encoding="utf-8", newline="\n") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
            f.write("\n")

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print("\n=== 自测汇总（公开练习集，仅供参考） ===")
        print(f"  提交文件      : {Path(src).name}")
        print(f"  状态 / 通过   : {report['status']} ({report['ac_count']}/{report['total_cases']})")
        print(f"  折算得分      : {report['score']:.2f} / 100.00")
        print(f"  原始 log2 均值: {report['raw_log2_mean']:.4f}（最高 {report['raw_log2_max']:.4f}）")
        print(f"  奇点突破数    : {report['singularity_count']} / {report['total_cases']}（T >= 2^64 - 1）")
        print(f"  峰值 CPU/内存 : {report['max_time_ms']:.1f} ms / {report['max_mem_mb']:.1f} MiB（总耗时 {report['wall_s']:.2f} s）")


if __name__ == "__main__":
    main()
