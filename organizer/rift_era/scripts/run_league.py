"""组委会/开发端多策略联赛 + ELO & 场均 V 排行榜。

支持两种联赛赛制：
- `4v4`（默认）：全轮环两两 4v4 镜像对局，公平衡量双策略直接对抗强弱。
- `ffa`：8 席轮转混战，自动按席位轮换把所有参赛策略编排进 8 席对局。

用法示例：
    python3 organizer/rift_era/scripts/run_league.py medium advanced super --seed-count 2 --ticks 80 --legacy-map
    python3 organizer/rift_era/scripts/run_league.py agents/submissions/*.zip advanced --mode 4v4 --seeds 7,11 --ticks 200 --legacy-map
"""
from __future__ import annotations

import argparse
from fractions import Fraction
import glob
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT.parents[1]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(ROOT / 'scripts'))

from organizer.rift_era.pipeline import match_runner  # noqa: E402
from organizer.rift_era.score import elo  # noqa: E402
import run_match as run_match_mod  # noqa: E402

DEFAULT_SEEDS = (7, 11, 13, 17, 19, 23, 29, 31)


def collect_specs(patterns: list[str]) -> list[tuple[str | Path, str]]:
    expanded: list[str] = []
    for item in patterns:
        if any(ch in item for ch in '*?['):
            hits = sorted(glob.glob(item))
            expanded.extend(hits if hits else [item])
        else:
            expanded.append(item)
    resolved = []
    seen_labels: dict[str, int] = {}
    for raw in expanded:
        seat, label = run_match_mod.resolve_spec(raw)
        if label in seen_labels:
            seen_labels[label] += 1
            label = f'{label}#{seen_labels[label]}'
        else:
            seen_labels[label] = 1
        resolved.append((seat, label))
    return resolved


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description='裂陆纪元（rift_era）多策略 ELO 联赛')
    ap.add_argument('bots', nargs='+', help='至少 2 个参赛策略（tier 名 / 目录 / .zip / 通配符）')
    ap.add_argument('--mode', choices=('4v4', 'ffa'), default='4v4', help='赛制：4v4 全轮环（默认）或 ffa 8 席混战')
    ap.add_argument('--seeds', default='', help='逗号分隔种子列表（覆盖 --seed-count）')
    ap.add_argument('--seed-count', type=int, default=2, help='默认种子表前 N 个（默认 2）')
    ap.add_argument('--no-swap', action='store_true', help='4v4 赛制下禁用交换镜像局')
    ap.add_argument('--ticks', type=int, default=200, help='每局 tick 数（40 的正整数倍，默认 200）')
    ap.add_argument('--legacy-map', action='store_true', help='跳过 150 tick 开局验收，用于快速联赛')
    ap.add_argument('--sandbox', default='auto', choices=('auto', 'bwrap', 'windows', 'none', 'cgroup'))
    ap.add_argument('--k', type=float, default=elo.DEFAULT_K, help='ELO K 系数（默认 24）')
    ap.add_argument('--out', type=Path, default=None, help='输出目录（默认 results/<时间戳>/）')
    args = ap.parse_args(argv)

    if args.ticks <= 0 or args.ticks % 40:
        raise SystemExit('--ticks 必须是 40 的正整数倍')

    entries = collect_specs(args.bots)
    if len(entries) < 2:
        raise SystemExit('联赛至少需要 2 个有效策略')

    seeds = ([int(x.strip()) for x in args.seeds.split(',') if x.strip()]
             if args.seeds else list(DEFAULT_SEEDS[:max(1, args.seed_count)]))
    started = time.monotonic()

    ratings = {label: elo.DEFAULT_INIT_ELO for _, label in entries}
    book = {label: {'w': 0, 'd': 0, 'l': 0, 'vs': [], 'faults': 0} for _, label in entries}
    matches_log = []

    print(f'== 裂陆纪元联赛（{args.mode}）| {len(entries)} 策略 | {len(seeds)} seed | {args.ticks} ticks | K={args.k:g} ==')

    if args.mode == '4v4':
        swaps = (False,) if args.no_swap else (False, True)
        for i in range(len(entries)):
            for j in range(i + 1, len(entries)):
                sa, la = entries[i]
                sb, lb = entries[j]
                for seed in seeds:
                    for sw in swaps:
                        if not sw:
                            seats = [sa if idx % 2 == 0 else sb for idx in range(8)]
                            labels = [la if idx % 2 == 0 else lb for idx in range(8)]
                            a_idx, b_idx = [0, 2, 4, 6], [1, 3, 5, 7]
                        else:
                            seats = [sb if idx % 2 == 0 else sa for idx in range(8)]
                            labels = [lb if idx % 2 == 0 else la for idx in range(8)]
                            a_idx, b_idx = [1, 3, 5, 7], [0, 2, 4, 6]
                        rep = match_runner.run_match(
                            seed=seed, seats=seats, ticks=args.ticks,
                            legacy_map=args.legacy_map, sandbox=args.sandbox,
                        )
                        seat_vs = [(labels[idx], run_match_mod._exact_v(rep['nations'][f'n{idx}'])) for idx in range(8)]
                        mean_a = sum((seat_vs[idx][1] for idx in a_idx), Fraction(0)) / len(a_idx)
                        mean_b = sum((seat_vs[idx][1] for idx in b_idx), Fraction(0)) / len(b_idx)
                        fa = sum(int(rep['nations'][f'n{idx}']['faults']) for idx in a_idx)
                        fb = sum(int(rep['nations'][f'n{idx}']['faults']) for idx in b_idx)

                        ratings = elo.update_8seat_match(ratings, seat_vs, k=args.k)
                        book[la]['vs'].append(float(mean_a))
                        book[lb]['vs'].append(float(mean_b))
                        book[la]['faults'] += fa
                        book[lb]['faults'] += fb
                        lr = elo.log_ratio(mean_a, mean_b)
                        if abs(lr) <= elo.DEFAULT_DRAW_LOG_EPS:
                            book[la]['d'] += 1
                            book[lb]['d'] += 1
                            outcome = 'draw'
                        elif mean_a > mean_b:
                            book[la]['w'] += 1
                            book[lb]['l'] += 1
                            outcome = la
                        else:
                            book[lb]['w'] += 1
                            book[la]['l'] += 1
                            outcome = lb
                        matches_log.append({
                            'mode': '4v4', 'seed': seed, 'swapped': sw,
                            'A': la, 'B': lb, 'mean_V_A': float(mean_a), 'mean_V_B': float(mean_b),
                            'outcome': outcome, 'state_hash': rep['state_hash'],
                        })
                        print(f'  {la} vs {lb} (seed={seed}{" sw" if sw else ""}) -> {float(mean_a):.3f} vs {float(mean_b):.3f} [{outcome}]')
    else:
        n_bots = len(entries)
        for s_idx, seed in enumerate(seeds):
            seats = []
            labels = []
            for pos in range(8):
                seat, label = entries[(s_idx + pos) % n_bots]
                seats.append(seat)
                labels.append(label)
            rep = match_runner.run_match(
                seed=seed, seats=seats, ticks=args.ticks,
                legacy_map=args.legacy_map, sandbox=args.sandbox,
            )
            seat_vs = [(labels[idx], run_match_mod._exact_v(rep['nations'][f'n{idx}'])) for idx in range(8)]
            ratings = elo.update_8seat_match(ratings, seat_vs, k=args.k)
            by_bot: dict[str, list[Fraction]] = {}
            for idx, (lbl, v_ex) in enumerate(seat_vs):
                by_bot.setdefault(lbl, []).append(v_ex)
                book[lbl]['faults'] += int(rep['nations'][f'n{idx}']['faults'])
            means = {lbl: sum(vs, Fraction(0)) / len(vs) for lbl, vs in by_bot.items()}
            for lbl, mv in means.items():
                book[lbl]['vs'].append(float(mv))
            best_v = max(means.values())
            winners = [lbl for lbl, mv in means.items() if abs(elo.log_ratio(mv, best_v)) <= elo.DEFAULT_DRAW_LOG_EPS]
            for lbl in means:
                if lbl in winners:
                    book[lbl]['w' if len(winners) == 1 else 'd'] += 1
                else:
                    book[lbl]['l'] += 1
            matches_log.append({
                'mode': 'ffa', 'seed': seed, 'seats': labels,
                'mean_V': {k: float(v) for k, v in means.items()}, 'winners': winners,
                'state_hash': rep['state_hash'],
            })
            print(f'  FFA seed={seed} -> winners={winners}  ' +
                  ' '.join(f'{k}:{float(v):.2f}' for k, v in sorted(means.items(), key=lambda kv: -kv[1])))

    rows = []
    for _, label in entries:
        b = book[label]
        vs = b['vs']
        rows.append({
            'name': label,
            'elo': round(ratings[label], 2),
            'w': b['w'],
            'd': b['d'],
            'l': b['l'],
            'avg_v': round(sum(vs) / len(vs), 4) if vs else 0.0,
            'best_v': round(max(vs), 4) if vs else 0.0,
            'faults': b['faults'],
        })
    rows.sort(key=lambda r: (-r['elo'], -r['avg_v'], r['name']))
    table = elo.render_table(rows)

    out_dir = args.out or (ROOT / 'results' / time.strftime('%Y%m%d-%H%M%S'))
    out_dir.mkdir(parents=True, exist_ok=True)
    elapsed = round(time.monotonic() - started, 2)
    md = (
        f'# 裂陆纪元联赛排行榜（{args.mode}，{len(seeds)} 种子，{args.ticks} tick，K={args.k:g}）\n\n'
        f'- 总用时：{elapsed:.1f}s\n'
        f'- 地图模式：{"legacy" if args.legacy_map else "atlas"}\n\n'
        f'{table}\n'
    )
    (out_dir / 'leaderboard.md').write_text(md, encoding='utf-8', newline='\n')
    (out_dir / 'league.json').write_text(
        json.dumps({'mode': args.mode, 'seeds': seeds, 'ticks': args.ticks, 'legacy_map': args.legacy_map,
                    'k': args.k, 'wall_seconds': elapsed, 'rows': rows, 'matches': matches_log},
                   ensure_ascii=False, indent=2) + '\n',
        encoding='utf-8',
        newline='\n',
    )
    print('\n' + table)
    print(f'\n结果目录：{out_dir}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
