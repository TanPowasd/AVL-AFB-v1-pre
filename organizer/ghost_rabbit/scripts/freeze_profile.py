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
    "engine/common.py",
    "engine/judge_core.hpp",
    "engine/practice_interactor.cpp",
    "engine/runner.py",
    "hidden/official_interactor.cpp",
    "hidden/schedule.py",
)


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--revision", default="2026-11-23-gr-4fam-32-sandbox-v2")
    ap.add_argument("--skip-demo", action="store_true", help="跳过重新录制 replays/replay_demo.json")
    ns = ap.parse_args()

    if not ns.skip_demo:
        demo_out = ROOT / "replays" / "replay_demo.json"
        cmd = [
            sys.executable,
            str(ROOT / "scripts" / "record_replay.py"),
            str(ROOT / "agents" / "baselines" / "staggered_125.cpp"),
            "--seed",
            "20261101",
            "--max-frames",
            "5000",
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
        "version": "ghost_rabbit_v2",
        "profile_revision": ns.revision,
        "problem": "ghost_rabbit",
        "submission_ext": ".cpp",
        "evaluation_protocol": "online_interactive_dynamic_adversary",
        "seed_source": "os_csprng",
        "scenario_sampling": "per_run_random",
        "official_cases": 32,
        "official_families": 4,
        "cases_per_family": 8,
        "max_turns": 5000,
        "max_traps": 4000,
        "solver_time_limit_ms": 5000,
        "solver_memory_limit_mb": 512,
        "fourth_constraint": "local_3ap_free_dist_le_6",
        "frozen_files": frozen,
    }
    out_path = ROOT / "config" / "scoring_v2.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(cfg, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(f"Wrote {out_path} ({len(frozen)} frozen files, revision={ns.revision})")


if __name__ == "__main__":
    main()
