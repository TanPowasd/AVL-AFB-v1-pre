# -*- coding: utf-8 -*-
"""单场对抗脚本：支持单向（A 攻 B 守）或双向互换（--swap），可指定配置与种子。

用法：
  python scripts/run_match.py --a agents/submissions/my_agent.py --b lru --swap --seeds 11,23
  python scripts/run_match.py --a agents/submissions/my_agent.py --b --self --swap --config-idx 0 --seed-count 2
  python scripts/run_match.py --a lru --b fifo --config-idx 0 --seeds 1 2
"""
from __future__ import annotations

import argparse
from dataclasses import asdict
import glob
import json
import os
import random
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "engine"))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
from engine import play  # noqa: E402
import policy  # noqa: E402
import spar  # noqa: E402


EXTRA_PRESETS = {
    "starter": os.path.join(ROOT, "agents", "starter.py"),
}


def parse_seeds(raw: list[str] | None) -> list[int]:
    if not raw:
        return []
    out: list[int] = []
    for token in raw:
        for part in token.split(","):
            part = part.strip()
            if part:
                out.append(int(part))
    return out


def resolve_agent(spec: str) -> tuple[str, str]:
    """解析短名或路径，返回 (label, abs_path)。"""
    if spec in spar.PRESETS:
        path = spar.PRESETS[spec]
        policy.check_agent_path(path)
        return spec, os.path.abspath(path)
    if spec in EXTRA_PRESETS:
        path = EXTRA_PRESETS[spec]
        policy.check_agent_path(path)
        return spec, os.path.abspath(path)
    if policy.is_contestant_workspace():
        policy.check_agent_path(spec)
        cand = spec
    else:
        hits = glob.glob(spec)
        cand = hits[0] if hits else spec
        policy.check_agent_path(cand)
    if not os.path.isfile(cand):
        raise policy.PolicyError(f"找不到策略文件：{spec}")
    abs_p = os.path.abspath(cand)
    return os.path.splitext(os.path.basename(abs_p))[0], abs_p


