# -*- coding: utf-8 -*-
"""冻结《最小努力》（minimum_effort）config/scoring_v2.json SHA-256 校验清单。"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--revision", default="2026-11-24-me-sandbox-metrics")
    ns = ap.parse_args()

    rel_paths = [
        "config/format.json",
        "engine/common.py",
        "engine/runner.py",
        "data/official/subtasks.json",
        "data/official/manifest.json",
        "data/official/SHA256SUMS",
        "agents/reference/leaderboard_profile_search.cpp",
    ]
    for i in range(1, 37):
        rel_paths.append(f"data/official/{i:02d}.in")
        rel_paths.append(f"data/official/{i:02d}.out")

    frozen: dict[str, str] = {}
    for rel in rel_paths:
        p = ROOT / rel
        if not p.is_file():
            raise FileNotFoundError(f"Missing frozen file: {rel}")
        frozen[rel] = sha256_file(p)

    cfg = {
        "version": "minimum_effort_v2",
        "profile_revision": ns.revision,
        "problem": "minimum_effort",
        "submission_ext": ".cpp",
        "evaluation_protocol": "exact_per_test_subtasks_weighted",
        "official_cases": 36,
        "memory_limit_mb": 128,
        "max_score": 100,
        "efficiency_weights": {
            "time": 0.70,
            "mem": 0.30,
            "time_free_ms": 50.0,
            "mem_free_mb": 16.0,
            "formula": "E = (1 - Wt * ft) * (1 - Wm * fm)",
        },
        "frozen_files": frozen,
    }
    out_path = ROOT / "config" / "scoring_v2.json"
    out_path.write_text(json.dumps(cfg, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(f"Wrote {out_path} ({len(frozen)} frozen files, revision={ns.revision})")


if __name__ == "__main__":
    main()
