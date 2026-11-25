# -*- coding: utf-8 -*-
"""《压缩奇点》虚拟机与评测机吞吐性能基准测试脚本（scripts/bench_vm.py）。

测量本机上直跳超级指令 SIMD 虚拟机（fast_vm4_avx512 / avx2）与纯 C99 寄存器驻留 AOT JIT（jit_c99_avx512 / avx2）
的真实原始步/秒吞吐，并给出跑满 3×10^12 原始步护栏的预计物理耗时。
"""
from __future__ import annotations

import os
from pathlib import Path
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "engine"))
import runner  # noqa: E402
import vm  # noqa: E402


def _make_binary_counter(k_bits: int) -> str:
    """精确执行 2^k 次迭代的双轨二进制计数器（总步数随 k 呈指数增长，纯合法停机程序）。"""
    return ">+" + ">>+" * k_bits + ">+[<[<<]>>>[+>>]+<[>>]<]"


def _make_simd_sweep(width: int, rounds: int) -> str:
    """SIMD [>] / [<] 往返扫描压力程序（每个往返覆盖 width 格）。"""
    # 在 cell 1..width 放 1，cell 0 与 cell width+1 为 0 哨兵；用简单外层计数器跑 rounds 轮
    # 为保持程序极短，使用二层循环设置：直接构建一个 2000 格 1-跑道并往返 250 次
    return ">+" * width + "<" + ("[<]>[>]<" * rounds)


def _make_stride_sweep(n_iters: int, width: int, stride: int) -> str:
    step_r = ">" * stride
    step_l = "<" * stride
    return (
        "<+" * n_iters
        + "[>]"
        + (step_r + "+") * width
        + step_l * width
        + "<["
        + f"[<]>+>[>]{step_r}[{step_r}]{step_l}[{step_l}]<"
        + "]"
    )


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    vm.ensure_vm_binary()
    print("=== 《压缩奇点》高性能虚拟机吞吐压测 ===")
    print(f"{'Workload':<30s} {'Engine':<16s} {'|P|':>7s} {'RawSteps':>14s} {'RunMs':>9s} {'B steps/s':>10s} {'Est 3e12':>10s}")
    print("-" * 104)

    workloads = [
        ("2^22 binary counter", _make_binary_counter(22), None),
        ("2^24 binary counter", _make_binary_counter(24), None),
        ("SIMD [>]/[<] 2000x500", _make_stride_sweep(2000, 500, 1), None),
        ("SIMD [>>]/[<<] 1000x500", _make_stride_sweep(1000, 500, 2), None),
        ("SIMD [>>>>]/[<<<<] 1000x500", _make_stride_sweep(1000, 500, 4), None),
        ("2^20 counter + IF-XOR gate", _make_binary_counter(20)[:-1] + ">>>+[+>+>+<<]<<<]", None),
    ]

    step_cap = float(vm.DEFAULT_STEP_LIMIT)
    for label, prog, target in workloads:
        # 1. FAST_VM
        r_fast = vm.simulate_program(prog, mode="fast") if target is None else vm.verify_program(target, prog, mode="fast")
        sps_f = r_fast["steps"] / max(1e-9, r_fast["elapsed_ms"] / 1000.0)
        est_f = step_cap / max(1.0, sps_f)
        print(
            f"{label:<30s} {r_fast['engine']:<16s} {r_fast['prog_len']:>7d} {r_fast['steps']:>14,d} "
            f"{r_fast['elapsed_ms']:>9.2f} {sps_f/1e9:>10.2f} {est_f:>8.1f} s"
        )
        # 2. JIT_C99
        r_jit = vm.simulate_program(prog, mode="jit") if target is None else vm.verify_program(target, prog, mode="jit")
        assert r_jit["steps"] == r_fast["steps"] and r_jit["p_final"] == r_fast["p_final"], "Engine mismatch!"
        sps_j = r_jit["steps"] / max(1e-9, r_jit["elapsed_ms"] / 1000.0)
        est_j = step_cap / max(1.0, sps_j)
        print(
            f"{label:<30s} {r_jit['engine']:<16s} {r_jit['prog_len']:>7d} {r_jit['steps']:>14,d} "
            f"{r_jit['elapsed_ms']:>9.2f} {sps_j/1e9:>10.2f} {est_j:>8.1f} s"
        )

    # 4. 并行评测机吞吐测试（rle_loop.cpp 在全部 5 组公开样本上并发跑分）
    samples_dir = ROOT / "data" / "samples"
    import json
    manifest = json.loads((samples_dir / "manifest.json").read_text(encoding="utf-8"))
    case_specs = []
    for c in manifest["cases"]:
        p = samples_dir / c["in"]
        _L, S = vm.parse_input_text(p.read_text(encoding="utf-8"))
        case_specs.append({
            "name": c["name"],
            "family": c.get("family", "anonymous"),
            "target_str": S,
            "target_file_path": str(p),
            "b_ref_bits": c.get("b_ref_bits"),
        })
    exec_info = runner.detect_parallel_workers(None, len(case_specs))
    with tempfile.TemporaryDirectory(prefix="afb_cs_bench_") as tmp:
        comp = runner.compile_submission(str(ROOT / "agents/baselines/rle_loop.cpp"), tmp)
        assert comp.ok, comp.log
        t0 = time.perf_counter()
        rows = runner.evaluate_cases_parallel(comp.exe_path, case_specs, workers=exec_info["workers"])
        dt = time.perf_counter() - t0
    agg = runner.aggregate_cases(rows)
    print("-" * 98)
    print(
        f"并行评测机（rle_loop.cpp × {len(case_specs)} 公开样本，含 4×10^5，workers={exec_info['workers']}）："
        f"{agg['ac_count']}/{agg['total_cases']} AC · 墙钟 {dt:.2f} s · 总原分均分 {agg['raw_score']:.2f}"
    )


if __name__ == "__main__":
    main()
