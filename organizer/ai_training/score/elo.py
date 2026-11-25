# -*- coding: utf-8 -*-
"""前沿实验室 PvP 对战 ELO + 估值分差倍率计分、加赛辅助与 Markdown 榜单渲染。"""
from __future__ import annotations

import math
from typing import Any

DEFAULT_VALUE_SCALE = 12710.23
DEFAULT_TIEBREAK_SEEDS = (173, 181, 191, 193, 197, 199, 211, 223)
DEFAULT_SEED_POOL = (1, 2, 3, 5, 7, 11, 13, 17, 19, 23, 29, 31, 37, 41, 43, 47)


def pick_seeds(cfg_all: dict[str, Any] | None, count: int) -> list[int]:
    """按配置种子与确定性素数池生成 count 个互不重复的种子。"""
    count = max(1, int(count))
    seen: set[int] = set()
    out: list[int] = []
    for src in ((cfg_all or {}).get("seeds") or (), DEFAULT_SEED_POOL):
        for s in src:
            si = int(s)
            if si not in seen:
                seen.add(si)
                out.append(si)
                if len(out) >= count:
                    return out
    nxt = max(seen) + 1 if seen else 1
    while len(out) < count:
        if nxt not in seen:
            seen.add(nxt)
            out.append(nxt)
        nxt += 1
    return out


def tiebreak_seeds(cfg_all: dict[str, Any] | None, used_seeds=(), count: int | None = None) -> list[int]:
    """返回尚未在常规对局中使用过的扩展加赛种子列表。"""
    used = {int(s) for s in used_seeds}
    raw = (cfg_all or {}).get("tiebreakSeeds") or DEFAULT_TIEBREAK_SEEDS
    out = [int(s) for s in raw if int(s) not in used]
    if count is not None:
        return out[: max(0, int(count))]
    return out


def is_half_score(wins: int, draws: int, games: int) -> bool:
    """判定对抗是否恰好取得 50.0% 得分率（2×胜 + 平 == 总局数）。"""
    return games > 0 and (2 * int(wins) + int(draws) == int(games))


def expected(ra: float, rb: float) -> float:
    return 1.0 / (1.0 + 10.0 ** ((float(rb) - float(ra)) / 400.0))


def update(ra: float, rb: float, score_a: float, margin: float = 1.0, k: float = 24.0) -> tuple[float, float]:
    ea = expected(ra, rb)
    delta_a = float(k) * float(margin) * (float(score_a) - ea)
    return float(ra) + delta_a, float(rb) - delta_a


def margin_factor(val_w: float, val_l: float, scale: float = DEFAULT_VALUE_SCALE, cap: float = 2.0) -> float:
    """按终局股权估值分差与 VALUE_SCALE 计算 ELO 胜差倍率，落在 [1.0, cap]。"""
    sc = max(1e-6, float(scale))
    diff = abs(float(val_w) - float(val_l))
    return min(float(cap), 1.0 + math.tanh(diff / sc))


def adv_score(val_a: float, val_b: float, scale: float = DEFAULT_VALUE_SCALE) -> float:
    """与 scoring_v2 一致的对抗分（0..100，持平为 50）。"""
    sc = max(1e-6, float(scale))
    return 50.0 + 50.0 * math.tanh((float(val_a) - float(val_b)) / sc)


def judge_game(val_a: float, val_b: float, draw_margin_pct: float = 1.0) -> str:
    """单局胜负判定：估值差不超过较高绝对值的 draw_margin_pct% 时判平。"""
    va, vb = float(val_a), float(val_b)
    band = max(1e-6, (float(draw_margin_pct) / 100.0) * max(1.0, abs(va), abs(vb)))
    if abs(va - vb) <= band:
        return "draw"
    return "A" if va > vb else "B"


def aggregate_match(
    label_a: str,
    label_b: str,
    games: list[dict[str, Any]],
    replays: list[str] = (),
) -> dict[str, Any]:
    """将多局（含加赛）汇总为标准 PvP 对战战报结构。"""
    tally = {"A": 0, "B": 0, "draw": 0}
    vals_a, vals_b = [], []
    cs_a, cs_b = [], []
    advs_a, advs_b = [], []
    for g in games:
        tally[g["outcome"]] += 1
        sa = int(g["seat_a"])
        va = float(g["value"][sa])
        vb = float(g["value"][1 - sa])
        vals_a.append(va)
        vals_b.append(vb)
        cs_a.append(float(g["C"][sa]))
        cs_b.append(float(g["C"][1 - sa]))
        sc_a = float(g.get("adv_score_a", adv_score(va, vb)))
        advs_a.append(sc_a)
        advs_b.append(100.0 - sc_a)
    n = max(1, len(games))
    return {
        "mode": "pvp_match",
        "A": label_a,
        "B": label_b,
        "tally": tally,
        "avg_value": [round(sum(vals_a) / n, 2), round(sum(vals_b) / n, 2)],
        "avg_C": [round(sum(cs_a) / n, 4), round(sum(cs_b) / n, 4)],
        "avg_adv_score": [round(sum(advs_a) / n, 2), round(sum(advs_b) / n, 2)],
        "games": list(games),
        "replays": list(replays),
    }


def summarize_match(res: dict[str, Any]) -> str:
    t = res["tally"]
    av = res["avg_value"]
    ac = res["avg_C"]
    sc = res["avg_adv_score"]
    return (
        f"{res['A']} vs {res['B']}  胜:平:负 = {t['A']}:{t['draw']}:{t['B']}  "
        f"场均估值 {av[0]:.1f}M vs {av[1]:.1f}M  场均C {ac[0]:.4f} vs {ac[1]:.4f}  "
        f"对抗分 {sc[0]:.2f}:{sc[1]:.2f}"
    )


def render_table(rows: list[dict[str, Any]]) -> str:
    """渲染 Markdown 排行榜表格。"""
    cols = ["排名", "选手", "ELO", "胜", "平", "负", "场均估值(M)", "场均能力C", "场均对抗分"]
    widths = [4, 24, 7, 4, 4, 4, 12, 10, 10]
    header = "| " + " | ".join(cols) + " |"
    sep = "|" + "|".join("-" * (w + 1) for w in widths) + "|"
    body = []
    for i, r in enumerate(rows, 1):
        cells = [
            str(i),
            str(r["name"])[:24],
            f"{float(r['elo']):.0f}",
            str(int(r["w"])),
            str(int(r["d"])),
            str(int(r["l"])),
            f"{float(r.get('avg_value', 0.0)):.1f}",
            f"{float(r.get('avg_C', 0.0)):.4f}",
            f"{float(r.get('avg_adv_score', 50.0)):.2f}",
        ]
        body.append("| " + " | ".join(cells) + " |")
    return "\n".join([header, sep] + body)
