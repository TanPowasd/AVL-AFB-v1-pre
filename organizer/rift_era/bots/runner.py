"""Development runner for the tier bots (medium / advanced / super).

The engine CLI ``match --bot`` only knows the in-tree SimpleBot, and this branch
must not edit ``engine/``.  This runner drives the same public entry points the
CLI and ``strategy-match`` use (Catalog, world candidate / accepted_world,
initialize, match_economy, Economy observations, economic_development_init,
finalize_match) and never emits an official score.

Two seat transports:

* ``inproc`` (default): each tier's ``bot.py`` is imported by path into its own
  module namespace and receives exactly the init/obs dicts the IPC would carry.
* ``pipe``: every non-``none`` seat runs ``python3 main.py`` from its tier
  directory as a plain child process, driven by the engine's own
  ``StrategySession`` + ``MatchDriver`` (paging, window tags, time accounting).
  No kernel sandbox: development only, never official.

Examples::

    python3 -m organizer.rift_era.bots.runner --ticks 400 --legacy-map \
        --seats super,advanced,medium,simple,none,none,none,none --out run.json
    python3 -m organizer.rift_era.bots.runner --ticks 40 --legacy-map --seats super --transport pipe
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
TIERS = ('none', 'simple', 'medium', 'advanced', 'super')


def available_bots() -> tuple[str, ...]:
    names = list(TIERS)
    for child in sorted(HERE.iterdir()):
        if child.is_dir() and (child / 'bot.py').is_file() and child.name not in names:
            names.append(child.name)
    return tuple(names)


def load_tier(tier: str):
    """Import bots/<tier>/bot.py under a private module name (no cross-tier sharing)."""
    path = HERE / tier / 'bot.py'
    spec = importlib.util.spec_from_file_location(f'rift_tier_bot_{tier}', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class SimpleSeat:
    def __init__(self, init):
        from ..sdk import SimpleBot
        self.bot = SimpleBot(init.get('catalog', {}))

    def decide(self, obs):
        return self.bot.decide(obs)


def make_seat(tier: str, init: dict):
    if tier == 'simple':
        return SimpleSeat(init)
    return load_tier(tier).Bot(init)


def parse_seats(text: str) -> list[str]:
    valid = available_bots()
    seats = [s.strip() for s in text.split(',') if s.strip()]
    if len(seats) == 1:
        seats = seats * 8
    if len(seats) != 8 or any(s not in valid for s in seats):
        raise SystemExit(f'--seats needs 1 or 8 of {valid}')
    return seats


def build_engine(seed: int, ticks: int, legacy: bool):
    from ..engine.catalog import Catalog
    from ..engine.initialization import initialize
    from ..engine.match import accepted_world, match_economy
    from ..engine.world import generate_candidate
    catalog = Catalog()
    attempts = []
    started = time.monotonic()
    world = generate_candidate(seed) if legacy else accepted_world(catalog, seed, report=attempts)
    accept_s = time.monotonic() - started
    engine = match_economy(catalog, initialize(catalog, world), total_ticks=ticks, fingerprint_each_tick=False)
    return engine, world, attempts, accept_s


def pillar_summary(engine) -> dict:
    collector = engine.state.national_power
    result = {}
    for owner, rows in collector.rows.items():
        n = len(rows) or 1
        mean = {k: float(sum(getattr(r, k) for r in rows) / n)
                for k in ('population', 'industry', 'control', 'technology', 'international', 'total', 'va')}
        last = rows[-1]
        result[owner] = {
            'mean_pillars': {k: round(v, 4) for k, v in mean.items()},
            'last_pillars': {k: round(float(getattr(last, k)), 4)
                             for k in ('population', 'industry', 'control', 'technology', 'international', 'total',
                                       'va', 'nfa_ru')},
            'known_techs': sorted(engine.state.research[owner].known),
            # cycle-total trace every 10 cycles (100 ticks) for checkpoint tables
            'total_every_100_ticks': [round(float(r.total), 3) for r in rows[9::10]],
        }
    return result


def run_inproc(args, seats):
    from ..engine.driver import economic_development_init, economic_development_messages
    from ..engine.economy import Economy
    from ..engine.match import finalize_match, MATCH_PROFILE
    engine, world, attempts, accept_s = build_engine(args.seed, args.ticks, args.legacy_map)
    economy = Economy(engine.state)
    owners = sorted(engine.state.initial.known)
    bots, timing = {}, {}
    for owner, tier in zip(owners, seats):
        if tier == 'none':
            continue
        init = economic_development_init(economy, owner, engine.clock) | {'scope': MATCH_PROFILE}
        started = time.perf_counter()
        bots[owner] = make_seat(tier, init)
        timing[owner] = {'init_s': time.perf_counter() - started, 'windows': 0, 'total_s': 0.0, 'max_s': 0.0,
                         'orders': 0}
    started = time.monotonic()
    sim_s = 0.0
    while engine.tick < engine.clock.total_ticks:
        tick = engine.tick
        batch = {}
        if engine.clock.is_decision(tick):
            for owner, bot in bots.items():
                obs = economic_development_messages(economy, owner, tick, engine.clock)
                t0 = time.perf_counter()
                orders = bot.decide(obs)
                dt = time.perf_counter() - t0
                row = timing[owner]
                row['windows'] += 1
                row['total_s'] += dt
                row['max_s'] = max(row['max_s'], dt)
                row['orders'] += len(orders)
                if orders:
                    batch[owner] = tuple(orders)
        t0 = time.perf_counter()
        engine.step(batch or None)
        sim_s += time.perf_counter() - t0
        if args.progress and engine.tick % args.progress == 0:
            rows = engine.state.national_power.rows
            line = ' '.join(f'{p}:{float(rows[p][-1].total):.2f}' for p in owners if rows[p])
            print(json.dumps({'tick': engine.tick, 'wall_s': round(time.monotonic() - started, 1)}), line,
                  file=sys.stderr, flush=True)
    final = finalize_match(engine)
    return engine, world, attempts, accept_s, final, timing, sim_s, time.monotonic() - started


class PlainProcess:
    """Unsandboxed child process satisfying the engine's ManagedProcess protocol (dev only)."""

    def __init__(self, directory: Path):
        self.process = subprocess.Popen([sys.executable, '-u', 'main.py'], cwd=directory,
                                        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                        bufsize=0, env={'PATH': os.environ.get('PATH', '/usr/bin:/bin')})
        self.input_capacity_bytes = 0

    def pause(self):
        if self.process.poll() is None:
            try:
                os.kill(self.process.pid, signal.SIGSTOP)
                os.waitpid(self.process.pid, os.WUNTRACED)
            except ProcessLookupError:
                pass

    def resume(self):
        if self.process.poll() is None:
            os.kill(self.process.pid, signal.SIGCONT)

    def close(self):
        if self.process.poll() is None:
            os.kill(self.process.pid, signal.SIGCONT)
            self.process.kill()
        self.process.wait(timeout=5)
        for stream in (self.process.stdin, self.process.stdout, self.process.stderr):
            stream.close()


