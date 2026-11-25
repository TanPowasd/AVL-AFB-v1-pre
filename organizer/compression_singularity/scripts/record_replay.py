# -*- coding: utf-8 -*-
"""《压缩奇点》离线回放与虚拟机轨迹录制脚本（scripts/record_replay.py）。

用法：
  # 编译并在公开样例上录制完整回放（含 VM 执行轨迹，可用 viz/player.html 打开）
  python scripts/record_replay.py agents/starter.cpp --out replays/replay_starter.json

  # 仅录制指定样本
  python scripts/record_replay.py agents/baselines/rle_loop.cpp --case 00_example specimen_1 --out replays/replay_rle.json
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "engine"))
import policy  # noqa: E402
import replay  # noqa: E402
import runner  # noqa: E402
import vm  # noqa: E402

SAMPLES_DIR = Path(ROOT) / "data" / "samples"


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="编译 C++20 求解器并录制带 VM 执行轨迹的离线回放 JSON")
    ap.add_argument("src", nargs="?", default="", help="C++20 源文件路径")
    ap.add_argument("--case", nargs="*", default=None, help="可选：只录制指定样例名")
    ap.add_argument("--trace-points", type=int, default=100, help="每个测试点采样的 VM 轨迹点数（默认 100）")
    ap.add_argument("--out", type=Path, required=True, help="回放 JSON 输出路径（选手区须位于 replays/ 下）")
    ns = ap.parse_args()

    src = ns.src or policy.default_submission_path()
    if not src:
        ap.error("请指定 C++20 源文件路径")

    try:
        policy.check_source_path(src)
        policy.check_output_dir(str(ns.out), "replays")
    except policy.PolicyError as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(2)

    manifest = json.loads((SAMPLES_DIR / "manifest.json").read_text(encoding="utf-8"))
    cases = manifest.get("cases", [])
    if ns.case is not None:
        wanted = set(ns.case)
        cases = [c for c in cases if c["name"] in wanted]
        if not cases:
            ap.error(f"找不到指定样例：{ns.case}")

    vm.ensure_vm_binary()
    with tempfile.TemporaryDirectory(prefix="afb_cs_rec_") as build_dir:
        comp = runner.compile_submission(src, build_dir, ROOT)
        if not comp.ok:
            print(f"[CE] 编译失败：\n{comp.log}", file=sys.stderr)
            sys.exit(1)
        rows = []
        previews: dict[str, str] = {}
        for c in cases:
            in_path = SAMPLES_DIR / c["in"]
            raw_in = in_path.read_text(encoding="utf-8")
            L, S = vm.parse_input_text(raw_in)
            res = runner.evaluate_target_case(
                comp.exe_path,
                c["name"],
                S,
                family=c.get("family", "sample"),
                b_ref_bits=c.get("b_ref_bits"),
                trace_points=max(10, ns.trace_points),
            )
            rows.append(res)
            print(
                f"  [rec] {res['name']:<16s} {res['verdict']:<16s} |P|={res['prog_len']:<8d} "
                f"raw={res['raw_score']:8.2f} norm={res['norm_score']:6.2f} trace={len(res.get('trace', []))}"
            )

    summary = runner.aggregate_cases(rows)
    payload = replay.build_replay_payload(
        title=runner.TITLE,
        submission=Path(src).name,
        cases=rows,
        summary=summary,
        program_previews=previews,
    )
    ns.out.parent.mkdir(parents=True, exist_ok=True)
    ns.out.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(f"回放已保存：{ns.out}（原分均分 {summary['raw_score']:.2f}，折算分 {summary['score']:.2f}/100）")


if __name__ == "__main__":
    main()
