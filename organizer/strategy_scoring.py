"""Random-scenario, fixed-opponent 0–100 strategy evaluation (scoring_v2).

Each game runs in a fresh interpreter, including in-process farm policies.
The private profile is not part of the exported contestant kit.
"""
from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import math
import os
from pathlib import Path
import random
import secrets
import shutil
import signal
import statistics
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED

ROOT = Path(__file__).resolve().parent
VERSION = "scoring_v2"
SANDBOX = False
PROBLEMS = ("ai_training", "backbone", "cache_duel", "kaggriculture", "rift_era")
MIB = 1024**2
GAME_MEMORY_ESTIMATE = 512 * MIB


def cgroup_cpu_limits(root=Path('/sys/fs/cgroup')):
    """Best-effort Linux v1/v2 quota detection, including visible parent limits."""
    locations = {(root, 'v2'), (root / 'cpu', 'v1'), (root / 'cpu,cpuacct', 'v1')}
    try:
        for line in Path('/proc/self/cgroup').read_text().splitlines():
            _, controllers, relative = line.split(':', 2)
            relative = Path(relative.lstrip('/'))
            if '..' in relative.parts:
                continue
            if not controllers:
                mount, kind = root, 'v2'
            elif 'cpu' in controllers.split(','):
                mount, kind = root / controllers, 'v1'
            else:
                continue
            current = mount / relative
            while current.is_relative_to(mount):
                locations.add((current, kind))
                if current == mount:
                    break
                current = current.parent
    except (OSError, ValueError):
        pass
    limits = []
    for directory, kind in locations:
        try:
            if kind == 'v2':
                quota, period = (directory / 'cpu.max').read_text().split()
            else:
                quota = (directory / 'cpu.cfs_quota_us').read_text().strip()
                period = (directory / 'cpu.cfs_period_us').read_text().strip()
            if quota != 'max' and int(quota) > 0 and int(period) > 0:
                limits.append(max(1, int(quota) // int(period)))
        except (OSError, ValueError):
            pass
    return limits


def detect_cpu_capacity():
    logical = os.cpu_count()
    process = getattr(os, 'process_cpu_count', lambda: None)()
    try:
        affinity_ids = sorted(os.sched_getaffinity(0))
        affinity = len(affinity_ids)
    except (AttributeError, OSError):
        affinity, affinity_ids = None, None
    quota = min(cgroup_cpu_limits(), default=None) if sys.platform.startswith('linux') else None
    constraints = [n for n in (logical, process, affinity, quota) if isinstance(n, int) and n > 0]
    return dict(logical_cpus=logical, process_cpus=process, affinity_cpus=affinity,
                affinity_ids=affinity_ids, quota_cpus=quota,
                available_cpus=min(constraints) if constraints else 1)


def cgroup_memory_limits(root=Path('/sys/fs/cgroup')):
    locations = {(root, 'v2'), (root / 'memory', 'v1')}
    try:
        for line in Path('/proc/self/cgroup').read_text().splitlines():
            _, controllers, relative = line.split(':', 2)
            relative = Path(relative.lstrip('/'))
            if '..' in relative.parts:
                continue
            if not controllers:
                mount, kind = root, 'v2'
            elif 'memory' in controllers.split(','):
                mount, kind = root / 'memory', 'v1'
            else:
                continue
            current = mount / relative
            while current.is_relative_to(mount):
                locations.add((current, kind))
                if current == mount:
                    break
                current = current.parent
    except (OSError, ValueError):
        pass
    limits = []
    for directory, kind in locations:
        try:
            limit_name, used_name = (('memory.max', 'memory.current') if kind == 'v2'
                                     else ('memory.limit_in_bytes', 'memory.usage_in_bytes'))
            limit = int((directory / limit_name).read_text().strip())
            used = int((directory / used_name).read_text().strip())
            if limit > 0 and used >= 0:
                limits.append((limit, max(0, limit-used)))
        except (OSError, ValueError):
            pass
    return limits


def detect_memory_capacity():
    total = available = None
    source = 'unknown'
    try:
        if sys.platform.startswith('linux'):
            fields = {line.split(':')[0]: int(line.split()[1])*1024
                      for line in Path('/proc/meminfo').read_text().splitlines() if ':' in line}
            total, available = fields['MemTotal'], fields['MemAvailable']
            source = 'proc_meminfo'
        elif os.name == 'nt':
            import ctypes
            class MemoryStatus(ctypes.Structure):
                _fields_ = [('length', ctypes.c_uint32), ('load', ctypes.c_uint32)] + [
                    (name, ctypes.c_uint64) for name in ('total', 'available', 'page_total',
                                                        'page_available', 'virtual_total',
                                                        'virtual_available', 'extended_available')]
            status = MemoryStatus()
            status.length = ctypes.sizeof(status)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
                total, available = status.total, status.available
                source = 'GlobalMemoryStatusEx'
        elif hasattr(os, 'sysconf'):
            page = os.sysconf('SC_PAGE_SIZE')
            total = page * os.sysconf('SC_PHYS_PAGES')
            available = page * os.sysconf('SC_AVPHYS_PAGES')
            source = 'sysconf'
    except (OSError, ValueError, KeyError, AttributeError):
        pass
    if sys.platform.startswith('linux'):
        for limit, remaining in cgroup_memory_limits():
            total = min(total, limit) if total is not None else limit
            available = min(available, remaining) if available is not None else remaining
            source += '+cgroup'
    return dict(total_bytes=total, available_bytes=available, source=source)


def rift_scoring():
    """裂陆纪元 hooks (8 seats, shadow match, zip submissions); see organizer/rift_era/pipeline/scoring.py."""
    if str(ROOT.parent) not in sys.path:
        sys.path.insert(0, str(ROOT.parent))
    from organizer.rift_era.pipeline import scoring
    return scoring


def resource_budget(capacity, memory, game_memory=GAME_MEMORY_ESTIMATE):
    cores = capacity['available_cpus']
    reserved_cpus = min(cores-1, max(1, math.ceil(cores/4)))
    cpu_limit = max(1, cores-reserved_cpus)
    total, available = memory.get('total_bytes'), memory.get('available_bytes')
    reserve = min(total // 2, 4096 * MIB, max(512 * MIB, total // 5)) if total else None
    memory_limit = max(0, (available-reserve)//game_memory) if reserve is not None and available is not None else 1
    ids = capacity.get('affinity_ids')
    return dict(reserved_cpus=reserved_cpus, cpu_worker_limit=cpu_limit,
                memory=memory, reserved_memory_bytes=reserve,
                estimated_bytes_per_game=game_memory, memory_worker_limit=memory_limit,
                safe_workers=min(cpu_limit, memory_limit),
                worker_affinity=ids[:cpu_limit] if ids else None)


def worker_count(requested, total_games, budget):
    if total_games < 1 or (requested is not None and requested < 1):
        raise ValueError('worker and game counts must be positive')
    if budget['safe_workers'] < 1:
        raise RuntimeError('Insufficient available memory after reserving system headroom; no games started')
    return min(total_games, budget['safe_workers'], requested if requested is not None else budget['safe_workers'])


def memory_allows_start(budget):
    if budget['memory'].get('available_bytes') is None:
        return True  # Unknown at startup: resource_budget already limits this to one game.
    current = detect_memory_capacity()['available_bytes']
    reserve = budget['reserved_memory_bytes']
    return current is not None and reserve is not None and current >= reserve + budget['estimated_bytes_per_game']


def run_scheduled(jobs, workers, budget, on_complete=None, pressure_timeout=30.0):
    """Bound pending work and stop admitting new games while memory is tight."""
    results = [None] * len(jobs)
    next_index = completed = peak = pauses = 0
    blocked_since = None
    with ThreadPoolExecutor(max_workers=workers) as executor:
        pending = {}
        try:
            while next_index < len(jobs) or pending:
                while next_index < len(jobs) and len(pending) < workers:
                    if not memory_allows_start(budget):
                        pauses += 1
                        break
                    spec = dict(jobs[next_index], resource_policy={'affinity': budget['worker_affinity'],
                                                                  'lower_priority': True})
                    pending[executor.submit(execute, spec)] = next_index
                    next_index += 1
                    peak = max(peak, len(pending))
                if not pending:
                    if next_index == len(jobs):
                        break
                    blocked_since = time.monotonic() if blocked_since is None else blocked_since
                    if time.monotonic() - blocked_since >= pressure_timeout:
                        raise RuntimeError('Memory headroom unavailable; evaluation stopped without publishing a score')
                    time.sleep(.2)
                    continue
                blocked_since = None
                done, _ = wait(pending, timeout=.5, return_when=FIRST_COMPLETED)
                for future in done:
                    results[pending.pop(future)] = future.result()
                    completed += 1
                    if on_complete:
                        on_complete(completed)
        except BaseException:
            for future in pending:
                future.cancel()
            raise
    return results, dict(peak_inflight=peak, memory_pauses=pauses)


def apply_resource_policy(policy):
    applied = {'affinity': False, 'lower_priority': False}
    if not policy:
        return applied
    if policy.get('affinity') and hasattr(os, 'sched_setaffinity'):
        try:
            os.sched_setaffinity(0, policy['affinity'])
            applied['affinity'] = True
        except OSError:
            pass
    if policy.get('lower_priority'):
        try:
            if os.name == 'nt':
                import ctypes
                kernel = ctypes.windll.kernel32
                kernel.GetCurrentProcess.restype = ctypes.c_void_p
                kernel.SetPriorityClass.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
                applied['lower_priority'] = bool(kernel.SetPriorityClass(kernel.GetCurrentProcess(), 0x4000))
            elif hasattr(os, 'nice'):
                os.nice(10)
                applied['lower_priority'] = True
        except (OSError, AttributeError):
            pass
    return applied


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def profile_fingerprint(cfg):
    return hashlib.sha256(json.dumps(cfg, sort_keys=True).encode() + Path(__file__).read_bytes()).hexdigest()


def margin_score(mine, opponent, scale):
    if not all(math.isfinite(x) for x in (mine, opponent, scale)) or scale <= 0:
        raise ValueError("Non-finite value or non-positive margin scale")
    return 50.0 + 50.0 * math.tanh((mine - opponent) / scale)


def percent(value):
    if not math.isfinite(value) or not 0 <= value <= 1:
        raise ValueError(f"Expected a metric in [0,1], got {value}")
    return 100.0 * value


def draw_seeds(count, excluded=()):
    """Draw each seed directly from the OS CSPRNG; save the resulting list."""
    excluded = set(excluded)
    if type(count) is not int or not 1 <= count <= 2**31 - 1 - len(excluded):
        raise ValueError('seed count must be positive and fit the seed space')
    seeds, used = [], set(excluded)
    while len(seeds) < count:
        seed = secrets.randbelow(2**31 - 1) + 1
        if seed not in used:
            seeds.append(seed)
            used.add(seed)
    return seeds


def bootstrap_ci(seed_scores):
    """Resample complete seed blocks, retaining paired seats/opponents/modes."""
    values = [seed_scores[k] for k in sorted(seed_scores)]
    if len(values) < 2:
        return None
    rng = random.Random(20261102)
    means = sorted(statistics.fmean(rng.choices(values, k=len(values))) for _ in range(2000))
    return [means[49], means[1949]]


def load_profile(problem):
    base = (ROOT / problem).resolve()
    cfg = json.loads((base / "config/scoring_v2.json").read_text(encoding="utf-8"))
    if cfg["version"] != VERSION or cfg["problem"] != problem:
        raise ValueError("Scoring profile/version mismatch")
    if (cfg.get('scenario_sampling') != 'per_run_random' or cfg.get('seed_source') != 'os_csprng'
            or type(cfg.get('seed_count')) is not int or cfg['seed_count'] < 1):
        raise ValueError('Profile must specify OS-random sampling and a positive default seed count')
    opponents = cfg["opponents"]
    if not opponents or len({o['id'] for o in opponents}) != len(opponents):
        raise ValueError("Missing or duplicate frozen opponents")
    for relative, expected in cfg["frozen_files"].items():
        path = (base / relative).resolve()
        if problem == "rift_era":
            # Tier bots may live outside the problem dir (RIFT_ERA_BOTS_ROOT); still hash-pinned.
            path = rift_scoring().profile_path(relative)
        elif not path.is_relative_to(base):
            raise ValueError(f"Frozen source outside problem: {problem}/{relative}")
        if not path.is_file() or digest(path) != expected:
            raise ValueError(f"Frozen source changed: {problem}/{relative}; create a new profile")
    for opponent in opponents:
        if opponent['path'] not in cfg['frozen_files']:
            raise ValueError("Opponent is not fingerprinted")
    calibration = cfg.get('calibration')
    if calibration and digest(base / calibration['evidence']) != calibration['evidence_sha256']:
        raise ValueError('Calibration evidence changed')
    if problem not in ("cache_duel", "rift_era"):
        margin_score(0.0, 0.0, cfg["margin_scale"])
    weights = cfg["component_weights"]
    if any(not math.isfinite(w) or w <= 0 for w in weights.values()) or not math.isclose(sum(weights.values()), 1):
        raise ValueError("Component weights must be positive and sum to one")
    return cfg


def make_jobs(problem, candidate, cfg, seeds=None, ticks=None):
    """The schedule for a candidate never depends on other submissions."""
    if not seeds or len(set(seeds)) != len(seeds) or any(type(s) is not int for s in seeds):
        raise ValueError('Pass unique scenario seeds sampled once for the whole batch')
    if problem == 'rift_era':
        return rift_scoring().make_jobs(candidate, cfg, seeds, ticks)
    jobs = []
    for seed in seeds:
        common = dict(problem=problem, candidate=str(Path(candidate).resolve()), seed=seed,
                      margin_scale=cfg.get('margin_scale'), world_salt=cfg.get('world_salt', 'practice'),
                      ticks=cfg.get('ticks') if ticks is None else ticks,
                      match=cfg.get('match', {}), game_timeout_s=cfg['game_timeout_s'])
        if problem == 'ai_training':
            jobs.append(dict(common, component='solo', opponent='solo_npc', seat=0, config_id='default'))
        configs = cfg['configs'] if problem == 'cache_duel' else [None]
        for opponent in cfg['opponents']:
            for index, configuration in enumerate(configs):
                for seat in (0, 1):
                    component = ('attack' if seat == 0 else 'defense') if problem == 'cache_duel' else 'adv'
                    spec = dict(common, component=component, opponent=opponent['id'], seat=seat,
                                opponent_path=str(ROOT / problem / opponent['path']), config_id=str(index))
                    if configuration is not None:
                        spec['cache_config'] = dict(configuration)
                        if ticks is not None:
                            spec['cache_config']['length'] = ticks
                    jobs.append(spec)
    return jobs


def game_key(game):
    return (game['seed'], game['component'], game['opponent'], game['config_id'], game['seat'])


def aggregate(candidate, games, cfg, expected_jobs):
    expected = {game_key(j) for j in expected_jobs}
    keys = [game_key(g) for g in games]
    if len(keys) != len(set(keys)) or set(keys) != expected:
        raise ValueError("Incomplete, duplicate or unexpected matches; refusing a biased score")
    seeds = sorted({j['seed'] for j in expected_jobs})
    components, seed_scores = {}, dict.fromkeys(seeds, 0.0)
    for component, weight in cfg['component_weights'].items():
        per_seed = []
        for seed in seeds:
            sample = [g['score'] for g in games if g['seed'] == seed and g['component'] == component]
            if not sample or any(not math.isfinite(v) or not 0 <= v <= 100 for v in sample):
                raise ValueError(f"Invalid score samples for {component}/{seed}")
            value = statistics.fmean(sample)
            per_seed.append(value)
            seed_scores[seed] += weight * value
        components[component] = statistics.fmean(per_seed)
    raw = {}
    for key in sorted({k for g in games for k in g['raw']}):
        values = [g['raw'][key] for g in games if key in g['raw']]
        if not all(math.isfinite(v) for v in values):
            raise ValueError(f"Non-finite raw metric: {key}")
        raw[key] = statistics.fmean(values)
    by_opponent = {}
    for name in sorted({g['opponent'] for g in games}):
        by_opponent[name] = {
            component: statistics.fmean(g['score'] for g in games
                                       if g['opponent'] == name and g['component'] == component)
            for component in sorted({g['component'] for g in games if g['opponent'] == name})}
    return dict(bot=Path(candidate).stem, status='OK', score=statistics.fmean(seed_scores.values()),
                max_score=100, components=components, ci95=bootstrap_ci(seed_scores),
                seed_scores={str(k): v for k, v in seed_scores.items()}, raw=raw,
                by_opponent=by_opponent, n_games=len(games),
                fault_games=sum(bool(g['candidate_fault']) for g in games),
                opponent_fault_games=sum(bool(g['opponent_fault']) for g in games))


def run_game(spec):
    """Only called in the disposable worker process."""
    if spec['problem'] == 'rift_era':
        return rift_scoring().run_scored_game(spec)
    base = ROOT / spec['problem']
    sys.path.insert(0, str(base / 'engine'))
    seat = spec['seat']
    candidate = spec['candidate']
    other = spec.get('opponent_path')
    paths = [candidate, other] if seat == 0 else [other, candidate]
    if spec['problem'] == 'kaggriculture':
        import arena
        import loader
        random.seed(spec['seed'])
        policies = []
        for i, path in enumerate(paths):
            try:
                random.seed(spec['seed'] * 2 + i)
                # Only the profile's opponent is trusted; submissions never inherit trust by name.
                fn = (loader.load_agent if i == seat else loader.load_trusted)(path)[1]
                policies.append(arena.isolate_policy_rng(fn, spec['seed'] * 2 + i))
            except Exception as exc:
                if i != seat:
                    raise RuntimeError(f"Frozen opponent could not load: {path}") from exc
                return dict(score=0.0, raw={}, candidate_fault=True, opponent_fault=False,
                            faults={'load_error': str(exc)}, result={'reason': 'load_error'})
        match_cfg = dict(spec['match'])
        match_cfg['episodeSteps'] = spec['ticks']
        result = arena.play_game(policies, spec['seed'], cfg=match_cfg)
        stats = result['stats']
        dead = result['forfeited']
        # Match the documented farm forfeit: technical loss is zero, not residual cash.
        if dead[seat]:
            score = 0.0
        elif dead[1 - seat]:
            score = 100.0
        else:
            score = margin_score(result['cash'][seat], result['cash'][1-seat], spec['margin_scale'])
        return dict(score=score, raw={'cash': result['cash'][seat], 'opponent_cash': result['cash'][1-seat],
                                     'cash_gap': result['cash'][seat] - result['cash'][1-seat]},
                    candidate_fault=bool(stats[seat]['errors'] or stats[seat]['timeout']),
                    opponent_fault=bool(stats[1-seat]['errors'] or stats[1-seat]['timeout']),
                    faults=stats, result=result)

    import engine
    if spec['problem'] == 'cache_duel':
        from dataclasses import asdict
        prev_max_procs = os.environ.get('AVL_SANDBOX_MAX_PROCS')
        prev_cpu_lim = os.environ.get('AVL_SANDBOX_CPU_LIMIT_MS')
        os.environ['AVL_SANDBOX_MAX_PROCS'] = '1'
        os.environ['AVL_SANDBOX_CPU_LIMIT_MS'] = '25000'
        try:
            result = asdict(engine.play(*paths, seed=spec['seed'], **spec['cache_config']))
        finally:
            if prev_max_procs is None:
                os.environ.pop('AVL_SANDBOX_MAX_PROCS', None)
            else:
                os.environ['AVL_SANDBOX_MAX_PROCS'] = prev_max_procs
            if prev_cpu_lim is None:
                os.environ.pop('AVL_SANDBOX_CPU_LIMIT_MS', None)
            else:
                os.environ['AVL_SANDBOX_CPU_LIMIT_MS'] = prev_cpu_lim
        role = 'attacker' if seat == 0 else 'defender'
        opposite = 'defender' if seat == 0 else 'attacker'
        faults = result['faults']
        cand_dead = bool(faults.get(f'{role}_dead'))
        opp_dead = bool(faults.get(f'{opposite}_dead'))
        cand_fault_cnt = int(faults.get(role, 0))
        opp_fault_cnt = int(faults.get(opposite, 0))
        raw_score = percent(1 - result['def_score'] if seat == 0 else result['def_score'])
        if cand_dead or cand_fault_cnt > 0:
            score = 0.0
        elif opp_dead or opp_fault_cnt > 0:
            score = 100.0
        else:
            score = raw_score
        return dict(score=score,
                    raw={'def_misses': result['def_misses'], 'opt_misses': result['opt_misses'],
                         'def_ratio': result['def_score']},
                    candidate_fault=bool(cand_dead or cand_fault_cnt),
                    opponent_fault=bool(opp_dead or opp_fault_cnt),
                    faults=faults, result=result)
    if spec['problem'] == 'ai_training':
        engine.W.SALT = spec['world_salt']
        kwargs = {'ticks': spec['ticks']}
        if spec['component'] == 'solo':
            paths = [candidate, engine.SOLO_NPC]
            kwargs['inproc_b'] = True
        result = engine.play(*paths, spec['seed'], **kwargs)
        metric = 'value'
    else:
        engine.SALT = spec['world_salt']
        result = engine.play(*paths, spec['seed'], ticks=spec['ticks'])
        metric = 'nw'
    result.pop('replay', None)
    mine, other = result[metric][seat], result[metric][1-seat]
    solo_c = result['C'][0] if spec['component'] == 'solo' else 0.0
    if not (math.isfinite(mine) and math.isfinite(solo_c)):
        # Engines reject bad numbers; this is the backstop for a candidate-caused blow-up.
        return dict(score=0.0, raw={}, candidate_fault=True, opponent_fault=False,
                    faults={'non_finite': metric}, result={'reason': 'non_finite'})
    if not math.isfinite(other):
        raise RuntimeError(f'Opponent produced a non-finite {metric}')
    raw = {metric: result[metric][seat], 'opponent_' + metric: result[metric][1-seat],
           metric + '_gap': result[metric][seat] - result[metric][1-seat]}
    if spec['component'] == 'solo':
        raw['C'] = result['C'][0]
        score = percent(raw['C'])
    else:
        score = margin_score(result[metric][seat], result[metric][1-seat], spec['margin_scale'])
    faults = result['faults']
    has_fault = lambda f: bool(f['timeouts'] or f['bad_json'] or f['dead'] or f.get('illegal'))
    return dict(score=score, raw=raw, candidate_fault=has_fault(faults[seat]),
                opponent_fault=has_fault(faults[1-seat]), faults=faults, result=result)


def execute(spec):
    started = time.monotonic()
    with subprocess.Popen([sys.executable, '-X', 'utf8', '-u', str(Path(__file__).resolve()), '--worker'],
                          stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          text=True, encoding='utf-8', start_new_session=(os.name != 'nt'),
                          env={**os.environ, 'PYTHONHASHSEED': '0', 'PYTHONUTF8': '1'}) as proc:
        try:
            stdout, stderr = proc.communicate(json.dumps(dict(spec, sandbox=SANDBOX)), timeout=spec['game_timeout_s'])
        except subprocess.TimeoutExpired:
            if os.name == 'nt':
                subprocess.run(['taskkill', '/PID', str(proc.pid), '/T', '/F'],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
            else:
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            proc.kill()
            proc.communicate()
            raise RuntimeError(f'Game watchdog expired ({game_key(spec)}); no score published')
        if proc.returncode:
            raise RuntimeError(f"Game worker failed ({game_key(spec)}): {stderr[-4000:]}")
    record = json.loads(stdout)
    record.update({k: spec[k] for k in ('seed', 'component', 'opponent', 'config_id', 'seat')})
    record['wall_s'] = time.monotonic() - started
    return record


def write_report(path, report):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + '\n'
    if path.exists():
        previous = json.loads(path.read_text(encoding='utf-8'))
        tag = VERSION if previous.get('scoring_version') == VERSION else 'legacy_v1'
        backup = path.with_name(f'{path.stem}.{tag}.{digest(path)[:12]}.json')
        if not backup.exists():
            shutil.copy2(path, backup)
    with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent, delete=False) as fh:
        temp = Path(fh.name)
        fh.write(text)
    try:
        temp.replace(path)
    finally:
        temp.unlink(missing_ok=True)


def official_seed_count(problem, count, default):
    return count == default if problem == 'rift_era' else count >= default


def main(problem):
    parser = argparse.ArgumentParser(description=f'{problem}: random scenarios, fixed opponents, 0–100 scoring_v2')
    parser.add_argument('--submissions', type=Path, default=ROOT / problem / 'agents/submissions')
    parser.add_argument('--bots', nargs='+', help='Explicit candidate paths or baseline names')
    parser.add_argument('--jobs', type=int, help='Requested concurrent games; always capped by CPU/memory headroom')
    parser.add_argument('--out', type=Path)
    parser.add_argument('--seeds', type=int, nargs='+', help='Replay the recorded seed list (non-official)')
    parser.add_argument('--seed-count', type=int, help='Number of fresh random scenarios; default from profile')
    parser.add_argument('--ticks', type=int, help='Override episode length; marks output as practice')
    parser.add_argument('--practice', action='store_true')
    args = parser.parse_args()
    if (args.jobs is not None and args.jobs < 1) or (args.ticks is not None and args.ticks < 2):
        parser.error('jobs must be positive and ticks must be at least 2')
    if args.seeds is not None and len(set(args.seeds)) != len(args.seeds):
        parser.error('duplicate seeds are not allowed')
    cfg = load_profile(problem)
    count = cfg['seed_count'] if args.seed_count is None else args.seed_count
    if count < 1:
        parser.error('seed-count must be positive')
    if args.seeds is not None:
        if args.seed_count is not None and args.seed_count != len(args.seeds):
            parser.error('seed-count must match the explicit seed list')
        seeds = args.seeds
    else:
        seeds = draw_seeds(count, cfg.get('calibration_seeds', []))
    official = (not args.practice and args.seeds is None and args.ticks is None
                and official_seed_count(problem, count, cfg['seed_count']))
    global SANDBOX
    import submission_sandbox
    SANDBOX = submission_sandbox.official_ready() if official else submission_sandbox.available()
    out = args.out or ROOT / problem / 'results' / ('leaderboard.json' if official else 'practice_v2.json')
    if not official and out.resolve() == (ROOT / problem / 'results/leaderboard.json').resolve():
        parser.error('practice results must not overwrite the official leaderboard')
    candidates = []
    ext = cfg.get('submission_ext', '.py')
    if args.bots:
        for name in args.bots:
            paths = [Path(name), args.submissions / (name.removesuffix(ext) + ext)]
            if ext != '.py':
                paths.append(args.submissions / (name.removesuffix('.py') + '.py'))
            for kind in ('baselines', 'reference'):
                paths.append(ROOT / problem / 'agents' / kind / (name.removesuffix(ext) + ext))
                if ext != '.py':
                    paths.append(ROOT / problem / 'agents' / kind / (name.removesuffix('.py') + '.py'))
            found = next((p.resolve() for p in paths if p.is_file()), None)
            if found is None:
                parser.error(f'candidate not found: {name}')
            candidates.append(found)
    else:
        candidates = sorted(p.resolve() for p in args.submissions.glob('*' + ext) if not p.name.startswith('_'))
    if not candidates:
        parser.error('no candidates found')
    if len({p.stem for p in candidates}) != len(candidates):
        parser.error('candidate names must be unique')
    profile_id = profile_fingerprint(cfg)
    capacity = detect_cpu_capacity()
    budget = resource_budget(capacity, detect_memory_capacity(), cfg.get('game_memory_bytes', GAME_MEMORY_ESTIMATE))
    report = dict(scoring_version=VERSION, official=official, problem=problem, max_score=100,
                  profile_id=profile_id, seeds=seeds,
                  execution={'mode': 'auto' if args.jobs is None else 'manual',
                             'requested_jobs': args.jobs, 'cpu': capacity, 'resources': budget,
                             'sandbox': SANDBOX},
                  sampling={'mode': 'random' if args.seeds is None else 'replay',
                            'source': 'os_csprng' if args.seeds is None else 'explicit_seed_list',
                            'seed_count': len(seeds)},
                  ticks=cfg.get('ticks') if args.ticks is None else args.ticks,
                  component_weights=cfg['component_weights'], margin_scale=cfg.get('margin_scale'),
                  opponents=cfg['opponents'], calibration=cfg.get('calibration'),
                  ci_method='95% percentile bootstrap of complete seed blocks; 2000 resamples',
                  table=[], games={})
    print(f"{problem}: {VERSION}, {'OFFICIAL' if official else 'PRACTICE'}, profile={profile_id[:12]}", flush=True)
    print(f"  {len(seeds)} seeds; source={report['sampling']['source']}; seeds={seeds}", flush=True)
    for candidate in candidates:
        source_digest = digest(candidate)
        jobs = make_jobs(problem, candidate, cfg, seeds, args.ticks)
        for job in jobs:
            job['official'] = official
        workers = worker_count(args.jobs, len(jobs), budget)
        report['execution']['workers'] = workers
        print(f"  CPU logical={capacity['logical_cpus']}, available={capacity['available_cpus']}; "
              f"reserved={budget['reserved_cpus']}; concurrent games={workers} "
              f"({report['execution']['mode']}, requested={args.jobs})", flush=True)
        games, scheduler = run_scheduled(
            jobs, workers, budget,
            on_complete=lambda completed: print(f'  {candidate.stem}: {completed}/{len(jobs)} games', flush=True))
        report['execution']['scheduler'] = scheduler
        row = aggregate(candidate, games, cfg, jobs)
        if digest(candidate) != source_digest:
            raise RuntimeError(f'Submission changed during evaluation: {candidate}')
        row['submission_sha256'] = source_digest
        sandboxes = sorted({g['result']['sandbox'] for g in games
                            if isinstance(g.get('result'), dict) and 'sandbox' in g['result']})
        if sandboxes:
            row['sandbox'] = sandboxes
        report['table'].append(row)
        report['games'][candidate.stem] = games
        print(f"  {candidate.stem}: {row['score']:.2f}/100, components={row['components']}, CI95={row['ci95']}", flush=True)
    report['table'].sort(key=lambda r: (-r['score'], r['bot']))
    if load_profile(problem) != cfg:
        raise RuntimeError('Frozen profile changed during evaluation')
    write_report(out, report)
    print(f'Results: {out}', flush=True)


if __name__ == '__main__':
    if sys.argv[1:] == ['--worker']:
        sys.stdin.reconfigure(encoding='utf-8')
        sys.stdout.reconfigure(encoding='utf-8')
        sys.stderr.reconfigure(encoding='utf-8')
        specification = json.load(sys.stdin)
        applied = apply_resource_policy(specification.get('resource_policy'))
        if specification.get('sandbox'):
            import submission_sandbox
            submission_sandbox.install(specification['problem'])
        with contextlib.redirect_stdout(sys.stderr):
            game = run_game(specification)
        game['resource_policy_applied'] = applied
        print(json.dumps(game, ensure_ascii=False, allow_nan=False))
    else:
        raise SystemExit('Use organizer/<problem>/scripts/score.py')
