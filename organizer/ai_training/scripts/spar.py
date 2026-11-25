# -*- coding: utf-8 -*-
"""公开陪练自测脚本（交换座位对局；支持 .zip、含 main.py 的目录与单文件 .py）。

用法：
  python scripts/spar.py --list
  python scripts/spar.py ../submissions/ai_training.zip scaling --seed-count 2
  python scripts/spar.py agents/submissions/my_agent --all --seed-count 2
  python scripts/spar.py agents/submissions/my_agent.zip --solo --seed-count 2
"""
from __future__ import annotations

import argparse
import json
import math
import os
import random
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "engine"))
import archive  # noqa: E402
import engine  # noqa: E402
import policy  # noqa: E402

PRESETS = {
    "blank": os.path.join(ROOT, "agents", "baselines", "blank.py"),
    "scaling": os.path.join(ROOT, "agents", "baselines", "scaling.py"),
    "merchant": os.path.join(ROOT, "agents", "baselines", "merchant.py"),
}
SPECIAL = {
    "template": os.path.join(ROOT, "agents", "template"),
}
ORDER = ["blank", "scaling", "merchant"]
DESC = {
    "blank": "白板单轮盲训",
    "scaling": "重探针缩放律策略",
    "merchant": "重商业化 API 扩张策略",
}
SCALE = engine.VALUE_SCALE
METRIC_KEY = "value"
HAS_SOLO = True
DEFAULT_LOCAL_SEEDS = 4


def resolve(name: str) -> str:
    if name in PRESETS:
        policy.check_agent_path(PRESETS[name])
        return PRESETS[name]
    if name in SPECIAL:
        policy.check_agent_path(SPECIAL[name])
        return SPECIAL[name]
    cand = name
    if not archive.is_submission_path(cand):
        alt = os.path.join(ROOT, name)
        if archive.is_submission_path(alt):
            cand = alt
    policy.check_agent_path(cand)
    if archive.is_submission_path(cand):
        return os.path.abspath(cand)
    raise SystemExit(f"未知陪练或无效提交（需要 .zip、含 main.py 的目录或 .py）：{name}（用 --list 查看公开陪练）")


def _play_one(a: str, b: str, seed: int, ticks: int | None):
    kw = {}
    if ticks:
        kw["ticks"] = ticks
    r = engine.play(a, b, seed, **kw)
    r.pop("replay", None)
    return r


def _accum_faults(agg: dict, fl: dict) -> None:
    agg["timeouts"] += int(fl.get("timeouts", 0))
    agg["bad_json"] += int(fl.get("bad_json", 0))
    agg["dead"] += 1 if fl.get("dead") else 0
    agg["illegal"] += int(fl.get("illegal", 0))
    agg["max_step_s"] = round(max(agg["max_step_s"], float(fl.get("max_step_s", 0.0))), 3)


