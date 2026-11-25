"""录制裂陆纪元（rift_era）可视化对局回放（afb-rift-era-replay/1）。

用法示例（在选手包根目录执行）：
    python3 scripts/record_replay.py --a agents/template --b advanced --seed 7 --ticks 80 --legacy-map
    python3 scripts/record_replay.py --a ../submissions/rift_era.zip --b medium --seat 2 --seeds 7,11 --ticks 200 --legacy-map
    python3 scripts/record_replay.py --seats agents/template,advanced,advanced,advanced,advanced,advanced,advanced,advanced --ticks 80 --legacy-map

说明：
- 录制在策略决策计时之外执行，不改变引擎随机流、状态哈希或任何国力计算。
- 默认开启复算校验（可通过 --no-verify 跳过）：同一 seed/席位用引擎重跑一遍并核对 state_hash 与精确分数 V 逐位一致。
- 生成的 JSON 回放可直接拖入离线播放器 viz/player.html 查看 512×512 世界地图与宏观仪表盘。
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
KIT = HERE.parent

if (HERE / 'replay_viz.py').is_file():
    sys.path.insert(0, str(HERE))
elif (KIT / 'replay_viz.py').is_file():
    sys.path.insert(0, str(KIT))

import replay_viz  # noqa: E402


def is_contestant_workspace(root: Path = KIT) -> bool:
    marker = root / 'config' / 'workspace.json'
    if marker.is_file():
        try:
            payload = json.loads(marker.read_text(encoding='utf-8'))
            if payload.get('mode') == 'contestant':
                return True
        except (OSError, ValueError):
            return True
    return (root / 'runtime' / 'pkg').is_dir()


def _allowed_path(path: Path, root: Path) -> bool:
    resolved = path.resolve()
    kit_resolved = root.resolve()
    if resolved.is_relative_to(kit_resolved):
        return True
    unified = (kit_resolved.parent / 'submissions' / 'rift_era.zip').resolve()
    return resolved == unified


def _named_bots(root: Path) -> dict[str, Path]:
    return {
        'medium': root / 'agents' / 'medium',
        'advanced': root / 'agents' / 'advanced',
        'template': root / 'agents' / 'template',
    }


def resolve_seat(spec: str, root: Path = KIT, *, contestant: bool = True) -> tuple[str | Path, str]:
    raw = spec.strip()
    if raw in ('none', 'simple'):
        return raw, raw
    named = _named_bots(root)
    if raw in named:
        target = named[raw]
        if target.is_dir() and (target / 'main.py').is_file():
            return target.resolve(), raw
        raise SystemExit(f'内置对手 {raw!r} 缺失：{target}')
    candidate = Path(raw)
    if not candidate.is_absolute():
        candidate = (root / candidate) if (root / candidate).exists() else candidate
    resolved = candidate.resolve()
    if contestant and not _allowed_path(resolved, root):
        raise SystemExit(
            f'路径越界：{spec!r}。选手工作区仅允许本目录内路径、公开对手（none/simple/medium/advanced/template）'
            '以及统一交卷 ../submissions/rift_era.zip。'
        )
    if resolved.is_dir() and (resolved / 'main.py').is_file():
        return resolved, resolved.name
    if resolved.is_file() and resolved.suffix == '.zip':
        return resolved, resolved.stem
    raise SystemExit(f'席位 {spec!r} 无效：需要 none/simple/medium/advanced/template、含 main.py 的目录或 .zip')


def build_seats(args, root: Path = KIT, *, contestant: bool = True) -> tuple[tuple[str | Path, ...], tuple[str, ...]]:
    if args.seats and (args.a or args.b):
        raise SystemExit('--seats 与 --a/--b 不能同时指定')
    if args.seats:
        tokens = [t.strip() for t in args.seats.split(',') if t.strip()]
        if len(tokens) == 1:
            tokens *= 8
        if len(tokens) != 8:
            raise SystemExit('--seats 需要 1 个或 8 个（用逗号分隔）')
        resolved = [resolve_seat(t, root, contestant=contestant) for t in tokens]
        return tuple(r[0] for r in resolved), tuple(r[1] for r in resolved)
    if not args.a:
        raise SystemExit('请指定 --a <策略>（搭配 --b <对手>）或 --seats <1或8个席位>')
    if not 0 <= args.seat < 8:
        raise SystemExit('--seat 必须在 0..7 之间')
    a_seat, a_label = resolve_seat(args.a, root, contestant=contestant)
    b_spec = args.b or 'advanced'
    b_seat, b_label = resolve_seat(b_spec, root, contestant=contestant)
    seats = [b_seat] * 8
    labels = [b_label] * 8
    seats[args.seat] = a_seat
    labels[args.seat] = a_label
    return tuple(seats), tuple(labels)


def _reference_engine_run(seed: int, seats: tuple, ticks: int, legacy_map: bool) -> dict:
    catalog_mod = replay_viz._import_engine('catalog')
    dev_seats_mod = replay_viz._import_engine('dev_seats')
    init_mod = replay_viz._import_engine('initialization')
    match_mod = replay_viz._import_engine('match')
    world_mod = replay_viz._import_engine('world')

    catalog = catalog_mod.Catalog()
    world = world_mod.generate_candidate(seed) if legacy_map else match_mod.accepted_world(catalog, seed)
    engine = match_mod.match_economy(catalog, init_mod.initialize(catalog, world),
                                     total_ticks=ticks, fingerprint_each_tick=False)
    if any(s != 'none' for s in seats):
        return dev_seats_mod.run_seated_match(engine, seats)
    det_mod = replay_viz._import_engine('determinism')
    while engine.tick < engine.clock.total_ticks:
        engine.step(None)
    engine.state.audit()
    return match_mod.finalize_match(engine) | {'state_hash': det_mod.fingerprint(engine.state.snapshot())}


def _safe_tag(text: str) -> str:
    cleaned = ''.join(ch if ch.isalnum() or ch in ('-', '_') else '_' for ch in text)
    return (cleaned or 'bot')[:24]


def _rel_display(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return str(path.resolve())


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description='录制裂陆纪元（rift_era）可视化回放')
    ap.add_argument('--seats', default='', help='逗号分隔的 1 个或 8 个席位')
    ap.add_argument('--a', default='', help='待测策略（坐 --seat，默认 0）')
    ap.add_argument('--b', default='', help='其余 7 席对手（搭配 --a，默认 advanced）')
    ap.add_argument('--seat', type=int, default=0, help='--a 的席位编号 0..7（默认 0）')
    ap.add_argument('--seed', type=int, default=1, help='随机种子（默认 1）')
    ap.add_argument('--seeds', default='', help='逗号分隔的多个种子（覆盖 --seed）')
    ap.add_argument('--ticks', type=int, default=200, help='总 tick 数（必须为 40 的正整数倍，默认 200）')
    ap.add_argument('--every', type=int, default=10, help='采样间隔 tick（默认 10=每周期一帧）')
    ap.add_argument('--legacy-map', action='store_true', help='跳过 150 tick 开局地图验收，用于快速录制调试')
    ap.add_argument('--out', type=Path, default=KIT / 'replays', help='输出目录（默认 replays/）')
    ap.add_argument('--no-verify', action='store_true', help='跳过引擎独立复算校验（更快）')
    ap.add_argument('--serve', action='store_true', help='录完后起本地 HTTP 播放服务（选手工作区禁用）')
    ap.add_argument('--port', type=int, default=8777, help='本地 HTTP 端口（默认 8777）')
    args = ap.parse_args(argv)

    contestant = is_contestant_workspace(KIT)
    if args.serve and contestant:
        print('选手工作区不提供 HTTP 共享服务；请用浏览器直接打开 viz/player.html 并拖入回放 JSON。', file=sys.stderr)
        return 2

    out_dir = args.out if args.out.is_absolute() else (KIT / args.out)
    out_resolved = out_dir.resolve()
    if contestant and not out_resolved.is_relative_to(KIT.resolve()):
        print(f'输出目录越界：{args.out}；选手工作区回放必须保存在工作区内（默认 replays/）。', file=sys.stderr)
        return 2

    if args.ticks <= 0 or args.ticks % 40:
        raise SystemExit('--ticks 必须是 40 的正整数倍')
    if args.every <= 0:
        raise SystemExit('--every 必须是正整数')

    seats, labels = build_seats(args, KIT, contestant=contestant)
    seeds = [int(x.strip()) for x in args.seeds.split(',') if x.strip()] if args.seeds else [int(args.seed)]
    out_resolved.mkdir(parents=True, exist_ok=True)

    written: list[Path] = []
    for seed in seeds:
        data = replay_viz.record_match_replay(
            seed=seed,
            seats=seats,
            ticks=args.ticks,
            sample_every=args.every,
            legacy_map=args.legacy_map,
            seat_labels=labels,
        )
        ok, msg = replay_viz.verify_against_engine(data)
        if not ok:
            print(f'[失败] seed={seed} 结构校验未通过：{msg}', file=sys.stderr)
            return 3

        tag_a = _safe_tag(labels[args.seat]) if args.a else _safe_tag(labels[0])
        tag_b = _safe_tag(args.b or 'advanced') if args.a else '8seat'
        fname = f'replay_seed{seed}_{args.ticks}t_{tag_a}_vs_{tag_b}.json'
        path = replay_viz.save_replay(out_resolved / fname, data)
        size_kb = path.stat().st_size / 1024.0
        meta = data['meta']
        win = meta['winner']
        win_v = meta['nations'][win]['V']
        print(
            f"[回放] {fname}  {size_kb:6.1f} KB  帧={len(data['frames'])}  "
            f"胜者={win}({meta['winner_label']}) V={win_v:.3f}  hash={meta['state_hash'][:12]}  用时={meta['wall_seconds']}s"
        )

        if not args.no_verify:
            ref = _reference_engine_run(seed, seats, args.ticks, args.legacy_map)
            ok2, msg2 = replay_viz.verify_against_engine(data, ref)
            print(f"        复算校验：{'通过（state_hash 与精确分数 V 逐位一致）' if ok2 else '失败：' + msg2}")
            if not ok2:
                return 4
        written.append(path)

    print('\n产出：')
    for p in written:
        print('  ' + _rel_display(p, KIT))
    print('用浏览器打开 viz/player.html 并拖入上述回放 JSON 即可观看。')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
