"""组委会/开发端双策略 PvP 对抗工具（4v4 镜像席位 / 1v1+6 填充席位）。

用法示例：
    python3 organizer/rift_era/scripts/duel.py advanced medium --seeds 7,11 --ticks 80 --legacy-map
    python3 organizer/rift_era/scripts/duel.py bot_a.zip bot_b.zip --mode 4v4 --seed-count 4 --ticks 400 --legacy-map
    python3 organizer/rift_era/scripts/duel.py bot_a.zip bot_b.zip --mode 1v1 --filler medium --seeds 7 --ticks 80 --legacy-map --record-replay
"""
from __future__ import annotations

import argparse
from fractions import Fraction
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT.parents[1]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(ROOT / 'kit'))
sys.path.insert(0, str(ROOT / 'scripts'))

from organizer.rift_era.pipeline import match_runner  # noqa: E402
from organizer.rift_era.score import elo  # noqa: E402
import run_match as run_match_mod  # noqa: E402

DEFAULT_SEEDS = (7, 11, 13, 17, 19, 23, 29, 31)


def _discover_two_submissions() -> list[str]:
    sub_dir = ROOT / 'agents' / 'submissions'
    if not sub_dir.is_dir():
        return []
    found: list[Path] = []
    for item in sorted(sub_dir.iterdir()):
        if item.name.startswith('.'):
            continue
        if item.is_file() and item.suffix == '.zip':
            found.append(item)
        elif item.is_dir() and (item / 'main.py').is_file():
            found.append(item)
    return [str(p) for p in found]


