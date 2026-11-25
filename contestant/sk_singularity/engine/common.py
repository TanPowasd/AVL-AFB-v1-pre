# -*- coding: utf-8 -*-
"""《双符奇点》（sk_singularity）C++20 编译、裁判二进制缓存与隔离沙盒运行基础设施。

特性：
  - 兼容 AVL_SANDBOX_WRAP（Linux bubblewrap / Windows AppContainer + Job Object）
  - 支持在禁写 :tmpdir 沙箱中自动回退到工作区内 logs/.tmp
  - 按源码 SHA-256 自动编译并缓存原生 C++20 确定性裁判（judge_cli）
  - 精确采集选手 CPU 时间、墙钟时间、峰值内存与输出长度护栏（防 OLE/TLE/MLE）
"""
from __future__ import annotations

import atexit
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import queue
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from typing import Any

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
    mle: bool = False


def find_cxx() -> str:
    return os.environ.get("CXX", "g++")


_PROBLEM_ROOT = Path(__file__).resolve().parent.parent
_JUDGE_BUILD_LOCK = threading.Lock()
_LAUNCHER_BUILD_LOCK = threading.Lock()
_JUDGE_CACHE: dict[str, str] = {}


def writable_temp_root(fallback_root: str | Path | None = None) -> Path:
    """优先使用系统临时目录；若处于禁写 :tmpdir 沙箱则自动回退到工作区内 logs/.tmp。"""
    try:
        sys_tmp = Path(tempfile.gettempdir())
        probe = sys_tmp / f".afb_sk_probe_{os.getpid()}_{time.time_ns()}"
        probe.write_bytes(b"")
        probe.unlink(missing_ok=True)
        return sys_tmp
    except OSError:
        base = Path(fallback_root).resolve() if fallback_root else _PROBLEM_ROOT
        alt = base / "logs" / ".tmp"
        alt.mkdir(parents=True, exist_ok=True)
        return alt


