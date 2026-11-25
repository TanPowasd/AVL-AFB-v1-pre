# -*- coding: utf-8 -*-
"""组委会全轮环联赛 + ELO 排行榜（支持通配符与多目录收集）。

用法：
  python scripts/run_league.py agents/baselines/*.py agents/submissions/*.py --seed-count 2
  python scripts/run_league.py agents/baselines/*.py agents/reference/*.py --seeds 1 2 --jobs 4
"""
from __future__ import annotations

import argparse
from concurrent.futures import ProcessPoolExecutor
import glob
import json
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "engine"))
sys.path.insert(0, os.path.join(ROOT, "score"))
import elo  # noqa: E402
from engine import play  # noqa: E402


def safe_rel(path: str) -> str:
    try:
        return os.path.relpath(path, ROOT).replace("\\", "/")
    except ValueError:
        return os.path.abspath(path)


def collect(patterns: list[str]) -> list[str]:
    files: list[str] = []
    for p in patterns:
        hits = glob.glob(p)
        files.extend(hits if hits else [p])
    out = []
    for x in sorted(set(os.path.abspath(f) for f in files)):
        base = os.path.basename(x)
        if x.endswith(".py") and not base.startswith("_") and os.path.isfile(x):
            out.append(x)
    return out


def _run_pair(task):
    idx_a, idx_b, path_a, path_b, cfg_idx, cfg, seed = task
    r_def_a = play(path_b, path_a, seed=seed, **cfg)
    r_def_b = play(path_a, path_b, seed=seed, **cfg)
    return {
        "idx_a": idx_a,
        "idx_b": idx_b,
        "config_idx": cfg_idx,
        "seed": seed,
        "def_score_a": r_def_a.def_score,
        "def_score_b": r_def_b.def_score,
        "faults_a": r_def_a.faults["defender"] + r_def_b.faults["attacker"],
        "faults_b": r_def_b.faults["defender"] + r_def_a.faults["attacker"],
    }


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="cache_duel 全轮环联赛 + ELO 排行榜")
    ap.add_argument("agents", nargs="+", help="bot 文件或通配符（如 agents/baselines/*.py）")
    ap.add_argument("--seed-count", type=int, default=2, help="每配置使用的种子数（默认 2）")
    ap.add_argument("--seeds", type=int, nargs="+", default=None, help="显式指定种子列表（覆盖 --seed-count）")
    ap.add_argument("--k", type=float, default=24.0, help="ELO K 系数（默认 24）")
    ap.add_argument("--margin-cap", type=float, default=2.0, help="ELO 分差倍率上限（默认 2.0）")
    ap.add_argument("--init-elo", type=float, default=1500.0, help="初始 ELO（默认 1500）")
    ap.add_argument("--draw-margin", type=float, default=elo.DEFAULT_DRAW_MARGIN,
                    help="对称分差平局阈值（默认 0.005）")
    ap.add_argument("--jobs", type=int, default=min(4, os.cpu_count() or 1), help="并行工作进程数")
    ap.add_argument("--out", default="", help="输出目录（默认 results/<时间戳>/）")
    args = ap.parse_args()

    paths = collect(args.agents)
    if len(paths) < 2:
        print(f"至少需要 2 个可运行的 bot 文件（当前找到 {len(paths)} 个）。")
        sys.exit(2)

    # 重名消歧：同 stem 时附加父目录名
    raw_stems = [os.path.splitext(os.path.basename(p))[0] for p in paths]
    names: list[str] = []
    for p, stem in zip(paths, raw_stems):
        if raw_stems.count(stem) > 1:
            parent = os.path.basename(os.path.dirname(p))
            names.append(f"{parent}/{stem}")
        else:
            names.append(stem)

    cfg_all = json.load(open(os.path.join(ROOT, "config", "format.json"), encoding="utf-8"))
    configs = cfg_all["configs"]
    if args.seeds:
        seeds = list(args.seeds)
    else:
        base_seeds = list(cfg_all.get("seeds") or [1, 2, 3, 4])
        while len(base_seeds) < max(1, args.seed_count):
            base_seeds.append(base_seeds[-1] + 1)
        seeds = base_seeds[: max(1, args.seed_count)]

    print(
        f"[联赛] {len(names)} 名选手 · {len(configs)} 配置 × {len(seeds)} 种子 × 双向攻防  "
        f"ELO(init={args.init_elo:g}, K={args.k:g}, margin_cap={args.margin_cap:g})"
    )

    tasks = []
    for i in range(len(paths)):
        for j in range(i + 1, len(paths)):
            for cfg_idx, cfg in enumerate(configs):
                for seed in seeds:
                    tasks.append((i, j, paths[i], paths[j], cfg_idx, cfg, seed))

    t0 = time.time()
    if args.jobs <= 1 or len(tasks) <= 1:
        results = [_run_pair(t) for t in tasks]
    else:
        with ProcessPoolExecutor(max_workers=min(args.jobs, len(tasks))) as ex:
            results = list(ex.map(_run_pair, tasks))

    ratings = {nm: float(args.init_elo) for nm in names}
    book = {
        nm: {"w": 0, "d": 0, "l": 0, "scores": [], "defs": [], "atts": [], "margins": [], "faults": 0}
        for nm in names
    }
    h2h: dict[tuple[str, str], list[float]] = {}

    for r in results:
        la = names[r["idx_a"]]
        lb = names[r["idx_b"]]
        da = r["def_score_a"]
        db = r["def_score_b"]
        outcome, res_a, sym_a = elo.pair_outcome(da, db, draw_margin=args.draw_margin)
        sym_b = 1.0 - sym_a
        mf = 1.0 if outcome == "draw" else elo.margin_factor(sym_a, sym_b, cap=args.margin_cap)
        ratings[la], ratings[lb] = elo.update(ratings[la], ratings[lb], res_a, margin=mf, k=args.k)

        book[la]["scores"].append(sym_a * 100.0)
        book[lb]["scores"].append(sym_b * 100.0)
        book[la]["defs"].append(da * 100.0)
        book[lb]["defs"].append(db * 100.0)
        book[la]["atts"].append((1.0 - db) * 100.0)
        book[lb]["atts"].append((1.0 - da) * 100.0)
        margin_pts = (sym_a - sym_b) * 100.0
        book[la]["margins"].append(margin_pts)
        book[lb]["margins"].append(-margin_pts)
        book[la]["faults"] += r["faults_a"]
        book[lb]["faults"] += r["faults_b"]
        h2h.setdefault((la, lb), []).append(sym_a * 100.0)
        h2h.setdefault((lb, la), []).append(sym_b * 100.0)

        if outcome == "draw":
            book[la]["d"] += 1
            book[lb]["d"] += 1
        elif outcome == "A":
            book[la]["w"] += 1
            book[lb]["l"] += 1
        else:
            book[lb]["w"] += 1
            book[la]["l"] += 1

    rows = []
    for nm in names:
        b = book[nm]
        n = max(1, len(b["scores"]))
        rows.append({
            "name": nm,
            "elo": round(ratings[nm], 2),
            "w": b["w"],
            "d": b["d"],
            "l": b["l"],
            "avg_score": round(sum(b["scores"]) / n, 3),
            "avg_defense": round(sum(b["defs"]) / n, 3),
            "avg_attack": round(sum(b["atts"]) / n, 3),
            "avg_margin": round(sum(b["margins"]) / n, 3),
            "faults": b["faults"],
        })
    rows.sort(key=lambda x: (-x["elo"], -x["avg_score"]))

    elapsed = time.time() - t0
    table = elo.render_table(rows)
    out_dir = args.out or os.path.join(ROOT, "results", time.strftime("%Y%m%d-%H%M%S"))
    os.makedirs(out_dir, exist_ok=True)

    # 交叉胜率矩阵（行对列的平均综合分 0-100）
    ordered = [r["name"] for r in rows]
    mat_lines = ["| 选手 \\ 对手 | " + " | ".join(ordered) + " |",
                 "|---|" + "|".join(["---:"] * len(ordered)) + "|"]
    for a in ordered:
        cells = []
        for b in ordered:
            if a == b:
                cells.append("—")
            else:
                vals = h2h.get((a, b), [])
                cells.append(f"{sum(vals) / len(vals):.1f}" if vals else "—")
        mat_lines.append(f"| {a} | " + " | ".join(cells) + " |")
    matrix_md = "\n".join(mat_lines)

    lb_path = os.path.join(out_dir, "leaderboard.md")
    with open(lb_path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(
            f"# Cache Duel 联赛排行榜（{len(configs)} 配置 × {len(seeds)} 种子，K={args.k:g}，margin 上限 ×{args.margin_cap:g}）\n\n"
            f"- 共完成 {len(results)} 对对称双局（{len(results) * 2} 局），耗时 {elapsed:.1f}s\n"
            f"- 胜负判定：对称综合分 `0.5 * (def_A + (1 - def_B))`，分差 ≤ {args.draw_margin * 100:.2f} 分判平\n\n"
            f"{table}\n\n"
            f"## 交叉综合分矩阵（行对列，满分 100）\n\n"
            f"{matrix_md}\n"
        )

    json_path = os.path.join(out_dir, "league.json")
    with open(json_path, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(
            {
                "configs": configs,
                "seeds": seeds,
                "elo": {"init": args.init_elo, "k": args.k, "margin_cap": args.margin_cap, "draw_margin": args.draw_margin},
                "elapsed_s": round(elapsed, 2),
                "rows": rows,
                "h2h": {f"{a}|{b}": round(sum(v) / len(v), 3) for (a, b), v in h2h.items()},
                "pairs": results,
            },
            fh,
            ensure_ascii=False,
            indent=2,
        )

    print("\n" + table)
    print(f"\n已保存：{safe_rel(out_dir)}（leaderboard.md, league.json）")


if __name__ == "__main__":
    main()
