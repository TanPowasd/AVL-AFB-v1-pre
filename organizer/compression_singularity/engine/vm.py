# -*- coding: utf-8 -*-
"""《压缩奇点》高性能确定性虚拟机接口、常驻零重分配服务池、AOT JIT 与评分公式。"""
from __future__ import annotations

import atexit
import hashlib
import json
import lzma
import math
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import threading
from typing import Any

LOG2_6 = math.log2(6.0)
DEFAULT_STEP_LIMIT = 3 * 10**12
DEFAULT_TAPE_BOUND = 10**7
DEFAULT_VM_CPU_LIMIT_S = 600.0

ENGINE_DIR = Path(__file__).resolve().parent
VM_CPP_PATH = ENGINE_DIR / "vm.cpp"
_CACHED_VM_BIN: str | None = None
_VM_BUILD_LOCK = threading.Lock()


def pack_bits_msb(s: str) -> bytes:
    """将二进制串 S 按高位优先（MSB-first）紧凑打包为 ceil(L/8) 字节，末字节低位补 0。"""
    n = len(s)
    if n == 0:
        return b""
    pad = (-n) % 8
    padded = s + ("0" * pad) if pad else s
    return int(padded, 2).to_bytes((n + pad) // 8, "big")


def compute_b_ref(s: str) -> int:
    """用标准库 liblzma（FORMAT_RAW + FILTER_LZMA2, preset=9）计算有效载荷字节数 × 8。"""
    raw_bytes = pack_bits_msb(s)
    payload = lzma.compress(
        raw_bytes,
        format=lzma.FORMAT_RAW,
        filters=[{"id": lzma.FILTER_LZMA2, "preset": 9}],
    )
    return max(8, len(payload) * 8)


def compute_raw_score(b_ref_bits: int, prog_len: int) -> float:
    """题面原公式（不封顶）：100 * B_ref / (|P| * log2(6))。"""
    if b_ref_bits <= 0 or prog_len <= 0:
        return 0.0
    return 100.0 * float(b_ref_bits) / (float(prog_len) * LOG2_6)


_NORM_HALF_POW = 1000.0 ** 0.75


def compute_norm_score(raw_score: float) -> float:
    """单调饱和百分制折算分：100 * r^0.75 / (r^0.75 + 1000^0.75)（r=1000 对应 50.00 分）。"""
    if not math.isfinite(raw_score) or raw_score <= 0.0:
        return 0.0
    rp = raw_score ** 0.75
    return 100.0 * rp / (rp + _NORM_HALF_POW)


def parse_input_text(raw: str) -> tuple[int, str]:
    lines = raw.splitlines()
    if len(lines) < 2:
        raise ValueError("Input must have at least 2 lines: L and binary string S")
    L = int(lines[0].strip())
    S = lines[1].strip()
    if L <= 0 or len(S) != L:
        raise ValueError(f"Length mismatch: header L={L}, actual len(S)={len(S)}")
    if any(ch not in ("0", "1") for ch in S):
        raise ValueError("Target string S must contain only '0' and '1'")
    return L, S


def ensure_vm_binary() -> str:
    """按 vm.cpp 内容哈希编译并缓存高优化 C++20 参考虚拟机二进制（-O3 -march=native 回退 -O3）。"""
    global _CACHED_VM_BIN
    if _CACHED_VM_BIN and os.path.isfile(_CACHED_VM_BIN):
        return _CACHED_VM_BIN

    with _VM_BUILD_LOCK:
        if _CACHED_VM_BIN and os.path.isfile(_CACHED_VM_BIN):
            return _CACHED_VM_BIN
        src_bytes = VM_CPP_PATH.read_bytes()
        digest = hashlib.sha256(src_bytes).hexdigest()[:16]
        ext = ".exe" if os.name == "nt" else ""
        uid = os.getuid() if hasattr(os, "getuid") else os.environ.get("USERNAME", "win")
        out_path = Path(tempfile.gettempdir()) / f"afb_cs_vm4_{uid}_{digest}{ext}"
        if out_path.is_file() and out_path.stat().st_size > 0:
            _CACHED_VM_BIN = str(out_path)
            return _CACHED_VM_BIN

        cxx = os.environ.get("CXX", "g++")
        with tempfile.TemporaryDirectory(prefix="afb_cs_vm_build_") as tmp:
            tmp_exe = Path(tmp) / f"vm{ext}"
            last_err = ""
            for flags in (
                ["-std=gnu++20", "-O3", "-march=native", "-funroll-loops", "-pipe"],
                ["-std=gnu++20", "-O3", "-pipe"],
            ):
                cmd = [cxx, *flags, str(VM_CPP_PATH), "-o", str(tmp_exe)]
                proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60)
                if proc.returncode == 0 and tmp_exe.is_file():
                    break
                last_err = (proc.stdout or "") + "\n" + (proc.stderr or "")
            else:
                raise RuntimeError(f"Failed to compile reference VM ({cxx}):\n{last_err}")
            try:
                shutil.copy2(tmp_exe, out_path)
            except OSError:
                if not (out_path.is_file() and out_path.stat().st_size > 0):
                    raise
        _CACHED_VM_BIN = str(out_path)
        return _CACHED_VM_BIN


