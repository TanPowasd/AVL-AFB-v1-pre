# -*- coding: utf-8 -*-
"""基于隐藏强参考求解器（agents/reference/algebra_synth.cpp）实测校准 L_ref 锚点，
并验证三档基线（starter / beam_evo / algebra_synth）在练习集与隐藏正式集上的 0-100 区分度。
"""
from __future__ import annotations

import argparse
import json
import math
import os
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "engine"))
sys.path.insert(0, str(ROOT / "hidden"))
import common  # noqa: E402
import runner  # noqa: E402

PRACTICE_PATH = ROOT / "config" / "practice_suite.json"
OFFICIAL_PATH = ROOT / "hidden" / "official_suite.json"


def eval_solver_on_cases(
    src_path: Path,
    judge_exe: str,
    cases: list[dict],
    tl_ms: int,
    workers: int,
) -> list[dict]:
    tmp_root = str(common.writable_temp_root(ROOT))
    with tempfile.TemporaryDirectory(prefix="afb_sk_cal_", dir=tmp_root, ignore_cleanup_errors=True) as td:
        comp = runner.compile_submission(str(src_path), td, str(ROOT))
        if not comp.ok:
            raise RuntimeError(f"Compile failed for {src_path.name}:\n{comp.log}")
        return runner.evaluate_cases_parallel(
            judge_exe,
            comp.exe_path,
            cases,
            workers=workers,
            solver_tl_ms=tl_ms,
        )


def calibrate_suite(
    suite_path: Path,
    judge_exe: str,
    ref_src: Path,
    beam_src: Path,
    starter_src: Path,
    *,
    tl_ms: int,
    workers: int,
    write: bool = False,
) -> None:
    data = json.loads(suite_path.read_text(encoding="utf-8"))
    cases = list(data["cases"])
    print(f"\n=== 校准 {suite_path.name} ({len(cases)} 测, TL={tl_ms}ms, workers={workers}) ===", flush=True)

    # Evaluate both normal and S<->K flipped orientations for reference solver (takes median/geo-mean)
    ref_res_0 = eval_solver_on_cases(ref_src, judge_exe, cases, tl_ms=tl_ms, workers=workers)
    cases_flip = [runner.apply_sk_flip(c, True) for c in cases]
    ref_res_1 = eval_solver_on_cases(ref_src, judge_exe, cases_flip, tl_ms=tl_ms, workers=workers)
    beam_res = eval_solver_on_cases(beam_src, judge_exe, cases, tl_ms=tl_ms, workers=workers)
    start_res = eval_solver_on_cases(starter_src, judge_exe, cases, tl_ms=tl_ms, workers=workers)

    # Compute smooth log-damped anchors:
    # Geometric mean of (normal, flipped) reference runs avoids lucky 40,000-bit spikes distorting L_ref!
    raw_anchors: list[float] = []
    for i, c in enumerate(cases):
        r0 = float(ref_res_0[i]["log2_steps"]) if ref_res_0[i]["verdict"] == "AC" else 1.0
        r1 = float(ref_res_1[i]["log2_steps"]) if ref_res_1[i]["verdict"] == "AC" else 1.0
        bl = float(beam_res[i]["log2_steps"]) if beam_res[i]["verdict"] == "AC" else 1.0
        r_geo = math.sqrt(max(1.0, r0) * max(1.0, r1))
        if c["name"] == "P1_01" and int(c["N"]) == 8:
            anc = 8.0
        else:
            # Blend r_geo with 1.25 * beam so anchors are stable and ref centers right around ~50/100
            anc = max(12.0, math.sqrt(max(r_geo, bl * 1.2) * max(r_geo, 12.0)))
            # Cap single-case extreme spikes at 600 so super-exponential breakthroughs score 75-97/100!
            anc = min(anc, 600.0)
        raw_anchors.append(anc)

    # Smooth monotone non-decreasing within each family as N grows, capped at 600.0
    last_by_fam: dict[str, float] = {}
    final_anchors: list[float] = []
    for i, c in enumerate(cases):
        fam = c["family"]
        anc = raw_anchors[i]
        if fam in last_by_fam and anc < last_by_fam[fam] * 1.05:
            anc = last_by_fam[fam] * 1.12
        anc = min(anc, 600.0)
        anc = round(anc, 2)
        last_by_fam[fam] = anc
        final_anchors.append(anc)

    print(
        f"{'Case':<7s} {'Family':<15s} {'N':>3s} {'Start_L':>8s} {'Beam_L':>8s} {'Ref0_L':>8s} {'Ref1_L':>8s} "
        f"{'L_ref':>8s} | {'Start':>6s} {'Beam':>6s} {'Ref0':>6s} {'Ref1':>6s}"
    )
    print("-" * 102)
    s_norms, b_norms, r0_norms, r1_norms = [], [], [], []
    for i, c in enumerate(cases):
        sl = float(start_res[i]["log2_steps"]) if start_res[i]["verdict"] == "AC" else 0.0
        bl = float(beam_res[i]["log2_steps"]) if beam_res[i]["verdict"] == "AC" else 0.0
        r0 = float(ref_res_0[i]["log2_steps"]) if ref_res_0[i]["verdict"] == "AC" else 0.0
        r1 = float(ref_res_1[i]["log2_steps"]) if ref_res_1[i]["verdict"] == "AC" else 0.0
        lref = final_anchors[i]
        sn = runner.compute_norm_score(sl, lref)
        bn = runner.compute_norm_score(bl, lref)
        rn0 = runner.compute_norm_score(r0, lref)
        rn1 = runner.compute_norm_score(r1, lref)
        s_norms.append(sn)
        b_norms.append(bn)
        r0_norms.append(rn0)
        r1_norms.append(rn1)
        print(
            f"{c['name']:<7s} {c['family']:<15s} {c['N']:>3d} {sl:>8.2f} {bl:>8.2f} {r0:>8.2f} {r1:>8.2f} "
            f"{lref:>8.2f} | {sn:>6.2f} {bn:>6.2f} {rn0:>6.2f} {rn1:>6.2f}"
        )
        if write:
            data["cases"][i]["l_ref"] = lref

    print("-" * 102)
    print(
        f"MEAN 0-100: starter = {sum(s_norms)/len(s_norms):.2f}, "
        f"beam_evo = {sum(b_norms)/len(b_norms):.2f}, "
        f"algebra_synth(orig) = {sum(r0_norms)/len(r0_norms):.2f}, "
        f"algebra_synth(flip) = {sum(r1_norms)/len(r1_norms):.2f}"
    )
    if write:
        suite_path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
        print(f"Updated {suite_path}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--tl", type=int, default=5000)
    ap.add_argument("--workers", type=int, default=min(6, max(1, (os.cpu_count() or 2) // 2)))
    ap.add_argument("--write", action="store_true", help="将校准后的 L_ref 写回 practice_suite.json 与 official_suite.json")
    args = ap.parse_args()

    judge_exe = common.ensure_judge_binary(ROOT)
    ref_src = ROOT / "agents" / "reference" / "algebra_synth.cpp"
    beam_src = ROOT / "agents" / "baselines" / "beam_evo.cpp"
    starter_src = ROOT / "agents" / "starter.cpp"

    calibrate_suite(
        PRACTICE_PATH, judge_exe, ref_src, beam_src, starter_src,
        tl_ms=args.tl, workers=args.workers, write=args.write,
    )
    calibrate_suite(
        OFFICIAL_PATH, judge_exe, ref_src, beam_src, starter_src,
        tl_ms=args.tl, workers=args.workers, write=args.write,
    )


if __name__ == "__main__":
    main()
