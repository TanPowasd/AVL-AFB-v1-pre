"""Organizer development CLI. Never emits a provisional formal game score."""

import argparse
from contextlib import ExitStack
import json
from pathlib import Path

from .engine import ENGINE_VERSION
from .engine.catalog import Catalog
from .engine.determinism import canonical, primitive, runtime_fingerprint, engine_source_fingerprint
from .engine.errors import InfrastructureFault
from .engine.release import PENDING_RELEASE_GATES, require_official_readiness
from .engine.replay import ReplayWriter, verify_replay
from .engine.scenarios import supply_chain, replay_supply_chain
from .engine.world import generate_candidate, MAP_VERSION
from .engine.initialization import initialize
from .engine.determinism import fingerprint
from .engine.bootstrap import run_opening


def _seated(args, seats, *, official: bool) -> dict:
    """Shared match/strategy-match path for external seats (MatchDriver pipeline)."""
    from .engine.dev_seats import run_seated_match
    from .engine.match import accepted_world, match_economy, MATCH_PROFILE
    from .engine.world import generate_candidate
    launcher = None
    if args.cgroup is not None:
        from .engine.sandbox import Sandbox, runtime_mounts
        launcher = Sandbox(args.cgroup)
        launcher.preflight()
        launcher.mounts = runtime_mounts()
    elif official:
        raise ValueError('--official requires --cgroup')
    catalog = Catalog()
    world = generate_candidate(args.seed) if args.legacy_map else accepted_world(catalog, args.seed)
    game = match_economy(catalog, initialize(catalog, world), total_ticks=args.ticks,
                         fingerprint_each_tick=args.command == 'strategy-match')
    report = run_seated_match(game, seats, replay=args.replay, launcher=launcher, official=official,
                              manifest={'profile': MATCH_PROFILE, 'rules_hash': catalog.fingerprint})
    return report | {'seed': args.seed, 'map_version': world.generator_version}


