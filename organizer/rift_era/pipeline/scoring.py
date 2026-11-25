"""裂陆纪元 scoring_v2 hooks used by ``organizer/strategy_scoring.py``.

Formula (designs/rift_era/07 §3)::

    vtilde(V) = V + 10            if V >= 0
              = 100 / (10 - V)    otherwise
    R         = vtilde(V_player) / vtilde(V_shadow)
    score     = 50 + 50 * tanh(0.6 * ln R)            (0..100, continuous)

Supports an arbitrary scoring pool of ``N >= 1`` frozen opponents listed in
``scoring_v2.json["opponents"]``. For the k-th seed:

* Candidate sits at ``seat = k mod 8``.
* The 8-seat *shadow match* fills all 8 seats deterministically from the
  configured scoring pool: the designated shadow opponent at ``seat`` rotates
  across all ``N`` opponents via ``shadow_idx = (k + k // lcm(N, 8)) % N``
  (balancing both opponents and seats even when ``N`` shares a factor with 8),
  and the remaining 7 seats cycle evenly across all ``N`` opponents
  (``lineup[s] = opponents[(shadow_idx + ((s - seat) % 8)) % N]``).
* When ``N == 1``, every seat is ``opponents[0]`` (exact single-opponent table).
* The candidate match replaces ONLY ``seat`` with the submission while keeping
  the other 7 seats identical to the shadow match.

Shadow results are cached per ``(seed, ticks, map, engine hash, rules hash,
ordered 8-seat lineup hashes, sandbox, official)`` under
``organizer/rift_era/results/shadow_cache/`` so every candidate in a batch is
compared against the exact same shadow and stale caches never collide when any
opponent in the pool changes or is added.

Precision: V is exact (``Fraction``); ``seat_score()`` and ``seat_score_interval()``
wire ``engine.scoring.relative_ratio()`` and outward-rounded adaptive-precision
``engine.scoring.score_interval()`` so 07 §3's precision contract is honored
and huge rationals never overflow.
"""

from __future__ import annotations

import contextlib
from fractions import Fraction
try:
    import fcntl
except ImportError:  # pragma: no cover - Windows host has no fcntl
    fcntl = None
import hashlib
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading

if os.name == 'nt':
    import ctypes
    import msvcrt

    class _Overlapped(ctypes.Structure):
        _fields_ = [
            ('Internal', ctypes.c_size_t),
            ('InternalHigh', ctypes.c_size_t),
            ('Offset', ctypes.c_uint32),
            ('OffsetHigh', ctypes.c_uint32),
            ('hEvent', ctypes.c_void_p),
        ]

    _k32 = ctypes.WinDLL('kernel32', use_last_error=True)
    _k32.LockFileEx.argtypes = [
        ctypes.c_void_p, ctypes.c_uint32, ctypes.c_uint32,
        ctypes.c_uint32, ctypes.c_uint32, ctypes.POINTER(_Overlapped),
    ]
    _k32.LockFileEx.restype = ctypes.c_int
    _k32.UnlockFileEx.argtypes = [
        ctypes.c_void_p, ctypes.c_uint32, ctypes.c_uint32,
        ctypes.c_uint32, ctypes.POINTER(_Overlapped),
    ]
    _k32.UnlockFileEx.restype = ctypes.c_int


@contextlib.contextmanager
def _file_lock(path: Path):
    with open(path, 'a+b') as lock:
        if fcntl is not None:
            fcntl.flock(lock, fcntl.LOCK_EX)
            yield
        elif os.name == 'nt':
            handle = msvcrt.get_osfhandle(lock.fileno())
            ov = _Overlapped()
            if not _k32.LockFileEx(handle, 0x2, 0, 1, 0, ctypes.byref(ov)):
                raise ctypes.WinError(ctypes.get_last_error())
            try:
                yield
            finally:
                _k32.UnlockFileEx(handle, 0, 1, 0, ctypes.byref(ov))
        else:  # pragma: no cover
            yield


RIFT_ROOT = Path(__file__).resolve().parents[1]
SHADOW_CACHE = RIFT_ROOT / 'results' / 'shadow_cache'


def vtilde(value: Fraction) -> Fraction:
    from ..engine.scoring import positive_transform
    return positive_transform(Fraction(value))


