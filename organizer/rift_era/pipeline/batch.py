"""Multi-seed, eight-seat batch runner with resource-bounded concurrency and resume.

Every match runs in a fresh interpreter (``match_runner``) and its result JSON is
written atomically to ``<out-dir>/matches/seed-<seed>.json``. Re-running the same
command resumes: a finished, valid result whose engine/rules/bot fingerprints
match the frozen batch manifest is kept; anything else is rerun. A manifest that
no longer matches the code (engine, rules or bot hashes changed) is refused
instead of mixing results; pass a new --out-dir.

Concurrency follows 07 §4: ``reserve = min(max(0,C-1), max(1,ceil(C/4)))``,
``C_match = ceil(engine_cpu_peak) + active_strategies``, ``K_cpu = floor((C-reserve)/C_match)``,
``K_mem = floor((M_avail - max(4 GiB, .2 M_total)) / (engine_mem + resident*strategy_mem))``,
``K = max(0, min(K_cpu, K_mem))``. In practice mode a K below 1 still runs one
match at a time and the summary says ``oversubscribed: true``; nothing is
started while memory headroom is below one match's estimate and another match
is still running.

Examples::

    # 2 seeds, eight Super, 200-tick practice clock (non-official)
    python3 -m organizer.rift_era.pipeline.batch --seeds 11 12 --seats super --ticks 200 --out-dir runs/a
    # 64 fresh OS-random seeds, candidate rotated through seat k mod 8 among seven Super
    python3 -m organizer.rift_era.pipeline.batch --seed-count 64 --seats super --rotate my_bot.zip --out-dir runs/b
"""

from __future__ import annotations

import argparse
import json
import math
import os
from pathlib import Path
import secrets
import subprocess
import sys
import tempfile
import time

from .match_runner import OFFICIAL_TICKS, SEAT_COUNT, parse_seats, resolve_seat, seat_label, seat_sha256, versions

GIB = 1024**3
MIB = 1024**2
DEFAULT_ENGINE_MEMORY = 1536 * MIB   # measured order of magnitude for one engine; override with --engine-mem-mib
DEFAULT_STRATEGY_MEMORY = 1 * GIB    # 07 §4: 1 GiB per resident strategy process


