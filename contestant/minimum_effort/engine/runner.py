# -*- coding: utf-8 -*-
"""《最小努力》（minimum_effort / U755456）单点评测、精确输出比对与时间/空间效率加权计分引擎。"""
from __future__ import annotations

import hashlib
import math
import os
from pathlib import Path
from typing import Any

import common

PROBLEM_ID = "minimum_effort"
TITLE = "最小努力（Minimum Effort）"
DEFAULT_TIME_LIMIT_MS = 2000
MEMORY_LIMIT_MB = 128

# ---- 时间 / 空间效率权重（时间权重 > 空间权重）----
# 对每个通过（AC）的测试点，在获得该点原始分值 pts 的基础上再乘效率系数 E（对数刻度）：
#   ft = clamp(log(t / T0) / log(TL / T0), 0, 1)    t = 单点 CPU 用时 (ms)，TL = 该点时限 (1000/1500/2000 ms)
#   fm = clamp(log(m / M0) / log(ML / M0), 0, 1)    m = 峰值内存 (MiB)，ML = 内存限制 (128 MiB)
#   E  = (1 - TIME_WEIGHT * ft) * (1 - MEM_WEIGHT * fm)
# 用时 ≤ T0（50 ms）且内存 ≤ M0（16 MiB）不扣分（E = 1.0）；
# 顶到单点时限 TL 时时间因子保留 1 - TIME_WEIGHT = 0.30（最多扣 70%）；
# 顶到内存限制 ML 时空间因子保留 1 - MEM_WEIGHT = 0.70（最多扣 30%）；
# 两者相乘，单点 AC 最低保留 0.30 × 0.70 = 0.21。
# 单点最终得分 = pts × E（非 AC 为 0）；总分 = Σ(pts × E)；另报 raw_score（只看正确性）与 avg_efficiency。
TIME_WEIGHT = 0.70
MEM_WEIGHT = 0.30
TIME_FREE_MS = 50.0
MEM_FREE_MB = 16.0


def _logfrac(x: float, lo: float, hi: float) -> float:
    if x <= lo or hi <= lo:
        return 0.0
    return min(1.0, math.log(x / lo) / math.log(hi / lo))


def efficiency(case_res: dict[str, Any]) -> float:
    """计算单点时间/空间综合效率系数 E ∈ [0.21, 1.0]，使用该点自身时限 TL 与内存限制 ML。"""
    tl = float(case_res.get("time_limit_ms") or DEFAULT_TIME_LIMIT_MS)
    ml = float(case_res.get("memory_limit_mb") or MEMORY_LIMIT_MB)
    ft = _logfrac(float(case_res.get("time_ms") or 0.0), TIME_FREE_MS, tl)
    fm = _logfrac(float(case_res.get("mem_mb") or 0.0), MEM_FREE_MB, ml)
    return max(0.0, (1.0 - TIME_WEIGHT * ft) * (1.0 - MEM_WEIGHT * fm))


def digest_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def compile_submission(src_path: str, build_dir: str, _root_dir: str = "") -> common.CompileResult:
    out_exe = os.path.join(build_dir, "sol_bin")
    return common.compile_cpp([src_path], out_exe, include_dirs=None, stack_mb=MEMORY_LIMIT_MB)


def check_output(user_stdout: str, ans_text: str) -> tuple[bool, str]:
    exp = ans_text.split()
    got = user_stdout.split()
    if len(got) != len(exp):
        return False, f"Token count mismatch: expected {len(exp)}, got {len(got)}"
    if got != exp:
        return False, f"Expected {exp[0]!r}, got {got[0]!r}"
    return True, f"Matched ({exp[0]})"


