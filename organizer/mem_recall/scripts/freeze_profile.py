# -*- coding: utf-8 -*-
"""生成并冻结 mem_recall 的 config/scoring_v2.json（覆盖引擎、基线与全部 19 个隐藏集文件指纹）。"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROFILE_PATH = ROOT / "config" / "scoring_v2.json"
HIDDEN_DIR = ROOT / "hidden"

FROZEN_CODE_FILES = (
    "agents/baselines/bm25.py",
    "agents/baselines/charoverlap.py",
    "agents/starter.py",
    "config/format.json",
    "engine/policy.py",
    "engine/runner.py",
)

EXPECTED_OFFICIAL_SETS = (
    "DuRetrieval",
    "EcomRetrieval",
    "MMarcoRetrieval",
    "MedicalRetrieval",
    "VideoRetrieval",
    "arguana",
    "convomem",
    "cpzh",
    "hive",
    "kmb",
    "lme",
    "locomo",
    "locomo_zh",
    "membench",
    "nfcorpus",
    "perltqa",
    "scidocs",
    "scifact",
)


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main() -> None:
    ap = argparse.ArgumentParser(description="冻结 mem_recall scoring_v2.json")
    ap.add_argument("--revision", required=True, help="profile_revision 标识")
    args = ap.parse_args()

    frozen_files: dict[str, str] = {}
    for rel in FROZEN_CODE_FILES:
        p = ROOT / rel
        if not p.is_file():
            raise SystemExit(f"缺少冻结源文件：{p}")
        frozen_files[rel] = sha256_file(p)

    if not HIDDEN_DIR.is_dir():
        raise SystemExit(f"缺少隐藏集目录：{HIDDEN_DIR}")
    hidden_files: dict[str, str] = {}
    for p in sorted(HIDDEN_DIR.iterdir(), key=lambda x: x.name):
        if p.is_file() and (p.name.endswith(".json") or p.name.endswith(".json.gz")):
            hidden_files[p.name] = sha256_file(p)
    if len(hidden_files) != 19:
        raise SystemExit(f"期望 19 个隐藏集文件，实际发现 {len(hidden_files)} 个")

    profile = {
        "version": "mem_recall_v2",
        "problem": "mem_recall",
        "profile_revision": args.revision,
        "memory_mb": 2048,
        "expected_official_sets": list(EXPECTED_OFFICIAL_SETS),
        "frozen_files": frozen_files,
        "hidden_files": hidden_files,
    }
    PROFILE_PATH.write_text(json.dumps(profile, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(f"已冻结 {len(frozen_files)} 个引擎/基线文件 + {len(hidden_files)} 个隐藏集文件 -> {PROFILE_PATH}")


if __name__ == "__main__":
    main()