def compile_cpp(
    sources: list[str],
    out_exe: str,
    include_dirs: list[str] | None = None,
    stack_mb: int = 256,
    extra_flags: list[str] | None = None,
    timeout_sec: float = 45.0,
    use_sandbox_wrap: bool = True,
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

    wrap = os.environ.get("AVL_SANDBOX_WRAP") if use_sandbox_wrap else None
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


def ensure_judge_binary(root: str | Path | None = None) -> str:
    """编译并缓存原生 C++20 裁判二进制（不经过选手沙盒编译包装，启用 -O3，按源码 SHA-256 缓存）。"""
    base = Path(root).resolve() if root else _PROBLEM_ROOT
    src_cpp = (base / "engine" / "judge_cli.cpp").resolve()
    hdr_hpp = (base / "engine" / "sk_core.hpp").resolve()
    h = hashlib.sha256()
    h.update(b"judge_opt=-O3\0")
    for p in (src_cpp, hdr_hpp):
        h.update(p.name.encode("utf-8"))
        h.update(b"\0")
        h.update(p.read_bytes())
        h.update(b"\0")
    digest = h.hexdigest()[:16]
    cache_key = f"sk_judge_{digest}"
    with _JUDGE_BUILD_LOCK:
        cached = _JUDGE_CACHE.get(cache_key)
        if cached and os.path.isfile(cached):
            return cached
        cache_dir = writable_temp_root(base) / "afb_sk_singularity_bin"
        cache_dir.mkdir(parents=True, exist_ok=True)
        ext = ".exe" if IS_WIN else ""
        target = cache_dir / f"{cache_key}{ext}"
        if target.is_file():
            _JUDGE_CACHE[cache_key] = str(target)
            return str(target)
        tmp_out = cache_dir / f".tmp_{cache_key}_{os.getpid()}_{time.time_ns()}{ext}"
        res = compile_cpp(
            [str(src_cpp)],
            str(tmp_out),
            include_dirs=[str(base / "engine")],
            stack_mb=128,
            extra_flags=["-O3"],
            use_sandbox_wrap=False,
        )
        if not res.ok:
            raise RuntimeError(f"Failed to compile judge_cli.cpp:\n{res.log}")
        try:
            os.replace(res.exe_path, target)
        except OSError:
            if not target.is_file():
                shutil.copyfile(res.exe_path, target)
            try:
                os.unlink(res.exe_path)
            except OSError:
                pass
        _JUDGE_CACHE[cache_key] = str(target)
        return str(target)


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
    tmp_root = str(writable_temp_root())
    path = os.path.join(tmp_root, f"avl_sk_launcher_{os.getuid()}_{digest}")
    if os.path.isfile(path):
        return path
    with _LAUNCHER_BUILD_LOCK:
        if os.path.isfile(path):
            return path
        with tempfile.TemporaryDirectory(prefix="afb_sk_launcher_", dir=tmp_root) as tmp:
            src = os.path.join(tmp, "launcher.cpp")
            out = os.path.join(tmp, "launcher")
            with open(src, "w", encoding="utf-8") as f:
                f.write(_LAUNCHER_SOURCE)
            if not compile_cpp([src], out, use_sandbox_wrap=False).ok:
                return None
            try:
                os.replace(out, path)
            except OSError:
                if not os.path.isfile(path):
                    return None
    return path


def _read_win_sandbox_metrics(path: str) -> dict[str, Any] | None:
    if not path or not os.path.isfile(path):
        return None
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:
        return None


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


def _drain_stream(stream, sink: list[bytes], limit_bytes: int, ole_flag: list[bool] | None, proc: subprocess.Popen):
    total = 0
    try:
        while True:
            chunk = stream.read(16384)
            if not chunk:
                break
            if ole_flag is None:
                if total < limit_bytes:
                    sink.append(chunk[: limit_bytes - total])
                    total += len(chunk)
                continue
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
    box: dict[str, Any] = {}
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
    memory_limit_mb: int = 1024,
    max_output_bytes: int = 65536,
) -> RunResult:
    """在全新的独立空白沙盒目录中执行 C++20 求解器二进制并采集 CPU/墙钟/内存指标。"""
    exe_abs = os.path.abspath(exe_path)
    posix_cpu: list[Any] = [None, None]
    tmp_root = str(writable_temp_root())
    with tempfile.TemporaryDirectory(prefix="afb_sk_metrics_", dir=tmp_root, ignore_cleanup_errors=True) as metrics_dir, \
         tempfile.TemporaryDirectory(prefix="afb_sk_sandbox_", dir=tmp_root, ignore_cleanup_errors=True) as sandbox_dir:
        metrics_json_path = os.path.join(metrics_dir, "metrics.json")
        wrap = os.environ.get("AVL_SANDBOX_WRAP")
        tl_s = max(0.5, time_limit_ms / 1000.0)
        wall_budget = max(tl_s + (3.5 if wrap else 2.5), tl_s * 1.6)

        if wrap:
            env = dict(os.environ)
            env["AVL_SANDBOX_MEM_MB"] = str(max(64, memory_limit_mb))
            env["AVL_SANDBOX_CPU_LIMIT_MS"] = str(time_limit_ms)
            env["AVL_SANDBOX_WALL_LIMIT_MS"] = str(int(max(1000.0, wall_budget * 1000.0)))
            env["AVL_SANDBOX_METRICS_FILE"] = metrics_json_path
        else:
            env = {
                "PATH": os.environ.get("PATH", ""),
                "SystemRoot": os.environ.get("SystemRoot", r"C:\Windows"),
                "TEMP": sandbox_dir,
                "TMP": sandbox_dir,
                "TMPDIR": sandbox_dir,
                "HOME": sandbox_dir,
                "USERPROFILE": sandbox_dir,
            }
        creationflags = 0
        if IS_WIN:
            ctypes.windll.kernel32.SetErrorMode(0x0001 | 0x0002)
            creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)

        preexec_fn = None
        if not IS_WIN and not wrap:
            import resource

            def _limits():
                mem_bytes = max(256, memory_limit_mb + 64) * 1024 * 1024
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
            argv = [wrap, "--run", sandbox_dir, launcher or "", str(meter_w) if meter_w is not None else "", exe_abs]

        try:
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
                meter_w = None

            out_chunks: list[bytes] = []
            err_chunks: list[bytes] = []
            ole_flag = [False]
            timed_out = False

            t_err = threading.Thread(
                target=_drain_stream,
                args=(proc.stderr, err_chunks, 262144, None, proc),
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
                try:
                    if proc.stdin:
                        if stdin_bytes:
                            proc.stdin.write(stdin_bytes)
                            proc.stdin.flush()
                        proc.stdin.close()
                except OSError:
                    pass

            t_in = threading.Thread(target=_writer, daemon=True)
            t0 = time.perf_counter()
            t_in.start()

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

            rc_final = proc.returncode if proc.returncode is not None else -1
            sbx_mem_limit_hit = False
            if IS_WIN and wrap:
                sbx_m = _read_win_sandbox_metrics(metrics_json_path)
                if sbx_m is not None:
                    cpu_ms = float(sbx_m.get("cpu_ms", 0.0))
                    peak_mb = float(sbx_m.get("peak_mem_mb", 0.0))
                    if sbx_m.get("timed_out") or sbx_m.get("cpu_exceeded"):
                        timed_out = True
                    if sbx_m.get("mem_limit_hit"):
                        sbx_mem_limit_hit = True
                    if "exit_code" in sbx_m:
                        rc_final = int(sbx_m["exit_code"])
                else:
                    cpu_ms, peak_mb = _query_win32_metrics(proc)
            elif IS_WIN:
                cpu_ms, peak_mb = _query_win32_metrics(proc)
            elif posix_cpu[0] is not None:
                cpu_ms, peak_mb = posix_cpu[0], posix_cpu[1]
            else:
                cpu_ms, peak_mb = wall_ms, 0.0
            if meter_r is not None:
                meter = _read_meter(meter_r)
                if meter is not None:
                    rc_final, cpu_ms, peak_mb = meter
        finally:
            if meter_w is not None:
                try:
                    os.close(meter_w)
                except OSError:
                    pass
            if meter_r is not None:
                try:
                    os.close(meter_r)
                except OSError:
                    pass

        err_bytes = b"".join(err_chunks)
        bad_alloc_mle = (rc_final != 0) and (
            sbx_mem_limit_hit
            or rc_final in (0xC0000017, 0xC000012D)
            or (b"bad_alloc" in err_bytes)
        )
        mle = sbx_mem_limit_hit or (peak_mb > float(memory_limit_mb)) or bad_alloc_mle
        if not mle and (cpu_ms > time_limit_ms + 150.0 or wall_ms > wall_budget * 1000.0):
            timed_out = True

        return RunResult(
            returncode=rc_final,
            timed_out=timed_out,
            ole=ole_flag[0],
            cpu_ms=cpu_ms,
            wall_ms=wall_ms,
            peak_mem_mb=peak_mb,
            stdout=b"".join(out_chunks),
            stderr=err_bytes,
            mle=mle,
        )


_JUDGE_POOL_LOCK = threading.Lock()
_JUDGE_POOLS: dict[str, queue.LifoQueue["_JudgeBatchWorker"]] = {}
_ALL_JUDGE_WORKERS: set["_JudgeBatchWorker"] = set()
_MAX_IDLE_WORKERS_PER_EXE = max(4, min(24, (os.cpu_count() or 4)))
_MAX_BATCH_EXPR_BYTES = 4112


class _JudgeWatchdog:
    """轻量单线程看门狗，为常驻 judge_cli --batch 子进程提供零线程开销的超时强杀保护。"""

    def __init__(self) -> None:
        self._cond = threading.Condition()
        self._deadlines: dict[_JudgeBatchWorker, float] = {}
        self._started = False

    def _ensure_started_locked(self) -> None:
        if not self._started:
            self._started = True
            t = threading.Thread(target=self._loop, name="sk-judge-watchdog", daemon=True)
            t.start()

    def arm(self, worker: "_JudgeBatchWorker", deadline: float) -> None:
        with self._cond:
            self._ensure_started_locked()
            self._deadlines[worker] = deadline
            self._cond.notify()

    def disarm(self, worker: "_JudgeBatchWorker") -> None:
        with self._cond:
            self._deadlines.pop(worker, None)

    def _loop(self) -> None:
        while True:
            expired: list[_JudgeBatchWorker] = []
            with self._cond:
                while not self._deadlines:
                    self._cond.wait()
                now = time.monotonic()
                nearest = min(self._deadlines.values())
                if nearest > now:
                    self._cond.wait(timeout=min(nearest - now, 0.05))
                    now = time.monotonic()
                for w, dl in list(self._deadlines.items()):
                    if now >= dl:
                        expired.append(w)
                        self._deadlines.pop(w, None)
            for w in expired:
                w.trigger_timeout()


_JUDGE_WATCHDOG = _JudgeWatchdog()


class _JudgeBatchWorker:
    """单个常驻 judge_cli --batch 子进程封装，线程安全超时中断与自动清理。"""

    def __init__(self, judge_exe: str) -> None:
        self.judge_exe = judge_exe
        self._lock = threading.Lock()
        self._closed = False
        self._timed_out = False
        self.last_rc: int | None = None
        self.last_stderr: str = ""
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if IS_WIN else 0
        self._proc = subprocess.Popen(
            [judge_exe, "--batch"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            creationflags=creationflags,
        )
        with _JUDGE_POOL_LOCK:
            _ALL_JUDGE_WORKERS.add(self)

    def is_alive(self) -> bool:
        return (not self._closed) and (self._proc.poll() is None)

    def trigger_timeout(self) -> None:
        with self._lock:
            self._timed_out = True
            proc = self._proc
        try:
            proc.kill()
        except Exception:
            pass

    def evaluate(
        self,
        case_input_text: str,
        expr_bytes: bytes,
        *,
        l_ref: float,
        trace_steps: int,
        timeout_sec: float,
    ) -> tuple[str, dict[str, Any] | None]:
        case_bytes = case_input_text.encode("utf-8")
        send_expr = expr_bytes[:_MAX_BATCH_EXPR_BYTES] if len(expr_bytes) > _MAX_BATCH_EXPR_BYTES else expr_bytes
        header = f"EVAL {len(case_bytes)} {len(send_expr)} {l_ref:.8f} {int(trace_steps)}\n".encode("ascii")
        payload = header + case_bytes + send_expr

        self._timed_out = False
        deadline = time.monotonic() + max(0.1, float(timeout_sec))
        _JUDGE_WATCHDOG.arm(self, deadline)
        line = b""
        try:
            assert self._proc.stdin is not None and self._proc.stdout is not None
            self._proc.stdin.write(payload)
            self._proc.stdin.flush()
            line = self._proc.stdout.readline()
        except (OSError, ValueError):
            line = b""
        finally:
            _JUDGE_WATCHDOG.disarm(self)

        if self._timed_out:
            self.close(force=True)
            return "timeout", None

        if not line:
            self.close(force=True)
            return "broken", None

        try:
            data = json.loads(line.decode("utf-8", errors="replace"))
        except json.JSONDecodeError:
            self.close(force=True)
            return "broken", None
        return "ok", data

    def close(self, *, force: bool = False) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
        proc = self._proc
        try:
            if not force and proc.poll() is None and proc.stdin is not None:
                try:
                    proc.stdin.write(b"QUIT\n")
                    proc.stdin.flush()
                except OSError:
                    pass
            if proc.stdin is not None:
                try:
                    proc.stdin.close()
                except OSError:
                    pass
            if force:
                if proc.poll() is None:
                    try:
                        proc.kill()
                    except Exception:
                        pass
                try:
                    proc.wait(timeout=2.0)
                except Exception:
                    pass
            else:
                try:
                    proc.wait(timeout=0.3)
                except subprocess.TimeoutExpired:
                    try:
                        proc.kill()
                    except Exception:
                        pass
                    try:
                        proc.wait(timeout=2.0)
                    except Exception:
                        pass
            self.last_rc = proc.returncode
            if proc.stderr is not None:
                try:
                    self.last_stderr = proc.stderr.read(4096).decode("utf-8", errors="replace").strip()
                except Exception:
                    pass
            for stream in (proc.stdout, proc.stderr):
                if stream is not None:
                    try:
                        stream.close()
                    except OSError:
                        pass
        finally:
            with _JUDGE_POOL_LOCK:
                _ALL_JUDGE_WORKERS.discard(self)


def _get_judge_pool(exe_abs: str) -> queue.LifoQueue[_JudgeBatchWorker]:
    with _JUDGE_POOL_LOCK:
        pool = _JUDGE_POOLS.get(exe_abs)
        if pool is None:
            pool = queue.LifoQueue()
            _JUDGE_POOLS[exe_abs] = pool
        return pool


def _acquire_batch_worker(exe_abs: str) -> tuple[_JudgeBatchWorker, bool]:
    pool = _get_judge_pool(exe_abs)
    while True:
        try:
            w = pool.get_nowait()
        except queue.Empty:
            return _JudgeBatchWorker(exe_abs), False
        if w.is_alive():
            return w, True
        w.close(force=True)


def _release_batch_worker(exe_abs: str, worker: _JudgeBatchWorker) -> None:
    if not worker.is_alive():
        worker.close(force=True)
        return
    pool = _get_judge_pool(exe_abs)
    if pool.qsize() >= _MAX_IDLE_WORKERS_PER_EXE:
        worker.close(force=False)
        return
    pool.put(worker)


def shutdown_judge_pools() -> None:
    """关闭全部常驻 judge_cli --batch 池中子进程（由 atexit 自动调用，不遗留僵尸进程）。"""
    with _JUDGE_POOL_LOCK:
        workers = list(_ALL_JUDGE_WORKERS)
        pools = list(_JUDGE_POOLS.values())
        _JUDGE_POOLS.clear()
    for pool in pools:
        while True:
            try:
                pool.get_nowait()
            except queue.Empty:
                break
    for w in workers:
        try:
            w.close(force=False)
        except Exception:
            pass


atexit.register(shutdown_judge_pools)


def _timeout_judge_result(timeout_sec: float) -> dict[str, Any]:
    return {
        "verdict": "NL",
        "message": "Judge wall-clock watchdog expired",
        "halted": False,
        "leaves": 0,
        "is_exact": True,
        "exact_steps": 0,
        "steps_display": "0",
        "log2_steps": 0.0,
        "norm_score": 0.0,
        "singularity": False,
        "dag_nodes": 0,
        "judge_ms": timeout_sec * 1000.0,
    }


def _empty_judge_result(rc: int | None, stderr_text: str) -> dict[str, Any]:
    return {
        "verdict": "IE",
        "message": f"Empty judge output (rc={rc}): {stderr_text}",
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


def _run_judge_cli_oneshot(
    judge_exe: str,
    case_input_text: str,
    expr_bytes: bytes,
    *,
    l_ref: float = 0.0,
    trace_steps: int = 0,
    timeout_sec: float = 30.0,
) -> dict[str, Any]:
    """单次子进程回退模式（兼容不支持 --batch 的旧裁判二进制）。"""
    tmp_root = str(writable_temp_root())
    with tempfile.TemporaryDirectory(prefix="afb_sk_eval_", dir=tmp_root, ignore_cleanup_errors=True) as td:
        case_path = os.path.join(td, "case.in")
        expr_path = os.path.join(td, "expr.out")
        with open(case_path, "w", encoding="utf-8", newline="\n") as f:
            f.write(case_input_text)
        with open(expr_path, "wb") as f:
            f.write(expr_bytes)

        cmd = [judge_exe, "--case", case_path, "--expr", expr_path]
        if l_ref > 0.0:
            cmd.extend(["--lref", f"{l_ref:.8f}"])
        if trace_steps > 0:
            cmd.extend(["--trace", str(trace_steps)])

        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if IS_WIN else 0
        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout_sec,
                creationflags=creationflags,
            )
        except subprocess.TimeoutExpired:
            return _timeout_judge_result(timeout_sec)

        raw_out = (proc.stdout or "").strip()
        if not raw_out:
            return _empty_judge_result(proc.returncode, (proc.stderr or "").strip())
        return json.loads(raw_out)


def run_judge_cli(
    judge_exe: str,
    case_input_text: str,
    expr_bytes: bytes,
    *,
    l_ref: float = 0.0,
    trace_steps: int = 0,
    timeout_sec: float = 30.0,
) -> dict[str, Any]:
    """调用原生 C++20 确定性裁判二进制校验单个表达式（优先复用常驻 --batch 进程池），返回解析后的 JSON 字典。"""
    exe_abs = os.path.abspath(judge_exe)
    for attempt in range(2):
        worker, reused = _acquire_batch_worker(exe_abs)
        status, res = worker.evaluate(
            case_input_text,
            expr_bytes,
            l_ref=l_ref,
            trace_steps=trace_steps,
            timeout_sec=timeout_sec,
        )
        if status == "ok" and res is not None:
            _release_batch_worker(exe_abs, worker)
            return res
        if status == "timeout":
            return _timeout_judge_result(timeout_sec)
        # Broken pipe / unexpected exit
        if reused and attempt == 0:
            continue
        if (not reused) and worker.last_rc == 2:
            return _run_judge_cli_oneshot(
                exe_abs,
                case_input_text,
                expr_bytes,
                l_ref=l_ref,
                trace_steps=trace_steps,
                timeout_sec=timeout_sec,
            )
        return _empty_judge_result(worker.last_rc, worker.last_stderr)
    return _empty_judge_result(-1, "Judge batch retry exhausted")