def main() -> int:
    parser = argparse.ArgumentParser(description='裂陆纪元正式引擎开发工具')
    parser.add_argument('--official', action='store_true')
    commands = parser.add_subparsers(dest='command', required=True)
    commands.add_parser('check')
    world = commands.add_parser('world', help='construct an unaccepted spatial candidate and the major-nation starting template')
    world.add_argument('--seed', type=int, default=1000)
    world.add_argument('--legacy-map', action='store_true', help='old stratified candidate (development only)')
    bootstrap = commands.add_parser('bootstrap', help='execute the 150-tick opening economy and report its actual invariants')
    bootstrap.add_argument('--seed', type=int, default=1000)
    bootstrap.add_argument('--audit-every-phase', action='store_true')
    bootstrap.add_argument('--legacy-map', action='store_true', help='old stratified candidate (development only)')
    sandbox = commands.add_parser('sandbox-check', help='inspect the target cgroup delegation without running a submission')
    sandbox.add_argument('--cgroup', type=Path, required=True)
    strategy = commands.add_parser('strategy-opening', help='drive eight sandboxed submissions in the bounded development economy; no formal score')
    strategy.add_argument('--submissions', nargs=8, type=Path, required=True)
    strategy.add_argument('--cgroup', type=Path, required=True)
    strategy.add_argument('--replay', type=Path, required=True)
    strategy.add_argument('--seed', type=int, default=1000)
    strategy.add_argument('--ticks', type=int, default=150)
    strategy.add_argument('--audit-every-phase', action='store_true')
    match_replay = commands.add_parser('replay-strategy-opening', help='re-execute a development strategy replay and compare all tick hashes')
    match_replay.add_argument('path', type=Path)
    match = commands.add_parser('match', help='run a complete headless match (official clock 28,800 ticks) and the formal finalizer')
    match.add_argument('--seed', type=int, default=1000)
    match.add_argument('--ticks', type=int, default=28_800, help='below 28,800 is a practice clock (multiple of 40)')
    match.add_argument('--legacy-map', action='store_true', help='skip atlas acceptance; development only')
    match.add_argument('--bot', default='none', metavar='{none,simple,PATH}',
                       help='every seat: none, simple, or an external bot (directory with main.py, or a submission zip)')
    match.add_argument('--seats', nargs='+', metavar='SEAT',
                       help='eight per-nation seats in nation order (n0..n7): none|simple|PATH, space or comma separated')
    match.add_argument('--cgroup', type=Path, help='sandbox external seats; without it they run as unsandboxed dev processes')
    match.add_argument('--replay', type=Path, help='private replay path when external seats are driven')
    match.add_argument('--progress-every', type=int, default=0)
    full = commands.add_parser('strategy-match', help='drive eight sandboxed submissions through the complete match assembly')
    full.add_argument('--submissions', nargs=8, type=Path, help='eight submission zips (or directories)')
    full.add_argument('--bot', metavar='{none,simple,PATH}', help='same bot in every seat')
    full.add_argument('--seats', nargs='+', metavar='SEAT', help='eight per-nation seats: none|simple|PATH')
    full.add_argument('--cgroup', type=Path, help='required for --official; omitted = unsandboxed development pipe')
    full.add_argument('--replay', type=Path, required=True)
    full.add_argument('--seed', type=int, default=1000)
    full.add_argument('--ticks', type=int, default=28_800)
    full.add_argument('--legacy-map', action='store_true', help='skip atlas acceptance; development only')
    demo = commands.add_parser('scenario', help='run the integrated supply-chain development fixture')
    demo.add_argument('--seed', type=int, default=1)
    demo.add_argument('--ticks', type=int, default=100)
    demo.add_argument('--decision-ticks', type=int, default=5)
    demo.add_argument('--replay', type=Path)
    replay = commands.add_parser('verify-replay')
    replay.add_argument('path', type=Path)
    rerun = commands.add_parser('replay', help='re-execute the supply-chain fixture and verify every phase hash')
    rerun.add_argument('path', type=Path)
    args = parser.parse_args()
    try:
        if args.official:
            require_official_readiness()
        if args.command == 'sandbox-check':
            from .engine.sandbox import Sandbox
            Sandbox(args.cgroup).preflight()
            print(json.dumps({'delegation_preflight': True, 'full_sandbox_acceptance': False}))
            return 0
        if args.command == 'bootstrap':
            report = run_opening(args.seed, debug=args.audit_every_phase, atlas=not args.legacy_map)
            print(canonical(report).decode('utf-8'))
            return 0 if report.passed_core_checks else 2
        if args.command == 'strategy-opening':
            from .engine.archive import extracted_submission
            from .engine.driver import MatchDriver, economic_development_init, economic_development_messages
            from .engine.economy import Economy, opening_economy
            from .engine.ipc import StrategySession
            from .engine.sandbox import Sandbox, runtime_mounts
            launcher = Sandbox(args.cgroup)
            launcher.preflight()
            launcher.mounts = runtime_mounts()
            catalog = Catalog()
            game = opening_economy(catalog, initialize(catalog, generate_candidate(args.seed)),
                total_ticks=args.ticks, debug=args.audit_every_phase, commerce=True)
            economy = Economy(game.state)
            with ExitStack() as stack:
                sessions = {}
                for owner, archive in zip(sorted(game.state.initial.known), args.submissions):
                    submission = stack.enter_context(extracted_submission(archive))
                    sessions[owner] = stack.enter_context(StrategySession.launch(launcher, submission))
                driver = MatchDriver(game, sessions,
                    init_messages={p: economic_development_init(economy, p, game.clock) for p in sessions},
                    observe=lambda p, t: economic_development_messages(economy, p, t, game.clock),
                    finalize=lambda: {'scope': 'opening_economy_development', 'full_rules_accepted': False},
                    replay=args.replay, manifest={'profile': 'opening_economy_development', 'rules_hash': catalog.fingerprint})
                print(canonical(driver.run()).decode('utf-8'))
            return 0
        if args.command == 'match':
            import sys
            import time
            from .engine.dev_seats import resolve_seats, needs_driver, run_seated_match
            from .engine.match import run_match, match_economy, accepted_world
            seats = resolve_seats(args.bot, args.seats)
            started = time.monotonic()
            def progress(engine):
                if args.progress_every and engine.tick % args.progress_every == 0:
                    print(json.dumps({'tick': engine.tick, 'wall_seconds': round(time.monotonic() - started, 1)}),
                          file=sys.stderr, flush=True)
            if needs_driver(seats):
                report = _seated(args, seats, official=False)
                print(canonical(report).decode('utf-8'))
                return 0
            orders = None
            if 'simple' in seats:
                from .engine.match import SdkBotOrders
                orders = SdkBotOrders(None if len(set(seats)) == 1 else seats)
            report = run_match(args.seed, total_ticks=args.ticks, orders=orders, progress=progress,
                               accept_world=not args.legacy_map)
            print(canonical(report).decode('utf-8'))
            return 0
        if args.command == 'strategy-match':
            from .engine.dev_seats import resolve_seats
            if args.submissions and (args.bot or args.seats):
                raise ValueError('use --submissions, --bot or --seats, not several')
            if args.submissions:
                seats = resolve_seats(None, [str(p) for p in args.submissions])
            elif args.bot or args.seats:
                seats = resolve_seats(args.bot, args.seats)
            else:
                raise ValueError('strategy-match needs --submissions, --bot or --seats')
            report = _seated(args, seats, official=args.official)
            print(canonical(report).decode('utf-8'))
            return 0
        if args.command == 'replay-strategy-opening':
            from .engine.driver import replay_decisions
            from .engine.economy import opening_economy
            records = verify_replay(args.path)
            manifest = next(records)['payload']
            records.close()
            catalog = Catalog()
            if manifest.get('profile') != 'opening_economy_development' or manifest.get('rules_hash') != catalog.fingerprint:
                raise InfrastructureFault('replay profile or rules hash mismatch')
            game = opening_economy(catalog, initialize(catalog, generate_candidate(manifest['seed'])),
                total_ticks=manifest['clock']['total_ticks'], debug=True, commerce=True)
            print(canonical({'official': False, 'state_hash': replay_decisions(args.path, game),
                             'verification': 'reexecuted_strategy_windows_and_ticks'}).decode('utf-8'))
            return 0
        if args.command == 'verify-replay':
            count = sum(1 for _ in verify_replay(args.path))
            print(json.dumps({'verified_records': count, 'verification': 'hash_chain_only'}))
            return 0
        if args.command == 'replay':
            result = replay_supply_chain(args.path)
            print(json.dumps({'state_hash': result, 'verification': 'reexecuted_every_phase', 'official': False}))
            return 0
        catalog = Catalog()
        if args.command == 'check':
            print(json.dumps({'engine_version': ENGINE_VERSION, 'rules_version': catalog.version,
                'rules_hash': catalog.fingerprint, 'research_nodes': len(catalog.technologies),
                'engine_hash': engine_source_fingerprint(),
                'capability_milestones': len(catalog.milestones), 'goods': len(catalog.goods),
                'recipes': len(catalog.recipes), 'official_ready': not PENDING_RELEASE_GATES,
                'pending_release_gates': PENDING_RELEASE_GATES}, ensure_ascii=False, indent=2))
            return 0
        if args.command == 'world':
            if args.legacy_map:
                world = generate_candidate(args.seed)
            else:
                from .engine.world_atlas import generate_candidate as atlas_candidate
                world = atlas_candidate(args.seed)
            state = initialize(catalog, world)
            print(json.dumps({'map_version': world.generator_version, 'official': False, 'bootstrap_accepted': world.accepted,
                  'regions': len(world.regions), 'hubs': len(world.hubs), 'routes': len(world.routes),
                  'city_states': len(world.city_states), 'initial_lines': len(state.lines),
                  'initial_plants': len(state.plants), 'initial_mines': len(state.mines),
                  'initial_major_civilians_mU': sum(p.total_mU for p in state.populations.values()),
                  'initial_major_military_mU': sum(u.crew_mU for u in state.units.values()),
                  'state_hash': fingerprint(state.snapshot())}, ensure_ascii=False, indent=2))
            return 0
        engine = supply_chain(args.seed, total_ticks=args.ticks, decision_ticks=args.decision_ticks)
        manifest = {'scenario': 'supply_chain_v1', 'scope': 'component_integration_fixture',
                    'official': False, 'seed': args.seed, 'clock': engine.clock,
                    'engine_version': ENGINE_VERSION, 'rules_hash': catalog.fingerprint,
                    'engine_hash': engine_source_fingerprint(),
                    'runtime': runtime_fingerprint()}
        recorder = ReplayWriter(args.replay, manifest) if args.replay else None
        try:
            for _ in range(args.ticks):
                result = engine.step()
                if recorder:
                    recorder.write('tick', primitive(result))
            if recorder:
                recorder.finish({'ticks': engine.tick, 'state_hash': result.state_hash})
        finally:
            if recorder:
                recorder.close()
        print(canonical({'scope': 'component_integration_fixture', 'official': False,
                         'ticks': engine.tick, 'state_hash': result.state_hash,
                         'observation': engine.state.observed}).decode('utf-8'))
        return 0
    except (InfrastructureFault, ValueError, OSError) as exc:
        print(json.dumps({'error': type(exc).__name__, 'detail': str(exc)}, ensure_ascii=False))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