class _PersistentVMWorker:
    """常驻零重分配 VM 工作进程：单次分配 20 MB 纸带并仅清空脏区间，复用临时目录实现亚毫秒调用延迟。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._proc: subprocess.Popen | None = None
        self._tmp: tempfile.TemporaryDirectory[str] | None = None
        self._req_seq = 0

    def _ensure_tmp(self) -> Path:
        if self._tmp is None:
            self._tmp = tempfile.TemporaryDirectory(prefix="afb_cs_wkr_", ignore_cleanup_errors=True)
        return Path(self._tmp.name)

    def _ensure_alive(self) -> subprocess.Popen:
        if self._proc is None or self._proc.poll() is not None:
            vm_bin = ensure_vm_binary()
            self._proc = subprocess.Popen(
                [vm_bin, "--server"],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                bufsize=1,
            )
        return self._proc

    def close(self) -> None:
        with self._lock:
            p = self._proc
            self._proc = None
            if p is not None:
                try:
                    if p.stdin:
                        p.stdin.write("QUIT\n")
                        p.stdin.flush()
                        p.stdin.close()
                except OSError:
                    pass
                try:
                    p.wait(timeout=1.0)
                except Exception:
                    try:
                        p.kill()
                        p.wait(timeout=1.0)
                    except Exception:
                        pass
                for s in (p.stdout, p.stderr):
                    if s:
                        try:
                            s.close()
                        except OSError:
                            pass
            if self._tmp is not None:
                try:
                    self._tmp.cleanup()
                except Exception:
                    pass
                self._tmp = None

    def call_bytes(
        self,
        *,
        run_only: bool,
        target_bytes: bytes | None,
        target_file_path: str | None,
        program_bytes: bytes,
        step_limit: int,
        tape_bound: int,
        max_prog_len: int,
        cpu_limit_s: float,
        trace_points: int,
        profile: bool,
    ) -> dict[str, Any]:
        def _hex(s: str) -> str:
            return s.encode("utf-8").hex() if s else "-"

        with self._lock:
            wdir = self._ensure_tmp()
            self._req_seq += 1
            if run_only:
                t_path = ""
            elif target_file_path and os.path.isfile(target_file_path):
                t_path = target_file_path
            else:
                tp = wdir / "target.in"
                tp.write_bytes(target_bytes or b"")
                # Force reload in server cache when content changes on same path by toggling slot A/B
                t_slot = wdir / f"target_{self._req_seq & 1}.in"
                t_slot.write_bytes(target_bytes or b"")
                t_path = str(t_slot)
            p_path = wdir / "prog.bf"
            p_path.write_bytes(program_bytes)
            tr_path = wdir / "trace.json"
            trace_arg = str(tr_path) if trace_points > 0 else ""

            req = (
                f"REQ {'R' if run_only else 'V'} {int(step_limit)} {int(tape_bound)} "
                f"{int(max_prog_len)} {float(cpu_limit_s):.3f} {int(trace_points)} "
                f"{1 if profile else 0} {_hex(t_path)} {_hex(str(p_path))} {_hex(trace_arg)}\n"
            )
            proc = self._ensure_alive()
            assert proc.stdin is not None and proc.stdout is not None
            try:
                proc.stdin.write(req)
                proc.stdin.flush()
                line = proc.stdout.readline()
            except OSError:
                line = ""
            if not line:
                self._proc = None
                proc = self._ensure_alive()
                assert proc.stdin is not None and proc.stdout is not None
                proc.stdin.write(req)
                proc.stdin.flush()
                line = proc.stdout.readline()
            if not line:
                raise RuntimeError("Persistent VM worker returned empty response")
            res = json.loads(line)
            if trace_points > 0 and tr_path.is_file():
                res["trace"] = json.loads(tr_path.read_text(encoding="utf-8"))
            return res


_THREAD_LOCAL = threading.local()
_ALL_WORKERS: list[_PersistentVMWorker] = []
_ALL_WORKERS_LOCK = threading.Lock()


def _get_worker() -> _PersistentVMWorker:
    w = getattr(_THREAD_LOCAL, "vm_worker", None)
    if w is None:
        w = _PersistentVMWorker()
        _THREAD_LOCAL.vm_worker = w
        with _ALL_WORKERS_LOCK:
            _ALL_WORKERS.append(w)
    return w


def shutdown_vm_workers() -> None:
    with _ALL_WORKERS_LOCK:
        workers = list(_ALL_WORKERS)
        _ALL_WORKERS.clear()
    for w in workers:
        w.close()


atexit.register(shutdown_vm_workers)


def _run_vm_oneshot(
    target_path: str,
    prog_path: str,
    *,
    run_only: bool,
    step_limit: int,
    tape_bound: int,
    max_prog_len: int | None,
    cpu_limit_s: float,
    trace_points: int,
    trace_out: str,
    profile: bool,
    max_out_bits: int,
) -> dict[str, Any]:
    vm_bin = ensure_vm_binary()
    cmd = [
        vm_bin,
        "-" if run_only else target_path,
        prog_path,
        "--step-limit", str(int(step_limit)),
        "--tape-bound", str(int(tape_bound)),
        "--cpu-limit-s", str(float(cpu_limit_s)),
    ]
    if max_prog_len is not None:
        cmd.extend(["--max-prog-len", str(int(max_prog_len))])
    if run_only:
        cmd.extend(["--run-only", "--max-out-bits", str(int(max_out_bits))])
    if trace_points > 0 and trace_out:
        cmd.extend(["--trace-points", str(int(trace_points)), "--trace-out", trace_out])
    if profile:
        cmd.append("--profile")

    wall_timeout = max(5.0, float(cpu_limit_s) + 15.0)
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=wall_timeout,
        )
    except subprocess.TimeoutExpired:
        return {
            "verdict": "VM_TIMEOUT",
            "message": f"VM verification exceeded physical watchdog ({cpu_limit_s:.0f}s)",
            "prog_len": os.path.getsize(prog_path) if os.path.isfile(prog_path) else 0,
            "steps": step_limit,
            "out_len": 0,
            "p_final": 0,
            "p_min": 0,
            "p_max": 0,
            "elapsed_ms": wall_timeout * 1000.0,
            "op_counts": {k: 0 for k in "><+.[]"},
            "trace": [],
        }
    if proc.returncode != 0 or not proc.stdout.strip():
        raise RuntimeError(f"Reference VM internal failure (rc={proc.returncode}): {proc.stderr}")
    return json.loads(proc.stdout.strip().splitlines()[-1])


def verify_program_bytes(
    target_input_bytes: bytes,
    program_bytes: bytes,
    *,
    step_limit: int = DEFAULT_STEP_LIMIT,
    tape_bound: int = DEFAULT_TAPE_BOUND,
    max_prog_len: int | None = None,
    cpu_limit_s: float = DEFAULT_VM_CPU_LIMIT_S,
    trace_points: int = 0,
    profile: bool = False,
    mode: str = "fast",
    target_file_path: str | None = None,
) -> dict[str, Any]:
    """调用高性能参考虚拟机精确校验程序 P。

    mode:
      - 'fast' (默认): 常驻零重分配超级指令直接线索化 C++20 虚拟机（长时高步数自动用独立进程看门狗）
      - 'jit'        : 纯 C99 + AVX-512/AVX2 AOT 转译机器码执行（适合 |P| <= 32768 的 10^9–3×10^12 步重载校验）
      - 'ref'        : 零优化纯 Python 逐条参考机（仅建议用于小规模教学/对拍）
    """
    if mode == "ref":
        import vm_ref
        _L, S = parse_input_text(target_input_bytes.decode("ascii"))
        return vm_ref.run_reference_vm(
            S,
            program_bytes.decode("utf-8", errors="surrogateescape"),
            step_limit=step_limit,
            tape_bound=tape_bound,
            max_prog_len=max_prog_len,
        )

    if mode == "jit":
        import vm_jit
        stripped_len = len(program_bytes.rstrip(b"\r\n"))
        if stripped_len <= vm_jit.JIT_MAX_PROG_LEN and trace_points == 0 and not profile:
            return vm_jit.compile_and_run_jit(
                target_input_bytes,
                program_bytes,
                step_limit=step_limit,
                tape_bound=tape_bound,
                max_prog_len=max_prog_len,
                cpu_limit_s=cpu_limit_s,
                run_only=False,
            )

    # 常规校验走常驻零重分配服务池（亚毫秒级延迟）；超长步限（> 5e9）走独立进程看门狗
    use_persistent = (step_limit <= 5 * 10**9) and (cpu_limit_s <= 600.0)
    if use_persistent:
        result = _get_worker().call_bytes(
            run_only=False,
            target_bytes=target_input_bytes,
            target_file_path=target_file_path,
            program_bytes=program_bytes,
            step_limit=step_limit,
            tape_bound=tape_bound,
            max_prog_len=max_prog_len or 0,
            cpu_limit_s=cpu_limit_s,
            trace_points=trace_points,
            profile=profile,
        )
        if result.get("verdict") == "INTERNAL_ERROR":
            raise RuntimeError(f"VM server error: {result.get('message')}")
        result.setdefault("engine", "fast_vm2")
        return result

    with tempfile.TemporaryDirectory(prefix="afb_cs_verify_") as tmp:
        if target_file_path and os.path.isfile(target_file_path):
            t_path = target_file_path
        else:
            tp = Path(tmp) / "target.in"
            tp.write_bytes(target_input_bytes)
            t_path = str(tp)
        p_path = Path(tmp) / "prog.bf"
        p_path.write_bytes(program_bytes)
        tr_path = Path(tmp) / "trace.json"

        result = _run_vm_oneshot(
            t_path,
            str(p_path),
            run_only=False,
            step_limit=step_limit,
            tape_bound=tape_bound,
            max_prog_len=max_prog_len,
            cpu_limit_s=cpu_limit_s,
            trace_points=trace_points,
            trace_out=str(tr_path) if trace_points > 0 else "",
            profile=profile,
            max_out_bits=0,
        )
        if trace_points > 0 and tr_path.is_file():
            result["trace"] = json.loads(tr_path.read_text(encoding="utf-8"))
        result.setdefault("engine", "fast_vm2")
        return result


def verify_program(
    target_str: str,
    program_str: str,
    *,
    step_limit: int = DEFAULT_STEP_LIMIT,
    tape_bound: int = DEFAULT_TAPE_BOUND,
    max_prog_len: int | None = None,
    cpu_limit_s: float = DEFAULT_VM_CPU_LIMIT_S,
    trace_points: int = 0,
    profile: bool = False,
    mode: str = "fast",
) -> dict[str, Any]:
    target_bytes = f"{len(target_str)}\n{target_str}\n".encode("ascii")
    prog_bytes = program_str.encode("utf-8")
    return verify_program_bytes(
        target_bytes,
        prog_bytes,
        step_limit=step_limit,
        tape_bound=tape_bound,
        max_prog_len=max_prog_len,
        cpu_limit_s=cpu_limit_s,
        trace_points=trace_points,
        profile=profile,
        mode=mode,
    )


def simulate_program(
    program_str: str,
    *,
    step_limit: int = DEFAULT_STEP_LIMIT,
    tape_bound: int = DEFAULT_TAPE_BOUND,
    cpu_limit_s: float = DEFAULT_VM_CPU_LIMIT_S,
    max_out_bits: int = 2_000_000,
    trace_points: int = 0,
    profile: bool = False,
    mode: str = "fast",
) -> dict[str, Any]:
    """无目标串自由模拟执行程序 P，返回生成的比特流 O、精确步数、纸带区间、非零格与热点剖析。"""
    prog_bytes = program_str.encode("utf-8")
    if mode == "jit":
        import vm_jit
        return vm_jit.compile_and_run_jit(
            None,
            prog_bytes,
            step_limit=step_limit,
            tape_bound=tape_bound,
            cpu_limit_s=cpu_limit_s,
            run_only=True,
            max_out_bits=max_out_bits,
        )

    with tempfile.TemporaryDirectory(prefix="afb_cs_sim_") as tmp:
        p_path = Path(tmp) / "prog.bf"
        p_path.write_bytes(prog_bytes)
        tr_path = Path(tmp) / "trace.json"
        result = _run_vm_oneshot(
            "-",
            str(p_path),
            run_only=True,
            step_limit=step_limit,
            tape_bound=tape_bound,
            max_prog_len=max(len(program_str), 10**7),
            cpu_limit_s=cpu_limit_s,
            trace_points=trace_points,
            trace_out=str(tr_path) if trace_points > 0 else "",
            profile=profile,
            max_out_bits=max_out_bits,
        )
        if trace_points > 0 and tr_path.is_file():
            result["trace"] = json.loads(tr_path.read_text(encoding="utf-8"))
        result.setdefault("engine", "fast_vm2")
        return result
