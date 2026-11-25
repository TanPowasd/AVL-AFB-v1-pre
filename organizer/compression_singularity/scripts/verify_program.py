# -*- coding: utf-8 -*-
"""直接调用标准参考虚拟机校验单个输出程序文件（scripts/verify_program.py）。

用法：
  python scripts/verify_program.py data/samples/00_example.in data/samples/00_example.out
  python scripts/verify_program.py data/samples/00_example.in data/samples/00_example.out --trace replays/ex00.json
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
    ap = argparse.ArgumentParser(description="使用确定性单带参考虚拟机直接校验程序文件 P")
    ap.add_argument("target_in", type=Path, help="目标输入文件（第一行 L，第二行 0/1 串 S）")
    ap.add_argument("program_file", type=Path, help="程序文件（单行 > < + . [ ] 字符串）")
    ap.add_argument("--step-limit", type=int, default=vm.DEFAULT_STEP_LIMIT)
    ap.add_argument("--tape-bound", type=int, default=vm.DEFAULT_TAPE_BOUND)
    ap.add_argument("--trace", type=Path, default=None, help="可选：同时录制执行轨迹回放 JSON（须位于 replays/）")
    ns = ap.parse_args()

    try:
        policy.check_workspace_path(str(ns.target_in))
        policy.check_workspace_path(str(ns.program_file))
        if ns.trace is not None:
            policy.check_output_dir(str(ns.trace), "replays")
    except policy.PolicyError as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(2)

    if ns.trace is not None:
        payload = replay.record_program_replay(ns.target_in, ns.program_file, ns.trace)
        c = payload["cases"][0]
        print(json.dumps({k: v for k, v in c.items() if k != "trace"}, ensure_ascii=False, indent=2))
        print(f"回放轨迹已保存：{ns.trace}")
        sys.exit(0 if c["verdict"] == "AC" else 1)

    raw_in = ns.target_in.read_text(encoding="utf-8")
    L, S = vm.parse_input_text(raw_in)
    b_ref = vm.compute_b_ref(S)
    res = vm.verify_program_bytes(
        f"{L}\n{S}\n".encode("ascii"),
        ns.program_file.read_bytes(),
        step_limit=ns.step_limit,
        tape_bound=ns.tape_bound,
    )
    raw_sc = vm.compute_raw_score(b_ref, res["prog_len"]) if res["verdict"] == "AC" else 0.0
    res["b_ref_bits"] = b_ref
    res["raw_score"] = round(raw_sc, 4)
    res["norm_score"] = round(vm.compute_norm_score(raw_sc), 4)
    print(json.dumps(res, ensure_ascii=False, indent=2))
    sys.exit(0 if res["verdict"] == "AC" else 1)


if __name__ == "__main__":
    main()
