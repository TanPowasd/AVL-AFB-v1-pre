# -*- coding: utf-8 -*-
"""《天翼幽灵兔：四重奇点追截战》（ghost_rabbit）并行交互评测与双轨计分引擎。

供本地自测脚本（spar.py）、回放录制脚本（record_replay.py）与正式动态评测脚本（score.py）共用。
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
import ctypes
import hashlib
import json
import os
from pathlib import Path
import sys
from typing import Any, Callable

from common import (
    CompileResult,
    compile_cpp,
    ensure_interactor_binary,
    run_interactive_match,
)

PROBLEM_ID = "ghost_rabbit"
PROBLEM_DIR_NAME = "ghost_rabbit"
TITLE = "天翼幽灵兔：四重奇点追截战（The Winged Ghost Rabbit）"

SOLVER_TIME_LIMIT_MS = 5_000
SOLVER_MEMORY_LIMIT_MB = 512
DEFAULT_MAX_TURNS = 5_000
DEFAULT_MAX_TRAPS = 4_000

# 公开练习对手定义（供 spar.py 本地调试）
PRACTICE_OPPONENTS: tuple[dict[str, str], ...] = (
    {
        "id": "P1_BasicEscape",
        "tier": "Tier 1",
        "desc": "基础逃逸兔：防即死 + 远离陷阱质心 + 轻度随机扰动",
    },
    {
        "id": "P2_PrimeGreedy",
        "tier": "Tier 2",
        "desc": "素数贪心兔：主动寻找艾森斯坦素数跳板并沿最开阔扇区拉扯",
    },
    {
        "id": "P3_LiteBelief",
        "tier": "Tier 3",
        "desc": "轻量信念对抗兔：维护合法信念集，规避单点暴露与死胡同",
    },
)

PRACTICE_DEFAULT_SEEDS: tuple[int, ...] = (20261101, 20261102, 20261103)


def build_practice_suite(
    opponents: list[str] | None = None,
    seeds: list[int] | None = None,
) -> list[dict[str, Any]]:
    valid_ids = [o["id"] for o in PRACTICE_OPPONENTS]
    chosen_ops = opponents if opponents else valid_ids
    for op in chosen_ops:
        if op not in valid_ids:
            raise ValueError(f"Unknown practice opponent {op!r}; choose from {valid_ids}")
    chosen_seeds = list(seeds) if seeds else list(PRACTICE_DEFAULT_SEEDS)
    cases: list[dict[str, Any]] = []
    idx = 0
    for op in chosen_ops:
        for s in chosen_seeds:
            idx += 1
            cases.append(
                {
                    "name": f"{op}#{s}",
                    "family": op,
                    "strategy": op,
                    "seed": int(s),
                    "max_turns": DEFAULT_MAX_TURNS,
                    "max_traps": DEFAULT_MAX_TRAPS,
                }
            )
    return cases


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
    mem_per_worker_mb: int = 768,
) -> dict[str, Any]:
    """按 CPU 与内存余量计算安全并发对局数（交互题每个 worker 含交互器 + 求解器双进程）。"""
    cpus = max(1, os.cpu_count() or 1)
    cpu_safe = max(1, cpus - max(1, cpus // 4)) if cpus >= 2 else 1
    avail_mb = _available_memory_mb()
    if avail_mb is not None:
        usable_mb = max(0.0, avail_mb - 1536.0)
        mem_safe = max(1, int(usable_mb // max(256, mem_per_worker_mb)))
    else:
        mem_safe = cpu_safe
    safe_cap = max(1, min(cpu_safe, mem_safe, 12))
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
    return compile_cpp([src_path], out_exe, stack_mb=min(256, SOLVER_MEMORY_LIMIT_MB))


def ensure_practice_interactor(root_dir: str | Path) -> str:
    root = Path(root_dir).resolve()
    return ensure_interactor_binary(
        root / "engine" / "practice_interactor.cpp",
        extra_deps=[root / "engine" / "judge_core.hpp"],
    )


def evaluate_case(
    interactor_exe: str,
    solver_exe: str,
    case: dict[str, Any],
    *,
    solver_tl_ms: int = SOLVER_TIME_LIMIT_MS,
    solver_ml_mb: int = SOLVER_MEMORY_LIMIT_MB,
    replay_path: str | None = None,
    max_replay_frames: int = DEFAULT_MAX_TURNS,
) -> dict[str, Any]:
    rr = run_interactive_match(
        interactor_exe,
        solver_exe,
        strategy=str(case["strategy"]),
        seed=int(case["seed"]),
        max_turns=int(case.get("max_turns", DEFAULT_MAX_TURNS)),
        max_traps=int(case.get("max_traps", DEFAULT_MAX_TRAPS)),
        solver_tl_ms=solver_tl_ms,
        solver_ml_mb=solver_ml_mb,
        replay_path=replay_path,
        max_replay_frames=max_replay_frames,
    )
    return {
        "name": str(case["name"]),
        "family": str(case.get("family", case["strategy"])),
        "strategy": str(case["strategy"]),
        "seed": int(case["seed"]),
        "verdict": rr.verdict,
        "msg": rr.message,
        "P": rr.P,
        "Q": rr.Q,
        "P_red": rr.P_red,
        "Q_red": rr.Q_red,
        "q": round(rr.q, 6),
        "captured": rr.captured,
        "singularity": rr.singularity,
        "raw_score": round(rr.raw_score, 4),
        "norm_score": round(rr.norm_score, 4),
        "turns": rr.turns,
        "traps_placed": rr.traps_placed,
        "bounces": rr.bounces,
        "prime_flashes": rr.prime_flashes,
        "cpu_ms": rr.solver_cpu_ms,
        "active_ms": rr.solver_active_ms,
        "interactor_ms": rr.interactor_ms,
        "wall_ms": rr.wall_ms,
        "mem_mb": rr.peak_mem_mb,
    }


def evaluate_cases_parallel(
    interactor_exe: str,
    solver_exe: str,
    cases: list[dict[str, Any]],
    *,
    workers: int = 1,
    solver_tl_ms: int = SOLVER_TIME_LIMIT_MS,
    solver_ml_mb: int = SOLVER_MEMORY_LIMIT_MB,
    on_case_done: Callable[[int, int, dict[str, Any]], None] | None = None,
) -> list[dict[str, Any]]:
    n = len(cases)
    if n == 0:
        return []

    def _run_one(idx: int) -> tuple[int, dict[str, Any]]:
        res = evaluate_case(
            interactor_exe,
            solver_exe,
            cases[idx],
            solver_tl_ms=solver_tl_ms,
            solver_ml_mb=solver_ml_mb,
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
    """双轨汇总：
      - 套题 0-100 折算分 score = 全部测试点 norm_score 等权算术平均（防买彩票效应）
      - 单题题面原分 raw_score = 全通取最小 raw_score（有任意未 AC 则为 0；全通且全员 q<=1/2 则 singularity=True）
    """
    n = len(case_results)
    if n == 0:
        return {
            "score": 0.0,
            "max_score": 100.0,
            "raw_score": 0.0,
            "singularity": False,
            "ac_count": 0,
            "total_cases": 0,
            "by_family": {},
            "status": "WA",
        }

    ac_count = sum(1 for c in case_results if c.get("verdict") == "AC")
    norm_mean = sum(float(c.get("norm_score", 0.0)) for c in case_results) / n

    all_ac = (ac_count == n)
    all_sing = all_ac and all(bool(c.get("singularity", False)) for c in case_results)
    if all_sing:
        raw_agg = 0.0
    elif all_ac:
        finite_raws = [float(c.get("raw_score", 0.0)) for c in case_results if not bool(c.get("singularity", False))]
        raw_agg = min(finite_raws) if finite_raws else 0.0
    else:
        raw_agg = 0.0

    by_family: dict[str, dict[str, Any]] = {}
    for fam in sorted({str(c.get("family", "custom")) for c in case_results}):
        sub = [c for c in case_results if str(c.get("family", "custom")) == fam]
        sub_ac = sum(1 for c in sub if c.get("verdict") == "AC")
        sub_all_ac = (sub_ac == len(sub))
        sub_all_sing = sub_all_ac and all(bool(c.get("singularity", False)) for c in sub)
        if sub_all_sing:
            sub_raw = 0.0
        elif sub_all_ac:
            sub_fin = [float(c.get("raw_score", 0.0)) for c in sub if not bool(c.get("singularity", False))]
            sub_raw = min(sub_fin) if sub_fin else 0.0
        else:
            sub_raw = 0.0
        by_family[fam] = {
            "cases": len(sub),
            "ac": sub_ac,
            "singularity": sub_all_sing,
            "raw_score": round(sub_raw, 2),
            "norm_score": round(sum(float(c.get("norm_score", 0.0)) for c in sub) / len(sub), 2),
            "avg_turns": round(sum(int(c.get("turns", 0)) for c in sub) / len(sub), 1),
            "avg_traps": round(sum(int(c.get("traps_placed", 0)) for c in sub) / len(sub), 1),
        }

    verdicts = {c.get("verdict", "WA") for c in case_results}
    if all_ac:
        status = "AC"
    elif ac_count > 0:
        status = "PC"
    else:
        status = next((v for v in ("MLE", "TLE", "OLE", "RE", "PE", "WA") if v in verdicts), "WA")

    return {
        "score": round(norm_mean, 2),
        "max_score": 100.0,
        "raw_score": round(raw_agg, 2),
        "singularity": all_sing,
        "ac_count": ac_count,
        "total_cases": n,
        "by_family": by_family,
        "status": status,
    }


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
            raise ValueError(
                f"Frozen source changed: {PROBLEM_ID}/{rel} (expected {expected[:12]}, got {actual[:12]})"
            )
    return cfg
