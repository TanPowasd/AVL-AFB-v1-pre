"""cache_duel 循环赛。

用法:
  python tournament.py --bots agents/baselines --seeds 1 2 3 --out results/run.json

每对有序 (A 攻, B 守)，在每个配置 × 每个种子上各打一局（含自己打自己作参考，不计分）。
单个 bot 的总分 = 0.5 * 防守分 + 0.5 * 攻击分
  防守分 = 它当防守方时 def_score 的平均（对所有其他 bot 的攻击）
  攻击分 = 它当攻击方时 (1 - 对手 def_score) 的平均
"""
from __future__ import annotations

import argparse
import itertools
import json
import os
import sys
from concurrent.futures import ProcessPoolExecutor
from dataclasses import asdict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "engine"))
from engine import play  # noqa: E402

CONFIGS = [
    {"cache_size": 8, "universe": 64, "length": 3000},
    {"cache_size": 32, "universe": 256, "length": 5000},
    {"cache_size": 64, "universe": 4096, "length": 5000},
]


def _run(args):
    a, d, cfg, seed = args
    return asdict(play(a, d, seed=seed, **cfg))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bots", required=True, help="bot 目录，每个 .py 一个 bot")
    ap.add_argument("--seeds", type=int, nargs="+", default=[1, 2])
    ap.add_argument("--jobs", type=int, default=os.cpu_count() or 2)
    ap.add_argument("--out", default="")
    ns = ap.parse_args()

    bots = sorted(os.path.join(ns.bots, f) for f in os.listdir(ns.bots)
                  if f.endswith(".py") and not f.startswith("_"))
    jobs = [(a, d, c, s) for a, d in itertools.product(bots, bots)
            for c in CONFIGS for s in ns.seeds]
    with ProcessPoolExecutor(ns.jobs) as ex:
        results = list(ex.map(_run, jobs))

    name = lambda p: os.path.splitext(os.path.basename(p))[0]
    names = [name(b) for b in bots]
    mat: dict[tuple[str, str], list[float]] = {}
    for r in results:
        mat.setdefault((name(r["attacker"]), name(r["defender"])), []).append(r["def_score"])
    avg = {k: sum(v) / len(v) for k, v in mat.items()}

    table = []
    for b in names:
        others = [o for o in names if o != b] or [b]
        dfs = sum(avg[(o, b)] for o in others) / len(others)
        att = sum(1 - avg[(b, o)] for o in others) / len(others)
        table.append({"bot": b, "total": 0.5 * dfs + 0.5 * att, "defense": dfs, "attack": att})
    table.sort(key=lambda x: -x["total"])

    print(f"{'bot':<20}{'total':>8}{'defense':>9}{'attack':>8}")
    for row in table:
        print(f"{row['bot']:<20}{row['total']:>8.3f}{row['defense']:>9.3f}{row['attack']:>8.3f}")
    print("\n防守分矩阵（行=攻击方，列=防守方，值=OPT缺失/防守缺失）")
    print(" " * 14 + "".join(f"{n[:10]:>11}" for n in names))
    for a in names:
        print(f"{a[:13]:<14}" + "".join(f"{avg[(a, d)]:>11.3f}" for d in names))
    faults = [r for r in results if any(v for v in r["faults"].values())]
    if faults:
        print(f"\n有 {len(faults)} 局出现非法输出/超时，详见输出 JSON")

    if ns.out:
        os.makedirs(os.path.dirname(os.path.abspath(ns.out)), exist_ok=True)
        with open(ns.out, "w") as f:
            json.dump({"scoring_version": "legacy_v1", "official": False, "configs": CONFIGS, "seeds": ns.seeds, "table": table,
                       "matrix": {f"{a}|{d}": v for (a, d), v in avg.items()},
                       "matches": results}, f, ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
