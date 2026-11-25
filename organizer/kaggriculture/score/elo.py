"""ELO + margin 计分、加赛辅助与榜单渲染。"""

DEFAULT_TIEBREAK_SEEDS = (173, 181, 191, 193, 197, 199, 211, 223)


def tiebreak_seeds(cfg_all, used_seeds=()):
    """返回尚未在常规对局中使用过的扩展加赛种子列表。"""
    used = {int(s) for s in used_seeds}
    raw = cfg_all.get("tiebreakSeeds") or DEFAULT_TIEBREAK_SEEDS
    return [int(s) for s in raw if int(s) not in used]


def is_half_score(wins, draws, games):
    """判定对抗是否恰好取得 50.0% 得分率（2×胜 + 平 == 总局数）。"""
    return games > 0 and (2 * int(wins) + int(draws) == int(games))


def aggregate_match(label_a, label_b, games, replays=()):
    """将多局（含加赛）结果汇总为标准 arena.run_match 结构。"""
    tally = {"A": 0, "B": 0, "draw": 0}
    for g in games:
        tally[g["outcome"]] += 1
    cash_a = [g["cash"][g["seat_a"]] for g in games]
    cash_b = [g["cash"][1 - g["seat_a"]] for g in games]
    return {
        "mode": "match",
        "A": label_a,
        "B": label_b,
        "tally": tally,
        "avg_cash": [round(sum(cash_a) / len(cash_a), 1), round(sum(cash_b) / len(cash_b), 1)],
        "games": list(games),
        "replays": list(replays),
    }


def expected(ra, rb):
    return 1.0 / (1.0 + 10 ** ((rb - ra) / 400.0))


def update(ra, rb, score_a, margin=1.0, k=24.0):
    ea = expected(ra, rb)
    eb = 1.0 - ea
    delta_a = k * margin * (score_a - ea)
    return ra + delta_a, rb - delta_a


def margin_factor(cash_w, cash_l, cap=2.0):
    base = max(1.0, abs(cash_w), abs(cash_l))
    pct = abs(cash_w - cash_l) / base * 100.0
    return min(cap, 1.0 + pct / 50.0)


def render_table(rows):
    cols = ["排名", "选手", "ELO", "胜", "平", "负", "场均现金", "场均领先%"]
    widths = [4, 24, 7, 4, 4, 4, 10, 10]
    line = "| " + " | ".join(cols) + " |"
    sep = "|" + "|".join("-" * (w + 1) for w in widths) + "|"
    body = []
    for i, r in enumerate(rows, 1):
        cells = [str(i), str(r["name"])[:24], "%.0f" % r["elo"], str(r["w"]), str(r["d"]),
                 str(r["l"]), "%.0f" % r["avg_cash"], "%+.1f" % r["avg_margin_pct"]]
        body.append("| " + " | ".join(cells) + " |")
    return "\n".join([line, sep] + body)