def _fmt_faults(fl: dict) -> str:
    return (
        f"faults(to={fl['timeouts']},bad={fl['bad_json']},"
        f"dead={fl['dead']},ill={fl['illegal']})"
    )


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="公开陪练自测（支持 .zip / 目录 / .py）")
    ap.add_argument("mine", nargs="?", default="")
    ap.add_argument("opponent", nargs="?", default=ORDER[-1])
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--solo", action="store_true", help="跑单人赛模式（挑战内置 SOLO_NPC，检验最高真实能力 C）")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--seeds", type=int, nargs="+", help="指定种子重放；默认每次随机抽样")
    ap.add_argument(
        "--seed-count",
        type=int,
        default=None,
        help=f"随机种子数，本地自测默认 {DEFAULT_LOCAL_SEEDS}（正式评分默认 64）",
    )
    ap.add_argument("--ticks", type=int, default=0, help="可选：缩短局数快速调试接口")
    ap.add_argument("--unrestricted", action="store_true", help=argparse.SUPPRESS)
    ns = ap.parse_args()

    if ns.unrestricted and policy.is_contestant_workspace():
        print("选手工作区不支持 --unrestricted。", file=sys.stderr)
        sys.exit(2)

    if ns.list:
        print("可选公开陪练：")
        for k in ORDER:
            print(f"  {k:<10s} {DESC[k]}  <{os.path.relpath(PRESETS[k], ROOT)}>")
        if HAS_SOLO:
            print("  --solo     单人赛模式（挑战内置固定脚本 NPC 实验室，检验最高真实能力 C）")
        return
    if not ns.mine:
        ns.mine = policy.default_submission_path()
        if not ns.mine:
            print("可选公开陪练：")
            for k in ORDER:
                print(f"  {k:<10s} {DESC[k]}  <{os.path.relpath(PRESETS[k], ROOT)}>")
            print(
                "\n未检测到提交；请传入 .zip / 目录 / .py 路径，"
                "或打包至 ../submissions/ai_training.zip（或 agents/submissions/my_agent.zip）。"
            )
            return

    if ns.seed_count is None:
        ns.seed_count = DEFAULT_LOCAL_SEEDS
    if ns.seed_count < 1:
        ap.error("--seed-count 必须为正整数")
    seeds = ns.seeds if ns.seeds is not None else random.SystemRandom().sample(range(1, 2**31), ns.seed_count)
    if not seeds or len(set(seeds)) != len(seeds):
        ap.error("种子列表不能为空或重复")
    print("本次场景种子：", seeds, flush=True)
    ticks = ns.ticks if ns.ticks > 0 else None

    try:
        mine_abs = resolve(ns.mine)
    except policy.PolicyError as e:
        print(str(e), file=sys.stderr)
        sys.exit(2)

    if mine_abs.lower().endswith(".zip"):
        probs = archive.check_archive(mine_abs)
        if probs:
            print("压缩包校验失败：" + "; ".join(probs), file=sys.stderr)
            sys.exit(2)

    rows = []
    if HAS_SOLO and ns.solo:
        for idx, s in enumerate(seeds, 1):
            t0 = time.time()
            kw = {"inproc_b": True}
            if ticks:
                kw["ticks"] = ticks
            r = engine.play(mine_abs, engine.SOLO_NPC, s, **kw)
            r.pop("replay", None)
            dt = time.time() - t0
            c_val = r["C"][0]
            fl_raw = r["faults"][0]
            fl = {
                "timeouts": int(fl_raw.get("timeouts", 0)),
                "bad_json": int(fl_raw.get("bad_json", 0)),
                "dead": 1 if fl_raw.get("dead") else 0,
                "illegal": int(fl_raw.get("illegal", 0)),
                "max_step_s": round(float(fl_raw.get("max_step_s", 0.0)), 3),
            }
            print(
                f"[solo {idx}/{len(seeds)}] seed={s}  score={100 * c_val:.2f}/100  "
                f"C={c_val:.4f}  value={r['value'][0]:.1f}M  {_fmt_faults(fl)}  ({dt:.1f}s)",
                flush=True,
            )
            rows.append({
                "mode": "solo",
                "seed": s,
                "C": c_val,
                "score": 100 * c_val,
                "value": r["value"][0],
                "faults": fl,
                "sec": round(dt, 1),
            })
    else:
        targets = ORDER if ns.all else [ns.opponent]
        for t in targets:
            try:
                policy.check_opponent(mine_abs, resolve(t))
            except policy.PolicyError as e:
                print(str(e), file=sys.stderr)
                sys.exit(2)

        for name in targets:
            opp_abs = resolve(name)
            s_vals, my_vals, opp_vals = [], [], []
            my_faults = {"timeouts": 0, "bad_json": 0, "dead": 0, "illegal": 0, "max_step_s": 0.0}
            opp_faults = {"timeouts": 0, "bad_json": 0, "dead": 0, "illegal": 0, "max_step_s": 0.0}
            total_g = len(seeds) * 2
            done_g = 0
            t0 = time.time()
            for s in seeds:
                for seat in (0, 1):
                    a, b = (mine_abs, opp_abs) if seat == 0 else (opp_abs, mine_abs)
                    r = _play_one(a, b, s, ticks)
                    done_g += 1
                    mv = r[METRIC_KEY][seat]
                    ov = r[METRIC_KEY][1 - seat]
                    sc = 50.0 + 50.0 * math.tanh((mv - ov) / SCALE)
                    my_vals.append(mv)
                    opp_vals.append(ov)
                    s_vals.append(sc)
                    _accum_faults(my_faults, r["faults"][seat])
                    _accum_faults(opp_faults, r["faults"][1 - seat])
                    fl_m = r["faults"][seat]
                    print(
                        f"  [{name} {done_g}/{total_g}] seed={s} seat={seat}  "
                        f"V={mv:,.1f} vs {ov:,.1f}  s={sc:.1f}  "
                        f"ill={fl_m['illegal']} to={fl_m['timeouts']} dead={fl_m['dead']}  ({r['wall_s']:.1f}s)",
                        flush=True,
                    )
            dt = time.time() - t0
            s_avg = sum(s_vals) / len(s_vals)
            m_avg = sum(my_vals) / len(my_vals)
            o_avg = sum(opp_vals) / len(opp_vals)
            print(
                f"vs {name:<9s}  score={s_avg:.2f}/100  我均值={m_avg:,.1f}  对手均值={o_avg:,.1f}  "
                f"{_fmt_faults(my_faults)}  ({dt:.1f}s)",
                flush=True,
            )
            rows.append({
                "opp": name,
                "score": s_avg,
                "mine_mean": m_avg,
                "opp_mean": o_avg,
                "faults": my_faults,
                "opp_faults": opp_faults,
                "sec": round(dt, 1),
            })

    out_dir = os.path.join(ROOT, "results", f"{time.strftime('%Y%m%d-%H%M%S')}-{time.time_ns()}")
    policy.check_output_dir(out_dir, "results")
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, "spar.json")
    try:
        mine_rel = os.path.relpath(mine_abs, ROOT)
    except ValueError:
        mine_rel = mine_abs
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(
            {
                "scoring_version": "scoring_v2",
                "official": False,
                "max_score": 100,
                "mine": mine_rel,
                "mine_label": archive.submission_label(mine_abs),
                "seeds": seeds,
                "rows": rows,
            },
            f,
            ensure_ascii=False,
            indent=2,
        )
    print(f"\n记录已保存：{os.path.relpath(out_path, ROOT)}", flush=True)


if __name__ == "__main__":
    main()
