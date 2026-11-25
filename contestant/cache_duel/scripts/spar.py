# -*- coding: utf-8 -*-
"""缓存攻防陪练自测：让你的 bot 与公开基线互为攻守对打。

用法：
  python scripts/spar.py --list
  python scripts/spar.py agents/submissions/my_agent.py lru
  python scripts/spar.py agents/submissions/my_agent.py --all --seed-count 4 --jobs 4
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
import json
import os
import random
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "engine"))
from engine import play  # noqa: E402
import policy  # noqa: E402

PRESETS = {
    "fifo": os.path.join(ROOT, "agents", "baselines", "fifo.py"),
    "marking": os.path.join(ROOT, "agents", "baselines", "marking.py"),
    "rand": os.path.join(ROOT, "agents", "baselines", "rand.py"),
    "lru": os.path.join(ROOT, "agents", "baselines", "lru.py"),
}
ORDER = ["fifo", "marking", "rand", "lru"]
DESC = {
    "fifo": "FIFO 防守 + 80/20 热点攻击",
    "marking": "随机化 Marking 防守 + 随机排列扫描攻击",
    "rand": "随机驱逐防守 + 2C 均匀随机攻击",
    "lru": "LRU 防守 + C+1 循环扫描攻击",
}
LOCAL_DEFAULT_SEEDS = 4


def resolve(name: str, unrestricted: bool = False) -> str:
    if name in PRESETS:
        if not unrestricted:
            policy.check_agent_path(PRESETS[name])
        return PRESETS[name]
    if not unrestricted:
        policy.check_agent_path(name)
    if os.path.isfile(name):
        return os.path.abspath(name)
    raise SystemExit(f"未知陪练：{name}（用 --list 查看可选公开陪练）")


def _score_game(raw_score: float, cand_fault: int, cand_dead: bool, opp_fault: int, opp_dead: bool) -> float:
    if cand_dead or cand_fault > 0:
        return 0.0
    if opp_dead or opp_fault > 0:
        return 1.0
    return raw_score


def _play_pair(args):
    mine_abs, opp_abs, c, s = args
    r_def = asdict(play(opp_abs, mine_abs, seed=s, **c))
    r_att = asdict(play(mine_abs, opp_abs, seed=s, **c))
    def_sc = _score_game(
        r_def["def_score"],
        r_def["faults"]["defender"],
        bool(r_def["faults"].get("defender_dead")),
        r_def["faults"]["attacker"],
        bool(r_def["faults"].get("attacker_dead")),
    )
    att_sc = _score_game(
        1.0 - r_att["def_score"],
        r_att["faults"]["attacker"],
        bool(r_att["faults"].get("attacker_dead")),
        r_att["faults"]["defender"],
        bool(r_att["faults"].get("defender_dead")),
    )
    faults = (
        r_def["faults"]["defender"]
        + r_att["faults"]["attacker"]
        + (1 if r_def["faults"].get("defender_dead") else 0)
        + (1 if r_att["faults"].get("attacker_dead") else 0)
    )
    return def_sc, att_sc, faults


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="缓存攻防公开陪练自测")
    ap.add_argument("mine", nargs="?", default="")
    ap.add_argument("opponent", nargs="?", default="lru")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--seeds", type=int, nargs="+", help="指定种子重放；默认每次随机抽样")
    ap.add_argument("--seed-count", type=int, default=None,
                    help=f"本地陪练随机种子数，默认 {LOCAL_DEFAULT_SEEDS}（正式评测默认 64）")
    ap.add_argument("--jobs", type=int, default=min(4, os.cpu_count() or 1), help="并行对局线程数")
    ap.add_argument("--unrestricted", action="store_true", help=argparse.SUPPRESS)
    ns = ap.parse_args()

    if ns.unrestricted and policy.is_contestant_workspace():
        print("选手工作区不支持 --unrestricted。", file=sys.stderr)
        sys.exit(2)
    if ns.jobs < 1:
        ap.error("--jobs 必须为正整数")

    if ns.list:
        print("可选公开陪练：")
        for k in ORDER:
            print(f"  {k:<10s} {DESC[k]}  <{os.path.relpath(PRESETS[k], ROOT)}>")
        return
    if not ns.mine:
        ns.mine = policy.default_submission_path()
        if not ns.mine:
            print("可选公开陪练：")
            for k in ORDER:
                print(f"  {k:<10s} {DESC[k]}  <{os.path.relpath(PRESETS[k], ROOT)}>")
            print("\n未检测到提交文件；请传入路径，或保存至 ../submissions/cache_duel.py（或 agents/submissions/my_agent.py）。")
            return

    cfg_data = json.load(open(os.path.join(ROOT, "config", "format.json"), encoding="utf-8"))
    configs = cfg_data["configs"]
    if ns.seed_count is None:
        ns.seed_count = LOCAL_DEFAULT_SEEDS
    if ns.seed_count < 1:
        ap.error("--seed-count 必须为正整数")
    seeds = ns.seeds if ns.seeds is not None else random.SystemRandom().sample(range(1, 2**31), ns.seed_count)
    if not seeds or len(set(seeds)) != len(seeds):
        ap.error("种子列表不能为空或重复")
    print("本次场景种子：", seeds)
    targets = ORDER if ns.all else [ns.opponent]

    try:
        if not ns.unrestricted:
            policy.check_agent_path(ns.mine)
            for t in targets:
                policy.check_opponent(ns.mine, resolve(t, unrestricted=False))
        else:
            for t in targets:
                resolve(t, unrestricted=True)
    except policy.PolicyError as e:
        print(str(e), file=sys.stderr)
        sys.exit(2)

    if not os.path.isfile(ns.mine):
        print(f"找不到文件：{ns.mine}", file=sys.stderr)
        sys.exit(2)

    mine_abs = os.path.abspath(ns.mine)
    rows = []
    for name in targets:
        opp_abs = resolve(name, unrestricted=ns.unrestricted)
        t0 = time.time()
        tasks = [(mine_abs, opp_abs, c, s) for c in configs for s in seeds]
        if ns.jobs <= 1 or len(tasks) <= 1:
            outcomes = [_play_pair(task) for task in tasks]
        else:
            with ThreadPoolExecutor(max_workers=min(ns.jobs, len(tasks))) as ex:
                outcomes = list(ex.map(_play_pair, tasks))
        def_scores = [o[0] for o in outcomes]
        att_scores = [o[1] for o in outcomes]
        faults = sum(o[2] for o in outcomes)
        dt = time.time() - t0
        d_avg = 100 * sum(def_scores) / len(def_scores)
        a_avg = 100 * sum(att_scores) / len(att_scores)
        tot = 0.5 * d_avg + 0.5 * a_avg
        print(f"vs {name:<9s}  score={tot:.2f}/100  defense={d_avg:.3f}  attack={a_avg:.3f}  faults={faults}  ({dt:.1f}s)")
        rows.append({"opp": name, "score": tot, "defense": round(d_avg, 4), "attack": round(a_avg, 4), "faults": faults, "sec": round(dt, 1)})

    out_dir = os.path.join(ROOT, "results", f"{time.strftime('%Y%m%d-%H%M%S')}-{time.time_ns()}")
    policy.check_output_dir(out_dir, "results")
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, "spar.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump({"scoring_version": "scoring_v2", "official": False, "max_score": 100, "mine": os.path.relpath(mine_abs, ROOT), "seeds": seeds, "rows": rows}, f, ensure_ascii=False, indent=2)
    print(f"\n记录已保存：{os.path.relpath(out_path, ROOT)}")


if __name__ == "__main__":
    main()
