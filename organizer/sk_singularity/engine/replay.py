# -*- coding: utf-8 -*-
"""《双符奇点》（sk_singularity）归约追踪与 DAG 爆炸回放 JSON 录制器。"""
from __future__ import annotations

from pathlib import Path
import tempfile
from typing import Any

import common
import runner


def record_case_replay(
    solver_src: str | Path,
    case: dict[str, Any],
    *,
    root_dir: str | Path | None = None,
    tl_ms: int | None = None,
    trace_steps: int = 80,
) -> dict[str, Any]:
    root = Path(root_dir).resolve() if root_dir else Path(__file__).resolve().parent.parent
    judge_exe = common.ensure_judge_binary(root)
    eff_tl = int(tl_ms if tl_ms is not None else case.get("time_limit_ms", runner.SOLVER_TIME_LIMIT_MS))
    vmax = int(case.get("max_nodes", runner.DEFAULT_MAX_NODES))
    l_ref = float(case.get("l_ref", 20.0))
    stdin_text = runner.format_case_input(case, tl_ms=eff_tl, vmax=vmax)

    tmp_root = str(common.writable_temp_root(root))
    with tempfile.TemporaryDirectory(prefix="afb_sk_replay_", dir=tmp_root, ignore_cleanup_errors=True) as td:
        comp = runner.compile_submission(str(solver_src), td, str(root))
        if not comp.ok:
            raise RuntimeError(f"Compilation failed:\n{comp.log}")
        run = common.run_sandboxed(
            comp.exe_path,
            stdin_text.encode("utf-8"),
            time_limit_ms=eff_tl,
            memory_limit_mb=runner.SOLVER_MEMORY_LIMIT_MB,
            max_output_bytes=65536,
        )
        if run.timed_out or run.returncode != 0:
            raise RuntimeError(f"Solver failed (timed_out={run.timed_out}, rc={run.returncode})")
        jr = common.run_judge_cli(
            judge_exe,
            stdin_text,
            run.stdout,
            l_ref=l_ref,
            trace_steps=trace_steps,
        )

    return {
        "format": "sk-singularity-replay/1",
        "problem": runner.PROBLEM_ID,
        "title": runner.TITLE,
        "solver": Path(solver_src).stem,
        "case": {
            "name": case["name"],
            "family": case.get("family", "practice"),
            "N": int(case["N"]),
            "a_S": int(case["a_S"]),
            "a_K": int(case["a_K"]),
            "rhs_S": case["rhs_S"],
            "rhs_K": case["rhs_K"],
            "req": case.get("req", "-"),
            "max_nodes": vmax,
            "time_limit_ms": eff_tl,
            "l_ref": round(l_ref, 4),
        },
        "solver_metrics": {
            "cpu_ms": round(run.cpu_ms, 2),
            "wall_ms": round(run.wall_ms, 2),
            "mem_mb": round(run.peak_mem_mb, 2),
        },
        "result": jr,
    }
