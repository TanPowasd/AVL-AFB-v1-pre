# -*- coding: utf-8 -*-
"""纯 Python 逐条原始指令参考虚拟机（无任何宏步优化，用作形式化语义真值与 C++ VM 对拍基准）。"""
from __future__ import annotations

from typing import Any

ALPHABET = frozenset("><+.[]")


def strip_single_trailing_newline(raw: str) -> str:
    if raw.endswith("\r\n"):
        return raw[:-2]
    if raw.endswith("\n"):
        return raw[:-1]
    return raw


def validate_program(prog: str, max_prog_len: int) -> tuple[bool, str, str, dict[int, int]]:
    if not prog:
        return False, "SYNTAX_ERROR", "Empty program", {}
    if len(prog) > max_prog_len:
        return False, "PROGRAM_TOO_LONG", f"Program length {len(prog)} exceeds limit {max_prog_len}", {}
    stack: list[int] = []
    match: dict[int, int] = {}
    for i, ch in enumerate(prog):
        if ch not in ALPHABET:
            return False, "SYNTAX_ERROR", f"Illegal character {ch!r} at index {i}", {}
        if ch == "[":
            stack.append(i)
        elif ch == "]":
            if not stack:
                return False, "SYNTAX_ERROR", f"Unmatched ']' at index {i}", {}
            j = stack.pop()
            match[j] = i
            match[i] = j
    if stack:
        return False, "SYNTAX_ERROR", f"Unmatched '[' at index {stack[-1]}", {}
    return True, "OK", "OK", match


def run_reference_vm(
    target: str,
    prog: str,
    step_limit: int = 3 * 10**12,
    tape_bound: int = 10**7,
    max_prog_len: int | None = None,
) -> dict[str, Any]:
    """严格按形式化定义逐条执行指令，零优化。"""
    prog = strip_single_trailing_newline(prog)
    L = len(target)
    if max_prog_len is None:
        max_prog_len = 2 * L + 100

    ok, verdict, msg, match = validate_program(prog, max_prog_len)
    if not ok:
        return {
            "verdict": verdict,
            "message": msg,
            "prog_len": len(prog),
            "steps": 0,
            "out_len": 0,
            "target_len": L,
            "p_final": 0,
            "p_min": 0,
            "p_max": 0,
        }

    tape: dict[int, int] = {}
    ip = 0
    n = len(prog)
    p = 0
    p_min = 0
    p_max = 0
    t = 0
    out_pos = 0

    while ip < n:
        if t == step_limit:
            return {
                "verdict": "STEP_LIMIT_EXCEEDED",
                "message": f"Exceeded step limit {step_limit}",
                "prog_len": n,
                "steps": t,
                "out_len": out_pos,
                "target_len": L,
                "p_final": p,
                "p_min": p_min,
                "p_max": p_max,
            }
        op = prog[ip]
        t += 1
        if op == ">":
            p += 1
            if p > tape_bound:
                return {
                    "verdict": "TAPE_OUT_OF_BOUNDS",
                    "message": f"Head position {p} exceeded +{tape_bound}",
                    "prog_len": n,
                    "steps": t,
                    "out_len": out_pos,
                    "target_len": L,
                    "p_final": p,
                    "p_min": p_min,
                    "p_max": p_max,
                }
            if p > p_max:
                p_max = p
            ip += 1
        elif op == "<":
            p -= 1
            if p < -tape_bound:
                return {
                    "verdict": "TAPE_OUT_OF_BOUNDS",
                    "message": f"Head position {p} exceeded -{tape_bound}",
                    "prog_len": n,
                    "steps": t,
                    "out_len": out_pos,
                    "target_len": L,
                    "p_final": p,
                    "p_min": p_min,
                    "p_max": p_max,
                }
            if p < p_min:
                p_min = p
            ip += 1
        elif op == "+":
            tape[p] = 1 - tape.get(p, 0)
            ip += 1
        elif op == ".":
            bit = "1" if tape.get(p, 0) else "0"
            if out_pos >= L:
                return {
                    "verdict": "OUTPUT_MISMATCH",
                    "message": f"Output length exceeded target length {L}",
                    "prog_len": n,
                    "steps": t,
                    "out_len": out_pos,
                    "target_len": L,
                    "p_final": p,
                    "p_min": p_min,
                    "p_max": p_max,
                }
            if target[out_pos] != bit:
                return {
                    "verdict": "OUTPUT_MISMATCH",
                    "message": f"Bit mismatch at index {out_pos}: expected {target[out_pos]!r}, got {bit!r}",
                    "prog_len": n,
                    "steps": t,
                    "out_len": out_pos,
                    "target_len": L,
                    "p_final": p,
                    "p_min": p_min,
                    "p_max": p_max,
                }
            out_pos += 1
            ip += 1
        elif op == "[":
            if tape.get(p, 0) == 0:
                ip = match[ip] + 1
            else:
                ip += 1
        elif op == "]":
            if tape.get(p, 0) == 1:
                ip = match[ip] + 1
            else:
                ip += 1

    if out_pos != L:
        return {
            "verdict": "OUTPUT_MISMATCH",
            "message": f"Output too short: got {out_pos}, expected {L}",
            "prog_len": n,
            "steps": t,
            "out_len": out_pos,
            "target_len": L,
            "p_final": p,
            "p_min": p_min,
            "p_max": p_max,
        }

    return {
        "verdict": "AC",
        "message": "OK",
        "prog_len": n,
        "steps": t,
        "out_len": out_pos,
        "target_len": L,
        "p_final": p,
        "p_min": p_min,
        "p_max": p_max,
    }
