# -*- coding: utf-8 -*-
"""组委会 1v1 PvP 对战工具：默认自动取 agents/submissions/*.py 中的两个文件。

用法：
  python scripts/duel.py
  python scripts/duel.py agents/submissions/model_a.py agents/submissions/model_b.py --seeds 4
  python scripts/duel.py lru marking --seeds 2 --record-replay
"""
from __future__ import annotations

import argparse
from concurrent.futures import ProcessPoolExecutor
from dataclasses import asdict
import glob
import json
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "engine"))
sys.path.insert(0, os.path.join(ROOT, "score"))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import elo  # noqa: E402
from engine import play  # noqa: E402
import replay as replay_mod  # noqa: E402
import spar  # noqa: E402


def safe_rel(path: str) -> str:
    try:
        return os.path.relpath(path, ROOT).replace("\\", "/")
    except ValueError:
        return os.path.abspath(path)


def resolve_bot(spec: str) -> tuple[str, str]:
    if spec in spar.PRESETS:
        p = os.path.abspath(spar.PRESETS[spec])
        return spec, p
    p = os.path.abspath(spec)
    if not os.path.isfile(p):
        raise SystemExit(f"找不到 bot 文件：{spec}")
    return os.path.splitext(os.path.basename(p))[0], p


def _play_pair_job(args):
    path_a, path_b, cfg_idx, cfg, seed, draw_margin = args
    r_def_a = play(path_b, path_a, seed=seed, **cfg)
    r_def_b = play(path_a, path_b, seed=seed, **cfg)
    outcome, elo_a, sym_a = elo.pair_outcome(r_def_a.def_score, r_def_b.def_score, draw_margin=draw_margin)
    return {
        "config_idx": cfg_idx,
        "config": dict(cfg),
        "seed": seed,
        "outcome": outcome,
        "elo_score_a": elo_a,
        "pair_score_a": sym_a,
        "pair_score_b": 1.0 - sym_a,
        "def_score_a": r_def_a.def_score,
        "def_score_b": r_def_b.def_score,
        "faults_a": r_def_a.faults["defender"] + r_def_b.faults["attacker"],
        "faults_b": r_def_b.faults["defender"] + r_def_a.faults["attacker"],
        "b_attacks_a": asdict(r_def_a),
        "a_attacks_b": asdict(r_def_b),
    }


def run_pairs(path_a: str, path_b: str, configs: list[dict], seeds: list[int],
              draw_margin: float, jobs: int) -> list[dict]:
    tasks = [
        (path_a, path_b, cfg_idx, cfg, seed, draw_margin)
        for cfg_idx, cfg in enumerate(configs)
        for seed in seeds
    ]
    if jobs <= 1 or len(tasks) <= 1:
        return [_play_pair_job(t) for t in tasks]
    with ProcessPoolExecutor(max_workers=min(jobs, len(tasks))) as ex:
        return list(ex.map(_play_pair_job, tasks))


