import argparse
import glob
import json
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "engine"))
sys.path.insert(0, os.path.join(ROOT, "score"))
import arena  # noqa: E402
from loader import is_submission_path, load_agent, load_trusted  # noqa: E402
import elo  # noqa: E402


def collect(patterns):
    files = []
    for p in patterns:
        hits = glob.glob(p)
        files.extend(hits if hits else [p])
    return sorted(set(os.path.abspath(x) for x in files if is_submission_path(x)))


def main():
    ap = argparse.ArgumentParser(description="全轮环联赛 + ELO 排行榜")
    ap.add_argument("agents", nargs="+",
                    help="文件/通配符；可直接把 agents/reference/champion.py 放进联赛")
    ap.add_argument("--seed-count", type=int, default=4)
    ap.add_argument("--out", default="")
    ap.add_argument("--timeout", type=float, default=0.0,
                    help="覆盖单步时限秒数；0=按 config/format.json（官方 1s）")
    ap.add_argument("--overage", type=float, default=0.0,
                    help="覆盖每局超时池秒数；0=按 config（官方 60s）")
    ap.add_argument("--relaxed", action="store_true",
                    help="仅开发调试：放宽到 3s/5s，不得计入正式成绩")
    args = ap.parse_args()

    cfg_all = json.load(open(os.path.join(ROOT, "config", "format.json"), encoding="utf-8"))
    seeds = cfg_all["seeds"][: args.seed_count]
    k = float(cfg_all["elo"]["K"])
    cap = float(cfg_all["elo"]["marginCap"])
    # 正式口径来自 config/format.json 的 match 块（单步 1s + 每局 60s 超时池）；
    # 旧实现硬编码 3s/5s，会把「每步超 1s 就该吃超时池」的判罚整个抹掉。
    match_cfg = dict(cfg_all.get("match") or {})
    step_s = float(args.timeout) if args.timeout else float(match_cfg.get("actTimeoutS", 1.0))
    over_s = float(args.overage) if args.overage else float(match_cfg.get("overageBudgetS", 60.0))
    print("[赛制] %s：单步 %.2fs + 超时池 %.0fs，%d 种子 x 双座位，ELO K=%g" %
          ("放宽(仅调试)" if args.relaxed else "官方", step_s, over_s, len(seeds), k))

    loaded = []
    for f in collect(args.agents):
        trusted = "reference" in os.path.normpath(f).split(os.sep)
        try:
            label, fn = load_trusted(f) if trusted else load_agent(f)
            loaded.append((label, fn, f, trusted))
        except Exception as e:
            print("!! 拒赛 %s: %s" % (f, e))
    if len(loaded) < 2:
        print("至少需要 2 个可加载 agent")
        return

    ratings = {name: float(cfg_all["elo"]["init"]) for name, _, _, _ in loaded}
    book = {name: {"w": 0, "d": 0, "l": 0, "cash": [], "margin": []} for name, _, _, _ in loaded}

    t0 = time.time()
    for i in range(len(loaded)):
        for j in range(i + 1, len(loaded)):
            la, fa, _, trust_a = loaded[i]
            lb, fb, _, trust_b = loaded[j]
            slow = trust_a or trust_b  # 冠军 CFR 尖峰：官方口径靠 60s 池兜住，仅 --relaxed 放宽
            cfg_pair = dict(match_cfg)
            cfg_pair["actTimeoutS"] = (5.0 if slow else 3.0) if args.relaxed else step_s
            cfg_pair["overageBudgetS"] = over_s
            res = arena.run_match((la, fa), (lb, fb), seeds=seeds, cfg=cfg_pair)
            for g in res["games"]:
                seat_a = g["seat_a"]
                ca = g["cash"][seat_a]
                cb = g["cash"][1 - seat_a]
                w = g["winner"]
                if w == -1:
                    score_la, mf = 0.5, 1.0
                else:
                    winner_is_la = (w == seat_a)
                    score_la = 1.0 if winner_is_la else 0.0
                    cw, cl = (ca, cb) if winner_is_la else (cb, ca)
                    mf = elo.margin_factor(cw, cl, cap)
                ratings[la], ratings[lb] = elo.update(ratings[la], ratings[lb], score_la, margin=mf, k=k)
                book[la]["cash"].append(ca)
                book[lb]["cash"].append(cb)
                diff = (ca - cb) / max(1.0, abs(ca), abs(cb)) * 100.0
                if score_la == 0.5:
                    book[la]["d"] += 1
                    book[lb]["d"] += 1
                    book[la]["margin"].append(diff)
                    book[lb]["margin"].append(-diff)
                else:
                    wnm, lnm = (la, lb) if score_la == 1.0 else (lb, la)
                    wc = diff if score_la == 1.0 else -diff
                    book[wnm]["w"] += 1
                    book[lnm]["l"] += 1
                    book[wnm]["margin"].append(abs(wc))
                    book[lnm]["margin"].append(-abs(wc))
            print("%s vs %s -> %s" % (la, lb, arena.summarize_match(res)))

    rows = []
    for name in ratings:
        b = book[name]
        rows.append({"name": name, "elo": ratings[name], "w": b["w"], "d": b["d"], "l": b["l"],
                     "avg_cash": sum(b["cash"]) / len(b["cash"]) if b["cash"] else 0,
                     "avg_margin_pct": sum(b["margin"]) / len(b["margin"]) if b["margin"] else 0.0})
    rows.sort(key=lambda r: -r["elo"])
    table = elo.render_table(rows)
    out_dir = args.out or os.path.join(ROOT, "results", time.strftime("%Y%m%d-%H%M%S"))
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, "leaderboard.md"), "w", encoding="utf-8") as f:
        f.write("# 排行榜（轮环 %d 种子/对局组，K=%g，margin 上限 x%g）\n\n" % (len(seeds), k, cap))
        f.write("- 对局用时 %.1fs；胜负按终局现金差>%d%%，否则判平\n\n" % (time.time() - t0, int(cfg_all["match"]["drawMarginPct"])))
        f.write(table + "\n")
    with open(os.path.join(out_dir, "league.json"), "w", encoding="utf-8") as f:
        json.dump({"scoring_version": "legacy_elo", "official": False, "rows": rows, "seeds": seeds}, f, ensure_ascii=False, indent=1)
    print("\n" + table)
    print("saved:", out_dir)


if __name__ == "__main__":
    main()
