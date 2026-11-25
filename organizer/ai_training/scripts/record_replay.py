# -*- coding: utf-8 -*-
"""录制前沿实验室对局回放（afb-ai-training-replay/1，本地离线可视化用；支持 .zip / 目录 / .py）。

用法：
    python scripts/record_replay.py --a ../submissions/ai_training.zip --b merchant --seed 1
    python scripts/record_replay.py --a scaling --b merchant --seed 1 --ticks 4380 --out replays
    python scripts/record_replay.py --a agents/template --b scaling --seed 1 --swap
    python scripts/record_replay.py --a ../submissions/ai_training.zip --solo --seed 1 --ticks 4380
    python scripts/record_replay.py --a scaling --b merchant --seeds 1,2 --every 2 --no-verify

说明：
- 默认开启自校验与确定性复算校验（传 --no-verify 可跳过复算，加快预览录制）。
- 选手工作区内自动执行边界守卫（仅允许公开基线或自身镜像，禁止 --serve 与 --unrestricted）。
- 生成的 JSON 可直接拖入 viz/player.html 离线回放，无需联网。
"""
from __future__ import annotations

import argparse
import json
import os
import random
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "engine"))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import archive  # noqa: E402
import engine  # noqa: E402
import policy  # noqa: E402
import replay as replay_mod  # noqa: E402
import spar  # noqa: E402


def rel(path: str) -> str:
    """相对当前题目根目录显示；跨盘符时退化为绝对路径。"""
    try:
        return os.path.relpath(path, ROOT)
    except ValueError:
        return os.path.abspath(path)


def label_of(path: str, is_solo_npc: bool = False) -> str:
    if is_solo_npc:
        return "SOLO_NPC"
    return archive.submission_label(path)


def resolve_agent(name: str) -> str:
    if name in spar.PRESETS:
        target = spar.PRESETS[name]
        policy.check_agent_path(target)
        return os.path.abspath(target)
    if name in spar.SPECIAL:
        target = spar.SPECIAL[name]
        policy.check_agent_path(target)
        return os.path.abspath(target)
    cand = name
    if not archive.is_submission_path(cand):
        alt = os.path.join(ROOT, name)
        if archive.is_submission_path(alt):
            cand = alt
    policy.check_agent_path(cand)
    if archive.is_submission_path(cand):
        return os.path.abspath(cand)
    raise policy.PolicyError(
        f"找不到合法策略（需要 .zip、含 main.py 的目录、.py 或公开短名）：{name}（用 scripts/spar.py --list 查看公开陪练）"
    )