def summarize(res: dict) -> str:
    t = res["tally"]
    return (
        f"{res['A']} vs {res['B']} | 对称局 {t['A']}:{t['draw']}:{t['B']} (共 {len(res['pairs'])} 对 / {len(res['pairs']) * 2} 局) | "
        f"综合分 {res['avg_score'][0]:.2f} vs {res['avg_score'][1]:.2f} | "
        f"防守 {res['avg_defense'][0]:.2f}/{res['avg_defense'][1]:.2f}  "
        f"攻击 {res['avg_attack'][0]:.2f}/{res['avg_attack'][1]:.2f}"
    )


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="1v1 PvP 对战（全 3 配置 × 多种子 × 双向攻防）")
    ap.add_argument("files", nargs="*", help="可选：显式指定两个 bot 文件或短名；默认取 agents/submissions/*.py")
    ap.add_argument("--seeds", type=int, default=4, help="常规种子数量（默认 4，对应 3×4×2=24 局）")
    ap.add_argument("--seed-list", type=int, nargs="+", default=None, help="显式指定种子列表（覆盖 --seeds）")
    ap.add_argument("--no-tiebreak", action="store_true", help="禁用 50%% 得分率时自动加赛")
    ap.add_argument("--draw-margin", type=float, default=elo.DEFAULT_DRAW_MARGIN,
                    help="对称分差平局阈值（默认 0.005）")
    ap.add_argument("--jobs", type=int, default=min(4, os.cpu_count() or 1), help="并行工作进程数")
    ap.add_argument("--record-replay", action="store_true",
                    help="额外为每个配置的首个种子录制双向回放至 replays/")
    ap.add_argument("--out", default="", help="输出 JSON 路径（默认 results/duel_<时间戳>_A_vs_B.json）")
    args = ap.parse_args()

    files = list(args.files)
    if not files:
        files = sorted(glob.glob(os.path.join(ROOT, "agents", "submissions", "*.py")))
    if len(files) != 2:
        print(f"请将两个参赛 bot 放入 agents/submissions/<模型名>.py，或通过命令行传入两个路径（当前找到 {len(files)} 个）。")
        print("AI 入口指南：AI_README.md；聊天模式题面：prompts/task.txt")
        sys.exit(2)

    label_a, path_a = resolve_bot(files[0])
    label_b, path_b = resolve_bot(files[1])

    cfg_all = json.load(open(os.path.join(ROOT, "config", "format.json"), encoding="utf-8"))
    configs = cfg_all["configs"]
    if args.seed_list:
        used_seeds = list(args.seed_list)
    else:
        base_seeds = list(cfg_all.get("seeds") or [1, 2, 3, 4])
        while len(base_seeds) < max(1, args.seeds):
            base_seeds.append(base_seeds[-1] + 1)
        used_seeds = base_seeds[: max(1, args.seeds)]

    print(f"== 主赛：{label_a} vs {label_b}（{len(configs)} 配置 × {len(used_seeds)} 种子 × 双向攻防）==")
    t0 = time.time()
    pairs = run_pairs(path_a, path_b, configs, used_seeds, args.draw_margin, args.jobs)
    res = elo.aggregate_match(label_a, label_b, pairs)
    print(summarize(res))

    t = res["tally"]
    if not args.no_tiebreak and elo.is_half_score(t["A"], t["draw"], len(res["pairs"])):
        tb_seeds = elo.tiebreak_seeds(cfg_all, used_seeds, count=4)
        print(f"== 加赛（得分率 50.0%）：追加 {len(tb_seeds)} 种子 {tb_seeds} ==")
        extra = run_pairs(path_a, path_b, configs, tb_seeds, args.draw_margin, args.jobs)
        res = elo.aggregate_match(label_a, label_b, res["pairs"] + extra)
        used_seeds = used_seeds + tb_seeds
        print("== 合并结果：" + summarize(res) + " ==")

    replays: list[str] = []
    if args.record_replay:
        rep_dir = os.path.join(ROOT, "replays", f"duel_{time.strftime('%Y%m%d-%H%M%S')}_{label_a}_vs_{label_b}")
        os.makedirs(rep_dir, exist_ok=True)
        seed0 = used_seeds[0]
        eng_sha = replay_mod.sha256_file(os.path.join(ROOT, "engine", "engine.py"))
        for cfg_idx, cfg in enumerate(configs):
            for tag, att_l, att_p, def_l, def_p in (
                ("fw", label_a, path_a, label_b, path_b),
                ("sw", label_b, path_b, label_a, path_a),
            ):
                _, data = replay_mod.play_with_replay(
                    att_p, def_p, seed=seed0, attacker_label=att_l, defender_label=def_l,
                    engine_sha=eng_sha, **cfg,
                )
                rpath = os.path.join(rep_dir, f"replay_cfg{cfg_idx}_seed{seed0}_{tag}.json")
                replay_mod.save_replay(rpath, data)
                replays.append(safe_rel(rpath))
        res["replays"] = replays
        print(f"已录制 {len(replays)} 份回放：{safe_rel(rep_dir)}")

    res["seeds"] = used_seeds
    res["configs"] = configs
    res["elapsed_s"] = round(time.time() - t0, 2)

    out_path = args.out or os.path.join(
        ROOT, "results", f"duel_{time.strftime('%Y%m%d-%H%M%S')}_{label_a}_vs_{label_b}.json"
    )
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    with open(out_path, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(res, fh, ensure_ascii=False, indent=2)

    # 分配置概览
    print("\n分配置汇总：")
    for cfg_idx, cfg in enumerate(configs):
        sub = [p for p in res["pairs"] if p["config_idx"] == cfg_idx]
        sa = sum(p["pair_score_a"] for p in sub) / len(sub) * 100.0
        sb = 100.0 - sa
        print(f"  cfg#{cfg_idx} (C={cfg['cache_size']:>2},K={cfg['universe']:>4},N={cfg['length']}) -> "
              f"{label_a} {sa:6.2f} vs {label_b} {sb:6.2f}")

    print("\n== 结论 ==")
    tf = res["tally"]
    if tf["A"] == tf["B"]:
        sa, sb = res["avg_score"]
        note = "综合分持平" if sa == sb else (
            f"综合分 {label_a if sa > sb else label_b} 领先（{max(sa, sb):.2f} vs {min(sa, sb):.2f}）"
        )
        print(f"  {label_a} 与 {label_b} 战平（{tf['A']}:{tf['draw']}:{tf['B']}），{note}")
    else:
        win, lose = (label_a, label_b) if tf["A"] > tf["B"] else (label_b, label_a)
        print(f"  {win} 胜 {lose}（{max(tf['A'], tf['B'])}:{tf['draw']}:{min(tf['A'], tf['B'])}，共 {len(res['pairs'])} 对）")
    print(f"  犯规：{label_a}={res['faults']['A']}，{label_b}={res['faults']['B']}  耗时 {res['elapsed_s']:.2f}s")
    print(f"  战报：{safe_rel(out_path)}")


if __name__ == "__main__":
    main()
