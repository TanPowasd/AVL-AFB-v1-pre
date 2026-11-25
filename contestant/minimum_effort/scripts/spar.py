# -*- coding: utf-8 -*-
"""《最小努力》（minimum_effort）公开样例与本地自定义输入自测脚本（scripts/spar.py）。

用法：
  python scripts/spar.py --list
  python scripts/spar.py agents/starter.cpp
  python scripts/spar.py ../submissions/minimum_effort.cpp
  python scripts/spar.py ../submissions/minimum_effort.cpp --input logs/custom.in
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
import common  # noqa: E402
import policy  # noqa: E402
import runner  # noqa: E402

SAMPLES_DIR = os.path.join(ROOT, "data", "samples")


def list_samples() -> list[dict]:
    ins = sorted(f for f in os.listdir(SAMPLES_DIR) if f.endswith(".in"))
    cases = []
    for fn in ins:
        stem = fn[:-3]
        out_fn = stem + ".out"
        c = {
            "name": f"sample_{stem}",
            "id": stem,
            "in": fn,
            "is_sample": True,
            "time_limit_ms": 2000,
            "memory_limit_mb": runner.MEMORY_LIMIT_MB,
        }
        if os.path.isfile(os.path.join(SAMPLES_DIR, out_fn)):
            c["out"] = out_fn
        cases.append(c)
    return cases


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="《最小努力》公开样例本地编译与自测")
    ap.add_argument("src", nargs="?", default="", help="C++20 源文件路径（如 ../submissions/minimum_effort.cpp）")
    ap.add_argument("--list", action="store_true", help="列出公开样例")
    ap.add_argument("--input", default="", help="可选：测试单个自定义输入文件（需放在 data/samples/ 或 logs/ 下）")
    ap.add_argument("--tl", type=int, default=2000, help="样例/自定义输入单点时限 ms（默认 2000）")
    ap.add_argument("--out", default="", help="可选：保存 JSON 报告到指定目录（选手区须位于 results/ 下）")
    ap.add_argument("--unrestricted", action="store_true", help=argparse.SUPPRESS)
    ns = ap.parse_args()

    if ns.unrestricted and policy.is_contestant_workspace():
        print("选手工作区不支持 --unrestricted。", file=sys.stderr)
        sys.exit(2)

    samples = list_samples()
    if not ns.list and not ns.src:
        ns.src = policy.default_submission_path()
    if ns.list or not ns.src:
        print(f"《最小努力》公开自测样例（共 {len(samples)} 组）：")
        for c in samples:
            print(f"  - {c['name']}  (data/samples/{c['in']})")
        print("\n也可将自定义测试输入写入 logs/custom.in 并用 --input logs/custom.in 运行。")
        return

    try:
        policy.check_source_path(ns.src)
        if ns.input:
            policy.check_input_path(ns.input)
        if ns.out:
            policy.check_output_dir(ns.out, "results")
    except policy.PolicyError as e:
        print(str(e), file=sys.stderr)
        sys.exit(2)

    if not os.path.isfile(ns.src):
        print(f"找不到源文件：{ns.src}", file=sys.stderr)
        sys.exit(2)

    tmp_root = str(common.writable_temp_root(ROOT))
    with tempfile.TemporaryDirectory(prefix="afb_spar_me_", dir=tmp_root, ignore_cleanup_errors=True) as build_dir:
        comp = runner.compile_submission(ns.src, build_dir, ROOT)
        if not comp.ok:
            print(f"[CE] 编译失败 ({comp.elapsed_ms:.0f} ms):\n{comp.log}")
            sys.exit(1)

        print(
            f"编译成功 ({comp.elapsed_ms:.0f} ms) · 时限 {ns.tl} ms · 内存限制 {runner.MEMORY_LIMIT_MB} MiB "
            f"· 效率权重 (时间 {runner.TIME_WEIGHT:.2f} > 空间 {runner.MEM_WEIGHT:.2f}，免扣 {runner.TIME_FREE_MS:.0f} ms / {runner.MEM_FREE_MB:.0f} MiB)"
        )
        rows = []
        if ns.input:
            in_dir = os.path.dirname(os.path.abspath(ns.input))
            in_fn = os.path.basename(ns.input)
            case = {
                "name": in_fn,
                "id": in_fn,
                "in": in_fn,
                "is_sample": True,
                "time_limit_ms": ns.tl,
                "memory_limit_mb": runner.MEMORY_LIMIT_MB,
            }
            out_cand = os.path.splitext(os.path.abspath(ns.input))[0] + ".out"
            if os.path.isfile(out_cand):
                case["out"] = os.path.basename(out_cand)
            res = runner.run_case(comp.exe_path, case, in_dir, tl_override_ms=ns.tl)
            rows.append(res)
            eff_tag = f"E={res.get('efficiency', 0.0):.3f}" if res["verdict"] in ("AC", "RUN") else "E=0.000"
            print(
                f"  {res['name']:<20s} {res['verdict']:<4s} {res['time_ms']:7.1f} ms  "
                f"{res['mem_mb']:6.1f} MiB  {eff_tag}  {res['msg']}"
            )
        else:
            for c in samples:
                res = runner.run_case(comp.exe_path, c, SAMPLES_DIR, tl_override_ms=ns.tl)
                rows.append(res)
                eff_tag = f"E={res.get('efficiency', 0.0):.3f}" if res["verdict"] in ("AC", "RUN") else "E=0.000"
                print(
                    f"  {res['name']:<20s} {res['verdict']:<4s} {res['time_ms']:7.1f} ms  "
                    f"{res['mem_mb']:6.1f} MiB  {eff_tag}  {res['msg']}"
                )

    ac_cnt = sum(1 for r in rows if r["verdict"] == "AC")
    print(f"\n样例通过：{ac_cnt} / {len(rows)}")
    if ns.out:
        out_dir = os.path.abspath(ns.out)
        os.makedirs(out_dir, exist_ok=True)
        out_file = os.path.join(out_dir, "spar.json")
        Path(out_file).write_text(
            json.dumps({"src": os.path.relpath(os.path.abspath(ns.src), ROOT), "cases": rows}, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
            newline="\n",
        )
        print(f"自测记录已保存：{out_file}")


if __name__ == "__main__":
    main()
