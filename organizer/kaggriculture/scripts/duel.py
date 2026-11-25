import argparse
import glob
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "engine"))
sys.path.insert(0, os.path.join(ROOT, "score"))
import arena  # noqa: E402
import elo  # noqa: E402
from loader import is_submission_path, load_agent, load_trusted  # noqa: E402

BASE = os.path.join(ROOT, "agents", "baselines")
REF = os.path.join(ROOT, "agents", "reference")
args = None  # set in main()


def is_reference(path):
    return "reference" in os.path.normpath(os.path.abspath(path)).split(os.sep)


def load_or_none(path):
    if is_reference(path):
        try:
            return load_trusted(path)
        except Exception as e:
            print("!! 对照组加载失败 %s：%s" % (os.path.basename(path), e))
            return None
    try:
        return load_agent(path)
    except Exception as e:
        print("!! 拒赛 %s：%s" % (os.path.basename(path), e))
        return None


def main():
    ap = argparse.ArgumentParser(description="一键对战：默认自动取 agents/submissions 里的两个文件")
    ap.add_argument("files", nargs="*", help="可选：显式指定 agent 文件路径")
    ap.add_argument("--seeds", type=int, default=8, help="A vs B 的种子数（每种子双方各坐一次先手）")
    ap.add_argument("--no-tiebreak", action="store_true",
                    help="禁用 50%% 得分率时自动使用扩展 8 种子加测 16 局")
    ap.add_argument("--anchor-seeds", type=int, default=0,
                    help="锚点体检种子数；0=不跑（官方对局只跑双方对战）")
    ap.add_argument("--champion-seeds", type=int, default=0,
                    help="冠军对照组种子数；0=不跑（组织方已取消对照组）")
    ap.add_argument("--champion-timeout", type=float, default=0.0,
                    help="覆盖对照组单步基础时限；0=官方口径（1s/步 + 60s 超时池）")
    global args
    args = ap.parse_args()

    files = args.files
    if not files:
        sub_root = os.path.join(ROOT, "agents", "submissions")
        if os.path.isdir(sub_root):
            cands = []
            for name in sorted(os.listdir(sub_root)):
                if name.startswith("_") or name.startswith("."):
                    continue
                full = os.path.join(sub_root, name)
                if is_submission_path(full):
                    cands.append(full)
            files = cands
    if len(files) != 2:
        print("请把两个模型产出的提交压缩包（.zip，兼容目录或 .py）存到 agents/submissions/<模型名>.zip（当前找到 %d 个）" % len(files))
        print("给 AI 的入口：AI_README.md —— 直接把本文件夹路径交给模型，让它从这份文件读起")
        print("粘贴版题面：prompts/task.txt（与 AI_README.md 同源）")
        return
    a = load_or_none(files[0])
    b = load_or_none(files[1])
    if not a or not b:
        return
    anchors = []
    for name in ("passive", "random_legal", "greedy_farm"):
        ag = load_or_none(os.path.join(BASE, name + ".py"))
        if ag:
            anchors.append((name, ag))

    print("== 主赛：%s vs %s ==" % (a[0], b[0]))
    base_cfg = json.load(open(os.path.join(ROOT, "config", "format.json"), encoding="utf-8"))
    used_seeds = arena_seeds(args.seeds)
    cfg = timeout_cfg(args)
    res = arena.run_match(a, b, seeds=used_seeds, cfg=cfg)
    print(arena.summarize_match(res))
    t0 = res["tally"]
    if not args.no_tiebreak and elo.is_half_score(t0["A"], t0["draw"], len(res["games"])):
        tb_seeds = elo.tiebreak_seeds(base_cfg, used_seeds)
        if tb_seeds:
            print("== 加赛（得分率 50.0%%）：追加 %d 种子（+%d 局） ==" % (len(tb_seeds), len(tb_seeds) * 2))
            extra = arena.run_match(a, b, seeds=tb_seeds, cfg=cfg)
            res = elo.aggregate_match(
                res["A"], res["B"], res["games"] + extra["games"],
                res.get("replays", []) + extra.get("replays", []),
            )
            print("== 合并结果：%s ==" % arena.summarize_match(res))
    detail = res["games"]
    slow = [(g["stats"][g["seat_a"]]["step_ms_max"], g["stats"][g["seat_a"]]["errors"]) for g in detail]
    a_err = sum(s[1] for s in slow)
    a_slow = max([s[0] for s in slow]) if slow else 0
    print("   %s 状态：异常步合计 %d，单步峰值 %.0fms" % (a[0], a_err, a_slow))
    path = arena.save_result(res)
    print("   战报:", path)

    # 官方对局只跑双方对战：锚点体检与冠军对照组默认关闭（加 --anchor-seeds / --champion-seeds 才跑）
    if args.anchor_seeds > 0:
        print("== 锚点局（体检，不计名次）==")
        for tag, ag in anchors:
            for side, agent in (("A", a), ("B", b)):
                r2 = arena.run_match(agent, ag, seeds=arena_seeds(args.anchor_seeds),
                                     cfg=timeout_cfg(args))
                line = arena.summarize_match(r2)
                print("  [%s] %s" % (side, line))
    if args.champion_seeds > 0:
        champion_round(a, b)
    verdict(res)


def champion_round(a, b):
    champ = load_or_none(os.path.join(REF, "champion.py"))
    if not champ:
        return
    print("== 对照组（RSIdemo冠军 elite+CFR，不计名次）==")
    seeds = arena_seeds(args.champion_seeds)
    for side, agent in (("A", a), ("B", b)):
        r3 = arena.run_match(agent, champ, seeds=seeds,
                             cfg=timeout_cfg(args, args.champion_timeout))
        print("  [%s] %s" % (side, arena.summarize_match(r3)))


def timeout_cfg(a, per_step=None):
    base = json.load(open(os.path.join(ROOT, "config", "format.json"), encoding="utf-8"))
    cfg = dict(base.get("game", {}))
    ts = per_step if per_step else base["match"]["actTimeoutS"]
    cfg["actTimeoutS"] = float(ts)
    return cfg


def arena_seeds(n):
    cfg = __import__("json").load(open(os.path.join(ROOT, "config", "format.json"), encoding="utf-8"))
    return cfg["seeds"][: max(1, n)]


def verdict(res):
    t = res["tally"]
    print("== 结论 ==")
    if t["A"] == t["B"]:
        ca, cb = res["avg_cash"][0], res["avg_cash"][1]
        cash_note = "场均现金持平" if ca == cb else ("场均现金 %s 领先（%.0f vs %.0f）" % (
            res["A"] if ca > cb else res["B"], max(ca, cb), min(ca, cb)))
        print("   %s 与 %s %d 局后仍战平（%d:%d:%d），%s" % (
            res["A"], res["B"], len(res["games"]), t["A"], t["draw"], t["B"], cash_note))
    else:
        win = res["A"] if t["A"] > t["B"] else res["B"]
        lose = res["B"] if t["A"] > t["B"] else res["A"]
        print("   %s 胜 %s（%d:%d:%d，共 %d 局）" % (
            win, lose, max(t["A"], t["B"]), t["draw"], min(t["A"], t["B"]), len(res["games"])))


if __name__ == "__main__":
    main()