def _seat_layout(mode: str, swapped: bool, a_seat, b_seat, filler_seat, a_label: str, b_label: str, f_label: str):
    if mode == '4v4':
        if not swapped:
            seats = [a_seat if i % 2 == 0 else b_seat for i in range(8)]
            labels = [a_label if i % 2 == 0 else b_label for i in range(8)]
            a_idx = [0, 2, 4, 6]
            b_idx = [1, 3, 5, 7]
        else:
            seats = [b_seat if i % 2 == 0 else a_seat for i in range(8)]
            labels = [b_label if i % 2 == 0 else a_label for i in range(8)]
            a_idx = [1, 3, 5, 7]
            b_idx = [0, 2, 4, 6]
    else:
        seats = [filler_seat] * 8
        labels = [f_label] * 8
        if not swapped:
            seats[0], seats[4] = a_seat, b_seat
            labels[0], labels[4] = a_label, b_label
            a_idx, b_idx = [0], [4]
        else:
            seats[0], seats[4] = b_seat, a_seat
            labels[0], labels[4] = b_label, a_label
            a_idx, b_idx = [4], [0]
    return seats, labels, a_idx, b_idx


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description='裂陆纪元（rift_era）双策略 PvP 对战（4v4 / 1v1）')
    ap.add_argument('bots', nargs='*', help='两个策略（tier 名 / 含 main.py 的目录 / .zip）；省略时自动取 agents/submissions/ 下两个')
    ap.add_argument('--mode', choices=('4v4', '1v1'), default='4v4', help='对抗模式：4v4（默认）或 1v1（另 6 席填充）')
    ap.add_argument('--filler', default='medium', help='1v1 模式下其余 6 席填充对手（默认 medium）')
    ap.add_argument('--seeds', default='', help='逗号分隔的种子列表（覆盖 --seed-count）')
    ap.add_argument('--seed-count', type=int, default=2, help='默认种子表前 N 个（默认 2）')
    ap.add_argument('--no-swap', action='store_true', help='仅跑正向席位，不跑交换镜像局')
    ap.add_argument('--ticks', type=int, default=200, help='每局 tick 数（40 的正整数倍，默认 200）')
    ap.add_argument('--legacy-map', action='store_true', help='跳过 150 tick 正式地图验收，用于快速调试')
    ap.add_argument('--sandbox', default='auto', choices=('auto', 'bwrap', 'windows', 'none', 'cgroup'))
    ap.add_argument('--record-replay', action='store_true', help='为首局录制可视化回放至 replays/')
    ap.add_argument('--every', type=int, default=10, help='回放采样间隔 tick（默认 10）')
    ap.add_argument('--out', type=Path, default=None, help='战报 JSON 路径（默认 results/duel_<timestamp>.json）')
    args = ap.parse_args(argv)

    specs = list(args.bots) if args.bots else _discover_two_submissions()
    if len(specs) != 2:
        print(f'请提供 2 个策略路径或档位名，或在 agents/submissions/ 下放置 2 个提交（当前找到 {len(specs)} 个）')
        return 2
    if args.ticks <= 0 or args.ticks % 40:
        raise SystemExit('--ticks 必须是 40 的正整数倍')

    a_seat, a_label = run_match_mod.resolve_spec(specs[0])
    b_seat, b_label = run_match_mod.resolve_spec(specs[1])
    if a_label == b_label:
        a_label = f'{a_label}#A'
        b_label = f'{b_label}#B'
    f_seat, f_label = run_match_mod.resolve_spec(args.filler)

    seeds = ([int(x.strip()) for x in args.seeds.split(',') if x.strip()]
             if args.seeds else list(DEFAULT_SEEDS[:max(1, args.seed_count)]))
    swaps = (False,) if args.no_swap else (False, True)

    print(f'== PvP 对抗（{args.mode}）：{a_label} vs {b_label} | {len(seeds)} seed × {len(swaps)} 镜像 | {args.ticks} ticks ==')

    started = time.monotonic()
    ratings = {a_label: elo.DEFAULT_INIT_ELO, b_label: elo.DEFAULT_INIT_ELO}
    if args.mode == '1v1':
        ratings.setdefault(f_label, elo.DEFAULT_INIT_ELO)

    tally = {'A': 0, 'B': 0, 'draw': 0}
    a_vs: list[Fraction] = []
    b_vs: list[Fraction] = []
    a_faults = 0
    b_faults = 0
    games = []
    replay_paths = []

    for gi, seed in enumerate(seeds):
        for sw in swaps:
            seats, labels, a_idx, b_idx = _seat_layout(args.mode, sw, a_seat, b_seat, f_seat, a_label, b_label, f_label)
            if args.record_replay and gi == 0 and not sw:
                import replay_viz
                rep_data = replay_viz.record_match_replay(
                    seed=seed, seats=seats, ticks=args.ticks,
                    sample_every=args.every, legacy_map=args.legacy_map, seat_labels=labels,
                )
                rname = f'duel_{args.mode}_seed{seed}_{args.ticks}t_{a_label}_vs_{b_label}.json'
                rpath = replay_viz.save_replay(ROOT / 'replays' / rname, rep_data)
                replay_paths.append(str(rpath))
                print(f'  [回放] {rpath}')

            report = match_runner.run_match(
                seed=seed, seats=seats, ticks=args.ticks,
                legacy_map=args.legacy_map, sandbox=args.sandbox,
            )
            seat_vs = []
            for i in range(8):
                owner = f'n{i}'
                v_ex = run_match_mod._exact_v(report['nations'][owner])
                seat_vs.append((labels[i], v_ex))
            mean_a = sum((seat_vs[i][1] for i in a_idx), Fraction(0)) / len(a_idx)
            mean_b = sum((seat_vs[i][1] for i in b_idx), Fraction(0)) / len(b_idx)
            a_vs.append(mean_a)
            b_vs.append(mean_b)
            fa = sum(int(report['nations'][f'n{i}']['faults']) for i in a_idx)
            fb = sum(int(report['nations'][f'n{i}']['faults']) for i in b_idx)
            a_faults += fa
            b_faults += fb

            lr = elo.log_ratio(mean_a, mean_b)
            if abs(lr) <= elo.DEFAULT_DRAW_LOG_EPS:
                outcome = 'draw'
            else:
                outcome = 'A' if mean_a > mean_b else 'B'
            tally[outcome] += 1
            ratings = elo.update_8seat_match(ratings, seat_vs)

            game_rec = {
                'seed': seed,
                'swapped': sw,
                'outcome': outcome,
                'mean_V_A': float(mean_a),
                'mean_V_B': float(mean_b),
                'mean_V_A_exact': f'{mean_a.numerator}/{mean_a.denominator}',
                'mean_V_B_exact': f'{mean_b.numerator}/{mean_b.denominator}',
                'faults_A': fa,
                'faults_B': fb,
                'state_hash': report['state_hash'],
                'wall_seconds': report['wall_seconds'],
            }
            games.append(game_rec)
            tag = '镜像' if sw else '正向'
            print(f"  seed={seed:<4d} [{tag}]  {a_label} V={float(mean_a):7.3f} vs {b_label} V={float(mean_b):7.3f}  -> {outcome}  (faults {fa}:{fb})")

    avg_a = float(sum(a_vs, Fraction(0)) / len(a_vs))
    avg_b = float(sum(b_vs, Fraction(0)) / len(b_vs))
    result = {
        'mode': f'duel_{args.mode}',
        'A': a_label,
        'B': b_label,
        'filler': f_label if args.mode == '1v1' else None,
        'seeds': seeds,
        'ticks': args.ticks,
        'legacy_map': args.legacy_map,
        'tally': tally,
        'avg_V': {'A': round(avg_a, 4), 'B': round(avg_b, 4)},
        'faults': {'A': a_faults, 'B': b_faults},
        'elo': {a_label: round(ratings[a_label], 2), b_label: round(ratings[b_label], 2)},
        'games': games,
        'replays': replay_paths,
        'wall_seconds': round(time.monotonic() - started, 2),
    }

    ts = time.strftime('%Y%m%d-%H%M%S')
    out_path = args.out or (ROOT / 'results' / f'duel_{args.mode}_{ts}.json')
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')

    print('\n== 对抗结论 ==')
    print(f"  比分（A:平:B）= {tally['A']}:{tally['draw']}:{tally['B']}  |  场均 V：{a_label} {avg_a:.3f} vs {b_label} {avg_b:.3f}")
    print(f"  ELO：{a_label} {ratings[a_label]:.1f} vs {b_label} {ratings[b_label]:.1f}  |  故障：{a_faults}:{b_faults}")
    if tally['A'] > tally['B']:
        print(f'  胜出：{a_label}')
    elif tally['B'] > tally['A']:
        print(f'  胜出：{b_label}')
    else:
        lead = a_label if avg_a > avg_b else (b_label if avg_b > avg_a else '持平')
        print(f'  局分战平，场均 V 领先：{lead}')
    print(f'  战报：{out_path}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
