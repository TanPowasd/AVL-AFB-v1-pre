# -*- coding: utf-8 -*-
"""《压缩奇点》高性能单带虚拟机交互模拟器与热点剖析器（scripts/simulate.py）。

用法示例：
  # 1. 自由运行内联程序（无需目标串），查看输出比特流、精确步数、吞吐与纸带非零格
  python scripts/simulate.py -e "+>+>+>+[....+.<]" --profile

  # 2. 对目标文件精确校验并打印得分与热点循环
  python scripts/simulate.py data/samples/00_example.out --target data/samples/00_example.in --profile

  # 3. 启用原生 C++20 AOT JIT 引擎运行高步数程序（单核 10+ B steps/s）
  python scripts/simulate.py my_prog.bf --target data/samples/specimen_1.in --mode jit

  # 4. 直接录制轨迹供 viz/player.html 可视化回放
  python scripts/simulate.py data/samples/00_example.out --target data/samples/00_example.in --trace replays/sim.json
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "engine"))
import policy  # noqa: E402
import replay  # noqa: E402
import vm  # noqa: E402


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="《压缩奇点》高性能单带比特虚拟机模拟器与热点剖析器")
    ap.add_argument("program_file", nargs="?", type=Path, default=None, help="程序文件路径（与 -e 二选一）")
    ap.add_argument("-e", "--expr", default="", help="直接传入内联 6 指令程序字符串")
    ap.add_argument("--target", type=Path, default=None, help="可选：目标输入文件（启用精确比对与计分）")
    ap.add_argument("--mode", choices=["fast", "jit", "ref"], default="fast", help="模拟引擎：fast（超指令直跳）/ jit（原生 AOT）/ ref（纯 Python）")
    ap.add_argument("--step-limit", type=int, default=vm.DEFAULT_STEP_LIMIT, help="最大原始步数（默认 3×10^12）")
    ap.add_argument("--tape-bound", type=int, default=vm.DEFAULT_TAPE_BOUND, help="读写头坐标上界（默认 10^7）")
    ap.add_argument("--max-out-bits", type=int, default=2_000_000, help="自由运行模式下最多捕获的输出比特数")
    ap.add_argument("--profile", action="store_true", help="输出热点超级指令耗时/步数剖析（fast 模式）")
    ap.add_argument("--out-bits", type=Path, default=None, help="自由运行模式下将生成比特流写入文件（须位于 logs/）")
    ap.add_argument("--trace", type=Path, default=None, help="录制回放 JSON（须位于 replays/）")
    ap.add_argument("--json", dest="as_json", action="store_true", help="以 JSON 格式输出结果")
    ns = ap.parse_args()

    if bool(ns.program_file) == bool(ns.expr):
        ap.error("请指定 program_file 或 -e/--expr 其中之一")

    try:
        if ns.program_file is not None:
            policy.check_workspace_path(str(ns.program_file))
        if ns.target is not None:
            policy.check_workspace_path(str(ns.target))
        if ns.out_bits is not None:
            policy.check_output_dir(str(ns.out_bits), "logs")
        if ns.trace is not None:
            policy.check_output_dir(str(ns.trace), "replays")
    except policy.PolicyError as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(2)

    prog_str = ns.expr if ns.expr else ns.program_file.read_text(encoding="utf-8")

    if ns.target is not None:
        raw_in = ns.target.read_text(encoding="utf-8")
        L, S = vm.parse_input_text(raw_in)
        b_ref = vm.compute_b_ref(S)
        res = vm.verify_program(
            S,
            prog_str,
            step_limit=ns.step_limit,
            tape_bound=ns.tape_bound,
            trace_points=120 if ns.trace else 0,
            profile=ns.profile,
            mode=ns.mode,
        )
        raw_sc = vm.compute_raw_score(b_ref, res["prog_len"]) if res["verdict"] == "AC" else 0.0
        res["b_ref_bits"] = b_ref
        res["raw_score"] = round(raw_sc, 4)
        res["norm_score"] = round(vm.compute_norm_score(raw_sc), 4)
        if ns.trace is not None:
            cname = ns.target.stem
            case_row = {
                "name": cname,
                "family": "simulate",
                **res,
            }
            payload = replay.build_replay_payload(
                title="压缩奇点（simulate）",
                submission=ns.program_file.name if ns.program_file else "<inline>",
                cases=[case_row],
                summary={
                    "score": round(res["norm_score"], 2),
                    "max_score": 100.0,
                    "raw_score": round(res["raw_score"], 2),
                    "ac_count": 1 if res["verdict"] == "AC" else 0,
                    "total_cases": 1,
                    "by_family": {},
                    "status": res["verdict"],
                },
                program_previews={cname: prog_str.rstrip("\r\n")},
            )
            ns.trace.parent.mkdir(parents=True, exist_ok=True)
            ns.trace.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    else:
        res = vm.simulate_program(
            prog_str,
            step_limit=ns.step_limit,
            tape_bound=ns.tape_bound,
            max_out_bits=ns.max_out_bits,
            trace_points=120 if ns.trace else 0,
            profile=ns.profile,
            mode=ns.mode,
        )
        if ns.out_bits is not None:
            bits = res.get("out_bits", "")
            ns.out_bits.parent.mkdir(parents=True, exist_ok=True)
            ns.out_bits.write_text(f"{len(bits)}\n{bits}\n", encoding="utf-8", newline="\n")

    elapsed_s = max(1e-9, float(res.get("elapsed_ms", 0.0)) / 1000.0)
    sps = float(res.get("steps", 0)) / elapsed_s
    res["steps_per_sec"] = round(sps, 1)

    if ns.as_json:
        out = {k: v for k, v in res.items() if k != "trace"}
        if "out_bits" in out and len(out["out_bits"]) > 256:
            out["out_bits_preview"] = out["out_bits"][:256] + "..."
            del out["out_bits"]
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return

    print(f"Engine   : {res.get('engine', ns.mode)}  |  Verdict: {res['verdict']} ({res['message']})")
    print(
        f"Program  : |P|={res['prog_len']:,}  IR_ops={res.get('ir_ops', '-')}  |  "
        f"Steps={res['steps']:,}  Time={res.get('elapsed_ms', 0.0):.3f} ms  ({sps/1e6:.2f} M steps/s)"
    )
    print(f"Tape     : p_final={res['p_final']}  span=[{res['p_min']}, {res['p_max']}]  out_len={res['out_len']:,}")
    if "b_ref_bits" in res:
        print(
            f"Scoring  : B_ref={res['b_ref_bits']:,} bit  |  "
            f"RawScore={res['raw_score']:.2f}  |  NormScore={res['norm_score']:.2f} / 100"
        )
    elif "out_bits" in res:
        bits = res["out_bits"]
        preview = bits[:128] + ("..." if len(bits) > 128 else "")
        print(f"Output   : {preview or '(empty)'}")
        if "nonzero_total" in res:
            print(f"NonZero  : {res['nonzero_total']} cells  sample={res.get('nonzero_sample', [])[:12]}")
    if res.get("hotspots"):
        print("Hotspots :")
        cleaned = prog_str.rstrip("\r\n")
        for h in res["hotspots"][:8]:
            snip = cleaned[h["src_lo"]:h["src_hi"]]
            if len(snip) > 28:
                snip = snip[:25] + "..."
            pct = 100.0 * h["raw_steps"] / max(1, res["steps"])
            print(f"  {h['kind']:<14s} [{h['src_lo']:>5d}:{h['src_hi']:<5d}]  hits={h['hits']:<10d} steps={h['raw_steps']:<13,d} ({pct:5.1f}%)  {snip}")
    if ns.trace is not None:
        print(f"Trace    : 已保存至 {ns.trace}")


if __name__ == "__main__":
    main()
