"""Complete-match assembly, formal world acceptance and the scoring finalizer.

``match_economy`` runs the same P1-P8 systems as the opening profile for the
whole clock (official 28,800 ticks) plus the referee national-power
collector. ``accepted_world`` draws atlas candidates and accepts one only
after a real 150-tick bootstrap of that candidate (P0-3). ``finalize_match``
computes exact V per nation; it certifies ``full_rules_accepted`` only when
the official clock ran to the end, every cycle was collected and no rule input
is still pending a mechanism. Release gates are untouched: a certified
finalizer is necessary, never sufficient, for an official run.
"""
from __future__ import annotations

from fractions import Fraction
import time

from .clock import Phase
from .determinism import engine_source_fingerprint, fingerprint, runtime_fingerprint
from .economy import assemble_economy
from .errors import InfrastructureFault
from .initialization import initialize
from .kernel import System
from .ledger import Account
from .national_power import NationalPower
from .world import BootstrapResult

MATCH_PROFILE = 'rift_full_match_v1'
BOOTSTRAP_TICKS = 150


def _collector_systems(state, economy, commands):
    collector = state.national_power
    return (System('national_power_begin', Phase.COMMANDS, lambda s, ctx: collector.begin_tick(ctx),
                   after=('material_tick',)),
            System('national_power_combat', Phase.COMBAT, collector.combat, after=('battle',)),
            System('national_power', Phase.SETTLEMENT, collector.settle_tick,
                   after=('industry_activity', 'storage_prices', 'migration_requests', 'consumption_receipts')))


def match_economy(catalog, initial, *, total_ticks: int = 28_800, debug: bool = False,
                  commerce: bool = True, fingerprint_each_tick: bool = True):
    """Full assembly. ``total_ticks`` below 28,800 is a practice clock."""
    if total_ticks % 40:
        raise InfrastructureFault('a match clock must hold four equal eras of whole cycles')
    cycles = total_ticks // 10

    def prepare(state):
        state.national_power = NationalPower(state, total_cycles=cycles)

    return assemble_economy(catalog, initial, total_ticks=total_ticks, debug=debug, commerce=commerce,
                            extra_systems=_collector_systems, prepare_state=prepare,
                            fingerprint_each_tick=fingerprint_each_tick)


def bootstrap_check(catalog, world, *, debug: bool = False) -> BootstrapResult:
    """Execute the real opening economy for 150 ticks on a candidate."""
    from .economy import opening_economy
    failures = []
    try:
        engine = opening_economy(catalog, initialize(catalog, world), total_ticks=BOOTSTRAP_TICKS, debug=debug)
        executed = 0
        for _ in range(BOOTSTRAP_TICKS):
            engine.step()
            executed += 1
        state = engine.state
        state.audit()
        for owner in sorted(state.initial.known):
            hub = f'{owner}-h0'
            if state.initial.inventory.balance(Account(owner, hub, 'available', 'food')) <= 0:
                failures.append(f'{owner}: food buffer exhausted')
            if state.initial.populations[hub].total_mU <= 0:
                failures.append(f'{owner}: population exhausted')
        if state.payments.arrears:
            failures.append('unpaid operating obligations')
    except InfrastructureFault as exc:
        return BootstrapResult(False, 0, (f'{type(exc).__name__}: {exc}',))
    return BootstrapResult(not failures, executed, tuple(failures))


def accepted_world(catalog, seed: int, *, debug: bool = False, report=None):
    """P0-3: atlas candidate accepted only by its own real 150-tick bootstrap."""
    from .world_atlas import generate_world
    attempts = []

    def bootstrap(world):
        started = time.monotonic()
        result = bootstrap_check(catalog, world, debug=debug)
        attempts.append({'attempt': world.attempt, 'accepted': result.accepted,
                         'ticks_executed': result.ticks_executed, 'failures': list(result.invariant_failures),
                         'wall_seconds': round(time.monotonic() - started, 3)})
        return result

    world = generate_world(seed, bootstrap)
    if report is not None:
        report.extend(attempts)
    return world


