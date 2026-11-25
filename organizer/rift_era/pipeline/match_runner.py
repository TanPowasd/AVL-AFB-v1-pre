"""Run ONE eight-seat match through the engine's MatchDriver with a chosen launcher.

Used by the batch runner and the scoring worker, always in a fresh interpreter.
Never official unless every formal condition holds; the engine release gate is
consulted by ``MatchDriver(official=True)`` itself and is not bypassed here.

CLI (single match, JSON to stdout or --out)::

    python3 -m organizer.rift_era.pipeline.match_runner --seed 7 --ticks 200 \
        --seats super,super,super,super,super,super,super,PATH/my.zip --out m.json
"""

from __future__ import annotations

import argparse
from contextlib import ExitStack
from fractions import Fraction
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import time

from . import PIPELINE_VERSION

RIFT_ROOT = Path(__file__).resolve().parents[1]
OFFICIAL_TICKS = 28_800
SEAT_COUNT = 8


def bots_root() -> Path:
    """Tier bots (medium/advanced/super). Override with RIFT_ERA_BOTS_ROOT."""
    env = os.environ.get('RIFT_ERA_BOTS_ROOT')
    return Path(env).resolve() if env else RIFT_ROOT / 'bots'


TIER_NAMES = ('medium', 'advanced', 'super')


def resolve_seat(spec: str):
    """'none' | 'simple' | a bot name under bots_root() | directory with main.py | .zip."""
    spec = spec.strip()
    if spec in ('none', 'simple'):
        return spec
    if spec in TIER_NAMES:
        path = bots_root() / spec
        if not (path / 'main.py').is_file():
            raise FileNotFoundError(f'tier bot {spec!r} not found under {bots_root()} (set RIFT_ERA_BOTS_ROOT)')
        return path.resolve()
    if '/' not in spec and '\\' not in spec and spec not in ('.', '..'):
        named_dir = bots_root() / spec
        if named_dir.is_dir() and (named_dir / 'main.py').is_file():
            return named_dir.resolve()
        named_zip = bots_root() / (spec if spec.endswith('.zip') else f'{spec}.zip')
        if named_zip.is_file():
            return named_zip.resolve()
    path = Path(spec)
    if path.is_dir() and (path / 'main.py').is_file():
        return path.resolve()
    if path.is_file() and path.suffix == '.zip':
        return path.resolve()
    raise ValueError(f'bad seat {spec!r}: none, simple, a bot under bots/, a dir with main.py or a .zip')


def parse_seats(text) -> list:
    tokens = [t for item in ([text] if isinstance(text, str) else text) for t in str(item).split(',') if t.strip()]
    if len(tokens) == 1:
        tokens = tokens * SEAT_COUNT
    if len(tokens) != SEAT_COUNT:
        raise ValueError(f'--seats needs 1 or {SEAT_COUNT} entries')
    return [resolve_seat(t) for t in tokens]


def seat_sha256(seat) -> str | None:
    """sha256 of a zip, a deterministic tree hash of a directory, or of sdk.py for 'simple'."""
    if seat == 'none':
        return None
    if seat == 'simple':
        return hashlib.sha256((RIFT_ROOT / 'sdk.py').read_bytes()).hexdigest()
    path = Path(seat)
    if path.is_file():
        return hashlib.sha256(path.read_bytes()).hexdigest()
    digest = hashlib.sha256()
    for file in sorted((p for p in path.rglob('*') if p.is_file() and '__pycache__' not in p.parts),
                       key=lambda p: p.relative_to(path).parts):
        digest.update(file.relative_to(path).as_posix().encode() + b'\0')
        digest.update(hashlib.sha256(file.read_bytes()).hexdigest().encode() + b'\n')
    return 'tree:' + digest.hexdigest()


def seat_label(seat) -> str:
    if isinstance(seat, str):
        return seat
    path = Path(seat)
    if path.parent.resolve() == bots_root().resolve():
        return path.stem if path.suffix == '.zip' else path.name
    return path.name


