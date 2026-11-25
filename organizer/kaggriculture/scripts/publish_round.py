#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把每轮的对外产出集中输出到 kaggriculture/publish/：

  · 比赛数据：league.json（裁判汇总，站点导入卡也能直接吃）、leaderboard.md、逐局数据.csv、战报.md
  · 回放：replays/roundN/*.json
  · 自动化登记文件：k4u-arena-import/1，站点「登记自动化」粘贴即登记

用法（cwd = kaggriculture/organizer）：
    python scripts/publish_round.py                  # 所有能找到的轮次
    python scripts/publish_round.py --rounds 3        # 只出第 3 轮
    python scripts/publish_round.py --out D:/x/publish
选手名映射：若 publish/site-names.json 存在（{"原始名": {"name": "站内名", "thinking": "high"}}），
登记文件里的 a.name 用站内名，原始名保留在 rawName，thinking 写在 a.thinking 供人工在下拉框选择。
"""
import argparse, csv, datetime, hashlib, io, json, os, shutil, sys

HERE = os.path.dirname(os.path.abspath(__file__))
ORG = os.path.dirname(HERE)
DEFAULT_OUT = os.path.join(os.path.dirname(ORG), "publish")
def _cn_1_to_99(n):
    d = ["", "一", "二", "三", "四", "五", "六", "七", "八", "九"]
    if 1 <= n <= 9:
        return d[n]
    if n == 10:
        return "十"
    if 11 <= n <= 19:
        return "十" + d[n - 10]
    if 20 <= n <= 99:
        return d[n // 10] + "十" + d[n % 10]
    return str(n)


CN = {i: _cn_1_to_99(i) for i in range(1, 100)}


def sha16(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for blk in iter(lambda: f.read(1 << 20), b""):
            h.update(blk)
    return h.hexdigest()[:16]


def kb(path):
    return "%.1f KB" % (os.path.getsize(path) / 1024.0)


def find_league_dir(n):
    for sub in ("league-head2head", "league"):
        d = os.path.join(ORG, "results", "round%d" % n, sub)
        if os.path.exists(os.path.join(d, "league.json")):
            return d
    return None


def mean(xs):
    xs = [float(x) for x in xs if x not in ("", None)]
    return (sum(xs) / len(xs)) if xs else 0.0


def publish_one(n, out_root, competition, name_map, all_rows, fields):
    src = find_league_dir(n)
    if not src:
        print("  跳过第 %d 轮：没找到 league.json" % n)
        return None
    dst = os.path.join(out_root, "round%d" % n)
    rp = os.path.join(dst, "回放")
    os.makedirs(rp, exist_ok=True)

    shutil.copy2(os.path.join(src, "league.json"), os.path.join(dst, "league.json"))
    lb = os.path.join(src, "leaderboard.md")
    if os.path.exists(lb):
        shutil.copy2(lb, os.path.join(dst, "leaderboard.md"))
    report = os.path.join(ORG, "results", "round%d" % n, "REPORT.md")
    if os.path.exists(report):
        shutil.copy2(report, os.path.join(dst, "战报.md"))

    rows = [r for r in all_rows if str(r.get("round")).strip() == str(n)]
    if rows and fields:
        with io.open(os.path.join(dst, "逐局数据.csv"), "w", encoding="utf-8-sig", newline="") as f:
            w = csv.DictWriter(f, fieldnames=fields)
            w.writeheader()
            w.writerows(rows)

    reps = []
    sr = os.path.join(ORG, "replays", "round%d" % n)
    if os.path.isdir(sr):
        for fn in sorted(os.listdir(sr)):
            if fn.lower().endswith(".json"):
                shutil.copy2(os.path.join(sr, fn), os.path.join(rp, fn))
                reps.append(fn)

    lg = json.load(io.open(os.path.join(dst, "league.json"), encoding="utf-8"))
    lgrows = lg.get("rows") or []
    seeds = lg.get("seeds") or []
    if len(lgrows) < 2:
        print("  第 %d 轮 league.json 少于两个选手，跳过登记文件" % n)
        return None
    a_raw, b_raw = str(lgrows[0]["name"]), str(lgrows[1]["name"])
    # 逐局 CSV 的 outcome 是相对该轮 CSV 里的 agent_a 计的；agent_a 不一定等于 league 第一行
    # （第 1、2 轮的 agent_a 是败方），所以必须按选手名归属，不能按字母 A/B 直接数。
    csv_a = (rows[0].get("agent_a") if rows else "") or ""
    if rows and csv_a not in (a_raw, b_raw):
        raise SystemExit("第 %d 轮：逐局 CSV 的 agent_a=%r 不在 league 的 %r / %r 中，无法归属胜负"
                         % (n, csv_a, a_raw, b_raw))
    flip = bool(rows) and csv_a == b_raw
    a_wins = sum(1 for r in rows if r.get("outcome") == ("B" if flip else "A"))
    b_wins = sum(1 for r in rows if r.get("outcome") == ("A" if flip else "B"))
    games = a_wins + b_wins
    margin = round((mean([r.get("margin") for r in rows]) if rows else 0) * (-1 if flip else 1))
    cash_a_col = "cash_b" if flip else "cash_a"
    cash_b_col = "cash_a" if flip else "cash_b"

    # 若该轮 league.json 含锚点（>2 行），它是「含锚点轮环」那一批，与「只跑双方」的官方口径不同批次：
    # 原始文件留档为 league-含锚点.json，再按逐局数据派生出双方口径的 league.json / leaderboard.md。
    mixed = len(lgrows) > 2
    if mixed:
        os.replace(os.path.join(dst, "league.json"), os.path.join(dst, "league-含锚点.json"))
        lbp = os.path.join(dst, "leaderboard.md")
        if os.path.exists(lbp):
            os.replace(lbp, os.path.join(dst, "leaderboard-含锚点.md"))
        other = a_raw if csv_a == b_raw else b_raw

        def won_by(r):
            o = r.get("outcome")
            return csv_a if o == "A" else (other if o == "B" else None)

        # 场均领先% 跟着官方口径：以联赛第一行（A）的现金为基准，A 为正、B 取相反数
        def lead_pct():
            return mean([(float(r[cash_a_col]) - float(r[cash_b_col])) / float(r[cash_a_col]) * 100
                         for r in rows if float(r.get(cash_a_col) or 0)]) if rows else 0

        def row_of(raw, cash_col, pct):
            w = sum(1 for r in rows if won_by(r) == raw)
            l = sum(1 for r in rows if won_by(r) not in (raw, None))
            d = sum(1 for r in rows if won_by(r) is None)
            cash = mean([r.get(cash_col) for r in rows]) if rows else 0
            return {"name": raw, "w": w, "d": d, "l": l,
                    "avg_cash": round(cash), "avg_margin_pct": round(pct, 2)}

        derived = {
            "format": "k4u-arena-head2head/1",
            "scope": "head2head",
            "games": len(rows),
            "seeds": seeds,
            "source": "results/exec_data.csv（该轮 A vs B 逐局）",
            "note": "该轮原始 league.json 含锚点对手（%d 行），与「只跑双方」的官方口径不同批次；"
                    "本文件按逐局数据派生双方口径，不含 ELO（ELO 随对手集合变化）" % len(lgrows),
            "rows": [row_of(a_raw, cash_a_col, lead_pct()), row_of(b_raw, cash_b_col, -lead_pct())],
        }
        with io.open(os.path.join(dst, "league.json"), "w", encoding="utf-8") as f:
            json.dump(derived, f, ensure_ascii=False, indent=2)
            f.write("\n")
        io.open(os.path.join(dst, "leaderboard.md"), "w", encoding="utf-8").write(
            "| 排名 | 选手 | 胜 | 平 | 负 | 场均现金 | 场均领先% |\n|---|---|---|---|---|---|---|\n" +
            "\n".join("| %d | %s | %d | %d | %d | %s | %+.1f |" % (
                i + 1, r["name"], r["w"], r["d"], r["l"], "{:,}".format(r["avg_cash"]),
                r["avg_margin_pct"]) for i, r in enumerate(derived["rows"])) + "\n")

    def side(raw, wins, cash_key, elo_row):
        m = (name_map or {}).get(raw) or {}
        return {
            "name": m.get("name") or raw,
            "rawName": raw,
            "thinking": m.get("thinking", ""),
            "cashAvg": round(mean([r.get(cash_key) for r in rows])) if rows else elo_row.get("avg_cash"),
            "gamesWon": wins,
            "eloFromLeague": round(float(elo_row.get("elo") or 0), 1),
        }

    payload = {
        "format": "k4u-arena-import/1",
        "competition": competition,
        "sourceKey": "%s:round%d:%s-vs-%s:%dseeds" % (competition, n, a_raw, b_raw, len(seeds)),
        "playedAt": datetime.datetime.fromtimestamp(
            os.path.getmtime(os.path.join(dst, "league.json"))).strftime("%Y-%m-%dT%H:%M:%S"),
        "note": "第%s轮轮环 · %d 种子双座位" % (CN.get(n, n), len(seeds)),
        "winner": "A" if a_wins > b_wins else ("B" if b_wins > a_wins else "draw"),
        "games": games,
        "aWins": a_wins,
        "bWins": b_wins,
        "margin": margin,
        "a": side(a_raw, a_wins, cash_a_col, lgrows[0]),
        "b": side(b_raw, b_wins, cash_b_col, lgrows[1]),
        "rawCsvAgentA": csv_a,
        "seeds": seeds,
    }
    # 一致性校验：league.json 的场均现金必须与逐局数据对得上（历史上出现过两份不同批次的产物混放）
    warn = []
    for key, lgrow in (("a", lgrows[0]), ("b", lgrows[1])):
        got = payload[key]["cashAvg"]
        want = float(lgrow.get("avg_cash") or 0)
        if want and abs(float(got or 0) - want) > 1:
            warn.append("%s 场均现金：逐局 %s vs league %s" % (
                payload[key]["rawName"], got, round(want)))
    if mixed:
        print("  ℹ️ 第%d轮原始 league.json 含 %d 行（带锚点），已留档为 league-含锚点.json；"
              "league.json/leaderboard.md 改为按逐局数据派生的双方口径" % (n, len(lgrows)))
    elif warn:
        print("  ⚠️ 第%d轮 league.json 与逐局数据不一致：%s（登记文件以逐局数据为准）" % (n, "；".join(warn)))

    imp = os.path.join(dst, "自动化登记文件.json")
    with io.open(imp, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
        f.write("\n")

    files = ["league.json"] + [x for x in ("leaderboard.md", "战报.md", "逐局数据.csv")
                              if os.path.exists(os.path.join(dst, x))]
    lines = ["# 第%s轮产出（自动生成，勿手改）" % CN.get(n, n), "",
             "生成时间：%s" % datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "",
             "来源：%s、results/exec_data.csv、replays/round%d/" % (
                 os.path.relpath(src, ORG).replace(os.sep, "/"), n), "",
             "| 文件 | 大小 | sha256(前16位) |", "|---|---|---|"]
    for name in files:
        p = os.path.join(dst, name)
        lines.append("| %s | %s | %s |" % (name, kb(p), sha16(p)))
    for name in reps:
        p = os.path.join(rp, name)
        lines.append("| 回放/%s | %s | %s |" % (name, kb(p), sha16(p)))
    lines += ["| 自动化登记文件.json | %s | %s |" % (kb(imp), sha16(imp)), "",
              "## 上站三步", "",
              "1. k4u.top → 比赛 → （管理视图）对局回放 → 逐份上传 回放/*.json（每份 ≤ 8 MB）。",
              "2. 登记自动化 → 粘贴 自动化登记文件.json 全文 → 核对预览里的名字与思考强度下拉框 → 登记。",
              "3. 若名字要改成站内名，改 publish/site-names.json 后重跑本脚本（或直接在预览框里改）。", "",
              "## 本轮口径", "",
              "- 双方：%s vs %s（原始选手名；逐局 CSV 的 agent_a=%s，outcome 相对它计）" % (
                  a_raw, b_raw, csv_a or "—"),
              "- 局比分：%d : %d（共 %d 局，%d 个种子双座位）" % (a_wins, b_wins, games, len(seeds)),
              "- 场均现金：%s / %s；场均净胜：%s" % (
                  payload["a"]["cashAvg"], payload["b"]["cashAvg"], margin),
              "- 登记文件里的名字：%s / %s（thinking：%s / %s）" % (
                  payload["a"]["name"], payload["b"]["name"],
                  payload["a"]["thinking"] or "未标注", payload["b"]["thinking"] or "未标注")]
    if mixed:
        lines += ["", "> 说明：本轮原始 league.json 是**含锚点对手的轮环**（%d 行），与官方「只跑双方」口径不同批次。" % len(lgrows),
                  "> 原始文件留档在 league-含锚点.json 与 leaderboard-含锚点.md；",
                  "> 本目录的 league.json / leaderboard.md / 逐局数据.csv / 自动化登记文件.json 全部为**双方 16 局**口径。"]
    elif warn:
        lines += ["", "> ⚠️ 本轮的 league.json 与逐局数据不一致，登记文件以**逐局数据**为准：" + "；".join(warn),
                  "> 需要人工确认哪一批是正式口径（league.json 里的 ELO 也按它算，登记文件里标为 eloFromLeague）。"]
    io.open(os.path.join(dst, "产出说明.md"), "w", encoding="utf-8").write("\n".join(lines) + "\n")
    print("  第%s轮 → %s（回放 %d 份，逐局 %d 行，%d : %d）" % (
        CN.get(n, n), os.path.relpath(dst, out_root), len(reps), len(rows), a_wins, b_wins))
    return dst


def main():
    ap = argparse.ArgumentParser(description="把每轮比赛数据、回放与自动化登记文件输出到 publish/")
    ap.add_argument("--rounds", default="", help="逗号分隔，缺省=自动扫描 results/round*")
    ap.add_argument("--out", default=DEFAULT_OUT, help="输出目录，缺省 kaggriculture/publish")
    ap.add_argument("--competition", default="kaggriculture", help="站点比赛 slug")
    args = ap.parse_args()

    if args.rounds:
        rounds = [int(x) for x in args.rounds.split(",") if x.strip()]
    else:
        rounds = []
        base = os.path.join(ORG, "results")
        if os.path.isdir(base):
            for d in sorted(os.listdir(base)):
                if d.startswith("round") and d[5:].isdigit():
                    rounds.append(int(d[5:]))
    if not rounds:
        print("没有找到任何 round* 目录")
        return 1

    name_map = {}
    nm = os.path.join(args.out, "site-names.json")
    if os.path.exists(nm):
        name_map = json.load(io.open(nm, encoding="utf-8"))
    else:
        print("  提示：没有 %s，登记文件将使用原始选手名" % os.path.relpath(nm, os.path.dirname(ORG)))

    all_rows, fields = [], []
    csv_path = os.path.join(ORG, "results", "exec_data.csv")
    if os.path.exists(csv_path):
        with io.open(csv_path, encoding="utf-8-sig", newline="") as f:
            rd = csv.DictReader(f)
            fields = rd.fieldnames or []
            all_rows = list(rd)

    os.makedirs(args.out, exist_ok=True)
    print("输出目录：%s" % args.out)
    made = []
    for n in rounds:
        d = publish_one(n, args.out, args.competition, name_map, all_rows, fields)
        if d:
            made.append(d)
    print("完成：%d 轮" % len(made))
    return 0


if __name__ == "__main__":
    sys.exit(main())