def finalize_match(engine) -> dict:
    """Formal finalizer. Reads the collector; never mints income or forgives debt."""
    state = engine.state
    collector = state.national_power
    if collector is None:
        raise InfrastructureFault('match assembly has no national-power collector')
    if engine.tick != engine.clock.total_ticks:
        raise InfrastructureFault('finalizer requires the complete clock')
    state.audit()
    values = collector.finalize(engine.clock)
    pending = collector.pending_components()
    outstanding = {p: {'sovereign_debt_book_ru_mU': state.sovereign_finance.debt_book(p),
                       'payables_book_ru_mU': state.payables.debt_book(p),
                       'loan_debt_book_ru_mU': state.contracts.loans.debt_book(p)} for p in sorted(values)}
    certified = engine.clock.official and not pending and all(
        row['cycles'] == collector.total_cycles for row in values.values())
    return {'scope': MATCH_PROFILE, 'full_rules_accepted': certified,
            'official_clock': engine.clock.official, 'pending_rule_inputs': list(pending),
            'nations': {p: {'V': row['V'], 'phi': row['phi'], 'era_means': list(row['era_means']),
                            'end_mean': row['end_mean'], 'last_cycle': row['last']} for p, row in values.items()},
            'outstanding_debt_unchanged': outstanding}


def run_match(seed: int, *, total_ticks: int = 28_800, world=None, orders=None, debug: bool = False,
              progress=None, accept_world: bool = True) -> dict:
    """Headless match (no sandboxed strategies): ``orders(engine, tick)`` may
    supply referee-side commands at decision ticks (reference bot, tests)."""
    from .catalog import Catalog
    from .world import generate_candidate
    catalog = Catalog()
    started = time.monotonic()
    attempts = []
    if world is None:
        world = accepted_world(catalog, seed, report=attempts) if accept_world else generate_candidate(seed)
    engine = match_economy(catalog, initialize(catalog, world), total_ticks=total_ticks, debug=debug,
                           fingerprint_each_tick=False)
    while engine.tick < engine.clock.total_ticks:
        tick = engine.tick
        batch = orders(engine, tick) if orders is not None and engine.clock.is_decision(tick) else None
        engine.step(batch or None)
        if progress is not None:
            progress(engine)
    summary = finalize_match(engine)
    return summary | {'seed': seed, 'ticks': engine.tick, 'map_version': world.generator_version,
                      'world_attempt': world.attempt, 'world_acceptance': attempts,
                      'state_hash': fingerprint(engine.state.snapshot()), 'engine_hash': engine_source_fingerprint(),
                      'runtime': runtime_fingerprint(), 'wall_seconds': round(time.monotonic() - started, 3)}


class SdkBotOrders:
    """In-process adaptor: each seat's SimpleBot sees only its filtered
    observation and the public catalog, exactly what the IPC init/obs carry."""
    def __init__(self, seats=None):
        # ``seats``: optional per-nation specs in sorted owner order; only
        # ``simple`` seats get a bot, ``none`` seats submit nothing.
        self.seats = tuple(seats) if seats is not None else None
        self.bots = {}
        self.economy = None

    def __call__(self, engine, tick):
        from ..sdk import SimpleBot
        from .economy import Economy
        from .determinism import primitive
        state = engine.state
        if self.economy is None or self.economy.s is not state:
            self.economy = Economy(state)
            catalog = primitive(state.catalog.tables)
            owners = sorted(state.initial.known)
            chosen = owners if self.seats is None else [p for p, seat in zip(owners, self.seats) if seat == 'simple']
            self.bots = {p: SimpleBot(catalog) for p in chosen}
        window = engine.clock.window(tick)
        result = {}
        for owner, bot in self.bots.items():
            obs = self.economy.observation(owner, tick=tick, window=window)
            orders = tuple(bot.decide(obs))
            if orders:
                result[owner] = orders
        return result