def _log(value: Fraction) -> float:
    if value <= 0:
        raise ValueError('vtilde must be positive')
    return math.log(value.numerator) - math.log(value.denominator)


def seat_score_interval(v_player, v_shadow, *, precision: int = 50):
    """Outward-rounded adaptive-precision Decimal ScoreInterval from exact V values."""
    from ..engine.scoring import relative_ratio, score_interval
    ratio = relative_ratio(Fraction(v_player), Fraction(v_shadow))
    if ratio == 1:
        return score_interval(ratio, precision=precision)
    prec = max(50, int(precision))
    interval = score_interval(ratio, precision=prec)
    while ((ratio > 1 and interval.lower <= 50) or (ratio < 1 and interval.upper >= 50)) and prec < 400:
        prec *= 2
        interval = score_interval(ratio, precision=prec)
    return interval


def seat_score(v_player, v_shadow) -> float:
    """0..100 per-match score from exact V values via outward-rounded score_interval."""
    vp, vs = Fraction(v_player), Fraction(v_shadow)
    if vp == vs:
        return 50.0
    interval = seat_score_interval(vp, vs)
    mid = (interval.lower + interval.upper) / 2
    score = float(mid)
    return min(100.0, max(0.0, score))


def parse_exact(entry) -> Fraction:
    text = entry['exact'] if isinstance(entry, dict) else entry
    numerator, denominator = str(text).split('/')
    return Fraction(int(numerator), int(denominator))


def profile_path(relative: str) -> Path:
    """Resolve a scoring_v2 path: ``bots/...`` under the tier-bot root, else under rift_era/."""
    from .match_runner import bots_root
    if relative.startswith('bots/'):
        root = bots_root().resolve()
        path = (root / relative[len('bots/'):]).resolve()
        if not path.is_relative_to(root):
            raise ValueError(f'profile path escapes bots root: {relative}')
        return path
    path = (RIFT_ROOT / relative).resolve()
    if not path.is_relative_to(RIFT_ROOT):
        raise ValueError(f'profile path escapes rift_era: {relative}')
    return path


def opponent_seat_path(relative: str) -> Path:
    """Resolve a scoring_v2 opponent path (``bots/<id>/main.py`` or ``bots/<id>.zip``) to a seat spec."""
    path = profile_path(relative)
    return path if path.suffix == '.zip' else path.parent


