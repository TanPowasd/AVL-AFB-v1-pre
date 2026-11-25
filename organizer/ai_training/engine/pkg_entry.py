# -*- coding: utf-8 -*-
"""前沿实验室多文件策略包入口启动器：将包目录置入 sys.path[0] 并运行根目录 main.py。"""
from __future__ import annotations

import os
import runpy
import sys


def main() -> None:
    if len(sys.argv) < 2:
        raise SystemExit("usage: pkg_entry.py <pkg_dir>")
    pkg_dir = os.path.abspath(sys.argv[1])
    entry = os.path.join(pkg_dir, "main.py")
    if not os.path.isfile(entry):
        raise SystemExit(f"missing main.py in {pkg_dir}")
    if pkg_dir in sys.path:
        sys.path.remove(pkg_dir)
    sys.path.insert(0, pkg_dir)
    try:
        os.chdir(pkg_dir)
    except OSError:
        pass
    sys.argv = [entry] + sys.argv[2:]
    runpy.run_path(entry, run_name="__main__")


if __name__ == "__main__":
    main()
