# -*- coding: utf-8 -*-
"""前沿实验室 1v1 PvP 对战脚本（仅限主办评测环境，不导出至选手包）。

用法：
    python scripts/duel.py
    python scripts/duel.py agents/submissions/model_a.py agents/submissions/model_b.py --seeds 4
    python scripts/duel.py agents/baselines/scaling.py agents/baselines/merchant.py --seeds 2 --ticks 4380 --record-replay
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "engine"))
sys.path.insert(0, os.path.join(ROOT, "score"))
import archive  # noqa: E402
import elo  # noqa: E402
import engine  # noqa: E402
import replay as replay_mod  # noqa: E402


def label_of(path: str) -> str:
    return archive.submission_label(path)


def _discover_submissions(sub_dir: str) -> list[str]:
    found: list[str] = []
    if not os.path.isdir(sub_dir):
        return found
    for entry in sorted(os.listdir(sub_dir)):
        if entry.startswith(".") or entry.startswith("_"):
            continue
        p = os.path.join(sub_dir, entry)
        if archive.is_submission_path(p):
            found.append(p)
    return found


def run_pair_games(
    path_a: str,
    path_b: str,
    seeds: list[int],
    ticks: int,
    draw_margin_pct: float = 1.0,
    record_first_dir: str = "",
    inproc: bool = False,
) -> tuple[list[dict], list[str]]:
    games: list[dict] = []
    replays: list[str] = []
    label_a, label_b = label_of(path_a), label_of(path_b)
    engine_sha = replay_mod.sha256_path(os.path.join(ROOT, "engine", "engine.py")) if record_first_dir else ""
    recorded = False

    for seed in seeds:
        for seat_a in (0, 1):
            pa, pb = (path_a, path_b) if seat_a == 0 else (path_b, path_a)
            res = engine.play(pa, pb, seed, ticks=ticks, inproc=inproc)
            if record_first_dir and not recorded and seat_a == 0:
                seats = [
                    {
                        "seat": 0,
                        "label": label_a,
                        "file": os.path.relpath(path_a, ROOT).replace(os.sep, "/"),
                        "file_sha256": replay_mod.sha256_path(path_a),
                    },
                    {
                        "seat": 1,
                        "label": label_b,
                        "file": os.path.relpath(path_b, ROOT).replace(os.sep, "/"),
                        "file_sha256": replay_mod.sha256_path(path_b),
                    },
                ]
                rdata = replay_mod.build_replay(res, seats=seats, seed=seed, ticks=ticks, mode="adv", engine_sha=engine_sha)
                rname = f"duel_{label_a}_vs_{label_b}_seed{seed}_fw.json"
                rpath = os.path.join(record_first_dir, rname)
                replay_mod.save_replay(rpath, rdata)
                replays.append(rpath)
                recorded = True

            va = float(res["value"][seat_a])
            vb = float(res["value"][1 - seat_a])
            outcome = elo.judge_game(va, vb, draw_margin_pct=draw_margin_pct)
            sc_a = round(elo.adv_score(va, vb, scale=engine.VALUE_SCALE), 2)
            games.append({
                "seed": seed,
                "seat_a": seat_a,
                "ticks": ticks,
                "value": res["value"],
                "C": res["C"],
                "outcome": outcome,
                "adv_score_a": sc_a,
                "faults": res["faults"],
                "wall_s": res["wall_s"],
            })
            print(
                f"  seed={seed:<4d} seatA={seat_a}  V={va:>8.1f}M vs {vb:>8.1f}M  "
                f"C={res['C'][seat_a]:.4f} vs {res['C'][1 - seat_a]:.4f}  "
                f"-> {outcome:<4s} (adv {sc_a:.1f})  [{res['wall_s']:.1f}s]",
                flush=True,
            )
    return games, replays


def _fault_summary(games: list[dict], is_a: bool) -> dict:
    timeouts = bad_json = dead = illegal = 0
    max_step = 0.0
    for g in games:
        seat = g["seat_a"] if is_a else (1 - g["seat_a"])
        fl = g["faults"][seat]
        timeouts += int(fl.get("timeouts", 0))
        bad_json += int(fl.get("bad_json", 0))
        dead += 1 if fl.get("dead") else 0
        illegal += int(fl.get("illegal", 0))
        max_step = max(max_step, float(fl.get("max_step_s", 0.0)))
    return {
        "timeouts": timeouts,
        "bad_json": bad_json,
        "dead_games": dead,
        "illegal": illegal,
        "max_step_s": round(max_step, 3),
    }


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="前沿实验室 1v1 PvP 对战（默认取 agents/submissions/*.py 中的两个文件）")
    ap.add_argument("files", nargs="*", help="可选：显式指定 2 个策略文件路径")
    ap.add_argument("--seeds", type=int, default=4, help="常规赛种子数（每种子交换座位跑 2 局，默认 4）")
    ap.add_argument("--seed-list", default="", help="可选：逗号分隔的显式种子列表（覆盖 --seeds）")
    ap.add_argument("--ticks", type=int, default=engine.TICKS, help=f"每局 tick 数（默认 {engine.TICKS}）")
    ap.add_argument("--no-tiebreak", action="store_true", help="禁用 50.0%% 得分率时自动加赛")
    ap.add_argument("--tiebreak-seeds", type=int, default=4, help="加赛种子数（默认 4）")
    ap.add_argument("--draw-margin-pct", type=float, default=1.0, help="平局估值容差百分比（默认 1.0%%）")
    ap.add_argument("--record-replay", action="store_true", help="将首个种子正向对局录制为回放到 replays/")
    ap.add_argument("--replay-dir", default=os.path.join(ROOT, "replays"), help="回放保存目录（默认 replays/）")
    ap.add_argument("--out", default="", help="战报 JSON 输出路径（默认 results/<timestamp>/duel.json）")
    ap.add_argument("--inproc", action="store_true", help="开发调试：进程内运行（跳过子进程隔离）")
    args = ap.parse_args()

    files = list(args.files)
    if not files:
        files = _discover_submissions(os.path.join(ROOT, "agents", "submissions"))
    if len(files) != 2:
        print(f"请将两个参赛策略（.zip / 含 main.py 的目录 / .py）保存至 agents/submissions/，或通过命令行传入 2 个路径（当前找到 {len(files)} 个）。")
        sys.exit(2)
    for f in files:
        if not archive.is_submission_path(f):
            print(f"找不到合法策略（需要 .zip、含 main.py 的目录或 .py）：{f}", file=sys.stderr)
            sys.exit(2)

    path_a, path_b = os.path.abspath(files[0]), os.path.abspath(files[1])
    label_a, label_b = label_of(path_a), label_of(path_b)
    cfg_all = json.load(open(os.path.join(ROOT, "config", "format.json"), encoding="utf-8"))
    ticks = int(args.ticks) if args.ticks and args.ticks > 0 else engine.TICKS
    if args.seed_list.strip():
        used_seeds = [int(x.strip()) for x in args.seed_list.split(",") if x.strip()]
    else:
        used_seeds = elo.pick_seeds(cfg_all, args.seeds)

    print(f"== 主赛：{label_a} vs {label_b}（{len(used_seeds)} 种子 × 2 座位，ticks={ticks}）==", flush=True)
    replay_dir = args.replay_dir if args.record_replay else ""
    games, replays = run_pair_games(
        path_a, path_b, used_seeds, ticks=ticks,
        draw_margin_pct=args.draw_margin_pct,
        record_first_dir=replay_dir,
        inproc=args.inproc,
    )
    res = elo.aggregate_match(label_a, label_b, games, replays)
    print(elo.summarize_match(res), flush=True)

    t0 = res["tally"]
    if not args.no_tiebreak and elo.is_half_score(t0["A"], t0["draw"], len(res["games"])):
        tb_seeds = elo.tiebreak_seeds(cfg_all, used_seeds, count=args.tiebreak_seeds)
        if tb_seeds:
            print(f"== 加赛（得分率 50.0%）：追加 {len(tb_seeds)} 种子（+{len(tb_seeds) * 2} 局） ==", flush=True)
            extra_games, extra_replays = run_pair_games(
                path_a, path_b, tb_seeds, ticks=ticks,
                draw_margin_pct=args.draw_margin_pct,
                record_first_dir="",
                inproc=args.inproc,
            )
            res = elo.aggregate_match(label_a, label_b, res["games"] + extra_games, res["replays"] + extra_replays)
            print("== 合并结果：" + elo.summarize_match(res) + " ==", flush=True)

    res["files"] = [path_a, path_b]
    res["fault_summary"] = {
        label_a: _fault_summary(res["games"], True),
        label_b: _fault_summary(res["games"], False),
    }
    res["draw_margin_pct"] = args.draw_margin_pct
    res["value_scale"] = engine.VALUE_SCALE

    for lbl in (label_a, label_b):
        fs = res["fault_summary"][lbl]
        print(
            f"   {lbl} 状态：超时 {fs['timeouts']}，坏JSON {fs['bad_json']}，崩溃局 {fs['dead_games']}，"
            f"非法动作 {fs['illegal']}，单步峰值 {fs['max_step_s'] * 1000:.0f}ms"
        )

    out_path = args.out or os.path.join(ROOT, "results", time.strftime("%Y%m%d-%H%M%S"), "duel.json")
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    with open(out_path, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(res, fh, ensure_ascii=False, indent=2)
    print("   战报：", out_path)
    if replays:
        print("   回放：", ", ".join(replays))

    t = res["tally"]
    print("== 结论 ==")
    if t["A"] == t["B"]:
        va, vb = res["avg_value"]
        note = "场均估值持平" if abs(va - vb) < 1e-6 else (
            f"场均估值 {label_a if va > vb else label_b} 领先（{max(va, vb):.1f}M vs {min(va, vb):.1f}M）"
        )
        print(f"   {label_a} 与 {label_b} {len(res['games'])} 局后战平（{t['A']}:{t['draw']}:{t['B']}），{note}")
    else:
        win = label_a if t["A"] > t["B"] else label_b
        lose = label_b if t["A"] > t["B"] else label_a
        print(f"   {win} 胜 {lose}（{max(t['A'], t['B'])}:{t['draw']}:{min(t['A'], t['B'])}，共 {len(res['games'])} 局）")


if __name__ == "__main__":
    main()
