# -*- coding: utf-8 -*-
"""cache_duel ELO + margin 计分、对称双局加赛与排行榜渲染。"""
from __future__ import annotations

DEFAULT_TIEBREAK_SEEDS = (173, 181, 191, 193, 197, 199, 211, 223)
DEFAULT_DRAW_MARGIN = 0.005  # 对称分差 |def_A - def_B| <= 0.005 (0.5 分) 判平


def pair_score(def_score_a: float, def_score_b: float) -> float:
    """计算 A 在对称双局（B攻A守 + A攻B守）中对 B 的综合得分 ∈ [0, 1]。

    公式：0.5 * (def_score_A + (1 - def_score_B))。
    满足严格零和：pair_score(a, b) + pair_score(b, a) == 1.0。
    """
    return 0.5 * (float(def_score_a) + (1.0 - float(def_score_b)))


def pair_outcome(
    def_score_a: float,
    def_score_b: float,
    draw_margin: float = DEFAULT_DRAW_MARGIN,
) -> tuple[str, float, float]:
    """判定一对对称双局的胜平负，返回 (outcome, elo_score_a, sym_score_a)。"""
    sa = pair_score(def_score_a, def_score_b)
    diff = float(def_score_a) - float(def_score_b)
    if abs(diff) <= float(draw_margin):
        return "draw", 0.5, sa
    if diff > 0:
        return "A", 1.0, sa
    return "B", 0.0, sa


def tiebreak_seeds(cfg_all: dict, used_seeds=(), count: int | None = None) -> list[int]:
    """返回尚未在常规对局中使用过的扩展加赛种子列表。"""
    used = {int(s) for s in used_seeds}
    raw = (cfg_all or {}).get("tiebreakSeeds") or DEFAULT_TIEBREAK_SEEDS
    out = [int(s) for s in raw if int(s) not in used]
    if count is not None:
        nxt = max(used | set(out) | {223}) + 1
        while len(out) < int(count):
            if nxt not in used:
                out.append(nxt)
            nxt += 1
        out = out[: int(count)]
    return out


def is_half_score(wins: int, draws: int, total_pairs: int) -> bool:
    """判定对抗是否恰好取得 50.0% 得分率（2×胜 + 平 == 总对称局数）。"""
    return total_pairs > 0 and (2 * int(wins) + int(draws) == int(total_pairs))


def expected(ra: float, rb: float) -> float:
    return 1.0 / (1.0 + 10.0 ** ((rb - ra) / 400.0))


def margin_factor(score_a: float, score_b: float, cap: float = 2.0) -> float:
    """按对称得分差放大 ELO 步长（差 0.5 时触及默认上限 2.0）。"""
    diff = abs(float(score_a) - float(score_b))
    return min(float(cap), 1.0 + diff * 2.0)


def update(
    ra: float,
    rb: float,
    score_a: float,
    margin: float = 1.0,
    k: float = 24.0,
) -> tuple[float, float]:
    ea = expected(ra, rb)
    delta_a = float(k) * float(margin) * (float(score_a) - ea)
    return ra + delta_a, rb - delta_a


def aggregate_match(
    label_a: str,
    label_b: str,
    pairs: list[dict],
    replays: list[str] = (),
) -> dict:
    """将多组对称双局（含加赛）汇总为统一战报结构。"""
    tally = {"A": 0, "B": 0, "draw": 0}
    a_def, a_att, a_tot = [], [], []
    b_def, b_att, b_tot = [], [], []
    faults_a = 0
    faults_b = 0
    for p in pairs:
        tally[p["outcome"]] += 1
        da = float(p["def_score_a"])
        db = float(p["def_score_b"])
        sa = pair_score(da, db)
        sb = 1.0 - sa
        a_def.append(da * 100.0)
        a_att.append((1.0 - db) * 100.0)
        a_tot.append(sa * 100.0)
        b_def.append(db * 100.0)
        b_att.append((1.0 - da) * 100.0)
        b_tot.append(sb * 100.0)
        faults_a += int(p.get("faults_a", 0))
        faults_b += int(p.get("faults_b", 0))

    n = max(1, len(pairs))
    return {
        "mode": "duel",
        "A": label_a,
        "B": label_b,
        "tally": tally,
        "avg_score": [round(sum(a_tot) / n, 3), round(sum(b_tot) / n, 3)],
        "avg_defense": [round(sum(a_def) / n, 3), round(sum(b_def) / n, 3)],
        "avg_attack": [round(sum(a_att) / n, 3), round(sum(b_att) / n, 3)],
        "faults": {"A": faults_a, "B": faults_b},
        "pairs": list(pairs),
        "replays": list(replays),
    }


def render_table(rows: list[dict]) -> str:
    cols = ["排名", "选手", "ELO", "胜", "平", "负", "综合分", "防守分", "攻击分", "均分差"]
    widths = [4, 22, 6, 4, 4, 4, 7, 7, 7, 7]
    line = "| " + " | ".join(cols) + " |"
    sep = "|" + "|".join("-" * (w + 1) for w in widths) + "|"
    body = []
    for i, r in enumerate(rows, 1):
        cells = [
            str(i),
            str(r["name"])[:22],
            f"{r['elo']:.0f}",
            str(r["w"]),
            str(r["d"]),
            str(r["l"]),
            f"{r['avg_score']:.2f}",
            f"{r['avg_defense']:.2f}",
            f"{r['avg_attack']:.2f}",
            f"{r['avg_margin']:+.2f}",
        ]
        body.append("| " + " | ".join(cells) + " |")
    return "\n".join([line, sep] + body)
