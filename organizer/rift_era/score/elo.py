"""8-seat ELO + log-ratio V margin rating & Markdown leaderboard renderer for rift_era."""
from __future__ import annotations

from collections import Counter
from fractions import Fraction
import math
from typing import Iterable, Mapping, Sequence

DEFAULT_INIT_ELO = 1500.0
DEFAULT_K = 24.0
DEFAULT_MARGIN_CAP = 2.0
DEFAULT_DRAW_LOG_EPS = 0.005


def to_fraction(value) -> Fraction:
    """Parse exact Fraction from Fraction, 'num/den', {'exact': ...}, {'$rational': ...}, or number."""
    if isinstance(value, Fraction):
        return value
    if isinstance(value, dict):
        if '$rational' in value:
            n, d = value['$rational']
            return Fraction(int(n), int(d))
        if 'exact' in value:
            return to_fraction(value['exact'])
        if 'V_exact' in value:
            return to_fraction(value['V_exact'])
    if isinstance(value, str) and '/' in value:
        n, d = value.split('/', 1)
        return Fraction(int(n), int(d))
    return Fraction(value)


def vtilde(v: Fraction) -> Fraction:
    """Positive monotone transform from rift_era rules 07 §3."""
    v = to_fraction(v)
    return v + 10 if v >= 0 else Fraction(100) / (10 - v)


def log_vtilde(v: Fraction) -> float:
    vt = vtilde(v)
    return math.log(vt.numerator) - math.log(vt.denominator)


def log_ratio(va: Fraction, vb: Fraction) -> float:
    """ln(vtilde(va) / vtilde(vb)) without floating overflow on huge rationals."""
    return log_vtilde(va) - log_vtilde(vb)


def expected(ra: float, rb: float) -> float:
    return 1.0 / (1.0 + 10.0 ** ((rb - ra) / 400.0))


def margin_factor(va: Fraction, vb: Fraction, cap: float = DEFAULT_MARGIN_CAP) -> float:
    """Scale-invariant margin multiplier in [1.0, cap] from |ln(vtilde(va)/vtilde(vb))|."""
    lr = abs(log_ratio(va, vb))
    return min(float(cap), 1.0 + lr * 1.5)


def update_pair(ra: float, rb: float, score_a: float, *, margin: float = 1.0,
                k: float = DEFAULT_K) -> tuple[float, float]:
    ea = expected(ra, rb)
    delta = float(k) * float(margin) * (float(score_a) - ea)
    return ra + delta, rb - delta


def update_8seat_match(
    ratings: Mapping[str, float],
    seat_results: Sequence[tuple[str, Fraction]],
    *,
    k: float = DEFAULT_K,
    cap: float = DEFAULT_MARGIN_CAP,
    draw_log_eps: float = DEFAULT_DRAW_LOG_EPS,
) -> dict[str, float]:
    """Order-invariant, exact zero-sum ELO update for an 8-seat match.

    Compares every cross-label pair (i, j) with distinct labels using exact V
    ratios and normalizes by (distinct_opponents * cross_pairs_between_pair)
    so 4v4 duels, 1v7 matches, and 8-way FFA share a consistent K scale.
    """
    out = {name: float(val) for name, val in ratings.items()}
    entries = [(str(label), to_fraction(v)) for label, v in seat_results]
    for label, _ in entries:
        out.setdefault(label, DEFAULT_INIT_ELO)

    distinct_labels = sorted({label for label, _ in entries})
    if len(distinct_labels) < 2:
        return out

    label_counts = Counter(label for label, _ in entries)
    distinct_opponents = len(distinct_labels) - 1
    deltas = {label: 0.0 for label in distinct_labels}

    for i in range(len(entries)):
        la, va = entries[i]
        for j in range(i + 1, len(entries)):
            lb, vb = entries[j]
            if la == lb:
                continue
            lr = log_ratio(va, vb)
            if abs(lr) <= draw_log_eps:
                score_a = 0.5
                mf = 1.0
            else:
                score_a = 1.0 if va > vb else 0.0
                mf = margin_factor(va, vb, cap=cap)
            ea = expected(out[la], out[lb])
            pair_count = label_counts[la] * label_counts[lb]
            weight = (float(k) / distinct_opponents) / pair_count
            delta = weight * mf * (score_a - ea)
            deltas[la] += delta
            deltas[lb] -= delta

    for label, d in deltas.items():
        out[label] += d
    return out


def render_table(rows: Iterable[Mapping[str, object]]) -> str:
    """Render a Markdown ELO & mean-V leaderboard table."""
    cols = ['排名', '策略', 'ELO', '胜', '平', '负', '场均 V', '最高 V', '故障']
    header = '| ' + ' | '.join(cols) + ' |'
    sep = '|---:|---|---:|---:|---:|---:|---:|---:|---:|'
    body = []
    for idx, row in enumerate(rows, 1):
        name = str(row.get('name', '?'))[:28]
        elo_v = float(row.get('elo', DEFAULT_INIT_ELO))
        w = int(row.get('w', 0))
        d = int(row.get('d', 0))
        l = int(row.get('l', 0))
        avg_v = float(row.get('avg_v', 0.0))
        best_v = float(row.get('best_v', avg_v))
        faults = int(row.get('faults', 0))
        body.append(
            f'| {idx} | {name} | {elo_v:.1f} | {w} | {d} | {l} | {avg_v:.3f} | {best_v:.3f} | {faults} |'
        )
    return '\n'.join([header, sep, *body])