def exact(value) -> dict:
    """Both a float for display and the exact rational (never sort by the float)."""
    if isinstance(value, dict) and '$rational' in value:
        value = Fraction(*value['$rational'])
    value = Fraction(value)
    return {'float': float(value), 'exact': f'{value.numerator}/{value.denominator}'}


def versions() -> dict:
    from ..engine import ENGINE_VERSION
    from ..engine.catalog import Catalog
    from ..engine.determinism import engine_source_fingerprint, runtime_fingerprint
    catalog = Catalog()
    return {'engine_version': ENGINE_VERSION, 'engine_hash': engine_source_fingerprint(),
            'rules_version': catalog.version, 'rules_hash': catalog.fingerprint,
            'pipeline_version': PIPELINE_VERSION, 'runtime': runtime_fingerprint()}


def run_match(*, seed: int, seats, ticks: int, legacy_map: bool = False, sandbox: str = 'auto',
              cgroup: Path | None = None, replay: Path | None = None, official: bool = False,
              progress=None) -> dict:
    """Drive one match. ``seats``: eight resolved seat specs (see :func:`resolve_seat`)."""
    from ..engine.catalog import Catalog
    from ..engine.dev_seats import materialized
    from ..engine.driver import MatchDriver, economic_development_init, economic_development_messages
    from ..engine.economy import Economy
    from ..engine.initialization import initialize
    from ..engine.ipc import StrategySession
    from ..engine.match import MATCH_PROFILE, accepted_world, finalize_match, match_economy
    from ..engine.world import generate_candidate
    from .hosting import select_launcher

    if len(seats) != SEAT_COUNT:
        raise ValueError('eight seats are required')
    if ticks <= 0 or ticks % 40:
        raise ValueError('ticks must be a positive multiple of 40')
    launcher = select_launcher(sandbox, cgroup=cgroup)
    if official and (launcher.sandbox not in {'cgroup', 'bwrap', 'windows_appcontainer_job'}
                     or ticks != OFFICIAL_TICKS or legacy_map):
        raise RuntimeError('official runs need cgroup/bwrap or AppContainer with Job Object, 28,800 ticks and the accepted atlas map')
    started = time.monotonic()
    catalog = Catalog()
    attempts: list = []
    world = generate_candidate(seed) if legacy_map else accepted_world(catalog, seed, report=attempts)
    accept_s = time.monotonic() - started
    engine = match_economy(catalog, initialize(catalog, world), total_ticks=ticks, fingerprint_each_tick=False)
    economy = Economy(engine.state)
    owners = sorted(engine.state.initial.known)
    with ExitStack() as stack:
        temp = Path(stack.enter_context(tempfile.TemporaryDirectory(prefix='rift-pipeline-')))
        sessions = {}
        seat_sandboxes = {}
        seat_trusted = {}
        for owner, seat in zip(owners, seats):
            if seat == 'none':
                continue
            root = stack.enter_context(materialized(seat, stack))
            if launcher.sandbox == 'cgroup':
                session = StrategySession.launch(launcher.cgroup_sandbox, root)
                seat_sandboxes[owner] = 'cgroup'
                seat_trusted[owner] = False
            else:
                managed = launcher.start(root)
                try:
                    session = StrategySession(managed)
                except Exception:
                    managed.close()
                    raise
                seat_sandboxes[owner] = getattr(managed, 'sandbox', launcher.sandbox)
                seat_trusted[owner] = bool(getattr(managed, 'trusted', False))
            sessions[owner] = stack.enter_context(session)
        if not sessions:
            raise ValueError('at least one non-none seat is required')
        untrusted_sandboxes = {seat_sandboxes[o] for o in sessions if not seat_trusted[o]}
        if untrusted_sandboxes and launcher.sandbox not in untrusted_sandboxes:
            if 'none' in untrusted_sandboxes:
                actual_sandbox = 'none'
            elif 'windows_job_isolated' in untrusted_sandboxes:
                actual_sandbox = 'windows_job_isolated'
            else:
                actual_sandbox = sorted(untrusted_sandboxes)[0]
            if official:
                raise RuntimeError(f'official match sandbox mismatch: declared {launcher.sandbox}, actual {sorted(untrusted_sandboxes)}')
        elif len(untrusted_sandboxes) > 1:
            if 'none' in untrusted_sandboxes:
                actual_sandbox = 'none'
            elif 'windows_job_isolated' in untrusted_sandboxes:
                actual_sandbox = 'windows_job_isolated'
            else:
                actual_sandbox = sorted(untrusted_sandboxes)[0]
            if official:
                raise RuntimeError(f'official match mixed sandboxes: {sorted(untrusted_sandboxes)}')
        else:
            actual_sandbox = next(iter(untrusted_sandboxes)) if untrusted_sandboxes else launcher.sandbox
        if progress is not None:
            original_step = engine.step

            def step(*args, **kwargs):
                result = original_step(*args, **kwargs)
                progress(engine)
                return result
            engine.step = step
        driver = MatchDriver(
            engine, sessions,
            init_messages={p: economic_development_init(economy, p, engine.clock) | {'scope': MATCH_PROFILE}
                           for p in sessions},
            observe=lambda p, t: economic_development_messages(economy, p, t, engine.clock),
            finalize=lambda: finalize_match(engine),
            replay=replay or temp / 'replay.jsonl', official=official,
            manifest={'profile': MATCH_PROFILE, 'rules_hash': catalog.fingerprint,
                      'transport': actual_sandbox})
        report = driver.run()
    wall = time.monotonic() - started
    nations = {}
    for owner, seat in zip(owners, seats):
        row = report['nations'][owner]
        nations[owner] = {'seat': seat_label(seat), 'bot_sha256': seat_sha256(seat),
                          'V': exact(row['V']), 'phi': exact(row['phi']),
                          'era_means': [exact(x)['float'] for x in row['era_means']],
                          'end_mean': exact(row['end_mean'])['float'],
                          'strategy_time': report['strategy_time'].get(owner),
                          'faults': len(report['faults'].get(owner, ()))}
    return {'kind': 'rift_era_match', 'official': bool(official and report.get('official')),
            'practice_clock': ticks != OFFICIAL_TICKS, 'seed': seed, 'ticks': report['ticks'],
            'legacy_map': legacy_map, 'map_version': world.generator_version, 'world_attempt': world.attempt,
            'world_acceptance_seconds': round(accept_s, 1), 'wall_seconds': round(wall, 1),
            'sandbox': actual_sandbox, 'sandbox_reason': launcher.reason,
            'seat_sandboxes': seat_sandboxes,
            'full_rules_accepted': report.get('full_rules_accepted'),
            'pending_rule_inputs': report.get('pending_rule_inputs'), 'state_hash': report['state_hash'],
            'valid': report.get('valid'), 'nations': nations} | versions()


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description='裂陆纪元：单局 8 席运行（组委会流水线）')
    parser.add_argument('--seed', type=int, required=True)
    parser.add_argument('--ticks', type=int, default=OFFICIAL_TICKS)
    parser.add_argument('--seats', required=True, help='1 or 8 comma-separated: none|simple|medium|advanced|super|DIR|ZIP')
    parser.add_argument('--legacy-map', action='store_true')
    parser.add_argument('--sandbox', choices=('auto', 'bwrap', 'none', 'cgroup', 'windows'), default='auto')
    parser.add_argument('--cgroup', type=Path)
    parser.add_argument('--official', action='store_true',
                        help='formal run: needs cgroup sandbox, 28,800 ticks, atlas map AND the engine release gate')
    parser.add_argument('--out', type=Path)
    args = parser.parse_args(argv)
    result = run_match(seed=args.seed, seats=parse_seats(args.seats), ticks=args.ticks,
                       legacy_map=args.legacy_map, sandbox=args.sandbox, cgroup=args.cgroup, official=args.official)
    text = json.dumps(result, ensure_ascii=False, indent=1)
    if args.out:
        args.out.write_text(text + '\n', encoding='utf-8')
    else:
        print(text)
    return 0


if __name__ == '__main__':
    sys.exit(main())
