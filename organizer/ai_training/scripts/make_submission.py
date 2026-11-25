# -*- coding: utf-8 -*-
"""将策略目录（根目录含 main.py）或单文件 .py 打包为前沿实验室提交压缩包 .zip，并做安全与冒烟校验。

用法：
  # 把多文件目录打包到顶层统一交卷目录
  python scripts/make_submission.py agents/template --out ../submissions/ai_training.zip

  # 把本地开发目录打包到题内 submissions/
  python scripts/make_submission.py agents/submissions/my_bot --out agents/submissions/my_agent.zip

  # 也支持把单个 .py 文件自动作为 main.py 打包成 .zip
  python scripts/make_submission.py agents/submissions/my_agent.py --out ../submissions/ai_training.zip
"""
from __future__ import annotations

import argparse
from pathlib import Path
import sys
import zipfile

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "engine"))
import archive  # noqa: E402
import engine  # noqa: E402
import policy  # noqa: E402

SKIP_DIRS = {"__pycache__", ".git", ".hg", ".svn", ".mypy_cache", ".pytest_cache"}
SKIP_SUFFIXES = {".pyc", ".pyo"}
SKIP_NAMES = {".DS_Store", "Thumbs.db"}


def pack_submission(src: Path, out: Path, verify: bool = True) -> Path:
    src = src.resolve()
    out = out.resolve()
    if out.suffix.lower() != ".zip":
        raise SystemExit(f"输出路径必须以 .zip 结尾：{out}")
    policy.check_workspace_path(str(src))
    policy.check_submission_output(str(out))

    entries: list[tuple[Path, str]] = []
    if src.is_dir():
        if not (src / "main.py").is_file():
            raise SystemExit(f"策略目录根目录缺少 main.py：{src}")
        for p in sorted(src.rglob("*"), key=lambda x: x.relative_to(src).as_posix()):
            rel = p.relative_to(src)
            if any(part in SKIP_DIRS for part in rel.parts):
                continue
            if p.is_dir():
                continue
            if p.name in SKIP_NAMES or p.suffix.lower() in SKIP_SUFFIXES:
                continue
            entries.append((p, rel.as_posix()))
    elif src.is_file() and src.suffix.lower() == ".py":
        entries.append((src, "main.py"))
    else:
        raise SystemExit(f"输入必须是含 main.py 的目录或 .py 文件：{src}")

    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_name(out.name + ".tmp")
    try:
        with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zf:
            for file_path, arcname in entries:
                info = zipfile.ZipInfo(arcname, date_time=(2026, 1, 1, 0, 0, 0))
                info.compress_type = zipfile.ZIP_DEFLATED
                info.external_attr = 0o100644 << 16
                zf.writestr(info, file_path.read_bytes())
        problems = archive.check_archive(str(tmp))
        if problems:
            raise SystemExit("压缩包校验失败：" + "; ".join(problems))
        tmp.replace(out)
    finally:
        if tmp.exists():
            tmp.unlink()

    if verify:
        res = engine.play(str(out), engine.SOLO_NPC, seed=1, ticks=24, inproc_b=True)
        fl = res["faults"][0]
        if fl["timeouts"] > 0 or fl["bad_json"] > 0 or fl["dead"] or fl["illegal"] > 0:
            raise SystemExit(
                f"冒烟试跑失败（timeouts={fl['timeouts']}, bad_json={fl['bad_json']}, "
                f"dead={fl['dead']}, illegal={fl['illegal']}）"
            )
        print(f"[冒烟通过] {archive.submission_label(str(out))}（24 tick 握手与动作校验 0 故障）")

    size_kb = out.stat().st_size / 1024.0
    print(f"[打包成功] {out}  ({size_kb:.1f} KB, {len(entries)} 个文件)")
    return out


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="打包并校验前沿实验室 .zip 压缩包提交")
    ap.add_argument("source", type=Path, help="策略目录（根目录含 main.py）或单文件 .py")
    ap.add_argument(
        "--out",
        type=Path,
        default=ROOT / "agents" / "submissions" / "my_agent.zip",
        help="输出 .zip 路径（统一交卷请设为 ../submissions/ai_training.zip）",
    )
    ap.add_argument("--no-verify", action="store_true", help="跳过 24 tick 加载与握手冒烟校验")
    args = ap.parse_args()
    try:
        pack_submission(args.source, args.out, verify=(not args.no_verify))
    except policy.PolicyError as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()
