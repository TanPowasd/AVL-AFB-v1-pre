"""组委会/开发端单局 8 席对局运行器（支持 medium/advanced/super/目录/.zip）。

用法示例：
    python3 organizer/rift_era/scripts/run_match.py --a advanced --b medium --seed 7 --ticks 80 --legacy-map
    python3 organizer/rift_era/scripts/run_match.py --seats super,super,super,super,advanced,advanced,advanced,advanced --seed 11 --ticks 200 --legacy-map
    python3 organizer/rift_era/scripts/run_match.py --a agents/submissions/my_bot.zip --b super --seed 7 --ticks 200 --legacy-map --record-replay
"""
from __future__ import annotations

import argparse
from fractions import Fraction
import json
import os
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]   # organizer/rift_era
REPO = ROOT.parents[1]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(ROOT / 'kit'))

from organizer.rift_era.pipeline import match_runner  # noqa: E402


def resolve_spec(spec: str) -> tuple[str | Path, str]:
    raw = spec.strip()
    if raw in ('none', 'simple'):
        return raw, raw
    if raw == 'template':
        p = (ROOT / 'kit' / 'template').resolve()
        if (p / 'main.py').is_file():
            return p, 'template'
    if raw in match_runner.TIER_NAMES:
        resolved = match_runner.resolve_seat(raw)
        return resolved, raw
    candidate = Path(raw)
    if not candidate.is_absolute() and not candidate.exists() and (ROOT / candidate).exists():
        candidate = ROOT / candidate
    resolved = match_runner.resolve_seat(str(candidate))
    label = resolved.stem if isinstance(resolved, Path) and resolved.is_file() else (
        resolved.name if isinstance(resolved, Path) else str(resolved)
    )
    return resolved, label


def build_seats(args) -> tuple[list, list[str]]:
    if args.seats and (args.a or args.b):
        raise SystemExit('--seats 与 --a/--b 不能同时指定')
    if args.seats:
        tokens = [t.strip() for t in args.seats.split(',') if t.strip()]
        if len(tokens) == 1:
            tokens *= 8
        if len(tokens) != 8:
            raise SystemExit('--seats 需要 1 个或 8 个（用逗号分隔）')
        resolved = [resolve_spec(t) for t in tokens]
        return [r[0] for r in resolved], [r[1] for r in resolved]
    if not args.a:
        raise SystemExit('请指定 --a <策略>（搭配 --b <对手>）或 --seats <1或8个席位>')
    if not 0 <= args.seat < 8:
        raise SystemExit('--seat 必须在 0..7 之间')
    a_seat, a_label = resolve_spec(args.a)
    b_seat, b_label = resolve_spec(args.b or 'super')
    seats = [b_seat] * 8
    labels = [b_label] * 8
    seats[args.seat] = a_seat
    labels[args.seat] = a_label
    return seats, labels


def _exact_v(row: dict) -> Fraction:
    v_obj = row['V']
    text = v_obj['exact'] if isinstance(v_obj, dict) else str(v_obj)
    n, d = text.split('/', 1)
    return Fraction(int(n), int(d))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description='裂陆纪元（rift_era）单局 8 席对局运行器')
    ap.add_argument('--seats', default='', help='逗号分隔的 1 个或 8 个席位')
    ap.add_argument('--a', default='', help='待测策略（坐 --seat，默认 0）')
    ap.add_argument('--b', default='', help='其余 7 席对手（默认 super）')
    ap.add_argument('--seat', type=int, default=0, help='--a 的席位编号 0..7（默认 0）')
    ap.add_argument('--seed', type=int, default=1, help='随机种子（默认 1）')
    ap.add_argument('--ticks', type=int, default=200, help='总 tick 数（40 的正整数倍，默认 200）')
    ap.add_argument('--legacy-map', action='store_true', help='跳过 150 tick 开局地图验收，用于快速调试')
    ap.add_argument('--sandbox', default='auto', choices=('auto', 'bwrap', 'windows', 'none', 'cgroup'))
    ap.add_argument('--out', type=Path, default=None, help='战报输出路径（默认 results/match_...json）')
    ap.add_argument('--record-replay', action='store_true', help='同步录制可视化回放至 replays/')
    ap.add_argument('--every', type=int, default=10, help='录制回放时的采样间隔 tick（默认 10）')
    args = ap.parse_args(argv)

    if args.ticks <= 0 or args.ticks % 40:
        raise SystemExit('--ticks 必须是 40 的正整数倍')

    seats, labels = build_seats(args)
    started = time.monotonic()

    if args.record_replay:
        import replay_viz
        data = replay_viz.record_match_replay(
            seed=args.seed, seats=seats, ticks=args.ticks,
            sample_every=args.every, legacy_map=args.legacy_map, seat_labels=labels,
        )
        replay_name = f'replay_seed{args.seed}_{args.ticks}t_{labels[0]}.json'
        replay_path = replay_viz.save_replay(ROOT / 'replays' / replay_name, data)
        print(f'[回放] 已保存：{replay_path}')

    report = match_runner.run_match(
        seed=args.seed,
        seats=seats,
        ticks=args.ticks,
        legacy_map=args.legacy_map,
        sandbox=args.sandbox,
    )
    report['seat_labels'] = {f'n{i}': labels[i] for i in range(8)}
    vs = {owner: _exact_v(row) for owner, row in report['nations'].items()}
    best_v = max(vs.values())
    winner = min(p for p, v in vs.items() if v == best_v)
    report['winner'] = winner
    report['winner_label'] = report['seat_labels'][winner]
    report['wall_seconds_total'] = round(time.monotonic() - started, 3)

    out_path = args.out or (ROOT / 'results' / f'match_seed{args.seed}_{args.ticks}t.json')
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')

    print(f"== 单局结果：seed={args.seed}, ticks={args.ticks}, sandbox={report['sandbox']} ==")
    ranking = sorted(vs.items(), key=lambda kv: (-kv[1], kv[0]))
    for rank, (owner, v_exact) in enumerate(ranking, 1):
        row = report['nations'][owner]
        print(f"  #{rank} {owner} ({report['seat_labels'][owner]:<16s})  V={float(v_exact):8.3f}  faults={row['faults']}")
    print(f"胜者：{winner} ({report['winner_label']})  state_hash={report['state_hash'][:12]}  用时={report['wall_seconds_total']}s")
    print(f'战报：{out_path}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
