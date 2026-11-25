# -*- coding: utf-8 -*-
"""《双符奇点》（sk_singularity）并发评测编排、S<->K 同构变换与双轨计分汇总器。"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
import ctypes
import hashlib
import math
import os
from pathlib import Path
import sys
from typing import Any, Callable

import common

PROBLEM_ID = "sk_singularity"
TITLE = "双符奇点（The SK Singularity）"
SOLVER_TIME_LIMIT_MS = 5000
SOLVER_MEMORY_LIMIT_MB = 1024
DEFAULT_MAX_NODES = 10000000
SCORE_POWER = 0.85
SINGULARITY_LOG2_THRESHOLD = 64.0


def digest_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _available_memory_mb() -> float | None:
    if sys.platform == "win32":
        class _MEMSTAT(ctypes.Structure):
            _fields_ = [
                ("dwLength", ctypes.c_uint32),
                ("dwMemoryLoad", ctypes.c_uint32),
                ("ullTotalPhys", ctypes.c_uint64),
                ("ullAvailPhys", ctypes.c_uint64),
                ("ullTotalPageFile", ctypes.c_uint64),
                ("ullAvailPageFile", ctypes.c_uint64),
                ("ullTotalVirtual", ctypes.c_uint64),
                ("ullAvailVirtual", ctypes.c_uint64),
                ("ullAvailExtendedVirtual", ctypes.c_uint64),
            ]

        st = _MEMSTAT()
        st.dwLength = ctypes.sizeof(_MEMSTAT)
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(st)):
            return st.ullAvailPhys / (1024.0 * 1024.0)
        return None
    try:
        for line in Path("/proc/meminfo").read_text(encoding="ascii").splitlines():
            if line.startswith("MemAvailable:"):
                return int(line.split()[1]) / 1024.0
    except OSError:
        pass
    return None


def detect_parallel_workers(
    requested_workers: int | None,
    n_cases: int,
    *,
    mem_per_worker_mb: int = 1536,
) -> dict[str, Any]:
    """按宿主机逻辑核数与可用物理内存自适应计算安全并发评测线程数。"""
    cpus = max(1, os.cpu_count() or 1)
    cpu_safe = max(1, cpus - max(1, cpus // 4)) if cpus >= 2 else 1
    avail_mb = _available_memory_mb()
    if avail_mb is not None:
        usable_mb = max(0.0, avail_mb - 2048.0)
        mem_safe = max(1, int(usable_mb // max(256, mem_per_worker_mb)))
    else:
        mem_safe = cpu_safe
    safe_cap = max(1, min(cpu_safe, mem_safe, 24))
    case_cap = max(1, int(n_cases))
    if requested_workers is not None:
        if requested_workers < 1:
            raise ValueError("--workers/--jobs must be positive")
        chosen = max(1, min(requested_workers, cpus, case_cap))
        mode = "manual"
    else:
        chosen = max(1, min(safe_cap, case_cap))
        mode = "auto"
    return {
        "mode": mode,
        "workers": chosen,
        "logical_cpus": cpus,
        "safe_cap": safe_cap,
        "avail_mem_mb": round(avail_mb, 1) if avail_mb is not None else None,
    }


def swap_sk_str(s: str) -> str:
    if not s or s == "-":
        return s
    trans = str.maketrans({"S": "K", "K": "S"})
    return s.translate(trans)


def apply_sk_flip(case: dict[str, Any], flip: bool) -> dict[str, Any]:
    out = dict(case)
    out["flipped"] = bool(flip)
    if not flip:
        return out
    out["a_S"], out["a_K"] = case["a_K"], case["a_S"]
    out["rhs_S"], out["rhs_K"] = swap_sk_str(case["rhs_K"]), swap_sk_str(case["rhs_S"])
    out["req"] = swap_sk_str(case.get("req", "-"))
    return out


def format_case_input(
    case: dict[str, Any],
    *,
    tl_ms: int | None = None,
    vmax: int | None = None,
) -> str:
    n = int(case["N"])
    a_s = int(case["a_S"])
    a_k = int(case["a_K"])
    v = int(vmax if vmax is not None else case.get("max_nodes", DEFAULT_MAX_NODES))
    tl = int(tl_ms if tl_ms is not None else case.get("time_limit_ms", SOLVER_TIME_LIMIT_MS))
    rhs_s = str(case["rhs_S"]).strip()
    rhs_k = str(case["rhs_K"]).strip()
    req = str(case.get("req", "-")).strip() or "-"
    return f"{n} {a_s} {a_k} {v} {tl}\n{rhs_s}\n{rhs_k}\n{req}\n"


def compute_norm_score(log2_steps: float, l_ref: float) -> float:
    if not (log2_steps > 0.0 and l_ref > 0.0) or not math.isfinite(log2_steps):
        return 0.0
    a = log2_steps ** SCORE_POWER
    b = l_ref ** SCORE_POWER
    if not math.isfinite(a):
        return 100.0
    return max(0.0, min(100.0, 100.0 * a / (a + b)))


def compile_submission(src_path: str, build_dir: str, _root_dir: str = "") -> common.CompileResult:
    """编译选手单文件独立 C++20 求解器（不注入任何题库私有头文件）。"""
    out_exe = str(Path(build_dir) / "submission_solver")
    return common.compile_cpp(
        [src_path],
        out_exe,
        include_dirs=None,
        stack_mb=256,
        use_sandbox_wrap=True,
    )


def evaluate_case(
    judge_exe: str,
    solver_exe: str,
    case: dict[str, Any],
    *,
    solver_tl_ms: int | None = None,
    memory_limit_mb: int = SOLVER_MEMORY_LIMIT_MB,
    vmax: int | None = None,
) -> dict[str, Any]:
    tl_ms = int(solver_tl_ms if solver_tl_ms is not None else case.get("time_limit_ms", SOLVER_TIME_LIMIT_MS))
    node_lim = int(vmax if vmax is not None else case.get("max_nodes", DEFAULT_MAX_NODES))
    l_ref = float(case.get("l_ref", 20.0))
    stdin_text = format_case_input(case, tl_ms=tl_ms, vmax=node_lim)

    run = common.run_sandboxed(
        solver_exe,
        stdin_text.encode("utf-8"),
        time_limit_ms=tl_ms,
        memory_limit_mb=memory_limit_mb,
        max_output_bytes=65536,
    )

    base = {
        "name": str(case["name"]),
        "family": str(case.get("family", "unknown")),
        "N": int(case["N"]),
        "a_S": int(case["a_S"]),
        "a_K": int(case["a_K"]),
        "flipped": bool(case.get("flipped", False)),
        "has_req": str(case.get("req", "-")).strip() not in ("", "-"),
        "l_ref": round(l_ref, 4),
        "cpu_ms": round(run.cpu_ms, 2),
        "wall_ms": round(run.wall_ms, 2),
        "mem_mb": round(run.peak_mem_mb, 2),
        "returncode": int(run.returncode),
        "stderr": run.stderr.decode("utf-8", errors="replace").strip()[:500],
    }

    if run.ole:
        return {
            **base,
            "verdict": "OLE",
            "message": "Output limit exceeded",
            "halted": False,
            "leaves": 0,
            "is_exact": True,
            "exact_steps": 0,
            "steps_display": "0",
            "log2_steps": 0.0,
            "norm_score": 0.0,
            "singularity": False,
            "dag_nodes": 0,
            "judge_ms": 0.0,
        }
    if run.mle or run.peak_mem_mb > float(memory_limit_mb):
        return {
            **base,
            "verdict": "MLE",
            "message": f"Memory limit exceeded ({run.peak_mem_mb:.1f} MiB > {memory_limit_mb} MiB)",
            "halted": False,
            "leaves": 0,
            "is_exact": True,
            "exact_steps": 0,
            "steps_display": "0",
            "log2_steps": 0.0,
            "norm_score": 0.0,
            "singularity": False,
            "dag_nodes": 0,
            "judge_ms": 0.0,
        }
    if run.timed_out:
        return {
            **base,
            "verdict": "TLE",
            "message": f"Time limit exceeded ({tl_ms} ms)",
            "halted": False,
            "leaves": 0,
            "is_exact": True,
            "exact_steps": 0,
            "steps_display": "0",
            "log2_steps": 0.0,
            "norm_score": 0.0,
            "singularity": False,
            "dag_nodes": 0,
            "judge_ms": 0.0,
        }
    if run.returncode != 0:
        return {
            **base,
            "verdict": "RE",
            "message": f"Non-zero exit code ({run.returncode})",
            "halted": False,
            "leaves": 0,
            "is_exact": True,
            "exact_steps": 0,
            "steps_display": "0",
            "log2_steps": 0.0,
            "norm_score": 0.0,
            "singularity": False,
            "dag_nodes": 0,
            "judge_ms": 0.0,
        }

    jr = common.run_judge_cli(
        judge_exe,
        stdin_text,
        run.stdout,
        l_ref=l_ref,
        trace_steps=0,
    )
    log2_steps = float(jr.get("log2_steps", 0.0))
    norm_score = compute_norm_score(log2_steps, l_ref) if jr.get("halted") else 0.0
    return {
        **base,
        "verdict": str(jr.get("verdict", "IE")),
        "message": str(jr.get("message", "")),
        "halted": bool(jr.get("halted", False)),
        "leaves": int(jr.get("leaves", 0)),
        "expr_canonical": str(jr.get("expr_canonical", "")),
        "is_exact": bool(jr.get("is_exact", True)),
        "exact_steps": int(jr.get("exact_steps", 0)),
        "steps_display": str(jr.get("steps_display", "0")),
        "log2_steps": round(log2_steps, 6),
        "norm_score": round(norm_score, 4),
        "singularity": bool(jr.get("singularity", False)),
        "dag_nodes": int(jr.get("dag_nodes", 0)),
        "redex_fires": int(jr.get("redex_fires", 0)),
        "whnf_hits": int(jr.get("whnf_hits", 0)),
        "nf_hits": int(jr.get("nf_hits", 0)),
        "judge_ms": round(float(jr.get("judge_ms", 0.0)), 3),
    }


def evaluate_cases_parallel(
    judge_exe: str,
    solver_exe: str,
    cases: list[dict[str, Any]],
    *,
    workers: int = 1,
    solver_tl_ms: int | None = None,
    memory_limit_mb: int = SOLVER_MEMORY_LIMIT_MB,
    vmax: int | None = None,
    on_case_done: Callable[[int, int, dict[str, Any]], None] | None = None,
) -> list[dict[str, Any]]:
    n = len(cases)
    results: list[dict[str, Any] | None] = [None] * n
    done = 0
    if workers <= 1 or n <= 1:
        for i, cs in enumerate(cases):
            res = evaluate_case(
                judge_exe,
                solver_exe,
                cs,
                solver_tl_ms=solver_tl_ms,
                memory_limit_mb=memory_limit_mb,
                vmax=vmax,
            )
            results[i] = res
            done += 1
            if on_case_done:
                on_case_done(done, n, res)
    else:
        with ThreadPoolExecutor(max_workers=min(workers, n)) as ex:
            fut_map = {
                ex.submit(
                    evaluate_case,
                    judge_exe,
                    solver_exe,
                    cs,
                    solver_tl_ms=solver_tl_ms,
                    memory_limit_mb=memory_limit_mb,
                    vmax=vmax,
                ): i
                for i, cs in enumerate(cases)
            }
            for fut in as_completed(fut_map):
                idx = fut_map[fut]
                res = fut.result()
                results[idx] = res
                done += 1
                if on_case_done:
                    on_case_done(done, n, res)
    return [r for r in results if r is not None]


def aggregate_cases(case_results: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(case_results)
    if total == 0:
        return {
            "status": "EMPTY",
            "score": 0.0,
            "raw_log2_mean": 0.0,
            "raw_log2_max": 0.0,
            "singularity_count": 0,
            "ac_count": 0,
            "total_cases": 0,
            "by_family": {},
        }
    ac_count = sum(1 for r in case_results if r.get("verdict") == "AC")
    score = sum(float(r.get("norm_score", 0.0)) for r in case_results) / total
    raw_log2_mean = sum(float(r.get("log2_steps", 0.0)) for r in case_results) / total
    raw_log2_max = max(float(r.get("log2_steps", 0.0)) for r in case_results)
    sing_cnt = sum(1 for r in case_results if r.get("singularity"))

    fams: dict[str, list[dict[str, Any]]] = {}
    for r in case_results:
        fams.setdefault(str(r.get("family", "unknown")), []).append(r)
    by_family: dict[str, Any] = {}
    for fam, rows in sorted(fams.items()):
        m = len(rows)
        by_family[fam] = {
            "cases": m,
            "ac": sum(1 for x in rows if x.get("verdict") == "AC"),
            "mean_norm": round(sum(float(x.get("norm_score", 0.0)) for x in rows) / m, 2),
            "mean_log2": round(sum(float(x.get("log2_steps", 0.0)) for x in rows) / m, 3),
            "max_log2": round(max(float(x.get("log2_steps", 0.0)) for x in rows), 3),
            "singularities": sum(1 for x in rows if x.get("singularity")),
        }

    if ac_count == total:
        status = "AC"
    elif ac_count > 0:
        status = "PARTIAL"
    else:
        status = case_results[0].get("verdict", "WA")

    return {
        "status": status,
        "score": round(score, 2),
        "raw_log2_mean": round(raw_log2_mean, 4),
        "raw_log2_max": round(raw_log2_max, 4),
        "singularity_count": sing_cnt,
        "ac_count": ac_count,
        "total_cases": total,
        "by_family": by_family,
    }