def shadow_lineup(opponents: list[dict], index: int) -> tuple[int, dict, list[dict]]:
    """Deterministic 8-seat shadow lineup for the ``index``-th seed (arbitrary ``N >= 1`` pool).

    * ``seat = index % 8`` is the candidate seat.
    * ``shadow_idx = (index + index // lcm(N, 8)) % N`` chooses the replaced shadow
      opponent at ``seat`` so every opponent is balanced across both seeds and seats.
    * ``lineup[s] = opponents[(shadow_idx + ((s - seat) % 8)) % N]`` populates all
      8 seats evenly from the scoring pool (reducing to ``[opp0] * 8`` when ``N == 1``).
    """
    if not opponents:
        raise ValueError('scoring_v2 opponents pool must be non-empty')
    n = len(opponents)
    seat = index % 8
    period = math.lcm(n, 8)
    shadow_idx = (index + index // period) % n
    lineup = [opponents[(shadow_idx + ((s - seat) % 8)) % n] for s in range(8)]
    return seat, opponents[shadow_idx], lineup


def make_jobs(candidate: str, cfg: dict, seeds, ticks=None) -> list[dict]:
    """One job per seed; candidate replaces seat ``k mod 8`` in the deterministic N-opponent table."""
    from .match_runner import versions
    current = versions()
    stale = [k for k in ('engine_hash', 'rules_hash') if cfg.get(k) != current[k]]
    if stale:
        raise ValueError(f'rift_era profile frozen for another {stale}; run scripts/freeze_profile.py (new revision)')
    opponents = cfg.get('opponents') or []
    if not opponents:
        raise ValueError('rift_era profile has no scoring opponents')
    resolved_seats = {o['id']: str(opponent_seat_path(o['path'])) for o in opponents}
    jobs = []
    for index, seed in enumerate(seeds):
        seat, shadow_opp, lineup = shadow_lineup(opponents, index)
        shadow_seats = [resolved_seats[o['id']] for o in lineup]
        shadow_seat_ids = [o['id'] for o in lineup]
        jobs.append(dict(
            problem='rift_era',
            candidate=str(Path(candidate).resolve()),
            seed=seed,
            ticks=cfg['ticks'] if ticks is None else ticks,
            match=dict(cfg.get('match', {})),
            game_timeout_s=cfg['game_timeout_s'],
            component='adv',
            opponent=shadow_opp['id'],
            opponent_path=resolved_seats[shadow_opp['id']],
            shadow_seats=shadow_seats,
            shadow_seat_ids=shadow_seat_ids,
            seat=seat,
            config_id='0',
        ))
    return jobs


def _run_match(seed: int, seats: list[str], ticks: int, match: dict, out: Path, timeout: float,
               official: bool = False) -> dict:
    command = [sys.executable, '-X', 'utf8', '-m', 'organizer.rift_era.pipeline.match_runner',
               '--seed', str(seed), '--ticks', str(ticks), '--seats', ','.join(seats),
               '--sandbox', match.get('sandbox', 'auto'), '--out', str(out)]
    if match.get('legacy_map'):
        command.append('--legacy-map')
    if match.get('cgroup'):
        command += ['--cgroup', str(match['cgroup'])]
    if official:
        command.append('--official')
    result = subprocess.run(command, cwd=RIFT_ROOT.parents[1], capture_output=True, text=True, timeout=timeout,
                            env={**os.environ, 'PYTHONHASHSEED': '0', 'PYTHONUTF8': '1'})
    if result.returncode:
        raise RuntimeError(f'match runner failed (seed {seed}): {result.stderr[-4000:]}')
    return json.loads(out.read_text(encoding='utf-8'))


def shadow_key(seed: int, ticks: int, match: dict, lineup_sha) -> dict:
    """Cache key for one 8-seat shadow match; covers engine, rules, map, sandbox & full lineup hashes."""
    from .match_runner import versions
    v = versions()
    if isinstance(lineup_sha, str):
        hashes = [lineup_sha] * 8
    else:
        hashes = list(lineup_sha)
    if len(hashes) != 8:
        raise ValueError(f'shadow_key requires 8 seat hashes, got {len(hashes)}')
    return {
        'seed': seed,
        'ticks': ticks,
        'legacy_map': bool(match.get('legacy_map')),
        'sandbox': match.get('sandbox', 'auto'),
        'engine_hash': v['engine_hash'],
        'rules_hash': v['rules_hash'],
        'lineup_sha256': hashes,
    }


def shadow_match(seed: int, ticks: int, match: dict, shadow_seats, timeout: float,
                 cache_dir: Path | None = None, official: bool = False, force_refresh: bool = False) -> dict:
    """8-seat shadow match for this seed & lineup; computed once and cached (file-locked)."""
    from .match_runner import seat_sha256
    if isinstance(shadow_seats, (str, Path)):
        seats = [str(shadow_seats)] * 8
    else:
        seats = [str(s) for s in shadow_seats]
    if len(seats) != 8:
        raise ValueError(f'shadow_match requires 8 seats, got {len(seats)}')
    hash_cache = {}
    lineup_sha = []
    for s in seats:
        if s not in hash_cache:
            hash_cache[s] = seat_sha256(Path(s))
        lineup_sha.append(hash_cache[s])
    key = shadow_key(seed, ticks, match, lineup_sha) | {'official': official}
    name = 'shadow-' + hashlib.sha256(json.dumps(key, sort_keys=True).encode()).hexdigest()[:24]
    cache_dir = SHADOW_CACHE if cache_dir is None else cache_dir
    cache_dir.mkdir(parents=True, exist_ok=True)
    target = cache_dir / f'{name}.json'
    with _file_lock(cache_dir / f'{name}.lock'):
        if target.exists() and not force_refresh:
            data = json.loads(target.read_text(encoding='utf-8'))
            if data.get('shadow_key') == key and data.get('valid') is True:
                return data
        with tempfile.TemporaryDirectory(prefix='rift-shadow-') as temp:
            data = _run_match(seed, seats, ticks, match, Path(temp) / 'shadow.json', timeout, official)
        data['shadow_key'] = key
        tmp = target.with_name(f'{target.stem}.{os.getpid()}.{threading.get_ident()}.tmp')
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1) + '\n', encoding='utf-8', newline='\n')
        tmp.replace(target)
        return data