def run_pipe(args, seats):
    from contextlib import ExitStack
    from ..engine.driver import MatchDriver, economic_development_init, economic_development_messages
    from ..engine.economy import Economy
    from ..engine.ipc import StrategySession
    from ..engine.match import finalize_match, MATCH_PROFILE
    engine, world, attempts, accept_s = build_engine(args.seed, args.ticks, args.legacy_map)
    economy = Economy(engine.state)
    owners = sorted(engine.state.initial.known)
    started = time.monotonic()
    with ExitStack() as stack, tempfile.TemporaryDirectory() as tmp:
        sessions = {}
        for owner, tier in zip(owners, seats):
            if tier in ('none',):
                continue
            if tier == 'simple':
                raise SystemExit('pipe transport runs tier directories only (medium/advanced/super)')
            sessions[owner] = stack.enter_context(StrategySession(PlainProcess(HERE / tier)))
        driver = MatchDriver(engine, sessions,
                             init_messages={p: economic_development_init(economy, p, engine.clock) | {'scope': MATCH_PROFILE}
                                            for p in sessions},
                             observe=lambda p, t: economic_development_messages(economy, p, t, engine.clock),
                             finalize=lambda: finalize_match(engine),
                             replay=Path(tmp) / 'replay.jsonl',
                             manifest={'profile': MATCH_PROFILE, 'transport': 'unsandboxed_dev_pipe'})
        report = driver.run()
        clocks = {p: s.clock for p, s in sessions.items()}
    return engine, world, attempts, report, clocks, time.monotonic() - started


def main() -> int:
    parser = argparse.ArgumentParser(description='裂陆纪元 tier bots: development match runner (never official)')
    parser.add_argument('--seed', type=int, default=1000)
    parser.add_argument('--ticks', type=int, default=400, help='multiple of 40; 28,800 is the official clock')
    parser.add_argument('--legacy-map', action='store_true')
    parser.add_argument('--seats', default='super,advanced,medium,simple,none,none,none,none')
    parser.add_argument('--transport', choices=('inproc', 'pipe'), default='inproc')
    parser.add_argument('--progress', type=int, default=0)
    parser.add_argument('--out', type=Path)
    args = parser.parse_args()
    seats = parse_seats(args.seats)
    if args.transport == 'pipe':
        engine, world, attempts, report, clocks, wall = run_pipe(args, seats)
        result = {'transport': 'pipe', 'seed': args.seed, 'ticks': engine.tick, 'map_version': world.generator_version,
                  'seats': dict(zip(sorted(engine.state.initial.known), seats)), 'wall_seconds': round(wall, 1),
                  'driver_report_keys': sorted(report) if isinstance(report, dict) else str(type(report)),
                  'driver_report': json.loads(json.dumps(report, default=str))}
    else:
        engine, world, attempts, accept_s, final, timing, sim_s, wall = run_inproc(args, seats)
        owners = sorted(engine.state.initial.known)
        pillars = pillar_summary(engine)
        nations = {}
        for owner, tier in zip(owners, seats):
            row = final['nations'][owner]
            t = timing.get(owner)
            nations[owner] = {'tier': tier, 'V': float(row['V']), 'phi': float(row['phi']),
                              'era_means': [float(x) for x in row['era_means']], 'end_mean': float(row['end_mean'])} \
                | pillars[owner] | ({'decision_ms_mean': round(1000 * t['total_s'] / max(1, t['windows']), 3),
                                     'decision_ms_max': round(1000 * t['max_s'], 3), 'init_ms': round(1000 * t['init_s'], 3),
                                     'orders': t['orders'], 'windows': t['windows']} if t else {})
        result = {'transport': 'inproc', 'official': False, 'seed': args.seed, 'ticks': engine.tick,
                  'map_version': world.generator_version, 'world_attempt': world.attempt,
                  'world_acceptance_seconds': round(accept_s, 1), 'simulation_seconds': round(sim_s, 1),
                  'wall_seconds': round(wall, 1), 'sim_seconds_per_tick': round(sim_s / max(1, engine.tick), 4),
                  'nations': nations}
    text = json.dumps(result, ensure_ascii=False, indent=1, default=str)
    if args.out:
        args.out.write_text(text + '\n', encoding='utf-8')
    print(text)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
