# -*- coding: utf-8 -*-
"""《双符奇点》（sk_singularity）单测归约追踪与 DAG 爆炸回放录制脚本。"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "engine"))
import policy  # noqa: E402
import replay  # noqa: E402
import runner  # noqa: E402

PRACTICE_SUITE_PATH = ROOT / "config" / "practice_suite.json"


def main() -> None:
    ap = argparse.ArgumentParser(description="录制单测归约追踪与 DAG 爆炸回放 JSON（供 viz/player.html 可视化）")
    ap.add_argument("source", nargs="?", default="", help="待测 C++20 源码（默认 agents/baselines/beam_evo.cpp）")
    ap.add_argument("--case", default="P1_03", help="公开练习集测试点名（默认 P1_03）")
    ap.add_argument("--tl", type=int, default=2000, help="求解时限 ms（默认 2000）")
    ap.add_argument("--trace-steps", type=int, default=80, help="记录前多少步显式最左最外归约轨迹（默认 80）")
    ap.add_argument("--out", default="", help="输出 JSON 路径（默认 replays/replay_<case>.json）")
    args = ap.parse_args()

    src = args.source or str(ROOT / "agents" / "baselines" / "beam_evo.cpp")
    try:
        policy.check_source_path(src)
    except policy.PolicyError as exc:
        raise SystemExit(f"策略拒绝：{exc}")
    if not os.path.isfile(src):
        raise SystemExit(f"找不到源码文件：{src}")

    cases = json.loads(PRACTICE_SUITE_PATH.read_text(encoding="utf-8"))["cases"]
    matched = [c for c in cases if c["name"] == args.case]
    if not matched:
        raise SystemExit(f"找不到公开练习测试点：{args.case}")
    case = matched[0]

    out_path = Path(args.out) if args.out else (ROOT / "replays" / f"replay_{case['name'].lower()}.json")
    try:
        policy.check_output_dir(str(out_path.parent), "replays")
    except policy.PolicyError as exc:
        raise SystemExit(f"策略拒绝：{exc}")

    data = replay.record_case_replay(
        src,
        case,
        root_dir=ROOT,
        tl_ms=args.tl,
        trace_steps=args.trace_steps,
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    res = data["result"]
    print(
        f"Wrote {out_path} ({case['name']} {res['verdict']} |E|={res['leaves']} "
        f"log2T={res['log2_steps']:.3f} T={res['steps_display']} DAG={res['dag_nodes']} "
        f"trace_frames={len(res.get('trace', []))})"
    )


if __name__ == "__main__":
    main()
