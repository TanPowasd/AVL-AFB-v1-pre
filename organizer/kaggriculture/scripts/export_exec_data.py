"""导出「执行过程数据」：两轮正式对局的逐局指标 CSV。

用法: python scripts/export_exec_data.py [--out results/exec_data.csv] [--rounds 1 2]
说明: 用冻结副本按官方口径（1s/步 + 60s 池、8 种子×双座位）现跑一遍并记录逐局指标。
      现金与胜负与历史记录逐字节一致（确定性）；calls/total_s/step_ms_max 为本次测量值。
"""
import argparse, csv, hashlib, json, os, platform, sys, time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "engine"))
import arena
from loader import load_agent

ROUNDS = {
    1: ["results/round1/frozen/dsf4.1.py", "results/round1/frozen/qwen4.py"],
    2: ["results/round2/frozen/dsf4.1high.py", "results/round2/frozen/qwen4.py"],
    3: ["results/round3/frozen/gptsol6.1xhigh.py", "results/round3/frozen/qwen4.py"],
}
FIELDS = ["calls", "errors", "first_error", "step_ms_max", "total_s",
          "overage_used_s", "overage_left_s", "timeout", "timeout_step"]


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="results/exec_data.csv")
    ap.add_argument("--rounds", type=int, nargs="*", default=sorted(ROUNDS))
    args = ap.parse_args()
    fj = json.load(open(os.path.join(ROOT, "config", "format.json"), encoding="utf-8"))
    cfg = dict(arena.DEFAULT_CONFIG)
    cfg["actTimeoutS"] = float(fj["match"]["actTimeoutS"])
    cfg["overageBudgetS"] = float(fj["match"]["overageBudgetS"])
    cfg["errorBudget"] = int(fj["match"]["errorBudget"])
    cfg["drawMarginPct"] = float(fj["match"]["drawMarginPct"])
    seeds = fj["seeds"]
    eng = sha256_file(os.path.join(ROOT, "engine", "rules.py"))
    out = os.path.abspath(os.path.join(ROOT, args.out))
    os.makedirs(os.path.dirname(out), exist_ok=True)
    cols = ["round", "agent_a", "agent_b", "sha_a", "sha_b", "engine_sha256", "seed",
            "seat_of_a", "outcome", "winner_seat", "reason", "cash_a", "cash_b", "margin", "final_day"]
    cols += ["a_" + f for f in FIELDS] + ["b_" + f for f in FIELDS]
    rows, t_all = [], time.time()
    for rnd in args.rounds:
        pa = os.path.join(ROOT, ROUNDS[rnd][0])
        pb = os.path.join(ROOT, ROUNDS[rnd][1])
        a, b = load_agent(pa), load_agent(pb)
        man = json.load(open(os.path.join(ROOT, "results/round%d/manifest.json" % rnd), encoding="utf-8"))
        shas = {s["file"]: s["sha256"] for s in man["submissions"]}
        t0 = time.time()
        res = arena.run_match(a, b, seeds=seeds, cfg=cfg)
        print("round %d: %s vs %s -> %s（%.1fs）" % (rnd, a[0], b[0], res["tally"], time.time() - t0))
        for g in res["games"]:
            sa = int(g["seat_a"])
            cash, st = list(g["cash"]), g["stats"]
            row = {"round": rnd, "agent_a": a[0], "agent_b": b[0],
                   "sha_a": shas.get(os.path.basename(pa), "")[:16],
                   "sha_b": shas.get(os.path.basename(pb), "")[:16],
                   "engine_sha256": eng[:16], "seed": g["seed"],
                   "seat_of_a": "A" if sa == 0 else "B", "outcome": g["outcome"],
                   "winner_seat": "draw" if g["winner"] < 0 else ("A" if g["winner"] == 0 else "B"),
                   "reason": g["reason"], "cash_a": cash[sa], "cash_b": cash[1 - sa],
                   "margin": round(abs(cash[0] - cash[1]), 1), "final_day": g["final_day"]}
            for tag, s in (("a", st[sa]), ("b", st[1 - sa])):
                for f in FIELDS:
                    row[tag + "_" + f] = s.get(f, "")
            rows.append(row)
    with open(out, "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.DictWriter(fh, fieldnames=cols)
        w.writeheader()
        for row in rows:
            w.writerow(row)
    print("写出 %d 行 × %d 列 -> %s（共 %.1fs）" % (len(rows), len(cols), out, time.time() - t_all))
    print("python=%s  os=%s" % (platform.python_version(), platform.platform()))


if __name__ == "__main__":
    main()