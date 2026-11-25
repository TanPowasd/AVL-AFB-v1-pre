# -*- coding: utf-8 -*-
"""录制 cache_duel 对局回放（afb-cache-duel-replay/1），供离线可视化播放器复盘。

用法：
  python scripts/record_replay.py --a agents/submissions/my_agent.py --b lru --seed 11
  python scripts/record_replay.py --a lru --b fifo --seed 11 --config 0 --swap
  python scripts/record_replay.py --a agents/submissions/my_agent.py --b marking --seeds 11,23 --config 1
  python scripts/record_replay.py --a lru --b fifo --seed 11 --serve

说明：
- 录制直接调用 engine/replay.py（不修改冻结引擎），记录操作位于子进程计时之外。
- 默认执行双重校验：回放缓存/OPT 逐步自校验 + 调用 engine.play() 同种子复算校验（可用 --no-verify 跳过重跑）。
- 选手工作区红线持续生效：禁止越界输出、禁止互测他人提交、禁止 --serve 与 --unrestricted。
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import random
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "engine"))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import engine as engine_mod  # noqa: E402
import policy  # noqa: E402
import replay as replay_mod  # noqa: E402
import spar  # noqa: E402

EXTRA_PRESETS = {
    "starter": os.path.join(ROOT, "agents", "starter.py"),
}


def rel(path: str) -> str:
    """相对题目根目录显示；跨盘时退回绝对路径。"""
    try:
        return os.path.relpath(path, ROOT)
    except ValueError:
        return os.path.abspath(path)


def parse_seeds(raw: list[str] | None) -> list[int]:
    if not raw:
        return []
    out: list[int] = []
    for token in raw:
        for part in token.split(","):
            part = part.strip()
            if part:
                out.append(int(part))
    return out


def resolve_agent(spec: str) -> tuple[str, str]:
    if spec in spar.PRESETS:
        path = spar.PRESETS[spec]
        policy.check_agent_path(path)
        return spec, os.path.abspath(path)
    if spec in EXTRA_PRESETS:
        path = EXTRA_PRESETS[spec]
        policy.check_agent_path(path)
        return spec, os.path.abspath(path)
    if policy.is_contestant_workspace():
        policy.check_agent_path(spec)
        cand = spec
    else:
        hits = glob.glob(spec)
        cand = hits[0] if hits else spec
        policy.check_agent_path(cand)
    if not os.path.isfile(cand):
        raise policy.PolicyError(f"找不到策略文件：{spec}")
    abs_p = os.path.abspath(cand)
    return os.path.splitext(os.path.basename(abs_p))[0], abs_p


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="录制缓存攻防回放（afb-cache-duel-replay/1）")
    ap.add_argument("--a", required=True, help="选手 A（默认攻击方）：路径或短名（见 spar.py --list）")
    ap.add_argument("--b", default="lru", help="对手 B（默认防守方）：短名、路径或 self（默认 lru；也可用 --self）")
    ap.add_argument("--self", dest="mirror_self", action="store_true", help="以选手 A 自身镜像作为对手 B")
    ap.add_argument("--seed", type=int, default=None, help="随机种子；默认随机抽取，指定整数可复现")
    ap.add_argument("--seeds", nargs="+", default=None, help="逗号或空格分隔的多个种子（覆盖 --seed）")
    ap.add_argument("--swap", action="store_true", help="额外录制一局攻防互换（B 攻 A 守）")
    ap.add_argument("--config", type=int, default=0, help="config/format.json 配置索引（0/1/2，默认 0）")
    ap.add_argument("--cache-size", type=int, default=0, help="覆盖缓存容量 C；0=使用所选配置")
    ap.add_argument("--universe", type=int, default=0, help="覆盖键空间 K；0=使用所选配置")
    ap.add_argument("--length", type=int, default=0, help="覆盖步数 N；0=使用所选配置")
    ap.add_argument("--out", default=os.path.join(ROOT, "replays"), help="输出目录（默认 replays/）")
    ap.add_argument("--no-verify", action="store_true", help="跳过 engine.play() 同种子重跑复算校验")
    ap.add_argument("--unrestricted", action="store_true", help=argparse.SUPPRESS)
    ap.add_argument("--serve", action="store_true", help="录完后启动本地 HTTP 服务并打印播放地址")
    ap.add_argument("--port", type=int, default=8777, help="本地 HTTP 服务端口（默认 8777）")
    args = ap.parse_args()

    cfg_all = json.load(open(os.path.join(ROOT, "config", "format.json"), encoding="utf-8"))
    configs = cfg_all["configs"]
    if not (0 <= args.config < len(configs)):
        ap.error(f"--config 超出范围：应为 0..{len(configs) - 1}")
    base_cfg = configs[args.config]
    cache_size = int(args.cache_size) if args.cache_size else int(base_cfg["cache_size"])
    universe = int(args.universe) if args.universe else int(base_cfg["universe"])
    length = int(args.length) if args.length else int(base_cfg["length"])
    if cache_size < 1 or universe <= cache_size or length < 1:
        ap.error(f"非法配置参数：C={cache_size}, K={universe}, N={length}（需满足 C>=1, K>C, N>=1）")

    if args.seeds is not None:
        try:
            seeds = parse_seeds(args.seeds)
        except ValueError:
            ap.error("--seeds 必须为整数列表")
        if not seeds:
            ap.error("--seeds 不能为空")
    elif args.seed is not None:
        seeds = [int(args.seed)]
    else:
        seeds = [random.SystemRandom().randrange(1, 2**31)]

    try:
        policy.check_output_dir(args.out, "replays")
        if args.serve and policy.is_contestant_workspace():
            raise policy.PolicyError("选手工作区不提供 HTTP 共享服务；请用浏览器直接打开 viz/player.html 载入回放。")
        if args.unrestricted and policy.is_contestant_workspace():
            raise policy.PolicyError("选手工作区不支持 --unrestricted。")
        label_a, path_a = resolve_agent(args.a)
        if args.mirror_self or args.b in ("self", "--self"):
            label_b, path_b = f"{label_a}(镜像)", path_a
        else:
            label_b, path_b = resolve_agent(args.b)
        policy.check_pair(path_a, path_b, unrestricted=args.unrestricted)
    except policy.PolicyError as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(2)

    engine_sha = replay_mod.sha256_file(os.path.join(ROOT, "engine", "engine.py"))
    os.makedirs(args.out, exist_ok=True)
    custom = bool(args.cache_size or args.universe or args.length)
    cfg_tag = f"c{cache_size}k{universe}n{length}" if custom else f"cfg{args.config}"
    written: list[str] = []

    for seed in seeds:
        for swap in ((False, True) if args.swap else (False,)):
            att_path, def_path = (path_b, path_a) if swap else (path_a, path_b)
            att_label, def_label = (label_b, label_a) if swap else (label_a, label_b)
            res, data = replay_mod.play_with_replay(
                att_path,
                def_path,
                cache_size=cache_size,
                universe=universe,
                length=length,
                seed=seed,
                attacker_label=att_label,
                defender_label=def_label,
                engine_sha=engine_sha,
            )
            name = f"replay_{cfg_tag}_seed{seed}_{'sw' if swap else 'fw'}.json"
            out_path = os.path.join(args.out, name)
            try:
                policy.check_output_dir(out_path, "replays")
            except policy.PolicyError as exc:
                print(str(exc), file=sys.stderr)
                sys.exit(2)
            _, size_bytes = replay_mod.save_replay(out_path, data)
            loaded = json.load(open(out_path, encoding="utf-8"))
            good, msg = replay_mod.verify_against_engine(loaded, res)
            print(
                f"[回放] {name:<32s} {size_bytes / 1024.0:6.1f} KB  "
                f"{att_label}->{def_label}  "
                f"M_def={res.def_misses} OPT={res.opt_misses} def_score={res.def_score:.4f}  "
                f"自校验 {'OK' if good else '失败: ' + msg}"
            )
            if not good:
                sys.exit(3)
            if not args.no_verify:
                again = engine_mod.play(
                    att_path, def_path,
                    cache_size=cache_size, universe=universe, length=length, seed=seed,
                )
                same, vmsg = replay_mod.verify_against_engine(loaded, again)
                print(f"        引擎复算校验：{'一致' if same else '不一致'}（{vmsg}）")
                if not same:
                    sys.exit(4)
            written.append(out_path)

    print("\n产出：")
    for p in written:
        print("  " + rel(p).replace(os.sep, "/"))

    if args.serve:
        import http.server
        import socketserver

        shown = rel(written[-1])
        if os.path.isabs(shown) or shown.startswith(".."):
            print("提示：回放不在当前目录内，--serve 仅服务当前题目根目录，请将回放保存到 replays/ 下再播放。")
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
