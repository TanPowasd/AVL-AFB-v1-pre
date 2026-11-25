# -*- coding: utf-8 -*-
"""生成示范回放 replays/replay_demo.json 并冻结 config/scoring_v2.json 哈希清单。"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parent.parent

FROZEN_REL_PATHS: tuple[str, ...] = (
    "config/format.json",
    "config/practice_suite.json",
    "engine/sk_core.hpp",
    "engine/judge_cli.cpp",
    "engine/common.py",
    "engine/runner.py",
    "hidden/official_suite.json",
    "hidden/schedule.py",
    "agents/reference/algebra_synth.cpp",
)


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--revision", default="2026-11-24-sk-8fam-512-dyn-v5")
    ap.add_argument("--skip-demo", action="store_true", help="跳过重新录制 replays/replay_demo.json")
    ns = ap.parse_args()

    if not ns.skip_demo:
        demo_out = ROOT / "replays" / "replay_demo.json"
        cmd = [
            sys.executable,
            str(ROOT / "scripts" / "record_replay.py"),
            str(ROOT / "agents" / "baselines" / "beam_evo.cpp"),
            "--case",
            "P1_03",
            "--tl",
            "2000",
            "--trace-steps",
            "80",
            "--out",
            str(demo_out),
        ]
        subprocess.run(cmd, check=True)

    frozen = {}
    for rel in FROZEN_REL_PATHS:
        p = ROOT / rel
        if not p.is_file():
            raise FileNotFoundError(f"Missing frozen file: {rel}")
        frozen[rel] = sha256_file(p)

    cfg = {
        "version": "sk_singularity_v2",
        "profile_revision": ns.revision,
        "problem": "sk_singularity",
        "submission_ext": ".cpp",
        "evaluation_protocol": "blind_combinator_synthesis_exact_dag_dynamic_512",
        "seed_source": "os_csprng_512_distinct_64bit",
        "isomorphism_guard": "per_case_50pct_sk_swap",
        "official_cases": 512,
        "official_families": 8,
        "cases_per_family": 64,
        "official_budgets": [14, 18, 21, 24],
        "anchor_calibration": {
            "ref_solver": "agents/reference/algebra_synth.cpp",
            "ref_tl_ms": 600,
            "anchor_scale": 1.40,
            "min_lref": 12.0,
            "max_lref": 600.0,
        },
        "max_dag_nodes": 10000000,
        "max_stack": 40000000,
        "solver_time_limit_ms": 5000,
        "solver_memory_limit_mb": 1024,
        "score_power": 0.85,
        "singularity_log2_threshold": 64.0,
        "frozen_files": frozen,
    }
    out_path = ROOT / "config" / "scoring_v2.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(cfg, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(f"Wrote {out_path} ({len(frozen)} frozen files, revision={ns.revision})")


if __name__ == "__main__":
    main()
