# -*- coding: utf-8 -*-
"""《天翼幽灵兔》交互对局离线回放录制脚本（scripts/record_replay.py）。

用法：
  # 对全部 3 档公开练习对手各录 1 局，导出为多局回放包并用 viz/player.html 打开
  python scripts/record_replay.py agents/baselines/blitz_150.cpp --out replays/replay_blitz.json

  # 仅录制指定对手与种子
  python scripts/record_replay.py agents/baselines/staggered_125.cpp --opponent P2_PrimeGreedy --seed 20261101 --out replays/replay_p2.json
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
import common  # noqa: E402
import policy  # noqa: E402
import replay  # noqa: E402
import runner  # noqa: E402


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="编译 C++20 求解器并录制带逐回合蜂窝轨迹的离线回放 JSON")
    ap.add_argument("src", nargs="?", default="", help="C++20 源文件路径")
    ap.add_argument("--opponent", nargs="*", default=None, help="可选：只录制指定公开练习对手")
    ap.add_argument("--seed", type=int, nargs="*", default=[20261101], help="随机种子列表（默认 [20261101]）")
    ap.add_argument("--max-frames", type=int, default=2000, help="单局最多保留回放帧数（默认 2000）")
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

    if not os.path.isfile(src):
        print(f"找不到源文件：{src}", file=sys.stderr)
        sys.exit(2)

    cases = runner.build_practice_suite(opponents=ns.opponent, seeds=ns.seed)
    inter_exe = runner.ensure_practice_interactor(ROOT)

    with tempfile.TemporaryDirectory(prefix="afb_gr_rec_", dir=str(common.writable_temp_root(ROOT))) as build_dir:
        comp = runner.compile_submission(src, build_dir, ROOT)
        if not comp.ok:
            print(f"[CE] 编译失败：\n{comp.log}", file=sys.stderr)
            sys.exit(1)

        rows = []
        matches = []
        for idx, c in enumerate(cases):
            rp = os.path.join(build_dir, f"match_{idx}.json")
            res = runner.evaluate_case(
                inter_exe,
                comp.exe_path,
                c,
                replay_path=rp,
                max_replay_frames=max(50, ns.max_frames),
            )
            rows.append(res)
            if os.path.isfile(rp):
                mobj = json.loads(Path(rp).read_text(encoding="utf-8"))
                mobj["name"] = c["name"]
                mobj["cpu_ms"] = res["cpu_ms"]
                matches.append(mobj)
            print(
                f"  [rec] {res['name']:<26s} {res['verdict']:<5s} q={res['P_red']}/{res['Q_red']} "
                f"T={res['turns']:<4d} F={res['traps_placed']:<4d} Norm={res['norm_score']:6.2f}"
            )

    summary = runner.aggregate_cases(rows)
    payload = replay.build_replay_bundle(
        title=runner.TITLE,
        submission=Path(src).name,
        matches=matches,
        summary=summary,
    )
    ns.out.parent.mkdir(parents=True, exist_ok=True)
    ns.out.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(f"回放已保存：{ns.out}（{len(matches)} 局，折算均分 {summary['score']:.2f}/100，可用 viz/player.html 打开）")


if __name__ == "__main__":
    main()
