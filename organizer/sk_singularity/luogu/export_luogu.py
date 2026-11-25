# -*- coding: utf-8 -*-
"""导出《双符奇点》（sk_singularity）洛谷兼容评测包（statement.md + checker.cpp + data/*.in/*.ans）。"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil
import sys
import zipfile

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "engine"))
import runner  # noqa: E402


def export_luogu_bundle(out_dir: Path, suite: str = "official", make_zip: bool = False) -> Path:
    src_json = ROOT / ("hidden/official_suite.json" if suite == "official" else "config/practice_suite.json")
    cases = json.loads(src_json.read_text(encoding="utf-8"))["cases"]

    out_dir = out_dir.resolve()
    data_dir = out_dir / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(ROOT / "luogu" / "statement.md", out_dir / "statement.md")
    shutil.copyfile(ROOT / "luogu" / "checker.cpp", out_dir / "checker.cpp")

    for idx, c in enumerate(cases, start=1):
        stem = f"sk_{idx:02d}"
        (data_dir / f"{stem}.in").write_text(runner.format_case_input(c), encoding="utf-8", newline="\n")
        (data_dir / f"{stem}.ans").write_text(f"{float(c['l_ref']):.4f}\n", encoding="utf-8", newline="\n")

    if make_zip:
        zpath = out_dir.with_suffix(".zip")
        with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED) as zf:
            for p in sorted(out_dir.rglob("*")):
                if p.is_file():
                    zf.write(p, p.relative_to(out_dir).as_posix())
        return zpath
    return out_dir


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", type=Path, required=True, help="输出目录")
    ap.add_argument("--suite", choices=("official", "practice"), default="official")
    ap.add_argument("--zip", action="store_true")
    args = ap.parse_args()
    res = export_luogu_bundle(args.out, suite=args.suite, make_zip=args.zip)
    print("Exported Luogu bundle:", res)


if __name__ == "__main__":
    main()
