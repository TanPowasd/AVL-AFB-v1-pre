# -*- coding: utf-8 -*-
"""前沿实验室全轮环 PvP 联赛 + ELO 排行榜（仅限主办评测环境，不导出至选手包）。

用法：
    python scripts/run_league.py agents/baselines/*.py agents/submissions/*.py --seed-count 4
    python scripts/run_league.py agents/baselines/blank.py agents/baselines/scaling.py agents/baselines/merchant.py --seed-count 2 --ticks 4380
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


def collect(patterns: list[str]) -> list[str]:
    files: list[str] = []
    for p in patterns:
        hits = glob.glob(p)
        files.extend(hits if hits else [p])
    out = sorted({os.path.abspath(x) for x in files if archive.is_submission_path(x)})
    return out


def unique_labels(paths: list[str]) -> list[tuple[str, str]]:
    counts: dict[str, int] = {}
    out: list[tuple[str, str]] = []
    for p in paths:
        stem = archive.submission_label(p)
        counts[stem] = counts.get(stem, 0) + 1
        label = stem if counts[stem] == 1 else f"{stem}_{counts[stem]}"
        out.append((label, p))
    return out


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="前沿实验室全轮环 PvP 联赛 + ELO 排行榜")
    ap.add_argument("agents", nargs="+", help="策略文件路径或通配符（可混排基线、参考与参赛提交）")
    ap.add_argument("--seed-count", type=int, default=4, help="每组对抗的种子数（每种子双座位，默认 4）")
    ap.add_argument("--seeds", default="", help="可选：逗号分隔的显式种子列表（覆盖 --seed-count）")
    ap.add_argument("--ticks", type=int, default=engine.TICKS, help=f"每局 tick 数（默认 {engine.TICKS}）")
    ap.add_argument("--k", type=float, default=24.0, help="ELO K 系数（默认 24）")
    ap.add_argument("--init-elo", type=float, default=1500.0, help="初始 ELO 分（默认 1500）")
    ap.add_argument("--margin-cap", type=float, default=2.0, help="ELO 估值胜差倍率上限（默认 2.0）")
    ap.add_argument("--draw-margin-pct", type=float, default=1.0, help="平局估值容差百分比（默认 1.0%%）")
    ap.add_argument("--out", default="", help="输出目录（默认 results/<timestamp>/）")
    ap.add_argument("--inproc", action="store_true", help="开发调试：进程内运行（跳过子进程隔离）")
    args = ap.parse_args()

    cfg_all = json.load(open(os.path.join(ROOT, "config", "format.json"), encoding="utf-8"))
    if args.seeds.strip():
        seeds = [int(x.strip()) for x in args.seeds.split(",") if x.strip()]
    else:
        seeds = elo.pick_seeds(cfg_all, args.seed_count)
    ticks = int(args.ticks) if args.ticks and args.ticks > 0 else engine.TICKS
    k = float(args.k)
    cap = float(args.margin_cap)
    scale = float(engine.VALUE_SCALE)

    files = collect(args.agents)
    loaded = unique_labels(files)
    if len(loaded) < 2:
        print(f"至少需要 2 个有效策略（.zip / 含 main.py 的目录 / .py；当前找到 {len(loaded)} 个）。", file=sys.stderr)
        sys.exit(2)

    print(
        f"[联赛] {len(loaded)} 位选手，{len(seeds)} 种子 × 双座位，ticks={ticks}，"
        f"ELO K={k:g}，margin 上限 ×{cap:g}（VALUE_SCALE={scale:.2f}M）",
        flush=True,
    )

    ratings = {label: float(args.init_elo) for label, _ in loaded}
    book = {label: {"w": 0, "d": 0, "l": 0, "val": [], "C": [], "adv": []} for label, _ in loaded}
    pair_reports: list[dict] = []

    t0 = time.time()
    for i in range(len(loaded)):
        for j in range(i + 1, len(loaded)):
            la, pa = loaded[i]
            lb, pb = loaded[j]
            games = []
            for seed in seeds:
                for seat_a in (0, 1):
                    p0, p1 = (pa, pb) if seat_a == 0 else (pb, pa)
                    r = engine.play(p0, p1, seed, ticks=ticks, inproc=args.inproc)
                    va = float(r["value"][seat_a])
                    vb = float(r["value"][1 - seat_a])
                    ca = float(r["C"][seat_a])
                    cb = float(r["C"][1 - seat_a])
                    outcome = elo.judge_game(va, vb, draw_margin_pct=args.draw_margin_pct)
                    sc_a = elo.adv_score(va, vb, scale=scale)
                    games.append({
                        "seed": seed,
                        "seat_a": seat_a,
                        "value": r["value"],
                        "C": r["C"],
                        "outcome": outcome,
                        "adv_score_a": round(sc_a, 2),
                    })
                    if outcome == "draw":
                        score_la, mf = 0.5, 1.0
                        book[la]["d"] += 1
                        book[lb]["d"] += 1
                    elif outcome == "A":
                        score_la = 1.0
                        mf = elo.margin_factor(va, vb, scale=scale, cap=cap)
                        book[la]["w"] += 1
                        book[lb]["l"] += 1
                    else:
                        score_la = 0.0
                        mf = elo.margin_factor(vb, va, scale=scale, cap=cap)
                        book[lb]["w"] += 1
                        book[la]["l"] += 1
                    ratings[la], ratings[lb] = elo.update(ratings[la], ratings[lb], score_la, margin=mf, k=k)
                    book[la]["val"].append(va)
                    book[lb]["val"].append(vb)
                    book[la]["C"].append(ca)
                    book[lb]["C"].append(cb)
                    book[la]["adv"].append(sc_a)
                    book[lb]["adv"].append(100.0 - sc_a)

            summary = elo.aggregate_match(la, lb, games)
            pair_reports.append(summary)
            print("  " + elo.summarize_match(summary), flush=True)

    rows = []
    for label, path in loaded:
        b = book[label]
        n = max(1, len(b["val"]))
        rows.append({
            "name": label,
            "file": os.path.relpath(path, ROOT).replace(os.sep, "/"),
            "elo": round(ratings[label], 2),
            "w": b["w"],
            "d": b["d"],
            "l": b["l"],
            "avg_value": round(sum(b["val"]) / n, 2),
            "avg_C": round(sum(b["C"]) / n, 4),
            "avg_adv_score": round(sum(b["adv"]) / n, 2),
        })
    rows.sort(key=lambda r: (-r["elo"], -r["avg_adv_score"], r["name"]))
    table = elo.render_table(rows)
    elapsed = time.time() - t0

    out_dir = args.out or os.path.join(ROOT, "results", time.strftime("%Y%m%d-%H%M%S"))
    os.makedirs(out_dir, exist_ok=True)
    md_path = os.path.join(out_dir, "leaderboard.md")
    with open(md_path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(f"# 前沿实验室 PvP 联赛排行榜（{len(seeds)} 种子/组 × 双座位，K={k:g}，margin 上限 ×{cap:g}）\n\n")
        fh.write(
            f"- 对局总耗时：{elapsed:.1f}s；每局 ticks={ticks}；平局容差 {args.draw_margin_pct:g}%；"
            f"对抗分与 ELO 倍率尺度 VALUE_SCALE={scale:.2f}M\n"
            f"- 场景种子：`{seeds}`\n\n"
        )
        fh.write(table + "\n")

    json_path = os.path.join(out_dir, "league.json")
    with open(json_path, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(
            {
                "mode": "pvp_league",
                "seeds": seeds,
                "ticks": ticks,
                "elo_k": k,
                "init_elo": args.init_elo,
                "margin_cap": cap,
                "value_scale": scale,
                "draw_margin_pct": args.draw_margin_pct,
                "elapsed_s": round(elapsed, 2),
                "rows": rows,
                "matches": pair_reports,
            },
            fh,
            ensure_ascii=False,
            indent=2,
        )

    print("\n" + table)
    print("saved:", out_dir)


if __name__ == "__main__":
    main()