def run_case(
    exe_path: str,
    case: dict[str, Any],
    tests_dir: str,
    *,
    tl_override_ms: int | None = None,
    mem_limit_mb: int | None = None,
) -> dict[str, Any]:
    tl_ms = int(tl_override_ms if tl_override_ms else case.get("time_limit_ms", DEFAULT_TIME_LIMIT_MS))
    mem_mb = int(mem_limit_mb if mem_limit_mb else case.get("memory_limit_mb", MEMORY_LIMIT_MB))
    in_path = os.path.join(tests_dir, case["in"])
    with open(in_path, "rb") as f:
        stdin_bytes = f.read()

    rr = common.run_sandboxed(
        exe_path=exe_path,
        stdin_bytes=stdin_bytes,
        time_limit_ms=tl_ms,
        memory_limit_mb=mem_mb,
        max_output_bytes=1024 * 1024,
    )

    base = {
        "name": str(case["name"]),
        "id": str(case.get("id", case["name"])),
        "subtask": int(case.get("subtask", 0)),
        "points": float(case.get("points", 0.0)),
        "time_limit_ms": tl_ms,
        "memory_limit_mb": mem_mb,
        "is_sample": bool(case.get("is_sample", False)),
        "time_ms": round(rr.cpu_ms, 2),
        "wall_ms": round(rr.wall_ms, 2),
        "mem_mb": round(rr.peak_mem_mb, 2),
    }

    if rr.ole:
        return {**base, "verdict": "OLE", "raw_earned": 0.0, "efficiency": 0.0, "earned": 0.0, "msg": "Output limit exceeded"}
    if rr.peak_mem_mb > float(mem_mb):
        return {**base, "verdict": "MLE", "raw_earned": 0.0, "efficiency": 0.0, "earned": 0.0, "msg": f"Memory {rr.peak_mem_mb:.1f} MiB > {mem_mb} MiB"}
    if rr.timed_out or rr.cpu_ms > float(tl_ms):
        return {
            **base,
            "verdict": "TLE",
            "raw_earned": 0.0,
            "efficiency": 0.0,
            "earned": 0.0,
            "msg": f"CPU {rr.cpu_ms:.1f} ms (wall {rr.wall_ms:.1f} ms) > {tl_ms} ms",
        }
    if rr.returncode != 0:
        return {**base, "verdict": "RE", "raw_earned": 0.0, "efficiency": 0.0, "earned": 0.0, "msg": f"Exit code {rr.returncode}"}

    user_str = rr.stdout.decode("utf-8", errors="replace")
    out_fn = case.get("out")
    if not out_fn:
        tok = user_str.strip().splitlines()
        preview = tok[0][:64] if tok else "(empty)"
        return {**base, "verdict": "RUN", "raw_earned": 0.0, "efficiency": round(efficiency(base), 4), "earned": 0.0, "msg": f"Output: {preview}"}

    out_path = os.path.join(tests_dir, out_fn)
    with open(out_path, "r", encoding="utf-8") as f:
        ans_text = f.read()

    ok, msg = check_output(user_str, ans_text)
    raw_pts = float(case.get("points", 0.0)) if ok else 0.0
    eff = efficiency(base) if ok else 0.0
    return {
        **base,
        "verdict": "AC" if ok else "WA",
        "raw_earned": round(raw_pts, 4),
        "efficiency": round(eff, 4),
        "earned": round(raw_pts * eff, 4),
        "msg": msg,
    }


def score_results(case_results: list[dict[str, Any]], manifest: dict[str, Any]) -> dict[str, Any]:
    """每个测试点独立计分：AC 点得分 = 点分值 × 效率系数 E（时间权重 0.70 > 空间权重 0.30）；另报 raw_score 与 avg_efficiency。"""
    weighted_total = 0.0
    raw_total = 0.0
    max_score = 0.0
    by_subtask: dict[int, list[tuple[dict[str, Any], float, float]]] = {}
    for c in case_results:
        pts = float(c.get("points", 0.0))
        is_ac = c.get("verdict") == "AC"
        raw_e = pts if is_ac else 0.0
        eff = efficiency(c) if is_ac else 0.0
        w_e = raw_e * eff
        max_score += pts
        raw_total += raw_e
        weighted_total += w_e
        by_subtask.setdefault(int(c.get("subtask", 0)), []).append((c, raw_e, w_e))

    subtask_meta = {int(st["id"]): st for st in manifest.get("subtasks", [])}
    subtasks_out: list[dict[str, Any]] = []
    for sid in sorted(by_subtask.keys()):
        rows = by_subtask[sid]
        meta = subtask_meta.get(sid, {})
        st_pts = sum(float(r.get("points", 0.0)) for r, _, _ in rows)
        st_raw = sum(raw_e for _, raw_e, _ in rows)
        st_earned = sum(w_e for _, _, w_e in rows)
        subtasks_out.append({
            "id": sid,
            "name": str(meta.get("name", f"Subtask {sid}")),
            "points": round(st_pts, 2),
            "score": round(st_earned, 2),
            "raw_score": round(st_raw, 2),
            "avg_efficiency": round(st_earned / st_raw, 4) if st_raw > 0.0 else 0.0,
            "cases": len(rows),
            "passed": sum(1 for r, _, _ in rows if r.get("verdict") == "AC"),
            "max_time_ms": round(max((float(r.get("time_ms", 0.0)) for r, _, _ in rows), default=0.0), 2),
            "max_mem_mb": round(max((float(r.get("mem_mb", 0.0)) for r, _, _ in rows), default=0.0), 2),
        })

    return {
        "score": round(weighted_total, 2),
        "raw_score": round(raw_total, 2),
        "max_score": round(max_score, 2),
        "avg_efficiency": round(weighted_total / raw_total, 4) if raw_total > 0.0 else 0.0,
        "weights": {
            "time": TIME_WEIGHT,
            "mem": MEM_WEIGHT,
            "time_free_ms": TIME_FREE_MS,
            "mem_free_mb": MEM_FREE_MB,
            "memory_limit_mb": MEMORY_LIMIT_MB,
            "formula": "E = (1 - Wt * ft) * (1 - Wm * fm)",
        },
        "ac_count": sum(1 for c in case_results if c.get("verdict") == "AC"),
        "total_cases": len(case_results),
        "subtasks": subtasks_out,
    }
