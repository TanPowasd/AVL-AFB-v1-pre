# -*- coding: utf-8 -*-
"""《压缩奇点》高性能并行公开样本与自定义输入本地自测脚本。

用法：
  python scripts/spar.py --list
  python scripts/spar.py ../submissions/compression_singularity.cpp
  python scripts/spar.py agents/starter.cpp --case 00_example specimen_1 --jobs 4
  python scripts/spar.py agents/baselines/rle_loop.cpp --input logs/custom.in --profile
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "engine"))
import policy  # noqa: E402
import runner  # noqa: E402
import vm  # noqa: E402

SAMPLES_DIR = os.path.join(ROOT, "data", "samples")


def load_sample_manifest() -> list[dict]:
    mpath = os.path.join(SAMPLES_DIR, "manifest.json")
    if os.path.isfile(mpath):
        with open(mpath, "r", encoding="utf-8") as f:
            return json.load(f).get("cases", [])
    cases = []
    for fn in sorted(f for f in os.listdir(SAMPLES_DIR) if f.endswith(".in")):
        cases.append({"name": fn[:-3], "in": fn, "family": "sample"})
    return cases


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="《压缩奇点》公开样本高速并行编译与虚拟机精确校验")
    ap.add_argument("src", nargs="?", default="", help="C++20 源文件路径（默认 ../submissions/compression_singularity.cpp）")
    ap.add_argument("--list", action="store_true", help="列出全部公开示例与匿名标本及 B_ref 基准")
    ap.add_argument("--case", nargs="*", default=None, help="可选：只运行指定样本名（如 00_example specimen_1）")
    ap.add_argument("--input", default="", help="可选：测试单个自定义输入文件（需位于 data/samples/ 或 logs/ 下）")
    ap.add_argument("--tl", type=int, default=runner.SOLVER_TIME_LIMIT_MS, help="单点求解时限（毫秒，默认 60000）")
    ap.add_argument("--jobs", type=int, default=None, help="并行校验工作线程数（默认自动适配）")
    ap.add_argument("--vm-mode", choices=["fast", "jit", "auto"], default="auto", help="虚拟机引擎模式（默认 auto：紧凑长串程序自动启用 C99 AOT JIT，其余走零开销 fast）")
    ap.add_argument("--profile", action="store_true", help="输出每个样本的 VM 热点指令剖析")
    ap.add_argument("--unrestricted", action="store_true", help=argparse.SUPPRESS)
    ns = ap.parse_args()

    if ns.unrestricted and policy.is_contestant_workspace():
        print("选手工作区不支持 --unrestricted。", file=sys.stderr)
        sys.exit(2)

    samples = load_sample_manifest()
    if not ns.list and not ns.src:
        ns.src = policy.default_submission_path()
    if ns.list or not ns.src:
        print(f"公开示例与匿名标本（共 {len(samples)} 组，位于 data/samples/）：")
        print(f"  {'样本名':<18s} {'类型':<10s} {'L':>9s} {'B_ref(bit)':>12s}  文件")
        for c in samples:
            L = c.get("length", "-")
            bref = c.get("b_ref_bits", "-")
            print(f"  {c['name']:<18s} {c.get('family', 'specimen'):<10s} {str(L):>9s} {str(bref):>12s}  data/samples/{c['in']}")
        print("\n起步模板：agents/starter.cpp · 公开基线：agents/baselines/rle_loop.cpp")
        print("交互模拟器：python scripts/simulate.py --help · 性能压测：python scripts/bench_vm.py")
        print("自定义输入：将测试文件写入 logs/custom.in 并执行 python scripts/spar.py <src> --input logs/custom.in")
        return

    try:
        policy.check_source_path(ns.src)
        if ns.input:
            policy.check_input_path(ns.input)
    except policy.PolicyError as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(2)

    if not os.path.isfile(ns.src):
        print(f"找不到源文件：{ns.src}", file=sys.stderr)
        sys.exit(2)

    if ns.case is not None:
        wanted = set(ns.case)
        selected = [c for c in samples if c["name"] in wanted]
        if not selected:
            print(f"未找到指定样本：{ns.case}", file=sys.stderr)
            sys.exit(2)
    else:
        selected = samples

    vm.ensure_vm_binary()
    t_all = time.perf_counter()

    with tempfile.TemporaryDirectory(prefix="afb_cs_spar_") as build_dir:
        comp = runner.compile_submission(ns.src, build_dir, ROOT)
        if not comp.ok:
            print(f"[CE] 编译失败 ({comp.elapsed_ms:.0f} ms):\n{comp.log}")
            sys.exit(1)

        if ns.input:
            in_abs = os.path.abspath(ns.input)
            _L, S = vm.parse_input_text(Path(in_abs).read_text(encoding="utf-8"))
            case_specs = [{
                "name": Path(in_abs).stem,
                "family": "custom",
                "target_str": S,
                "target_file_path": in_abs,
                "b_ref_bits": None,
            }]
        else:
            case_specs = []
            for c in selected:
                in_path = os.path.join(SAMPLES_DIR, c["in"])
                _L, S = vm.parse_input_text(Path(in_path).read_text(encoding="utf-8"))
                case_specs.append({
                    "name": c["name"],
                    "family": c.get("family", "anonymous"),
                    "target_str": S,
                    "target_file_path": in_path,
                    "b_ref_bits": c.get("b_ref_bits"),
                })

        exec_info = runner.detect_parallel_workers(ns.jobs, len(case_specs))
        workers = exec_info["workers"]
        print(
            f"编译成功 ({comp.elapsed_ms:.0f} ms) · 并发 {workers} 线程 · VM={ns.vm_mode} · "
            f"求解时限 {ns.tl} ms · 内存 {runner.SOLVER_MEMORY_LIMIT_MB} MB"
        )
        print("-" * 108)
        print(
            f"{'Case':<18s} {'Verdict':<18s} {'|P|':>9s} {'B_ref':>9s} {'RawScore':>10s} {'Norm':>7s} "
            f"{'Steps':>11s} {'SolveMs':>8s} {'VmMs':>7s}"
        )
        print("-" * 108)

        rows = runner.evaluate_cases_parallel(
            comp.exe_path,
            case_specs,
            workers=workers,
            solver_tl_ms=ns.tl,
            profile=ns.profile,
            vm_mode=ns.vm_mode,
        )

        for res in rows:
            print(
                f"{res['name']:<18s} {res['verdict']:<18s} {res['prog_len']:>9d} {res['b_ref_bits']:>9d} "
                f"{res['raw_score']:>10.2f} {res['norm_score']:>7.2f} {res['steps']:>11d} "
                f"{res['wall_ms']:>8.1f} {res['vm_ms']:>7.1f}"
            )
            if res["verdict"] != "AC":
                print(f"    -> {res['msg']}")
            if ns.profile and res.get("hotspots"):
                top3 = ", ".join(
                    f"{h['kind']}[{h['src_lo']}:{h['src_hi']}]={h['raw_steps']:,}({100.0*h['raw_steps']/max(1,res['steps']):.1f}%)"
                    for h in res["hotspots"][:3]
                )
                print(f"    hotspots: {top3}")

    summary = runner.aggregate_cases(rows)
    dt_s = time.perf_counter() - t_all
    print("-" * 108)
    print(
        f"汇总：{summary['ac_count']}/{summary['total_cases']} AC  |  "
        f"题面原分（不封顶均分）= {summary['raw_score']:.2f}  |  "
        f"百分制折算分 = {summary['score']:.2f} / 100  |  总耗时 {dt_s:.2f}s"
    )

    out_dir = os.path.join(ROOT, "results", time.strftime("%Y%m%d-%H%M%S") + f"-{time.time_ns() % 1000000:06d}")
    policy.check_output_dir(out_dir, "results")
    os.makedirs(out_dir, exist_ok=True)
    out_file = os.path.join(out_dir, "spar.json")
    with open(out_file, "w", encoding="utf-8", newline="\n") as f:
        json.dump(
            {
                "src": os.path.relpath(os.path.abspath(ns.src), ROOT),
                "execution": {**exec_info, "vm_mode": ns.vm_mode, "wall_s": round(dt_s, 3)},
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