def safe_rel(path: str) -> str:
    try:
        rel = os.path.relpath(path, ROOT).replace("\\", "/")
    except ValueError:
        return os.path.basename(path)
    if rel.startswith("../..") or os.path.isabs(rel):
        return os.path.basename(path)
    return rel


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="缓存攻防单场对抗：A vs B（支持 --swap 双向攻防）")
    ap.add_argument("--a", required=True, help="选手 A（默认作攻击方）：路径或短名")
    ap.add_argument("--b", default="", help="对手 B（默认作防守方）：路径、短名或 self（也可改用 --self 标志）")
    ap.add_argument("--self", dest="mirror_self", action="store_true", help="以选手 A 自身镜像作为对手 B")
    ap.add_argument("--swap", action="store_true", help="互换攻防角色，每配置×每种子打双向两局")
    ap.add_argument("--config-idx", type=int, default=None,
                    help="指定 config/format.json 的配置索引（0/1/2）；默认运行全部配置")
    ap.add_argument("--seeds", nargs="+", default=None,
                    help="指定种子（支持逗号或空格分隔，如 11,23 或 11 23）；默认随机抽样")
    ap.add_argument("--seed-count", type=int, default=2,
                    help="未指定 --seeds 时抽取的随机种子数（默认 2）")
    ap.add_argument("--out", default="", help="输出目录（默认 results/<时间戳>/）")
    ap.add_argument("--unrestricted", action="store_true", help=argparse.SUPPRESS)
    args = ap.parse_args()

    cfg_all = json.load(open(os.path.join(ROOT, "config", "format.json"), encoding="utf-8"))
    all_configs = cfg_all["configs"]
    if args.config_idx is not None:
        if not (0 <= args.config_idx < len(all_configs)):
            ap.error(f"--config-idx 超出范围：应为 0..{len(all_configs) - 1}")
        selected = [(args.config_idx, all_configs[args.config_idx])]
    else:
        selected = list(enumerate(all_configs))

    if args.seeds is not None:
        try:
            seeds = parse_seeds(args.seeds)
        except ValueError:
            ap.error("--seeds 必须为整数列表")
        if not seeds or len(set(seeds)) != len(seeds):
            ap.error("种子列表不能为空或重复")
    else:
        if args.seed_count < 1:
            ap.error("--seed-count 必须为正整数")
        seeds = random.SystemRandom().sample(range(1, 2**31), args.seed_count)

    try:
        if args.unrestricted and policy.is_contestant_workspace():
            raise policy.PolicyError("选手工作区不支持 --unrestricted。")
        label_a, path_a = resolve_agent(args.a)
        if args.mirror_self or args.b in ("self", "--self"):
            label_b, path_b = f"{label_a}(镜像)", path_a
        elif not args.b:
            raise policy.PolicyError("请通过 --b 指定对手，或传入 --self 进行镜像对局。")
        else:
            label_b, path_b = resolve_agent(args.b)
        policy.check_pair(path_a, path_b, unrestricted=args.unrestricted)
        out_dir = args.out or os.path.join(
            ROOT, "results", f"{time.strftime('%Y%m%d-%H%M%S')}-{time.time_ns()}"
        )
        policy.check_output_dir(out_dir, "results")
    except policy.PolicyError as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(2)

    print(f"对阵：{label_a} vs {label_b}  模式：{'双向攻防(--swap)' if args.swap else '单向(A攻B守)'}")
    print(f"配置：{[i for i, _ in selected]}  种子：{seeds}")

    games = []
    a_att_scores: list[float] = []
    b_def_scores: list[float] = []
    a_def_scores: list[float] = []
    b_att_scores: list[float] = []
    tally = {"A": 0, "B": 0, "draw": 0}
    faults_a = 0
    faults_b = 0
    t0 = time.time()

    for cfg_idx, cfg in selected:
        for seed in seeds:
            r_ab = play(path_a, path_b, seed=seed, **cfg)
            att_a = 1.0 - r_ab.def_score
            a_att_scores.append(att_a)
            b_def_scores.append(r_ab.def_score)
            faults_a += r_ab.faults["attacker"]
            faults_b += r_ab.faults["defender"]
            games.append({
                **asdict(r_ab),
                "config_idx": cfg_idx,
                "seed": seed,
                "direction": "A->B",
                "attacker": label_a,
                "defender": label_b,
                "attacker_file": safe_rel(path_a),
                "defender_file": safe_rel(path_b),
                "att_score": att_a,
            })
            print(
                f"  cfg#{cfg_idx} (C={cfg['cache_size']:>2},K={cfg['universe']:>4},N={cfg['length']}) "
                f"seed={seed}  {label_a}->{label_b}  "
                f"miss={r_ab.def_misses}/{r_ab.opt_misses}  "
                f"def={r_ab.def_score:.4f}  att={att_a:.4f}  "
                f"faults=({r_ab.faults['attacker']},{r_ab.faults['defender']})"
            )

            if args.swap:
                r_ba = play(path_b, path_a, seed=seed, **cfg)
                att_b = 1.0 - r_ba.def_score
                a_def_scores.append(r_ba.def_score)
                b_att_scores.append(att_b)
                faults_b += r_ba.faults["attacker"]
                faults_a += r_ba.faults["defender"]
                games.append({
                    **asdict(r_ba),
                    "config_idx": cfg_idx,
                    "seed": seed,
                    "direction": "B->A",
                    "attacker": label_b,
                    "defender": label_a,
                    "attacker_file": safe_rel(path_b),
                    "defender_file": safe_rel(path_a),
                    "att_score": att_b,
                })
                diff = r_ba.def_score - r_ab.def_score
                if abs(diff) <= 0.005:
                    tally["draw"] += 1
                elif diff > 0:
                    tally["A"] += 1
                else:
                    tally["B"] += 1
                print(
                    f"  cfg#{cfg_idx} (C={cfg['cache_size']:>2},K={cfg['universe']:>4},N={cfg['length']}) "
                    f"seed={seed}  {label_b}->{label_a}  "
                    f"miss={r_ba.def_misses}/{r_ba.opt_misses}  "
                    f"def={r_ba.def_score:.4f}  att={att_b:.4f}  "
                    f"faults=({r_ba.faults['attacker']},{r_ba.faults['defender']})"
                )

    elapsed = time.time() - t0
    summary: dict = {
        "mode": "swap" if args.swap else "single",
        "official": False,
        "A": {"label": label_a, "file": safe_rel(path_a), "faults": faults_a},
        "B": {"label": label_b, "file": safe_rel(path_b), "faults": faults_b},
        "configs": [{"index": i, **c} for i, c in selected],
        "seeds": seeds,
        "elapsed_s": round(elapsed, 2),
        "games": games,
    }

    print(f"\n== 汇总（耗时 {elapsed:.2f}s，共 {len(games)} 局）==")
    if args.swap:
        a_def_avg = 100.0 * sum(a_def_scores) / len(a_def_scores)
        a_att_avg = 100.0 * sum(a_att_scores) / len(a_att_scores)
        a_tot = 0.5 * a_def_avg + 0.5 * a_att_avg
        b_def_avg = 100.0 * sum(b_def_scores) / len(b_def_scores)
        b_att_avg = 100.0 * sum(b_att_scores) / len(b_att_scores)
        b_tot = 0.5 * b_def_avg + 0.5 * b_att_avg
        summary["A"].update({"score": round(a_tot, 4), "defense": round(a_def_avg, 4), "attack": round(a_att_avg, 4)})
        summary["B"].update({"score": round(b_tot, 4), "defense": round(b_def_avg, 4), "attack": round(b_att_avg, 4)})
        summary["tally"] = tally
        print(f"  {label_a:<18s} score={a_tot:6.2f}/100  defense={a_def_avg:6.2f}  attack={a_att_avg:6.2f}  faults={faults_a}")
        print(f"  {label_b:<18s} score={b_tot:6.2f}/100  defense={b_def_avg:6.2f}  attack={b_att_avg:6.2f}  faults={faults_b}")
        print(f"  对称局胜平负（A:平:B）= {tally['A']}:{tally['draw']}:{tally['B']}")
    else:
        a_att_avg = 100.0 * sum(a_att_scores) / len(a_att_scores)
        b_def_avg = 100.0 * sum(b_def_scores) / len(b_def_scores)
        summary["A"]["attack"] = round(a_att_avg, 4)
        summary["B"]["defense"] = round(b_def_avg, 4)
        print(f"  攻击方 {label_a}: attack={a_att_avg:6.2f}/100  faults={faults_a}")
        print(f"  防守方 {label_b}: defense={b_def_avg:6.2f}/100  faults={faults_b}")

    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, "match.json")
    policy.check_output_dir(out_path, "results")
    with open(out_path, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(summary, fh, ensure_ascii=False, indent=2)
    print(f"记录已保存：{safe_rel(out_path)}")


if __name__ == "__main__":
    main()
