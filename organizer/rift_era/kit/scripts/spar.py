"""裂陆纪元（rift_era）统一本地自测入口（兼容全赛题 `python scripts/spar.py` 约定）。

用法：
  python3 scripts/spar.py --list
  python3 scripts/spar.py ../submissions/rift_era.zip
  python3 scripts/spar.py agents/template --opponent advanced --ticks 400 --legacy-map
"""
from __future__ import annotations

import argparse
from pathlib import Path
import subprocess
import sys

HERE = Path(__file__).resolve().parent
KIT = HERE.parent


def main() -> int:
    parser = argparse.ArgumentParser(description='裂陆纪元本地自测（非正式，无沙箱）')
    parser.add_argument('bot', nargs='?', default=str(KIT / 'agents' / 'template'),
                        help='待测策略目录（含 main.py）、提交 .zip 或内置名称（template/medium/advanced）')
    parser.add_argument('--list', action='store_true', help='列出公开练习对手与自测用法')
    parser.add_argument('--opponent', default='advanced', help='练习对手（medium/advanced/template/目录/.zip）')
    parser.add_argument('--seed', type=int, default=1)
    parser.add_argument('--seat', type=int, default=0)
    parser.add_argument('--ticks', type=int, default=40, help='练习 tick 数（40 的正整数倍，默认 40 供快速冒烟，长测建议 400）')
    parser.add_argument('--full-map', action='store_true', help='启用 150 tick 正式地图验收（默认使用 --legacy-map 快速自测）')
    parser.add_argument('--out', type=Path)
    args = parser.parse_args()

    if args.list:
        print('公开练习对手与模板：')
        print('  template  -> agents/template  (起步模板)')
        print('  medium    -> agents/medium    (公开练习对手：广度学者 + 库存上限)')
        print('  advanced  -> agents/advanced  (公开练习对手：固定系数计划 + 实验室突击)')
        print('\n提示：8 席对局需要 7 个对手，选手包公开基线凑不成 7 个不同对手；')
        print('      请自行制作多个不同风格或历史版本的对手 bot，并通过 local_match.py --seats / --opponent 自测。')
        print('\n常用命令：')
        print('  python3 scripts/spar.py agents/template')
        print('  python3 scripts/spar.py ../submissions/rift_era.zip --ticks 400')
        print('  python3 scripts/local_match.py score --bot agents/template --opponent advanced --ticks 400 --legacy-map')
        return 0

    cmd = [sys.executable, str(HERE / 'local_match.py'), 'score',
           '--bot', str(args.bot), '--opponent', str(args.opponent),
           '--seed', str(args.seed), '--seat', str(args.seat), '--ticks', str(args.ticks)]
    if not args.full_map:
        cmd.append('--legacy-map')
    if args.out is not None:
        cmd.extend(['--out', str(args.out)])
    return subprocess.call(cmd)


if __name__ == '__main__':
    raise SystemExit(main())
