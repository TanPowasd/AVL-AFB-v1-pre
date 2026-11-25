# -*- coding: utf-8 -*-
"""《压缩奇点》（The Compression Singularity）高性能并行评测与计分引擎。

供本地自测脚本（spar.py）与正式动态评测脚本（score.py）共用，支持：
  - 基于 CPU 核数与可用内存余量的自适应多工作线程并发评测（--jobs）
  - 共享输入文件零重复落盘 + 常驻零重分配 VM 服务池 / AOT JIT 双模校验
  - 实时进度回调与严格确定性有序汇总
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
import ctypes
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
from typing import Any, Callable

from common import CompileResult, compile_cpp, run_sandboxed
import vm

PROBLEM_ID = "compression_singularity"
PROBLEM_DIR_NAME = "compression_singularity"
TITLE = "压缩奇点（The Compression Singularity）"

SOLVER_TIME_LIMIT_MS = 60_000
SOLVER_MEMORY_LIMIT_MB = 1024
VM_STEP_LIMIT = vm.DEFAULT_STEP_LIMIT          # 3 * 10^12
VM_TAPE_BOUND = vm.DEFAULT_TAPE_BOUND          # 10^7
VM_CPU_LIMIT_S = vm.DEFAULT_VM_CPU_LIMIT_S     # 600s


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
    requested_jobs: int | None,
    n_cases: int,
    *,
    mem_per_worker_mb: int = 1200,
) -> dict[str, Any]:
    """按 CPU 与内存余量计算安全并发评测数（始终预留至少 1 核与 1.5 GiB 内存）。"""
    cpus = max(1, os.cpu_count() or 1)
    cpu_safe = max(1, cpus - max(1, cpus // 4)) if cpus >= 2 else 1
    avail_mb = _available_memory_mb()
    if avail_mb is not None:
        usable_mb = max(0.0, avail_mb - 1536.0)
        mem_safe = max(1, int(usable_mb // max(256, mem_per_worker_mb)))
    else:
        mem_safe = cpu_safe
    safe_cap = max(1, min(cpu_safe, mem_safe, 16))
    if requested_jobs is not None:
        if requested_jobs < 1:
            raise ValueError("--jobs must be positive")
        chosen = max(1, min(requested_jobs, safe_cap, max(1, n_cases)))
        mode = "manual"
    else:
        chosen = max(1, min(safe_cap, max(1, n_cases)))
        mode = "auto"
    return {
        "mode": mode,
        "workers": chosen,
        "logical_cpus": cpus,
        "safe_cap": safe_cap,
        "avail_mem_mb": round(avail_mb, 1) if avail_mb is not None else None,
    }


def compile_submission(src_path: str, build_dir: str, root_dir: str = "") -> CompileResult:
    out_exe = os.path.join(build_dir, "sol_bin")
    return compile_cpp([src_path], out_exe, stack_mb=min(512, SOLVER_MEMORY_LIMIT_MB))


def evaluate_target_case(
    exe_path: str,
    case_name: str,
    target_str: str,
    *,
    family: str = "custom",
    b_ref_bits: int | None = None,
    solver_tl_ms: int = SOLVER_TIME_LIMIT_MS,
    solver_ml_mb: int = SOLVER_MEMORY_LIMIT_MB,
    step_limit: int = VM_STEP_LIMIT,
    tape_bound: int = VM_TAPE_BOUND,
    vm_cpu_limit_s: float = VM_CPU_LIMIT_S,
    trace_points: int = 0,
    profile: bool = False,
    vm_mode: str = "fast",
    target_file_path: str | None = None,
    seed: int | None = None,
) -> dict[str, Any]:
    """在沙盒内运行选手 C++20 求解器，并用高性能 C++ 参考虚拟机精确校验输出程序 P。"""
    L = len(target_str)
    if b_ref_bits is None:
        b_ref_bits = vm.compute_b_ref(target_str)
    max_prog_len = 2 * L + 100
    stdin_bytes = f"{L}\n{target_str}\n".encode("ascii")

    rr = run_sandboxed(
        exe_path=exe_path,
        stdin_bytes=stdin_bytes,
        time_limit_ms=solver_tl_ms,
        memory_limit_mb=solver_ml_mb,
        max_output_bytes=max_prog_len + 4096,
    )

    base: dict[str, Any] = {
        "name": case_name,
        "family": family,
        "target_len": L,
        "b_ref_bits": int(b_ref_bits),
        "seed": seed,
        "time_ms": round(rr.cpu_ms, 2),
        "wall_ms": round(rr.wall_ms, 2),
        "mem_mb": round(rr.peak_mem_mb, 2),
        "prog_len": 0,
        "steps": 0,
        "p_min": 0,
        "p_max": 0,
        "vm_ms": 0.0,
        "vm_engine": vm_mode,
        "raw_score": 0.0,
        "norm_score": 0.0,
    }

    if rr.peak_mem_mb > solver_ml_mb:
        return {**base, "verdict": "MLE", "msg": f"Solver memory {rr.peak_mem_mb:.1f} MB > {solver_ml_mb} MB"}
    if rr.timed_out:
        return {**base, "verdict": "TLE", "msg": f"Solver exceeded {solver_tl_ms} ms (cpu {rr.cpu_ms:.1f} ms, wall {rr.wall_ms:.1f} ms)"}
    if rr.ole:
        return {**base, "verdict": "OLE", "msg": f"Solver stdout exceeded output limit ({max_prog_len + 4096} bytes)"}
    if rr.returncode != 0:
        err_tail = rr.stderr.decode("utf-8", errors="replace").strip()[-240:]
        detail = f": {err_tail}" if err_tail else ""
        return {**base, "verdict": "RE", "msg": f"Solver exited with code {rr.returncode}{detail}"}

    eff_mode = vm_mode
    if eff_mode == "auto":
        stripped_len = len(rr.stdout.rstrip(b"\r\n"))
        eff_mode = "jit" if (stripped_len <= 8192 and L >= 100_000 and trace_points == 0 and not profile) else "fast"

    vres = vm.verify_program_bytes(
        stdin_bytes,
        rr.stdout,
        step_limit=step_limit,
        tape_bound=tape_bound,
        max_prog_len=max_prog_len,
        cpu_limit_s=vm_cpu_limit_s,
        trace_points=trace_points,
        profile=profile,
        mode=eff_mode,
        target_file_path=target_file_path,
    )

    verdict = vres["verdict"]
    prog_len = int(vres.get("prog_len", 0))
    raw_sc = vm.compute_raw_score(b_ref_bits, prog_len) if verdict == "AC" else 0.0
    norm_sc = vm.compute_norm_score(raw_sc) if verdict == "AC" else 0.0

    rec: dict[str, Any] = {
        **base,
        "verdict": verdict,
        "msg": vres.get("message", ""),
        "prog_len": prog_len,
        "ir_ops": int(vres.get("ir_ops", 0)),
        "prog_bits": round(prog_len * vm.LOG2_6, 2),
        "steps": int(vres.get("steps", 0)),
        "out_len": int(vres.get("out_len", 0)),
        "p_final": int(vres.get("p_final", 0)),
        "p_min": int(vres.get("p_min", 0)),
        "p_max": int(vres.get("p_max", 0)),
        "vm_ms": round(float(vres.get("elapsed_ms", 0.0)), 2),
        "vm_engine": str(vres.get("engine", eff_mode)),
        "op_counts": vres.get("op_counts", {}),
        "raw_score": round(raw_sc, 4),
        "norm_score": round(norm_sc, 4),
    }
    if trace_points > 0:
        rec["trace"] = vres.get("trace", [])
    if profile and "hotspots" in vres:
        rec["hotspots"] = vres["hotspots"]
    return rec


def evaluate_cases_parallel(
    exe_path: str,
    cases: list[dict[str, Any]],
    *,
    workers: int = 1,
    solver_tl_ms: int = SOLVER_TIME_LIMIT_MS,
    solver_ml_mb: int = SOLVER_MEMORY_LIMIT_MB,
    step_limit: int = VM_STEP_LIMIT,
    tape_bound: int = VM_TAPE_BOUND,
    vm_cpu_limit_s: float = VM_CPU_LIMIT_S,
    trace_points: int = 0,
    profile: bool = False,
    vm_mode: str = "fast",
    on_case_done: Callable[[int, int, dict[str, Any]], None] | None = None,
) -> list[dict[str, Any]]:
    """多线程并发评测多个测试点，保持输出列表顺序严格与输入 cases 一致。"""
    n = len(cases)
    if n == 0:
        return []
    vm.ensure_vm_binary()

    with tempfile.TemporaryDirectory(prefix="afb_cs_targets_", ignore_cleanup_errors=True) as tdir:
        target_paths: list[str] = []
        for idx, c in enumerate(cases):
            existing = c.get("target_file_path")
            if existing and os.path.isfile(existing):
                target_paths.append(str(existing))
            else:
                tp = os.path.join(tdir, f"case_{idx:03d}.in")
                s = c["target_str"]
                with open(tp, "wb") as f:
                    f.write(f"{len(s)}\n{s}\n".encode("ascii"))
                target_paths.append(tp)

        def _run_one(idx: int) -> tuple[int, dict[str, Any]]:
            c = cases[idx]
            res = evaluate_target_case(
                exe_path,
                c["name"],
                c["target_str"],
                family=c.get("family", "custom"),
                b_ref_bits=c.get("b_ref_bits"),
                solver_tl_ms=solver_tl_ms,
                solver_ml_mb=solver_ml_mb,
                step_limit=step_limit,
                tape_bound=tape_bound,
                vm_cpu_limit_s=vm_cpu_limit_s,
                trace_points=trace_points,
                profile=profile,
                vm_mode=vm_mode,
                target_file_path=target_paths[idx],
                seed=c.get("seed"),
            )
            return idx, res

        ordered: list[dict[str, Any] | None] = [None] * n
        eff_workers = max(1, min(workers, n))
        done_count = 0
        if eff_workers == 1:
            for idx in range(n):
                i, res = _run_one(idx)
                ordered[i] = res
                done_count += 1
                if on_case_done is not None:
                    on_case_done(done_count, n, res)
        else:
            with ThreadPoolExecutor(max_workers=eff_workers) as pool:
                futs = [pool.submit(_run_one, idx) for idx in range(n)]
                for fut in as_completed(futs):
                    i, res = fut.result()
                    ordered[i] = res
                    done_count += 1
                    if on_case_done is not None:
                        on_case_done(done_count, n, res)

    return [r for r in ordered if r is not None]


def aggregate_cases(case_results: list[dict[str, Any]]) -> dict[str, Any]:
    """汇总各测试点成绩：算术平均 raw_score（不封顶主分）与 norm_score（0–100 折算分）。"""
    n = len(case_results)
    if n == 0:
        return {
            "score": 0.0,
            "max_score": 100.0,
            "raw_score": 0.0,
            "ac_count": 0,
            "total_cases": 0,
            "by_family": {},
            "status": "WA",
        }

    raw_mean = sum(float(c.get("raw_score", 0.0)) for c in case_results) / n
    norm_mean = sum(float(c.get("norm_score", 0.0)) for c in case_results) / n
    ac_count = sum(1 for c in case_results if c.get("verdict") == "AC")

    by_family: dict[str, dict[str, Any]] = {}
    for fam in sorted({str(c.get("family", "custom")) for c in case_results}):
        sub = [c for c in case_results if str(c.get("family", "custom")) == fam]
        by_family[fam] = {
            "cases": len(sub),
            "ac": sum(1 for c in sub if c.get("verdict") == "AC"),
            "raw_score": round(sum(float(c.get("raw_score", 0.0)) for c in sub) / len(sub), 2),
            "norm_score": round(sum(float(c.get("norm_score", 0.0)) for c in sub) / len(sub), 2),
        }

    verdicts = {c.get("verdict", "WA") for c in case_results}
    if ac_count == n:
        status = "AC"
    elif ac_count > 0:
        status = "PC"
    else:
        status = next(
            (v for v in ("TLE", "MLE", "RE", "OLE", "VM_TIMEOUT", "STEP_LIMIT_EXCEEDED",
                         "TAPE_OUT_OF_BOUNDS", "PROGRAM_TOO_LONG", "SYNTAX_ERROR", "OUTPUT_MISMATCH")
             if v in verdicts),
            "WA",
        )

    return {
        "score": round(norm_mean, 2),
        "max_score": 100.0,
        "raw_score": round(raw_mean, 2),
        "ac_count": ac_count,
        "total_cases": n,
        "by_family": by_family,
        "status": status,
    }


def run_sample_file(
    exe_path: str,
    in_path: str,
    *,
    case_name: str = "",
    family: str = "sample",
    b_ref_bits: int | None = None,
    solver_tl_ms: int = SOLVER_TIME_LIMIT_MS,
    trace_points: int = 0,
    profile: bool = False,
    vm_mode: str = "fast",
) -> dict[str, Any]:
    raw = Path(in_path).read_text(encoding="utf-8")
    _L, S = vm.parse_input_text(raw)
    name = case_name or Path(in_path).stem
    return evaluate_target_case(
        exe_path,
        name,
        S,
        family=family,
        b_ref_bits=b_ref_bits,
        solver_tl_ms=solver_tl_ms,
        trace_points=trace_points,
        profile=profile,
        vm_mode=vm_mode,
        target_file_path=os.path.abspath(in_path),
    )


def digest_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify_frozen_profile(root: Path) -> dict[str, Any]:
    cfg_path = root / "config" / "scoring_v2.json"
    cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
    if cfg.get("problem") != PROBLEM_ID:
        raise ValueError("scoring_v2.json problem mismatch")
    for rel, expected in cfg.get("frozen_files", {}).items():
        p = (root / rel).resolve()
        if not p.is_relative_to(root.resolve()) or not p.is_file():
            raise ValueError(f"Missing frozen file: {rel}")
        actual = digest_file(p)
        if actual != expected:
            raise ValueError(f"Frozen source changed: {PROBLEM_ID}/{rel} (expected {expected[:12]}, got {actual[:12]})")
    return cfg
