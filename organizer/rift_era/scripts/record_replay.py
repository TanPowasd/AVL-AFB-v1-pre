"""组委会/开发端裂陆纪元（rift_era）回放录制与本地预览 CLI。

相比选手包版，本入口额外支持：
- `super` 档位对手（自动解析 RIFT_ERA_BOTS_ROOT 或 organizer/rift_era/bots/）
- 任意外部策略目录或 .zip 路径
- `--serve` 启动 127.0.0.1 本地静态服务并打印直达播放链接

用法示例：
    python3 organizer/rift_era/scripts/record_replay.py --a advanced --b medium --seed 7 --ticks 80 --legacy-map
    python3 organizer/rift_era/scripts/record_replay.py --a my_bot.zip --b super --seed 11 --ticks 200 --legacy-map --serve
"""
from __future__ import annotations

import argparse
import http.server
import os
from pathlib import Path
import socketserver
import sys

ROOT = Path(__file__).resolve().parents[1]   # organizer/rift_era
REPO = ROOT.parents[1]
sys.path.insert(0, str(ROOT / 'kit'))
sys.path.insert(0, str(REPO))

import replay_viz  # noqa: E402


def bots_root() -> Path:
    env = os.environ.get('RIFT_ERA_BOTS_ROOT')
    return Path(env).resolve() if env else ROOT / 'bots'


def resolve_seat(spec: str) -> tuple[str | Path, str]:
    raw = spec.strip()
    if raw in ('none', 'simple'):
        return raw, raw
    if raw == 'template':
        target = ROOT / 'kit' / 'template'
        if (target / 'main.py').is_file():
            return target.resolve(), 'template'
    if raw in ('medium', 'advanced', 'super'):
        target = bots_root() / raw
        if (target / 'main.py').is_file():
            return target.resolve(), raw
        raise SystemExit(f'档位对手 {raw!r} 未找到：{target}（可设置 RIFT_ERA_BOTS_ROOT）')
    path = Path(raw)
    if not path.is_absolute() and not path.exists() and (ROOT / path).exists():
        path = ROOT / path
    resolved = path.resolve()
    if resolved.is_dir() and (resolved / 'main.py').is_file():
        return resolved, resolved.name
    if resolved.is_file() and resolved.suffix == '.zip':
        return resolved, resolved.stem
    raise SystemExit(f'席位 {spec!r} 无效：需要 none/simple/template/medium/advanced/super、含 main.py 的目录或 .zip')


def build_seats(args) -> tuple[tuple[str | Path, ...], tuple[str, ...]]:
    if args.seats and (args.a or args.b):
        raise SystemExit('--seats 与 --a/--b 不能同时指定')
    if args.seats:
        tokens = [t.strip() for t in args.seats.split(',') if t.strip()]
        if len(tokens) == 1:
            tokens *= 8
        if len(tokens) != 8:
            raise SystemExit('--seats 需要 1 个或 8 个（逗号分隔）')
        resolved = [resolve_seat(t) for t in tokens]
        return tuple(r[0] for r in resolved), tuple(r[1] for r in resolved)
    if not args.a:
        raise SystemExit('请指定 --a <策略>（可选 --b <对手>）或 --seats <1或8个席位>')
    if not 0 <= args.seat < 8:
        raise SystemExit('--seat 必须在 0..7 之间')
    a_seat, a_label = resolve_seat(args.a)
    b_seat, b_label = resolve_seat(args.b or 'advanced')
    seats = [b_seat] * 8
    labels = [b_label] * 8
    seats[args.seat] = a_seat
    labels[args.seat] = a_label
    return tuple(seats), tuple(labels)


def _reference_engine_run(seed: int, seats: tuple, ticks: int, legacy_map: bool) -> dict:
    from organizer.rift_era.engine.catalog import Catalog
    from organizer.rift_era.engine.determinism import fingerprint
    from organizer.rift_era.engine.dev_seats import run_seated_match
    from organizer.rift_era.engine.initialization import initialize
    from organizer.rift_era.engine.match import accepted_world, finalize_match, match_economy
    from organizer.rift_era.engine.world import generate_candidate

    catalog = Catalog()
    world = generate_candidate(seed) if legacy_map else accepted_world(catalog, seed)
    engine = match_economy(catalog, initialize(catalog, world), total_ticks=ticks, fingerprint_each_tick=False)
    if any(s != 'none' for s in seats):
        return run_seated_match(engine, seats)
    while engine.tick < engine.clock.total_ticks:
        engine.step(None)
    engine.state.audit()
    return finalize_match(engine) | {'state_hash': fingerprint(engine.state.snapshot())}


def _safe_tag(text: str) -> str:
    cleaned = ''.join(ch if ch.isalnum() or ch in ('-', '_') else '_' for ch in text)
    return (cleaned or 'bot')[:24]


def _rel_display(path: Path) -> str:
    try:
        return path.resolve().relative_to(ROOT.resolve()).as_posix()
    except ValueError:
        return str(path.resolve())


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description='录制裂陆纪元（rift_era）对局回放（开发/赛事端）')
    ap.add_argument('--seats', default='', help='逗号分隔的 1 个或 8 个席位')
    ap.add_argument('--a', default='', help='待测策略（坐 --seat，默认 0）')
    ap.add_argument('--b', default='', help='其余 7 席对手（搭配 --a，默认 advanced）')
    ap.add_argument('--seat', type=int, default=0, help='--a 的席位编号 0..7（默认 0）')
    ap.add_argument('--seed', type=int, default=1, help='随机种子（默认 1）')
    ap.add_argument('--seeds', default='', help='逗号分隔的多个种子（覆盖 --seed）')
    ap.add_argument('--ticks', type=int, default=200, help='总 tick 数（40 的正整数倍，默认 200）')
    ap.add_argument('--every', type=int, default=10, help='采样间隔 tick（默认 10=每周期一帧）')
    ap.add_argument('--legacy-map', action='store_true', help='跳过 150 tick 开局地图验收，用于快速录制')
    ap.add_argument('--out', type=Path, default=ROOT / 'replays', help='输出目录（默认 organizer/rift_era/replays/）')
    ap.add_argument('--no-verify', action='store_true', help='跳过引擎独立复算校验（更快）')
    ap.add_argument('--serve', action='store_true', help='录完后启动本地 HTTP 服务并打印播放地址')
    ap.add_argument('--port', type=int, default=8777, help='本地 HTTP 服务端口（默认 8777）')
    args = ap.parse_args(argv)

    if args.ticks <= 0 or args.ticks % 40:
        raise SystemExit('--ticks 必须是 40 的正整数倍')
    if args.every <= 0:
        raise SystemExit('--every 必须是正整数')

    seats, labels = build_seats(args)
    seeds = [int(x.strip()) for x in args.seeds.split(',') if x.strip()] if args.seeds else [int(args.seed)]
    out_dir = args.out.resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

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
        path = replay_viz.save_replay(out_dir / fname, data)
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
        print('  ' + _rel_display(p))

    if args.serve and written:
        shown = _rel_display(written[-1])
        if Path(shown).is_absolute() or shown.startswith('..'):
            print('提示：回放不在 organizer/rift_era/ 内，--serve 只服务该根目录，请放进 replays/。')
        url = f'http://127.0.0.1:{args.port}/viz/player.html#src=/{shown}'
        print(f'\n播放地址（Ctrl+C 停止）：\n  {url}')
        os.chdir(ROOT)
        with socketserver.TCPServer(('127.0.0.1', args.port), http.server.SimpleHTTPRequestHandler) as httpd:
            try:
                httpd.serve_forever()
            except KeyboardInterrupt:
                print('\n已停止。')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
