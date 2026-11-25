"""本地练习对局与练习计分（选手包用，非正式，无沙箱）。

    # 1) 8 席对局：模板 bot 坐 n0，其余 7 席公开的 Advanced
    python3 scripts/local_match.py match --seats agents/template,advanced,advanced,advanced,advanced,advanced,advanced,advanced --ticks 400
    # 2) 练习计分：与正式算法相同（影子局 vs 替换局），但对手池换成公开 bot（正式对手池不公开）
    python3 scripts/local_match.py score --bot agents/template --opponent advanced --seat 3 --seed 7 --ticks 400

席位写法：none | simple | medium | advanced | template | 目录（含 main.py） | .zip。
--ticks 必须是 40 的倍数；正式局 28,800 tick（约 5 小时），本地建议 400–2,000 先看趋势。
--legacy-map 跳过正式地图的 150 tick 开局验收（每局省约 100 秒），只用于快速调试。
"""
from __future__ import annotations

import argparse
from fractions import Fraction
import json
import math
from pathlib import Path
import sys
import time

KIT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(KIT / 'runtime' / 'pkg'))

NAMED = {'medium': KIT / 'agents' / 'medium', 'advanced': KIT / 'agents' / 'advanced',
         'template': KIT / 'agents' / 'template'}


def seat(spec: str):
    spec = spec.strip()
    if spec in ('none', 'simple'):
        return spec
    path = NAMED.get(spec, Path(spec))
    if path.is_dir() and (path / 'main.py').is_file():
        return path.resolve()
    if path.is_file() and path.suffix == '.zip':
        return path.resolve()
    raise SystemExit(f'席位 {spec!r} 无效：要 none/simple/medium/advanced/template、含 main.py 的目录或 .zip')


def seats_from(text: str) -> tuple:
    tokens = [t for t in text.split(',') if t.strip()]
    if len(tokens) == 1:
        tokens *= 8
    if len(tokens) != 8:
        raise SystemExit('--seats 需要 1 个或 8 个，用逗号分隔')
    return tuple(seat(t) for t in tokens)


def play(seed: int, seats: tuple, ticks: int, legacy_map: bool) -> dict:
    from rift_era.engine.catalog import Catalog
    from rift_era.engine.dev_seats import run_seated_match
    from rift_era.engine.initialization import initialize
    from rift_era.engine.match import accepted_world, match_economy
    from rift_era.engine.world import generate_candidate
    if ticks <= 0 or ticks % 40:
        raise SystemExit('--ticks 必须是 40 的正整数倍')
    started = time.monotonic()
    catalog = Catalog()
    world = generate_candidate(seed) if legacy_map else accepted_world(catalog, seed)
    engine = match_economy(catalog, initialize(catalog, world), total_ticks=ticks, fingerprint_each_tick=False)
    report = run_seated_match(engine, seats)
    nations = {}
    for owner, row in sorted(report['nations'].items()):
        v = row['V']
        v = Fraction(*v['$rational']) if isinstance(v, dict) else Fraction(v)
        nations[owner] = {'seat': report['seats'][owner], 'V': float(v), 'V_exact': f'{v.numerator}/{v.denominator}',
                          'faults': len(report['faults'].get(owner, ())),
                          'strategy_time': report['strategy_time'].get(owner)}
    return {'official': False, 'seed': seed, 'ticks': ticks, 'legacy_map': legacy_map,
            'wall_seconds': round(time.monotonic() - started, 1), 'nations': nations}


def vtilde(v: Fraction) -> Fraction:
    return v + 10 if v >= 0 else Fraction(100) / (10 - v)


def score(v_player: Fraction, v_shadow: Fraction) -> float:
    ln = lambda x: math.log(x.numerator) - math.log(x.denominator)
    return 50 + 50 * math.tanh(0.6 * (ln(vtilde(v_player)) - ln(vtilde(v_shadow))))


def exact(row) -> Fraction:
    n, d = row['V_exact'].split('/')
    return Fraction(int(n), int(d))


def main() -> int:
    parser = argparse.ArgumentParser(description='裂陆纪元本地练习（非正式）')
    sub = parser.add_subparsers(dest='cmd', required=True)
    m = sub.add_parser('match')
    m.add_argument('--seats', required=True)
    s = sub.add_parser('score')
    s.add_argument('--bot', required=True)
    s.add_argument('--opponent', default='advanced', help='影子局与另 7 席的对手（medium/advanced/目录/zip）')
    s.add_argument('--seat', type=int, default=0, help='你的席位 0..7（正式评测第 k 个 seed 用 k mod 8）')
    for p in (m, s):
        p.add_argument('--seed', type=int, default=1)
        p.add_argument('--ticks', type=int, default=400)
        p.add_argument('--legacy-map', action='store_true')
        p.add_argument('--out', type=Path)
    args = parser.parse_args()
    if args.cmd == 'match':
        result = play(args.seed, seats_from(args.seats), args.ticks, args.legacy_map)
    else:
        if not 0 <= args.seat < 8:
            raise SystemExit('--seat 取 0..7')
        opponent = seat(args.opponent)
        shadow = play(args.seed, (opponent,) * 8, args.ticks, args.legacy_map)
        seats = [opponent] * 8
        seats[args.seat] = seat(args.bot)
        mine = play(args.seed, tuple(seats), args.ticks, args.legacy_map)
        owner = f'n{args.seat}'
        vp, vs = exact(mine['nations'][owner]), exact(shadow['nations'][owner])
        result = {'official': False, 'practice_opponent': str(args.opponent), 'seed': args.seed, 'seat': args.seat,
                  'ticks': args.ticks, 'V_player': float(vp), 'V_shadow': float(vs),
                  'R': float(vtilde(vp) / vtilde(vs)), 'score': round(score(vp, vs), 4),
                  'note': '练习分：对手是公开 bot；正式分对手是不公开的评分对手，数值会不同',
                  'shadow_match': shadow, 'your_match': mine}
    text = json.dumps(result, ensure_ascii=False, indent=1)
    out = args.out or KIT / 'results' / f'{args.cmd}_seed{args.seed}_{args.ticks}t.json'
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text + '\n', encoding='utf-8')
    summary = {k: v for k, v in result.items() if k not in ('shadow_match', 'your_match', 'nations')}
    if 'nations' in result:
        summary['V'] = {p: round(n['V'], 3) for p, n in result['nations'].items()}
    print(json.dumps(summary, ensure_ascii=False, indent=1))
    print(f'完整结果：{out}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
