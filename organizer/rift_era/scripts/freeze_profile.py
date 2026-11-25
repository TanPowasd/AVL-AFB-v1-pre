"""(Re)freeze organizer/rift_era/config/scoring_v2.json for an arbitrary N >= 1 opponent pool.

Run after any scoring opponent, pipeline file, or engine/rules revision changes; it bumps
``profile_revision`` and locks SHA-256 hashes of all registered opponent packages plus
pipeline files so earlier leaderboards become stale (score_all reports ``stale_profile``).

Examples::

    # Re-freeze using opponents currently listed in config/scoring_v2.json (one-command workflow
    # after adding a crowdsourced opponent to bots/ and listing it in scoring_v2.json):
    python3 organizer/rift_era/scripts/freeze_profile.py --revision 2026-11-21-rift-3style-v2

    # Explicitly specify N >= 1 opponents (directory under bots/<id>/ with main.py, or bots/<id>.zip):
    python3 organizer/rift_era/scripts/freeze_profile.py --revision 2026-11-21-rift-3style-v2 \
        --opponents hegemon,arbiter,verdant
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT.parents[1]))

from organizer.rift_era.pipeline.match_runner import bots_root, versions  # noqa: E402
from organizer.rift_era.pipeline.scoring import profile_path  # noqa: E402

DEFAULT_OPPONENTS = ('hegemon', 'arbiter', 'verdant')
PIPELINE_FROZEN = (
    'pipeline/__init__.py',
    'pipeline/hosting.py',
    'pipeline/match_runner.py',
    'pipeline/scoring.py',
)


def resolve_opponent_entry(spec: str) -> dict:
    """Resolve an opponent spec (bot id, ``bots/<id>/main.py`` or ``bots/<id>.zip``) to an entry dict."""
    token = spec.strip()
    if not token:
        raise ValueError('empty opponent spec')
    if token.startswith('bots/'):
        rel = token.replace('\\', '/')
        target = profile_path(rel)
        if not target.is_file():
            raise FileNotFoundError(f'opponent entry not found: {rel} (resolved {target})')
        opp_id = target.stem if target.suffix == '.zip' else target.parent.name
        return {'id': opp_id, 'path': rel}
    broot = bots_root().resolve()
    dir_entry = broot / token / 'main.py'
    if dir_entry.is_file():
        return {'id': token, 'path': f'bots/{token}/main.py'}
    zip_name = token if token.endswith('.zip') else f'{token}.zip'
    zip_entry = broot / zip_name
    if zip_entry.is_file():
        return {'id': zip_entry.stem, 'path': f'bots/{zip_name}'}
    raise FileNotFoundError(f'opponent {token!r} not found under {broot} (expected <id>/main.py or <id>.zip)')


def collect_opponent_files(opponents: list[dict]) -> list[str]:
    """Return all deterministic relative source paths to freeze for every opponent package (N >= 1)."""
    broot = bots_root().resolve()
    frozen: list[str] = []
    seen: set[str] = set()
    for opp in opponents:
        rel = opp['path']
        target = profile_path(rel)
        if not target.is_file():
            raise FileNotFoundError(f'opponent source missing: {rel} ({target})')
        if target.suffix == '.zip':
            candidates = [rel]
        else:
            pkg_dir = target.parent
            candidates = []
            for p in sorted(pkg_dir.rglob('*'), key=lambda item: item.relative_to(pkg_dir).parts):
                if not p.is_file():
                    continue
                parts = p.relative_to(pkg_dir).parts
                if '__pycache__' in parts or p.suffix in ('.pyc', '.pyo') or any(part.startswith('.') for part in parts):
                    continue
                candidates.append('bots/' + p.relative_to(broot).as_posix())
        for item in candidates:
            if item not in seen:
                seen.add(item)
                frozen.append(item)
    return frozen


def load_opponents_from_config(config_path: Path) -> list[dict]:
    if not config_path.is_file():
        return [resolve_opponent_entry(name) for name in DEFAULT_OPPONENTS]
    data = json.loads(config_path.read_text(encoding='utf-8'))
    opponents = data.get('opponents') or []
    if not opponents:
        return [resolve_opponent_entry(name) for name in DEFAULT_OPPONENTS]
    return [{'id': str(o['id']), 'path': str(o['path'])} for o in opponents]


def build(revision: str, opponents: list[dict] | None = None) -> dict:
    if opponents is None:
        opponents = [resolve_opponent_entry(name) for name in DEFAULT_OPPONENTS]
    if not opponents:
        raise ValueError('opponents pool must be non-empty')
    ids = [o['id'] for o in opponents]
    if len(set(ids)) != len(ids):
        raise ValueError(f'duplicate opponent ids: {ids}')
    v = versions()
    frozen_list = collect_opponent_files(opponents) + list(PIPELINE_FROZEN)
    frozen_files = {rel: hashlib.sha256(profile_path(rel).read_bytes()).hexdigest() for rel in frozen_list}
    return {
        'version': 'scoring_v2',
        'problem': 'rift_era',
        'submission_ext': '.zip',
        'opponents': opponents,
        'component_weights': {'adv': 1.0},
        'score_formula': (
            '50+50*tanh(0.6*ln(vtilde(V_player)/vtilde(V_shadow))); '
            'vtilde=V+10 (V>=0) else 100/(10-V); '
            'shadow = same seed/seat/clock with deterministic N-opponent 8-seat table lineup '
            'and rotating shadow opponent at seat k mod 8 (designs/rift_era/07 §3)'
        ),
        'seat_rotation': 'seat = k mod 8 for the k-th seed; shadow_idx = (k + k // lcm(N, 8)) mod N',
        'ticks': 28800,
        'match': {'sandbox': 'auto', 'legacy_map': False},
        'game_timeout_s': 43200,
        'game_memory_bytes': 1024**3,  # measured ~0.25 GiB (engine+8 seats, 80 ticks legacy); ~4x headroom
        'frozen_files': frozen_files,
        'engine_version': v['engine_version'],
        'engine_hash': v['engine_hash'],
        'rules_version': v['rules_version'],
        'rules_hash': v['rules_hash'],
        'seed_count': 64,
        'scenario_sampling': 'per_run_random',
        'seed_source': 'os_csprng',
        'calibration_seeds': [],
        'profile_revision': revision,
        'private': 'organizer only; never exported to the contestant kit',
    }


def parse_opponent_specs(tokens: list[str]) -> list[dict]:
    specs = [t.strip() for item in tokens for t in item.split(',') if t.strip()]
    return [resolve_opponent_entry(s) for s in specs]


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--revision', required=True)
    parser.add_argument('--opponents', nargs='+',
                        help='Optional N >= 1 opponent IDs or paths (comma or space separated). '
                             'If omitted, reads opponents from --out if present, else defaults to hegemon,arbiter,verdant.')
    parser.add_argument('--out', type=Path, default=ROOT / 'config' / 'scoring_v2.json')
    args = parser.parse_args(argv)
    opponents = parse_opponent_specs(args.opponents) if args.opponents else load_opponents_from_config(args.out)
    profile = build(args.revision, opponents)
    args.out.write_text(json.dumps(profile, ensure_ascii=False, indent=2) + '\n', encoding='utf-8', newline='\n')
    print(f'wrote {args.out} (bots root {bots_root()}, opponents={[o["id"] for o in opponents]}, '
          f'frozen={len(profile["frozen_files"])} files)')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
