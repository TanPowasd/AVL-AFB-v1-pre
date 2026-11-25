"""Pure five-pillar arithmetic, exact action debits and trajectory scoring.

Rank comparisons use exact positive ratios. Decimal score intervals are display
and export representations, so tiny real losses never become ties internally.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal, localcontext, ROUND_FLOOR, ROUND_CEILING
from fractions import Fraction

from .catalog import Catalog
from .errors import InvariantViolation
from .numeric import Q, clip


def sqrt_q(value: Fraction) -> Fraction:
    """Non-conserved welfare metric only; 50 decimal digits, frozen algorithm."""
    if value < 0:
        raise ValueError("negative square root")
    with localcontext() as ctx:
        ctx.prec = 50
        return Q((Decimal(value.numerator) / Decimal(value.denominator)).sqrt())


def population_pillar(population_mU: int, support_mU: int, *, homeland: bool,
                       hold_cycles: int, food: Fraction, synthetic_share: Fraction,
                       consumer_goods: Fraction, medicine: Fraction, advanced_medicine: Fraction,
                       water: Fraction, electricity: Fraction, combat: Fraction,
                       blockade: Fraction, pollution: Fraction, remediation: Fraction) -> Fraction:
    if population_mU == 0:
        return Q(0)
    support = Q(support_mU, 1000)
    legitimacy = clip(Q(1, 2) + support / 2, Q(1, 2), Q(1)) if homeland else clip(
        Q(15, 100) + Q(35, 100) * support * min(Q(1), Q(hold_cycles, 40)), Q(15, 100), Q(1, 2))
    qual = food
    if synthetic_share > Q(3, 10):
        qual = min(food, 1 - Q(3, 10) * (synthetic_share - Q(3, 10)) / Q(7, 10))
    f = Q(4, 5) * (qual / Q(4, 5)) ** 2 if qual < Q(4, 5) else Q(4, 5) + Q(1, 5) * min(Q(1), (qual - Q(4, 5)) / Q(1, 5))
    med = clip(medicine, Q(0), Q(1)) + clip(advanced_medicine, Q(0), Q(1))
    life = clip(f * (Q(55, 100) + Q(35, 100) * sqrt_q(clip(consumer_goods, Q(0), Q(2))) +
                          Q(1, 4) * sqrt_q(med) + Q(15, 100) * min(Q(1), water, electricity)), Q(0), Q(3, 2))
    security = security_quality(combat, blockade, pollution, remediation)
    return 100 * legitimacy * Q(population_mU, 200000) * life * security


def security_quality(combat: Fraction, blockade: Fraction, pollution: Fraction, remediation: Fraction) -> Fraction:
    """07 Qsec (also read by 10 C25)."""
    return clip(1 - Q(2, 5) * combat - Q(3, 10) * blockade -
                Q(1, 2) * (1 - Q(1, 2) * clip(remediation, Q(0), Q(1))) * pollution, Q(0), Q(1))


def eta_sink(physical_mU: Fraction, sink_debt_mU: Fraction,
             consumption20_mU: Fraction, cycles: int = 20) -> Fraction:
    """r7 (decision 0014): C is the mean over the cycles that already exist in
    the 20-cycle window (``cycles`` = 1..20), not a zero-padded mean20."""
    if type(cycles) is not int or not 1 <= cycles <= 20:
        raise ValueError("eta consumption window must hold 1..20 cycles")
    chi = (physical_mU + sink_debt_mU) / max(Q(1000), consumption20_mU / cycles)
    return Q(1) if chi <= 6 else max(Q(0), 1 - (chi - 6) / 6)


def action_loss(quantities_mU: Mapping[str, int | Fraction], weights: Mapping[str, Fraction]) -> Fraction:
    if any(q < 0 for q in quantities_mU.values()):
        raise ValueError("negative action consumption")
    return max(Q(1, 1000), sum((Q(q) / 1000 * weights[g] for g, q in quantities_mU.items()), Q(0)))


def industrial_pillar(va: Fraction, destroyed: Fraction, actions: Fraction,
                       net_transit: Fraction, nfa_ru: Fraction) -> Fraction:
    return Q(35, 100) * (va - destroyed - actions + Q(5, 100) * net_transit + nfa_ru / 1000)


def control_pillar(regional_sigma: Fraction, connected_book: Fraction,
                    satellites: int, population_mU: int, powered_slots: int,
                    readiness: Fraction) -> Fraction:
    military_cap = Q(15, 100) * Q(population_mU, 1000) + Q(1, 2) * powered_slots
    return regional_sigma + Q(1, 100) * connected_book + 2 * min(8, satellites) + Q(1, 5) * min(military_cap, readiness)


def technology_pillar(catalog: Catalog, known: frozenset[str], active: Mapping[str, Fraction],
                     licensed: Mapping[str, Fraction]) -> Fraction:
    spec = catalog.tables['scoring']['pillars']['tech']
    total = Q(0)
    for tid in known:
        if tid in catalog.technologies:
            weight = spec['tier_weights'][str(catalog.technologies[tid]['tier'])]
        elif tid in catalog.milestones:
            weight = spec['milestone_weights'][tid]
        else:
            raise InvariantViolation(f"unknown scoring technology: {tid}")
        threshold = spec['active_scale_rules'][tid]['theta']
        scale = active.get(tid, Q(0)) + Q(1, 2) * licensed.get(tid, Q(0))
        if scale < 0:
            raise InvariantViolation("negative active scale")
        total += weight * (Q(15, 100) + Q(85, 100) * min(Q(1), scale / threshold))
    return Q(3, 5) * total


def international_pillar(exports_weighted20: Fraction, credit: Fraction,
                          aligned_city_weight: Fraction, aid_weighted20: Fraction) -> Fraction:
    return Q(8, 100) * exports_weighted20 + Q(5, 2) * credit * aligned_city_weight + Q(5, 100) * aid_weighted20


@dataclass(frozen=True)
class Pillars:
    population: Fraction
    industry: Fraction
    control: Fraction
    technology: Fraction
    international: Fraction

    @property
    def total(self) -> Fraction:
        return (self.population / 4 + self.industry / 4 + self.control / 5 +
                Q(15, 100) * (self.technology + self.international))


def sustainability(*, maintenance_debts: tuple[Fraction, ...],
                    shortage20: tuple[Fraction, ...], unrest_population_mU: int,
                    controlled_population_mU: int, defaults: int,
                    default_last40: bool, peg_break_last40: bool) -> Fraction:
    maint = min(Q(3, 10), Q(5, 100) * sum(maintenance_debts) / max(1, len(maintenance_debts)))
    shortage = min(Q(3, 10), sum(shortage20[-20:], Q(0)) / 20)
    unrest = min(Q(1, 4), Q(1, 2) * Q(unrest_population_mU, max(1000, controlled_population_mU)))
    insolvency = min(Q(1, 4), Q(5, 100) * defaults) + Q(1, 10) * (int(default_last40) + int(peg_break_last40))
    return max(Q(35, 100), 1 - maint - shortage - unrest - insolvency)


def adjusted_end(value: Fraction, phi: Fraction) -> Fraction:
    if not Q(35, 100) <= phi <= 1:
        raise ValueError("invalid sustainability")
    return max(value, Q(0)) * phi + min(value, Q(0)) * (2 - phi) - 10 * (1 - phi)


class Trajectory:
    """Four equal eras plus the signed end window (07 §3).

    The official clock has 2880 cycles (eras of 720, end window 40). A practice
    clock with fewer cycles keeps the same weights with eras of total/4 cycles
    and an end window of min(40, total/4); it is never an official score.
    """
    def __init__(self, total_cycles: int = 2880):
        if total_cycles <= 0 or total_cycles % 4:
            raise ValueError("trajectory needs a positive multiple of four cycles")
        self.total_cycles = total_cycles
        self.era_length = total_cycles // 4
        self.cycles = 0
        self.era_sums = [Q(0)] * 4
        self.last40: deque[Fraction] = deque(maxlen=min(40, self.era_length))

    @property
    def official(self) -> bool:
        return self.total_cycles == 2880

    def commit(self, cycle: int, value: Fraction) -> None:
        if cycle != self.cycles or cycle >= self.total_cycles:
            raise InvariantViolation("duplicate, skipped or out-of-range scoring cycle")
        self.era_sums[cycle // self.era_length] += value
        self.last40.append(value)
        self.cycles += 1

    def era_means(self) -> tuple[Fraction, ...]:
        return tuple(s / self.era_length for s in self.era_sums)

    def end_mean(self) -> Fraction:
        return sum(self.last40, Q(0)) / self.last40.maxlen

    def value(self, phi: Fraction) -> Fraction:
        if self.cycles != self.total_cycles:
            raise InvariantViolation("formal trajectory requires all 2880 cycles")
        weights = (Q(8, 100), Q(14, 100), Q(24, 100), Q(29, 100))
        return sum((w * m for w, m in zip(weights, self.era_means())), Q(0)) + adjusted_end(self.end_mean(), phi) / 4

    def snapshot(self) -> dict:
        return {'total_cycles': self.total_cycles, 'cycles': self.cycles, 'era_sums': list(self.era_sums),
                'last40': list(self.last40)}


def positive_transform(value: Fraction) -> Fraction:
    return value + 10 if value >= 0 else 100 / (10 - value)


def relative_ratio(player: Fraction, shadow: Fraction) -> Fraction:
    return positive_transform(player) / positive_transform(shadow)


@dataclass(frozen=True)
class ScoreInterval:
    lower: Decimal
    upper: Decimal


def score_interval(ratio: Fraction, precision: int = 50) -> ScoreInterval:
    """Outward-rounded enclosure of 100/(1 + R**(-6/5)).

    Decimal.ln/exp are correctly rounded nearest-even; expand each by one ulp.
    All subsequent arithmetic uses explicitly directed contexts. Increasing
    precision refines the enclosure without losing the exact ratio.
    """
    if ratio <= 0 or precision < 16:
        raise ValueError("invalid score interval request")
    if ratio == 1:
        return ScoreInterval(Decimal(50), Decimal(50))
    with localcontext() as ctx:
        ctx.prec = precision
        ctx.Emax, ctx.Emin = 999_999_999, -999_999_999
        logs = [Decimal(n).ln() for n in (ratio.numerator, ratio.denominator)]
        lo0, hi0 = logs[0].next_minus(), logs[0].next_plus()
        lo1, hi1 = logs[1].next_minus(), logs[1].next_plus()
        ctx.rounding = ROUND_FLOOR
        log_lo = lo0 - hi1
        ctx.rounding = ROUND_CEILING
        log_hi = hi0 - lo1
        ctx.rounding = ROUND_FLOOR
        exp_arg_lo = -Decimal('1.2') * log_hi
        ctx.rounding = ROUND_CEILING
        exp_arg_hi = -Decimal('1.2') * log_lo
        exp_lo = exp_arg_lo.exp().next_minus()
        exp_hi = exp_arg_hi.exp().next_plus()
        ctx.rounding = ROUND_CEILING
        denom_hi = 1 + exp_hi
        ctx.rounding = ROUND_FLOOR
        denom_lo = 1 + exp_lo
        lower = Decimal(100) / denom_hi
        ctx.rounding = ROUND_CEILING
        upper = Decimal(100) / denom_lo
        return ScoreInterval(max(Decimal(0), lower), min(Decimal(100), upper))
