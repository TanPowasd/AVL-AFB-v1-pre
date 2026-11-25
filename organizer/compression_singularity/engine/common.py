# -*- coding: utf-8 -*-
"""《压缩奇点》C++20 编译与隔离沙盒运行基础设施。

兼容 AVL_SANDBOX_WRAP（Linux bubblewrap / Windows AppContainer + Job Object），
并支持 60s 级求解时限与最大 2L+4096 字节标准输出上限。
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import dataclass

IS_WIN = os.name == "nt"

if IS_WIN:
    import ctypes
    import ctypes.wintypes as wt

    class _PROCESS_MEMORY_COUNTERS(ctypes.Structure):
        _fields_ = [
            ("cb", wt.DWORD),
            ("PageFaultCount", wt.DWORD),
            ("PeakWorkingSetSize", ctypes.c_size_t),
            ("WorkingSetSize", ctypes.c_size_t),
            ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
            ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
            ("PagefileUsage", ctypes.c_size_t),
            ("PeakPagefileUsage", ctypes.c_size_t),
        ]


@dataclass
class CompileResult:
    ok: bool
    exe_path: str
    log: str
    elapsed_ms: float


@dataclass
class RunResult:
    returncode: int
    timed_out: bool
    ole: bool
    cpu_ms: float
    wall_ms: float
    peak_mem_mb: float
    stdout: bytes
    stderr: bytes


def find_cxx() -> str:
    return os.environ.get("CXX", "g++")


def compile_cpp(
    sources: list[str],
    out_exe: str,
    include_dirs: list[str] | None = None,
    stack_mb: int = 512,
    extra_flags: list[str] | None = None,
    timeout_sec: float = 45.0,
) -> CompileResult:
    cxx = find_cxx()
    if IS_WIN and not out_exe.lower().endswith(".exe"):
        out_exe += ".exe"
    os.makedirs(os.path.dirname(os.path.abspath(out_exe)), exist_ok=True)

    cmd = [cxx, "-std=gnu++20", "-O2", "-pipe"]
    if IS_WIN:
        stack_bytes = max(64, stack_mb) * 1024 * 1024
        cmd.append(f"-Wl,--stack,{stack_bytes}")
    for inc in include_dirs or []:
        cmd.extend(["-I", os.path.abspath(inc)])
    if extra_flags:
        cmd.extend(extra_flags)
    cmd.extend(os.path.abspath(s) for s in sources)
    cmd.extend(["-o", os.path.abspath(out_exe)])
    wrap = os.environ.get("AVL_SANDBOX_WRAP")
    if wrap:
        cmd = [wrap, "--compile", *cmd]

    t0 = time.perf_counter()
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_sec,
        )
        elapsed_ms = (time.perf_counter() - t0) * 1000.0
        log = (proc.stdout or "") + (proc.stderr or "")
        return CompileResult(
            ok=(proc.returncode == 0 and os.path.isfile(out_exe)),
            exe_path=os.path.abspath(out_exe),
            log=log.strip(),
            elapsed_ms=elapsed_ms,
        )
    except subprocess.TimeoutExpired:
        elapsed_ms = (time.perf_counter() - t0) * 1000.0
        return CompileResult(
            ok=False,
            exe_path=os.path.abspath(out_exe),
            log="Compilation timed out",
            elapsed_ms=elapsed_ms,
        )


_LAUNCHER_SOURCE = r"""
#include <errno.h>
#include <fcntl.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <sys/prctl.h>
#include <sys/resource.h>
#include <sys/wait.h>
#include <unistd.h>
int main(int argc, char **argv) {
    if (argc < 3) return 125;
    int fd = atoi(argv[1]);
    fcntl(fd, F_SETFD, FD_CLOEXEC);
    pid_t pid = fork();
    if (pid < 0) return 125;
    if (pid == 0) {
        prctl(PR_SET_PDEATHSIG, SIGKILL);
        execv(argv[2], argv + 2);
        _exit(127);
    }
    int status = 0;
    struct rusage ru;
    while (wait4(pid, &status, 0, &ru) < 0) {
        if (errno != EINTR) return 125;
    }
    char buf[160];
    int n = snprintf(buf, sizeof buf, "%d %ld %ld %ld %ld %ld\n", status,
                     (long)ru.ru_utime.tv_sec, (long)ru.ru_utime.tv_usec,
                     (long)ru.ru_stime.tv_sec, (long)ru.ru_stime.tv_usec, (long)ru.ru_maxrss);
    if (write(fd, buf, n) != n) return 125;
    return 0;
}
"""


def _launcher_path() -> str | None:
    if not sys.platform.startswith("linux"):
        return None
    digest = hashlib.sha256(_LAUNCHER_SOURCE.encode()).hexdigest()[:16]
    path = os.path.join(tempfile.gettempdir(), f"avl_launcher_{os.getuid()}_{digest}")
    if os.path.isfile(path):
        return path
    with tempfile.TemporaryDirectory(prefix="afb_launcher_") as tmp:
        src = os.path.join(tmp, "launcher.cpp")
        out = os.path.join(tmp, "launcher")
        with open(src, "w", encoding="utf-8") as f:
            f.write(_LAUNCHER_SOURCE)
        if not compile_cpp([src], out).ok:
            return None
        shutil.move(out, path)
    return path


def _read_meter(fd: int) -> tuple[int, float, float] | None:
    try:
        parts = os.read(fd, 256).decode("ascii").split()
        status, us, uu, ss, su, rss = (int(x) for x in parts)
    except (OSError, ValueError):
        return None
    return os.waitstatus_to_exitcode(status), (us + ss) * 1000.0 + (uu + su) / 1000.0, rss / 1024.0


def _query_win32_metrics(proc: subprocess.Popen) -> tuple[float, float]:
    try:
        h = wt.HANDLE(int(proc._handle))
        ct, et, kt, ut = wt.FILETIME(), wt.FILETIME(), wt.FILETIME(), wt.FILETIME()
        cpu_ms = 0.0
        if ctypes.windll.kernel32.GetProcessTimes(
            h, ctypes.byref(ct), ctypes.byref(et), ctypes.byref(kt), ctypes.byref(ut)
        ):
            k_ticks = (kt.dwHighDateTime << 32) | kt.dwLowDateTime
            u_ticks = (ut.dwHighDateTime << 32) | ut.dwLowDateTime
            cpu_ms = (k_ticks + u_ticks) / 10000.0

        pmc = _PROCESS_MEMORY_COUNTERS()
        pmc.cb = ctypes.sizeof(_PROCESS_MEMORY_COUNTERS)
        peak_mb = 0.0
        if ctypes.windll.kernel32.K32GetProcessMemoryInfo(h, ctypes.byref(pmc), pmc.cb):
            peak_mb = pmc.PeakWorkingSetSize / (1024.0 * 1024.0)
        return cpu_ms, peak_mb
    except Exception:
        return 0.0, 0.0


def _drain_stream(stream, sink: list[bytes], limit_bytes: int, ole_flag: list[bool], proc: subprocess.Popen):
    total = 0
    try:
        while True:
            chunk = stream.read(65536)
            if not chunk:
                break
            total += len(chunk)
            if total <= limit_bytes:
                sink.append(chunk)
            else:
                ole_flag[0] = True
                try:
                    proc.kill()
                except Exception:
                    pass
                break
    except Exception:
        pass


def _posix_wait(proc: subprocess.Popen, timeout: float) -> tuple[bool, float | None, float | None]:
    box: dict = {}
    done = threading.Event()

    def _reap():
        try:
            _pid, status, ru = os.wait4(proc.pid, 0)
            box["status"], box["ru"] = status, ru
        except ChildProcessError:
            pass
        finally:
            done.set()

    threading.Thread(target=_reap, daemon=True).start()
    timed_out = False
    if not done.wait(timeout):
        timed_out = True
        try:
            proc.kill()
        except Exception:
            pass
        done.wait()
    if "status" in box:
        try:
            proc.returncode = os.waitstatus_to_exitcode(box["status"])
        except Exception:
            proc.returncode = -1
        ru = box["ru"]
        cpu_ms = (ru.ru_utime + ru.ru_stime) * 1000.0
        peak_mb = ru.ru_maxrss / (1024.0 * 1024.0 if sys.platform == "darwin" else 1024.0)
        return timed_out, cpu_ms, peak_mb
    proc.wait()
    return timed_out, None, None


def run_sandboxed(
    exe_path: str,
    stdin_bytes: bytes,
    time_limit_ms: int,
    memory_limit_mb: int,
    max_output_bytes: int = 8 * 1024 * 1024,
) -> RunResult:
    """在全新的独立空白沙盒目录中执行 C++ 二进制并采集 CPU/墙钟/内存指标。"""
    exe_abs = os.path.abspath(exe_path)
    posix_cpu: list = [None, None]
    wrap = os.environ.get("AVL_SANDBOX_WRAP")
    with (tempfile.TemporaryDirectory(prefix="afb_cs_sandbox_", ignore_cleanup_errors=True) as sandbox_dir,
          tempfile.TemporaryDirectory(prefix="avl-metrics-") as metrics_dir):
        env = {
            "PATH": os.environ.get("PATH", ""),
            "SystemRoot": os.environ.get("SystemRoot", r"C:\Windows"),
            "TEMP": sandbox_dir,
            "TMP": sandbox_dir,
            "TMPDIR": sandbox_dir,
            "HOME": sandbox_dir,
            "USERPROFILE": sandbox_dir,
        }
        metrics_path = Path(metrics_dir) / "metrics.json" if IS_WIN and wrap else None
        if metrics_path is not None:
            env["AVL_SANDBOX_METRICS_FILE"] = str(metrics_path)
            env["AVL_SANDBOX_CPU_LIMIT_MS"] = str(time_limit_ms)
            env["AVL_SANDBOX_WALL_LIMIT_MS"] = str(time_limit_ms + 3000)

        creationflags = 0
        if IS_WIN:
            ctypes.windll.kernel32.SetErrorMode(0x0001 | 0x0002)
            creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)

        if wrap:
            env = {**os.environ, **env, "AVL_SANDBOX_MEM_MB": str(memory_limit_mb)}
        preexec_fn = None
        if not IS_WIN and not wrap:
            import resource

            def _limits():
                mem_bytes = max(256, memory_limit_mb + 64) * 1024 * 1024
                try:
                    resource.setrlimit(resource.RLIMIT_STACK, (mem_bytes, mem_bytes))
                except Exception:
                    pass
                try:
                    resource.setrlimit(resource.RLIMIT_AS, (mem_bytes, mem_bytes))
                except Exception:
                    pass

            preexec_fn = _limits

        launcher = None if IS_WIN else _launcher_path()
        meter_r = meter_w = None
        argv = [exe_abs]
        if launcher:
            meter_r, meter_w = os.pipe()
            argv = [launcher, str(meter_w), exe_abs]
        if wrap:
            argv = [wrap, "--run", sandbox_dir, launcher or "", str(meter_w), exe_abs]

        proc = subprocess.Popen(
            argv,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=sandbox_dir,
            env=env,
            creationflags=creationflags,
            preexec_fn=preexec_fn,
            pass_fds=(meter_w,) if launcher else (),
        )
        if meter_w is not None:
            os.close(meter_w)

        out_chunks: list[bytes] = []
        err_chunks: list[bytes] = []
        ole_flag = [False]
        timed_out = False

        t_err = threading.Thread(
            target=_drain_stream,
            args=(proc.stderr, err_chunks, 2 * 1024 * 1024, ole_flag, proc),
            daemon=True,
        )
        t_out = threading.Thread(
            target=_drain_stream,
            args=(proc.stdout, out_chunks, max_output_bytes, ole_flag, proc),
            daemon=True,
        )
        t_err.start()
        t_out.start()

        def _writer():
            if proc.stdin is None:
                return
            try:
                with proc.stdin as stream:
                    if stdin_bytes:
                        stream.write(stdin_bytes)
            except OSError:
                pass

        t_in = threading.Thread(target=_writer, daemon=True)
        t0 = time.perf_counter()
        t_in.start()

        tl_s = max(0.5, time_limit_ms / 1000.0)
        wall_budget = max(tl_s + 5.0, tl_s * 1.15)
        if IS_WIN:
            try:
                proc.wait(timeout=wall_budget)
            except subprocess.TimeoutExpired:
                timed_out = True
                try:
                    proc.kill()
                except Exception:
                    pass
                proc.wait()
        else:
            timed_out, posix_cpu[0], posix_cpu[1] = _posix_wait(proc, wall_budget)

        wall_ms = (time.perf_counter() - t0) * 1000.0
        t_in.join(timeout=2.0)
        t_out.join(timeout=2.0)
        t_err.join(timeout=2.0)
        for stream in (proc.stdout, proc.stderr):
            if stream is not None:
                try:
                    stream.close()
                except OSError:
                    pass

        if metrics_path is not None and not (timed_out or ole_flag[0]):
            try:
                metrics = json.loads(metrics_path.read_text(encoding="utf-8"))
                if metrics.get("running") or (env.get("AVL_SANDBOX_REQUIRE_APPCONTAINER") == "1"
                                               and metrics.get("appcontainer") is not True):
                    raise ValueError("incomplete isolation or metrics")
                cpu_ms = float(metrics["cpu_ms"])
                peak_mb = float(metrics["peak_mem_mb"])
                proc.returncode = int(metrics["exit_code"])
                timed_out = bool(metrics.get("timed_out") or metrics.get("cpu_exceeded"))
                if metrics.get("mem_limit_hit"):
                    peak_mb = max(peak_mb, memory_limit_mb + 1.0)
            except (OSError, ValueError, KeyError, TypeError) as exc:
                raise RuntimeError("Windows sandbox did not return valid contestant metrics") from exc
        elif IS_WIN:
            cpu_ms, peak_mb = _query_win32_metrics(proc)
        elif posix_cpu[0] is not None:
            cpu_ms, peak_mb = posix_cpu[0], posix_cpu[1]
        else:
            cpu_ms, peak_mb = wall_ms, 0.0
        if meter_r is not None:
            meter = _read_meter(meter_r)
            os.close(meter_r)
            if meter is not None:
                proc.returncode, cpu_ms, peak_mb = meter

        if cpu_ms > time_limit_ms or wall_ms > time_limit_ms + 3000.0:
            timed_out = True

        return RunResult(
            returncode=proc.returncode if proc.returncode is not None else -1,
            timed_out=timed_out,
            ole=ole_flag[0],
            cpu_ms=cpu_ms,
            wall_ms=wall_ms,
            peak_mem_mb=peak_mb,
            stdout=b"".join(out_chunks),
            stderr=b"".join(err_chunks),
        )