def concurrency_plan(*, cpus: float, memory_total: int | None, memory_available: int | None,
                     engine_cpu_peak: float = 1.0, engine_memory: int = DEFAULT_ENGINE_MEMORY,
                     strategy_memory: int = DEFAULT_STRATEGY_MEMORY, active_strategies: int = 1,
                     resident_strategies: int = SEAT_COUNT, practice: bool = True) -> dict:
    if cpus <= 0:
        raise ValueError('no CPU available')
    reserve = min(max(0, cpus - 1), max(1, math.ceil(cpus / 4)))
    c_match = math.ceil(engine_cpu_peak) + active_strategies
    k_cpu = math.floor((cpus - reserve) / c_match)
    m_match = engine_memory + resident_strategies * strategy_memory
    if memory_total is None or memory_available is None:
        k_mem, margin = 1, None
    else:
        margin = max(4 * GIB, math.ceil(memory_total / 5))
        k_mem = max(0, (memory_available - margin) // m_match)
    k = max(0, min(k_cpu, k_mem))
    oversubscribed = k < 1
    if oversubscribed and not practice:
        raise RuntimeError(f'host below one official match budget (K_cpu={k_cpu}, K_mem={k_mem}); refuse official run')
    return {'cpus': cpus, 'reserve_cpus': reserve, 'cpu_per_match': c_match, 'k_cpu': k_cpu,
            'memory_total': memory_total, 'memory_available': memory_available, 'memory_margin': margin,
            'memory_per_match': m_match, 'k_mem': k_mem, 'k': k, 'workers': max(1, k),
            'oversubscribed': oversubscribed}


def host_resources() -> tuple[float, int | None, int | None]:
    from organizer.strategy_scoring import detect_cpu_capacity, detect_memory_capacity
    memory = detect_memory_capacity()
    return detect_cpu_capacity()['available_cpus'], memory['total_bytes'], memory['available_bytes']


def draw_seeds(count: int) -> list[int]:
    seeds: list[int] = []
    while len(seeds) < count:
        seed = secrets.randbelow(2**31 - 1) + 1
        if seed not in seeds:
            seeds.append(seed)
    return seeds


def seats_for(index: int, base: list, rotate) -> list:
    seats = list(base)
    if rotate is not None:
        seats[index % SEAT_COUNT] = rotate
    return seats


def seat_token(seat) -> str:
    return seat if isinstance(seat, str) else str(seat)


def build_manifest(args, seeds, base, rotate) -> dict:
    return {'format': 'rift_batch_v1', 'seeds': seeds, 'ticks': args.ticks, 'legacy_map': args.legacy_map,
            'official_clock': args.ticks == OFFICIAL_TICKS and not args.legacy_map,
            'base_seats': [seat_token(s) for s in base], 'rotate': seat_token(rotate) if rotate else None,
            'bot_sha256': {seat_token(s): seat_sha256(s) for s in {*map(seat_token, base)} | ({seat_token(rotate)} if rotate else set())
                           if s != 'none'},
            'sandbox_request': args.sandbox} | {k: v for k, v in versions().items() if k != 'runtime'} | {
            'runtime': versions()['runtime']}


def compatible(old: dict, new: dict) -> list[str]:
    keys = ('seeds', 'ticks', 'legacy_map', 'base_seats', 'rotate', 'bot_sha256', 'engine_hash', 'rules_hash')
    return [k for k in keys if old.get(k) != new.get(k)]


def result_ok(path: Path, manifest: dict, seed: int) -> bool:
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return False
    return (data.get('valid') is True and data.get('seed') == seed and data.get('ticks') == manifest['ticks']
            and data.get('engine_hash') == manifest['engine_hash'] and data.get('rules_hash') == manifest['rules_hash'])


def match_command(seed: int, seats: list, args, out: Path) -> list[str]:
    command = [sys.executable, '-X', 'utf8', '-m', 'organizer.rift_era.pipeline.match_runner', '--seed', str(seed),
               '--ticks', str(args.ticks), '--seats', ','.join(seat_token(s) for s in seats),
               '--sandbox', args.sandbox, '--out', str(out)]
    if args.legacy_map:
        command.append('--legacy-map')
    if args.cgroup:
        command += ['--cgroup', str(args.cgroup)]
    return command


def memory_headroom_ok(plan: dict) -> bool:
    from organizer.strategy_scoring import detect_memory_capacity
    available = detect_memory_capacity()['available_bytes']
    if available is None or plan['memory_margin'] is None:
        return True
    return available >= plan['memory_margin'] + plan['memory_per_match']


def run_batch(args) -> dict:
    out_dir: Path = args.out_dir
    (out_dir / 'matches').mkdir(parents=True, exist_ok=True)
    (out_dir / 'logs').mkdir(exist_ok=True)
    base = parse_seats(args.seats)
    rotate = resolve_seat(args.rotate) if args.rotate else None
    manifest_path = out_dir / 'manifest.json'
    if manifest_path.exists():
        old = json.loads(manifest_path.read_text(encoding='utf-8'))
        seeds = old['seeds'] if args.seeds is None and args.seed_count is None else (args.seeds or old['seeds'])
        manifest = build_manifest(args, seeds, base, rotate)
        diff = compatible(old, manifest)
        if diff:
            raise SystemExit(f'existing batch manifest differs in {diff}; use a new --out-dir')
        manifest = old
        print(f'resuming batch in {out_dir}', file=sys.stderr)
    else:
        seeds = args.seeds or draw_seeds(args.seed_count or 1)
        if len(set(seeds)) != len(seeds):
            raise SystemExit('duplicate seeds')
        manifest = build_manifest(args, seeds, base, rotate)
        manifest['created_unix'] = int(time.time())
        manifest['seed_source'] = 'explicit' if args.seeds else 'os_csprng'
        tmp = manifest_path.with_suffix('.tmp')
        tmp.write_text(json.dumps(manifest, ensure_ascii=False, indent=1) + '\n', encoding='utf-8')
        tmp.replace(manifest_path)
    cpus, total, available = host_resources()
    practice = not manifest['official_clock']
    plan = concurrency_plan(cpus=cpus, memory_total=total, memory_available=available,
                            engine_memory=args.engine_mem_mib * MIB, strategy_memory=args.strategy_mem_mib * MIB,
                            practice=practice)
    workers = min(plan['workers'], args.jobs or plan['workers'])
    pending = []
    for index, seed in enumerate(manifest['seeds']):
        target = out_dir / 'matches' / f'seed-{seed}.json'
        if result_ok(target, manifest, seed):
            continue
        pending.append((index, seed, target))
    print(json.dumps({'seeds': len(manifest['seeds']), 'pending': len(pending), 'workers': workers,
                      'plan': plan}), file=sys.stderr, flush=True)
    running: dict = {}
    failures = []
    while pending or running:
        while pending and len(running) < workers and (not running or memory_headroom_ok(plan)):
            index, seed, target = pending.pop(0)
            tmp = target.with_name(target.name + '.partial')
            tmp.unlink(missing_ok=True)
            log = open(out_dir / 'logs' / f'seed-{seed}.log', 'ab')
            seats = seats_for(index, base, rotate)
            process = subprocess.Popen(match_command(seed, seats, args, tmp), stdout=log, stderr=log,
                                       env={**os.environ, 'PYTHONHASHSEED': '0', 'PYTHONUTF8': '1'})
            running[process] = (seed, target, tmp, log, time.monotonic())
        time.sleep(0.5)
        for process in [p for p in running if p.poll() is not None]:
            seed, target, tmp, log, started = running.pop(process)
            log.close()
            if process.returncode == 0 and result_ok(tmp, manifest, seed):
                tmp.replace(target)
                print(json.dumps({'seed': seed, 'done': True, 'wall_s': round(time.monotonic() - started, 1)}),
                      file=sys.stderr, flush=True)
            else:
                failures.append({'seed': seed, 'returncode': process.returncode})
                print(json.dumps({'seed': seed, 'failed': process.returncode}), file=sys.stderr, flush=True)
    summary = summarize(out_dir, manifest) | {'concurrency': plan | {'workers_used': workers}, 'failures': failures}
    tmp = out_dir / 'summary.json.tmp'
    tmp.write_text(json.dumps(summary, ensure_ascii=False, indent=1) + '\n', encoding='utf-8')
    tmp.replace(out_dir / 'summary.json')
    return summary


def summarize(out_dir: Path, manifest: dict) -> dict:
    rows, by_label = [], {}
    sandboxes = set()
    for seed in manifest['seeds']:
        path = out_dir / 'matches' / f'seed-{seed}.json'
        if not result_ok(path, manifest, seed):
            continue
        data = json.loads(path.read_text(encoding='utf-8'))
        sandboxes.add(data['sandbox'])
        rows.append({'seed': seed, 'sandbox': data['sandbox'],
                     'V': {p: n['V']['float'] for p, n in data['nations'].items()},
                     'seats': {p: n['seat'] for p, n in data['nations'].items()}})
        for nation in data['nations'].values():
            by_label.setdefault(nation['seat'], []).append(nation['V']['float'])
    return {'format': 'rift_batch_summary_v1', 'official': False,
            'official_clock': manifest['official_clock'], 'complete': len(rows) == len(manifest['seeds']),
            'matches_done': len(rows), 'matches_total': len(manifest['seeds']),
            'sandbox': sorted(sandboxes), 'engine_version': manifest['engine_version'],
            'engine_hash': manifest['engine_hash'], 'rules_version': manifest['rules_version'],
            'rules_hash': manifest['rules_hash'], 'bot_sha256': manifest['bot_sha256'],
            'mean_V_by_seat_label': {k: sum(v) / len(v) for k, v in sorted(by_label.items())},
            'matches': rows}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description='裂陆纪元：多 seed 8 席批量运行（断点续跑，非正式）')
    seeds = parser.add_mutually_exclusive_group()
    seeds.add_argument('--seeds', type=int, nargs='+')
    seeds.add_argument('--seed-count', type=int, help='fresh OS-random seeds')
    parser.add_argument('--seats', default='super', help='1 or 8: none|simple|medium|advanced|super|DIR|ZIP')
    parser.add_argument('--rotate', help='candidate placed at seat k mod 8 for the k-th seed')
    parser.add_argument('--ticks', type=int, default=OFFICIAL_TICKS, help='< 28,800 = practice (non-official)')
    parser.add_argument('--legacy-map', action='store_true', help='skip atlas acceptance (development only)')
    parser.add_argument('--sandbox', choices=('auto', 'bwrap', 'windows', 'none', 'cgroup'), default='auto')
    parser.add_argument('--cgroup', type=Path)
    parser.add_argument('--jobs', type=int, help='cap on concurrent matches (never above the resource plan)')
    parser.add_argument('--engine-mem-mib', type=int, default=DEFAULT_ENGINE_MEMORY // MIB)
    parser.add_argument('--strategy-mem-mib', type=int, default=DEFAULT_STRATEGY_MEMORY // MIB)
    parser.add_argument('--out-dir', type=Path, required=True)
    args = parser.parse_args(argv)
    if args.ticks <= 0 or args.ticks % 40:
        parser.error('--ticks must be a positive multiple of 40')
    summary = run_batch(args)
    print(json.dumps({k: v for k, v in summary.items() if k != 'matches'}, ensure_ascii=False, indent=1))
    return 0 if summary['complete'] else 1


if __name__ == '__main__':
    sys.exit(main())
