# -*- coding: utf-8 -*-
"""《压缩奇点》离线回放与虚拟机执行轨迹录制模块（engine/replay.py）。"""
from __future__ import annotations

import json
from pathlib import Path
import time
from typing import Any

import runner
import vm

REPLAY_FORMAT = "compression_singularity_replay/1"


def build_replay_payload(
    *,
    title: str,
    submission: str,
    cases: list[dict[str, Any]],
    summary: dict[str, Any],
    program_previews: dict[str, str] | None = None,
) -> dict[str, Any]:
    previews = program_previews or {}
    clean_cases = []
    for c in cases:
        item = dict(c)
        name = str(item.get("name", ""))
        if name in previews:
            prog = previews[name]
            item["program_preview"] = prog[:512] + ("..." if len(prog) > 512 else "")
        clean_cases.append(item)
    return {
        "format": REPLAY_FORMAT,
        "problem": runner.PROBLEM_ID,
        "title": title,
        "submission": submission,
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "log2_6": round(vm.LOG2_6, 10),
        "summary": summary,
        "cases": clean_cases,
    }


def record_program_replay(
    target_path: Path,
    program_path: Path,
    out_path: Path,
    *,
    case_name: str = "",
    family: str = "sample",
    trace_points: int = 120,
) -> dict[str, Any]:
    raw_in = target_path.read_text(encoding="utf-8")
    L, S = vm.parse_input_text(raw_in)
    prog_bytes = program_path.read_bytes()
    prog_str = prog_bytes.decode("utf-8", errors="replace").rstrip("\r\n")
    b_ref = vm.compute_b_ref(S)
    vres = vm.verify_program_bytes(
        f"{L}\n{S}\n".encode("ascii"),
        prog_bytes,
        trace_points=trace_points,
    )
    verdict = vres["verdict"]
    prog_len = int(vres.get("prog_len", 0))
    raw_sc = vm.compute_raw_score(b_ref, prog_len) if verdict == "AC" else 0.0
    norm_sc = vm.compute_norm_score(raw_sc) if verdict == "AC" else 0.0
    cname = case_name or target_path.stem
    case_row = {
        "name": cname,
        "family": family,
        "target_len": L,
        "b_ref_bits": b_ref,
        "verdict": verdict,
        "msg": vres.get("message", ""),
        "prog_len": prog_len,
        "prog_bits": round(prog_len * vm.LOG2_6, 2),
        "steps": int(vres.get("steps", 0)),
        "out_len": int(vres.get("out_len", 0)),
        "p_final": int(vres.get("p_final", 0)),
        "p_min": int(vres.get("p_min", 0)),
        "p_max": int(vres.get("p_max", 0)),
        "vm_ms": round(float(vres.get("elapsed_ms", 0.0)), 2),
        "op_counts": vres.get("op_counts", {}),
        "raw_score": round(raw_sc, 4),
        "norm_score": round(norm_sc, 4),
        "trace": vres.get("trace", []),
    }
    summary = runner.aggregate_cases([case_row])
    payload = build_replay_payload(
        title=runner.TITLE,
        submission=program_path.name,
        cases=[case_row],
        summary=summary,
        program_previews={cname: prog_str},
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    return payload