def parse_seeds(seed: int, seeds_arg: str) -> list[int]:
    if not seeds_arg.strip():
        return [int(seed)]
    out: list[int] = []
    for part in seeds_arg.split(","):
        s = part.strip()
        if not s:
            continue
        out.append(int(s))
    if not out:
        raise ValueError("--seeds 不能为空")
    return out


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="录制前沿实验室对局回放（afb-ai-training-replay/1）")
    ap.add_argument("--a", required=True, help="选手 A：.zip / 策略目录 / .py 或短名（blank/scaling/merchant/template）")
    ap.add_argument("--b", default="merchant", help="对手 B：公开基线短名或路径（默认 merchant；--solo 时忽略）")
    ap.add_argument("--solo", action="store_true", help="单人赛模式（对手为内置固定脚本 SOLO_NPC）")
    ap.add_argument("--seed", type=int, default=random.SystemRandom().randrange(1, 2**31),
                    help="场景种子（默认随机；指定整数可复现）")
    ap.add_argument("--seeds", default="", help="逗号分隔的多个种子（覆盖 --seed）")
    ap.add_argument("--swap", action="store_true", help="对抗赛模式下额外录制一局交换座位")
    ap.add_argument("--ticks", type=int, default=engine.TICKS,
                    help=f"模拟时长 tick 数（默认整局 {engine.TICKS}，快速预览可如 2190 或 4380）")
    ap.add_argument("--every", type=int, default=1, help="按天采样步长（默认 1=每天一帧）")
    ap.add_argument("--out", default=os.path.join(ROOT, "replays"), help="回放输出目录（默认 replays/）")
    ap.add_argument("--no-verify", action="store_true", help="跳过同种子复算校验（仅保留结构与终局自校验）")
    ap.add_argument("--unrestricted", action="store_true", help=argparse.SUPPRESS)
    ap.add_argument("--serve", action="store_true", help="录完后启动本地 HTTP 服务并打印播放地址")
    ap.add_argument("--port", type=int, default=8777, help="本地 HTTP 服务端口（默认 8777）")
    args = ap.parse_args()

    ticks = int(args.ticks) if args.ticks and args.ticks > 0 else engine.TICKS
    every = max(1, int(args.every))
    try:
        seeds = parse_seeds(args.seed, args.seeds)
    except ValueError as exc:
        ap.error(str(exc))

    try:
        if args.unrestricted and policy.is_contestant_workspace():
            raise policy.PolicyError("选手工作区不支持 --unrestricted。")
        policy.check_output_dir(args.out, "replays")
        if args.serve and policy.is_contestant_workspace():
            raise policy.PolicyError("选手工作区不提供 HTTP 共享服务；请用浏览器直接打开 viz/player.html 离线载入回放。")
        a_path = resolve_agent(args.a)
        if args.solo:
            b_path = os.path.abspath(engine.SOLO_NPC)
        else:
            b_path = resolve_agent(args.b)
            policy.check_pair(a_path, b_path, unrestricted=args.unrestricted)
    except policy.PolicyError as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(2)

    engine_sha = replay_mod.sha256_path(os.path.join(ROOT, "engine", "engine.py"))
    os.makedirs(args.out, exist_ok=True)
    mode = "solo" if args.solo else "adv"
    swap_list = (False,) if args.solo else ((False, True) if args.swap else (False,))
    if args.solo and args.swap:
        print("提示：单人赛模式固定使用 0 号座位，已忽略 --swap。")

    written: list[str] = []
    for seed in seeds:
        for swap in swap_list:
            paths = (b_path, a_path) if swap else (a_path, b_path)
            labels = (
                (label_of(b_path, args.solo), label_of(a_path))
                if swap
                else (label_of(a_path), label_of(b_path, args.solo))
            )
            seats = [
                {
                    "seat": i,
                    "label": labels[i],
                    "file": rel(paths[i]).replace(os.sep, "/"),
                    "file_sha256": replay_mod.sha256_path(paths[i]),
                }
                for i in range(2)
            ]
            res = engine.play(paths[0], paths[1], seed, ticks=ticks, inproc_b=args.solo)
            data = replay_mod.build_replay(
                res,
                seats=seats,
                seed=seed,
                ticks=ticks,
                mode=mode,
                engine_sha=engine_sha,
                every=every,
            )
            name = (
                f"replay_solo_seed{seed}.json"
                if args.solo
                else f"replay_seed{seed}_{'sw' if swap else 'fw'}.json"
            )
            out_path = os.path.join(args.out, name)
            policy.check_output_dir(out_path, "replays")
            replay_mod.save_replay(out_path, data)
            size = os.path.getsize(out_path)

            loaded = json.load(open(out_path, encoding="utf-8"))
            ok, msg = replay_mod.verify_against_engine(loaded, res)
            print(
                f"[回放] {name:<26s} {size / 1024.0:6.1f} KB  天={len(loaded['days']):<4d} "
                f"事件={len(loaded['events']):<3d}  终局 V={res['value'][0]:.1f}M vs {res['value'][1]:.1f}M  "
                f"C={res['C'][0]:.4f} vs {res['C'][1]:.4f}  自校验 {'OK' if ok else '失败: ' + msg}"
            )
            if not ok:
                sys.exit(3)

            if not args.no_verify:
                again = engine.play(paths[0], paths[1], seed, ticks=ticks, inproc_b=args.solo)
                again_data = replay_mod.build_replay(
                    again,
                    seats=seats,
                    seed=seed,
                    ticks=ticks,
                    mode=mode,
                    engine_sha=engine_sha,
                    every=every,
                )
                same_val = [round(v, 2) for v in again["value"]] == [round(v, 2) for v in res["value"]]
                same_c = [round(c, 5) for c in again["C"]] == [round(c, 5) for c in res["C"]]
                same_digest = replay_mod.frame_digest(again_data) == replay_mod.frame_digest(loaded)
                same = same_val and same_c and same_digest
                reason = "同种子重跑终局估值、能力 C 与回放帧完全一致" if same else (
                    f"重跑不一致：V {again['value']} vs {res['value']}, C {again['C']} vs {res['C']}"
                )
                print(f"        复算校验：{'一致' if same else '不一致'}（{reason}）")
                if not same:
                    sys.exit(4)

            written.append(out_path)

    print("\n产出：")
    for p in written:
        print("  " + rel(p).replace(os.sep, "/"))

    if args.serve and written:
        import http.server
        import socketserver

        shown = rel(written[-1])
        if os.path.isabs(shown) or shown.startswith(".."):
            print("提示：回放文件不在题目目录内，--serve 仅服务当前题目根目录，请输出至 replays/ 目录再播放。")
        url = f"http://127.0.0.1:{args.port}/viz/player.html#src=/{shown.replace(os.sep, '/')}"
        print(f"\n播放地址（Ctrl+C 停止）：\n  {url}")
        os.chdir(ROOT)
        with socketserver.TCPServer(("127.0.0.1", args.port), http.server.SimpleHTTPRequestHandler) as httpd:
            try:
                httpd.serve_forever()
            except KeyboardInterrupt:
                print("\n已停止。")


if __name__ == "__main__":
    main()