def run_scored_game(spec: dict) -> dict:
    """Worker entry: shadow (cached) + candidate match, scored for the candidate seat."""
    seat, seed, ticks, match = spec['seat'], spec['seed'], spec['ticks'], spec.get('match', {})
    timeout = float(spec['game_timeout_s'])
    owner = f'n{seat}'
    official = bool(spec.get('official'))
    shadow_seats = list(spec.get('shadow_seats') or [spec['opponent_path']] * 8)
    if len(shadow_seats) != 8:
        raise ValueError(f'expected 8 shadow seats, got {len(shadow_seats)}')
    shadow = shadow_match(seed, ticks, match, shadow_seats, timeout, official=official)
    seats = list(shadow_seats)
    seats[seat] = spec['candidate']
    with tempfile.TemporaryDirectory(prefix='rift-cand-') as temp:
        try:
            result = _run_match(seed, seats, ticks, match, Path(temp) / 'cand.json', timeout, official)
        except RuntimeError as exc:
            # A bad zip (no main.py, unsafe paths) is a candidate fault; engine faults stay errors.
            text = str(exc)
            if 'ERR_ARCHIVE' in text or 'bad seat' in text or 'main.py' in text:
                return dict(score=0.0, raw={}, candidate_fault=True, opponent_fault=False,
                            faults={'load_error': text[-1000:]}, result={'reason': 'load_error'})
            raise
    if shadow['sandbox'] != result['sandbox']:
        if shadow.get('sandbox') == 'none' and result.get('sandbox') != 'none':
            shadow = shadow_match(seed, ticks, match, shadow_seats, timeout, official=official, force_refresh=True)
        if shadow['sandbox'] != result['sandbox']:
            raise RuntimeError('shadow and candidate matches used different sandboxes')
    v_player, v_shadow = parse_exact(result['nations'][owner]['V']), parse_exact(shadow['nations'][owner]['V'])
    from ..engine.scoring import relative_ratio
    ratio = relative_ratio(v_player, v_shadow)
    interval = seat_score_interval(v_player, v_shadow)
    score = seat_score(v_player, v_shadow)
    me = result['nations'][owner]
    opponents = [n for p, n in result['nations'].items() if p != owner]
    time_row = me.get('strategy_time') or {}
    lineup_ids = list(spec.get('shadow_seat_ids') or [Path(s).name for s in shadow_seats])
    return dict(score=score,
                raw={'V': float(v_player), 'V_shadow': float(v_shadow),
                     'log_R': _log(vtilde(v_player)) - _log(vtilde(v_shadow)),
                     'opponent_mean_V': sum(n['V']['float'] for n in opponents) / len(opponents)},
                candidate_fault=bool(me['faults'] or time_row.get('custodial') or time_row.get('timeouts')),
                opponent_fault=any(n['faults'] for n in opponents),
                faults={p: n['faults'] for p, n in result['nations'].items()},
                result={'sandbox': result['sandbox'], 'sandbox_reason': result['sandbox_reason'],
                        'R_exact': f'{ratio.numerator}/{ratio.denominator}',
                        'score_interval': [str(interval.lower), str(interval.upper)],
                        'shadow_opponent': spec.get('opponent', lineup_ids[seat]),
                        'shadow_lineup': lineup_ids,
                        'engine_version': result['engine_version'], 'engine_hash': result['engine_hash'],
                        'rules_version': result['rules_version'], 'rules_hash': result['rules_hash'],
                        'state_hash': result['state_hash'], 'shadow_state_hash': shadow['state_hash'],
                        'ticks': result['ticks'], 'practice_clock': result['practice_clock'],
                        'map_version': result['map_version'], 'strategy_time': time_row,
                        'bot_sha256': me['bot_sha256'], 'wall_seconds': result['wall_seconds'],
                        'shadow_wall_seconds': shadow['wall_seconds']})
