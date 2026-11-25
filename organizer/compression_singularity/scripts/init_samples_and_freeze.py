# -*- coding: utf-8 -*-
"""生成匿名公开标本、样例回放并冻结 config/scoring_v2.json（仅评测维护用，不导出至选手包）。"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "engine"))
sys.path.insert(0, str(ROOT / "hidden"))
import generator  # noqa: E402
import replay  # noqa: E402
import vm  # noqa: E402


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--revision", default="2026-11-24-cs-sandbox-metrics", help="新冻结配置版本")
    args = parser.parse_args()
    samples_dir = ROOT / "data" / "samples"
    samples_dir.mkdir(parents=True, exist_ok=True)
    for d in ("results", "replays"):
        p = ROOT / d
        p.mkdir(exist_ok=True)
        (p / ".gitkeep").write_text("", encoding="utf-8", newline="\n")

    # 1. Exact Section IV example
    ex_s = "11110111101111011110"
    ex_p = "+>+>+>+[....+.<]"
    (samples_dir / "00_example.in").write_text(f"{len(ex_s)}\n{ex_s}\n", encoding="utf-8", newline="\n")
    (samples_dir / "00_example.out").write_text(f"{ex_p}\n", encoding="utf-8", newline="\n")
    ex_bref = vm.compute_b_ref(ex_s)

    cases_manifest = [{
        "name": "00_example",
        "in": "00_example.in",
        "out": "00_example.out",
        "family": "example",
        "length": len(ex_s),
        "b_ref_bits": ex_bref,
        "is_sample": True,
    }]

    # 2. 8 Anonymous Specimens (ZERO family tags or seeds leaked!)
    for name, fam, length, seed in generator.PUBLIC_SPECIMEN_SPECS:
        s, _meta = generator.generate_case(fam, length, seed)
        bref = vm.compute_b_ref(s)
        (samples_dir / f"{name}.in").write_text(f"{length}\n{s}\n", encoding="utf-8", newline="\n")
        cases_manifest.append({
            "name": name,
            "in": f"{name}.in",
            "family": "anonymous",
            "length": length,
            "b_ref_bits": bref,
            "is_sample": True,
        })

    (samples_dir / "manifest.json").write_text(
        json.dumps({"format": "compression_singularity_samples/1", "cases": cases_manifest}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )

    # 3. Verified sample replay from 00_example
    rep = replay.record_program_replay(
        samples_dir / "00_example.in",
        samples_dir / "00_example.out",
        ROOT / "replays" / "replay_sample_00.json",
        case_name="00_example",
        family="example",
        trace_points=40,
    )
    assert rep["cases"][0]["verdict"] == "AC" and rep["cases"][0]["steps"] == 40

    # 4. Freeze scoring_v2.json hashes
    frozen_rel = [
        "config/format.json",
        "engine/common.py",
        "engine/runner.py",
        "engine/vm.cpp",
        "engine/vm.py",
        "engine/vm_jit.py",
        "engine/vm_ref.py",
        "hidden/generator.py",
    ]
    frozen_map = {rel: hashlib.sha256((ROOT / rel).read_bytes()).hexdigest() for rel in frozen_rel}
    cfg = {
        "version": "compression_singularity_v1",
        "profile_revision": args.revision,
        "problem": "compression_singularity",
        "submission_ext": ".cpp",
        "evaluation_protocol": "two_stage_blind_generalization",
        "seed_source": "os_csprng",
        "scenario_sampling": "per_run_random",
        "official_cases": len(generator.OFFICIAL_SUITE_SPEC),
        "solver_time_limit_ms": 60000,
        "solver_memory_limit_mb": 1024,
        "vm_step_limit": 3000000000000,
        "vm_tape_bound": 10000000,
        "vm_cpu_limit_s": 600,
        "frozen_files": frozen_map,
    }
    (ROOT / "config" / "scoring_v2.json").write_text(
        json.dumps(cfg, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    print("Generated anonymous specimens, replay_sample_00.json, and scoring_v2.json OK!")
    for c in cases_manifest:
        print(f"  {c['name']:<12s} L={c['length']:<8d} B_ref={c['b_ref_bits']}")


if __name__ == "__main__":
    main()
