# -*- coding: utf-8 -*-
"""《天翼幽灵兔》（ghost_rabbit）C++20 编译、交互器二进制缓存与双进程沙盒管道对弈引擎。

特性：
  - 兼容 AVL_SANDBOX_WRAP（Linux bubblewrap / Windows AppContainer + Job Object）
  - 通过原生 OS 匿名管道（os.pipe）零拷贝直连 C++20 交互器与隔离求解器
  - 精确统计选手纯 CPU 时间、峰值内存与交互器剔除自身耗时后的 solver_active_ms
  - 内置后台监控防死循环/防休眠 TLE、防 MLE、防僵尸进程
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
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
class InteractiveRunResult:
    verdict: str
    message: str
    strategy: str
    seed: int
    P: int
    Q: int
    P_red: int
    Q_red: int
    q: float
    captured: bool
    singularity: bool
    raw_score: float
    norm_score: float
    turns: int
    traps_placed: int
    bounces: int
    prime_flashes: int
    solver_cpu_ms: float
    solver_active_ms: float
    interactor_ms: float
    wall_ms: float
    peak_mem_mb: float
    solver_returncode: int
    interactor_returncode: int
    solver_stderr: str


def find_cxx() -> str:
    return os.environ.get("CXX", "g++")


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


_INTERACTOR_BUILD_LOCK = threading.Lock()
_INTERACTOR_CACHE: dict[str, str] = {}
_PROBLEM_ROOT = Path(__file__).resolve().parent.parent


def writable_temp_root(fallback_root: str | Path | None = None) -> Path:
    """优先使用系统临时目录；若处于禁写 :tmpdir 沙箱则自动回退到工作区内 logs/.tmp。"""
    try:
        sys_tmp = Path(tempfile.gettempdir())
        probe = sys_tmp / f".afb_gr_probe_{os.getpid()}_{time.time_ns()}"
        probe.write_bytes(b"")
        probe.unlink(missing_ok=True)
        return sys_tmp
    except OSError:
        base = Path(fallback_root).resolve() if fallback_root else _PROBLEM_ROOT
        alt = base / "logs" / ".tmp"
        alt.mkdir(parents=True, exist_ok=True)
        return alt


def ensure_interactor_binary(src_cpp: str | Path, extra_deps: list[str | Path] | None = None) -> str:
    """编译并缓存原生 C++20 交互器（不经过选手沙盒编译包装，按源码 SHA-256 缓存）。"""
    src_path = Path(src_cpp).resolve()
    deps = [src_path] + [Path(d).resolve() for d in (extra_deps or [])]
    h = hashlib.sha256()
    for p in deps:
        h.update(p.name.encode("utf-8"))
        h.update(b"\0")
        h.update(p.read_bytes())
        h.update(b"\0")
    digest = h.hexdigest()[:16]
    cache_key = f"{src_path.stem}_{digest}"
    with _INTERACTOR_BUILD_LOCK:
        cached = _INTERACTOR_CACHE.get(cache_key)
        if cached and os.path.isfile(cached):
            return cached
        cache_dir = writable_temp_root(src_path.parent.parent) / "afb_ghost_rabbit_bin"
        cache_dir.mkdir(parents=True, exist_ok=True)
        ext = ".exe" if IS_WIN else ""
        target = cache_dir / f"{cache_key}{ext}"
        if target.is_file():
            _INTERACTOR_CACHE[cache_key] = str(target)
            return str(target)
        tmp_out = cache_dir / f".tmp_{cache_key}_{os.getpid()}_{time.time_ns()}{ext}"
        res = compile_cpp(
            [str(src_path)],
            str(tmp_out),
            include_dirs=[str(src_path.parent), str(src_path.parent.parent / "engine")],
            stack_mb=128,
            use_sandbox_wrap=False,
        )
        if not res.ok:
            raise RuntimeError(f"Failed to compile interactor {src_path.name}:\n{res.log}")
        try:
            os.replace(res.exe_path, target)
        except OSError:
            if not target.is_file():
                shutil.copyfile(res.exe_path, target)
            try:
                os.unlink(res.exe_path)
            except OSError:
                pass
        _INTERACTOR_CACHE[cache_key] = str(target)
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


_LAUNCHER_BUILD_LOCK = threading.Lock()


def _launcher_path() -> str | None:
    if not sys.platform.startswith("linux"):
        return None
    digest = hashlib.sha256(_LAUNCHER_SOURCE.encode()).hexdigest()[:16]
    tmp_root = str(writable_temp_root())
    path = os.path.join(tmp_root, f"avl_gr_launcher_{os.getuid()}_{digest}")
    if os.path.isfile(path):
        return path
    with _LAUNCHER_BUILD_LOCK:
        if os.path.isfile(path):
            return path
        with tempfile.TemporaryDirectory(prefix="afb_gr_launcher_", dir=tmp_root) as tmp:
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


def _query_linux_proc_cpu_mem(pid: int) -> tuple[float, float]:
    cpu_ms = 0.0
    peak_mb = 0.0
    try:
        stat_txt = Path(f"/proc/{pid}/stat").read_text(encoding="ascii")
        rp = stat_txt.rfind(")")
        if rp > 0:
            fields = stat_txt[rp + 2:].split()
            utime = int(fields[11])
            stime = int(fields[12])
            clk = os.sysconf(os.sysconf_names["SC_CLK_TCK"])
            cpu_ms = (utime + stime) * 1000.0 / float(clk)
    except Exception:
        pass
    try:
        for line in Path(f"/proc/{pid}/status").read_text(encoding="ascii").splitlines():
            if line.startswith("VmHWM:"):
                peak_mb = int(line.split()[1]) / 1024.0
                break
    except Exception:
        pass
    return cpu_ms, peak_mb


def _drain_stderr(stream, sink: list[bytes], max_bytes: int = 262144) -> None:
    total = 0
    try:
        while True:
            chunk = stream.read(8192)
            if not chunk:
                break
            if total < max_bytes:
                sink.append(chunk[: max_bytes - total])
                total += len(chunk)
    except Exception:
        pass


def _safe_close(fd: int | None) -> None:
    if fd is not None:
        try:
            os.close(fd)
        except OSError:
            pass


def _kill_proc(proc: subprocess.Popen | None) -> None:
    if proc is None:
        return
    try:
        if proc.poll() is None:
            proc.kill()
    except Exception:
        pass


def run_interactive_match(
    interactor_exe: str,
    solver_exe: str,
    *,
    strategy: str,
    seed: int,
    max_turns: int = 5000,
    max_traps: int = 4000,
    solver_tl_ms: int = 5000,
    solver_ml_mb: int = 512,
    replay_path: str | None = None,
    max_replay_frames: int = 5000,
) -> InteractiveRunResult:
    """用匿名管道直连交互器与沙盒化求解器，执行单局实时交互追截并采集精确判决与指标。"""
    inter_abs = os.path.abspath(interactor_exe)
    solver_abs = os.path.abspath(solver_exe)
    tmp_root = str(writable_temp_root())

    with tempfile.TemporaryDirectory(prefix="afb_gr_judge_", dir=tmp_root, ignore_cleanup_errors=True) as judge_dir, \
         tempfile.TemporaryDirectory(prefix="afb_gr_sandbox_", dir=tmp_root, ignore_cleanup_errors=True) as sandbox_dir:
        # 独立存放裁判输出与沙盒指标文件，彻底隔离于 sandbox_dir，杜绝相对路径篡改
        result_json_path = os.path.join(judge_dir, "result.json")
        metrics_json_path = os.path.join(judge_dir, "metrics.json")

        wrap = os.environ.get("AVL_SANDBOX_WRAP")
        active_grace_ms = 2500.0 if wrap else 1200.0
        active_wall_cap_ms = float(solver_tl_ms) + active_grace_ms + 2000.0
        hard_wall_cap_ms = max(35_000.0, float(solver_tl_ms) * 4.0 + 25_000.0)

        if wrap:
            # 保留宿主关键环境变量供沙盒垫片启用 Windows AppContainer，内部子进程由垫片自动清洗
            sol_env = dict(os.environ)
            sol_env["AVL_SANDBOX_MEM_MB"] = str(max(64, solver_ml_mb))
            sol_env["AVL_SANDBOX_CPU_LIMIT_MS"] = str(solver_tl_ms)
            sol_env["AVL_SANDBOX_WALL_LIMIT_MS"] = str(int(max(5000.0, hard_wall_cap_ms - 1500.0)))
            sol_env["AVL_SANDBOX_METRICS_FILE"] = metrics_json_path
        else:
            sol_env = {
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

            def _limits() -> None:
                mem_bytes = max(256, solver_ml_mb + 64) * 1024 * 1024
                try:
                    resource.setrlimit(resource.RLIMIT_AS, (mem_bytes, mem_bytes))
                except Exception:
                    pass

            preexec_fn = _limits

        launcher = None if IS_WIN else _launcher_path()
        meter_r = meter_w = None
        sol_argv = [solver_abs]
        if launcher:
            meter_r, meter_w = os.pipe()
            sol_argv = [launcher, str(meter_w), solver_abs]
        if wrap:
            sol_argv = [
                wrap,
                "--run",
                sandbox_dir,
                launcher or "",
                str(meter_w) if meter_w is not None else "",
                solver_abs,
            ]

        # 交互器侧给足局内交互墙钟容差，纯 CPU 与内存硬限由外层/Job Object 精确裁决
        inter_tl_cap_ms = int(solver_tl_ms + active_grace_ms + 1500.0)
        inter_argv = [
            inter_abs,
            "--strategy", strategy,
            "--seed", str(seed),
            "--max-turns", str(max_turns),
            "--max-traps", str(max_traps),
            "--solver-tl-ms", str(inter_tl_cap_ms),
            "--result", result_json_path,
        ]
        if replay_path:
            inter_argv.extend(["--replay", os.path.abspath(replay_path), "--max-replay-frames", str(max_replay_frames)])

        s2i_r, s2i_w = os.pipe()
        i2s_r, i2s_w = os.pipe()

        sol_proc: subprocess.Popen | None = None
        inter_proc: subprocess.Popen | None = None
        sol_err_chunks: list[bytes] = []
        inter_err_chunks: list[bytes] = []

        killed_for_tle = False
        killed_for_mle = False
        sbx_timed_out = False
        sbx_mem_limit_hit = False
        sol_cpu_peak = 0.0
        sol_mem_peak = 0.0

        t0 = time.perf_counter()
        try:
            try:
                inter_proc = subprocess.Popen(
                    inter_argv,
                    stdin=s2i_r,
                    stdout=i2s_w,
                    stderr=subprocess.PIPE,
                    cwd=judge_dir,
                    creationflags=creationflags,
                )
                pass_fds = (meter_w,) if (launcher and meter_w is not None) else ()
                sol_proc = subprocess.Popen(
                    sol_argv,
                    stdin=i2s_r,
                    stdout=s2i_w,
                    stderr=subprocess.PIPE,
                    cwd=sandbox_dir,
                    env=sol_env,
                    creationflags=creationflags,
                    preexec_fn=preexec_fn,
                    pass_fds=pass_fds,
                )
            finally:
                # 父进程立刻关闭子进程已继承的管道端，确保任一端退出时对端立即收到 EOF / EPIPE
                _safe_close(s2i_r)
                _safe_close(s2i_w)
                _safe_close(i2s_r)
                _safe_close(i2s_w)
                _safe_close(meter_w)

            t_serr = threading.Thread(target=_drain_stderr, args=(sol_proc.stderr, sol_err_chunks), daemon=True)
            t_ierr = threading.Thread(target=_drain_stderr, args=(inter_proc.stderr, inter_err_chunks), daemon=True)
            t_serr.start()
            t_ierr.start()

            # 实时监控循环：Windows 沙盒优先读取 Job Object 真实进程树 CPU/内存指标
            inter_done_at: float | None = None

            while True:
                sol_rc = sol_proc.poll()
                int_rc = inter_proc.poll()
                now = time.perf_counter()
                elapsed_ms = (now - t0) * 1000.0

                if sol_rc is None:
                    if IS_WIN and wrap:
                        sbx_m = _read_win_sandbox_metrics(metrics_json_path)
                        if sbx_m is not None:
                            scpu = float(sbx_m.get("cpu_ms", 0.0))
                            smem = float(sbx_m.get("peak_mem_mb", 0.0))
                        else:
                            scpu, smem = 0.0, 0.0
                        icpu, _ = _query_win32_metrics(inter_proc)
                    elif IS_WIN:
                        scpu, smem = _query_win32_metrics(sol_proc)
                        icpu, _ = _query_win32_metrics(inter_proc)
                    else:
                        scpu, smem = _query_linux_proc_cpu_mem(sol_proc.pid)
                        icpu, _ = _query_linux_proc_cpu_mem(inter_proc.pid)
                    if scpu > sol_cpu_peak:
                        sol_cpu_peak = scpu
                    if smem > sol_mem_peak:
                        sol_mem_peak = smem

                    if smem > solver_ml_mb + 4.0:
                        killed_for_mle = True
                        _kill_proc(sol_proc)
                        break
                    if scpu > float(solver_tl_ms) + 120.0:
                        killed_for_tle = True
                        if not (IS_WIN and wrap):
                            _kill_proc(sol_proc)
                            break
                    approx_sol_active = max(0.0, elapsed_ms - icpu)
                    if (not wrap and approx_sol_active > active_wall_cap_ms) or elapsed_ms > hard_wall_cap_ms:
                        killed_for_tle = True
                        _kill_proc(sol_proc)
                        break

                if sol_rc is not None and int_rc is not None:
                    break

                if int_rc is not None and sol_rc is None:
                    if inter_done_at is None:
                        inter_done_at = now
                    elif now - inter_done_at > 0.60:
                        # 交互器已判决并退出，回收未及时退出的求解器
                        _kill_proc(sol_proc)
                        break

                if elapsed_ms > hard_wall_cap_ms:
                    killed_for_tle = True
                    _kill_proc(sol_proc)
                    _kill_proc(inter_proc)
                    break

                time.sleep(0.02)

            # 收尾等待两进程退出
            try:
                sol_proc.wait(timeout=3.0)
            except subprocess.TimeoutExpired:
                _kill_proc(sol_proc)
                sol_proc.wait(timeout=2.0)
            try:
                inter_proc.wait(timeout=5.0)
            except subprocess.TimeoutExpired:
                _kill_proc(inter_proc)
                inter_proc.wait(timeout=2.0)

            wall_ms = (time.perf_counter() - t0) * 1000.0
            t_serr.join(timeout=1.0)
            t_ierr.join(timeout=1.0)
            for st in (sol_proc.stderr, inter_proc.stderr):
                if st is not None:
                    try:
                        st.close()
                    except OSError:
                        pass

            sol_rc_final = sol_proc.returncode if sol_proc.returncode is not None else -1
            int_rc_final = inter_proc.returncode if inter_proc.returncode is not None else -1

            if IS_WIN and wrap:
                sbx_m = _read_win_sandbox_metrics(metrics_json_path)
                if sbx_m is not None:
                    sol_cpu_peak = max(sol_cpu_peak, float(sbx_m.get("cpu_ms", 0.0)))
                    sol_mem_peak = max(sol_mem_peak, float(sbx_m.get("peak_mem_mb", 0.0)))
                    if sbx_m.get("timed_out") or sbx_m.get("cpu_exceeded"):
                        sbx_timed_out = True
                    if sbx_m.get("mem_limit_hit"):
                        sbx_mem_limit_hit = True
                    if "exit_code" in sbx_m:
                        sol_rc_final = int(sbx_m["exit_code"])
                else:
                    scpu_end, smem_end = _query_win32_metrics(sol_proc)
                    sol_cpu_peak = max(sol_cpu_peak, scpu_end)
                    sol_mem_peak = max(sol_mem_peak, smem_end)
            elif IS_WIN:
                scpu_end, smem_end = _query_win32_metrics(sol_proc)
                sol_cpu_peak = max(sol_cpu_peak, scpu_end)
                sol_mem_peak = max(sol_mem_peak, smem_end)
            elif meter_r is not None:
                meter = _read_meter(meter_r)
                if meter is not None:
                    sol_rc_final, m_cpu, m_rss = meter
                    sol_cpu_peak = max(sol_cpu_peak, m_cpu)
                    sol_mem_peak = max(sol_mem_peak, m_rss)
        finally:
            _safe_close(meter_r)

        data: dict[str, Any] = {}
        if os.path.isfile(result_json_path):
            try:
                data = json.loads(Path(result_json_path).read_text(encoding="utf-8"))
            except Exception:
                data = {}

        inter_verdict = str(data.get("verdict", "RE"))
        inter_msg = str(data.get("message", ""))
        sol_stderr = b"".join(sol_err_chunks).decode("utf-8", errors="replace").strip()
        inter_stderr = b"".join(inter_err_chunks).decode("utf-8", errors="replace").strip()
        if not data and inter_stderr:
            inter_msg = f"Interactor failed (rc={int_rc_final}): {inter_stderr[-240:]}"

        sol_active_ms = float(data.get("solver_active_ms", wall_ms))
        inter_ms = float(data.get("interactor_ms", 0.0))

        # 精确综合判决优先级
        verdict = inter_verdict
        message = inter_msg
        captured = bool(data.get("captured", False))
        singularity = bool(data.get("singularity", False))
        raw_score = float(data.get("raw_score", 0.0))
        norm_score = float(data.get("norm_score", 0.0))

        bad_alloc_mle = (sol_rc_final != 0) and (
            sbx_mem_limit_hit
            or sol_rc_final in (0xC0000017, 0xC000012D)
            or ("bad_alloc" in sol_stderr)
        )

        if killed_for_mle or sol_mem_peak > float(solver_ml_mb) or bad_alloc_mle:
            verdict = "MLE"
            if sol_mem_peak > float(solver_ml_mb):
                message = f"Solver peak memory {sol_mem_peak:.1f} MiB exceeded limit {solver_ml_mb} MiB"
            else:
                message = f"Solver memory allocation rejected by {solver_ml_mb} MiB limit (peak {sol_mem_peak:.1f} MiB)"
            captured = False
            singularity = False
            raw_score = 0.0
            norm_score = 0.0
        elif (
            killed_for_tle
            or sbx_timed_out
            or sol_cpu_peak > float(solver_tl_ms)
            or sol_active_ms > float(solver_tl_ms) + active_grace_ms
            or inter_verdict == "TLE"
        ):
            verdict = "TLE"
            message = (
                f"Solver exceeded time limit {solver_tl_ms} ms "
                f"(cpu {sol_cpu_peak:.1f} ms, active {sol_active_ms:.1f} ms)"
            )
            captured = False
            singularity = False
            raw_score = 0.0
            norm_score = 0.0
        elif inter_verdict in ("OLE", "WA"):
            # 保留交互器明确判定的 OLE / WA
            captured = False
            singularity = False
            raw_score = 0.0
            norm_score = 0.0
        elif inter_verdict == "PE":
            if sol_rc_final != 0 and "Unexpected EOF" in inter_msg:
                verdict = "RE"
                tail = f": {sol_stderr[-200:]}" if sol_stderr else ""
                message = f"Solver crashed with exit code {sol_rc_final} before completing interaction{tail}"
            captured = False
            singularity = False
            raw_score = 0.0
            norm_score = 0.0
        elif inter_verdict == "AC":
            if sol_rc_final != 0:
                verdict = "RE"
                tail = f": {sol_stderr[-200:]}" if sol_stderr else ""
                message = f"Solver exited with non-zero code {sol_rc_final} after capture{tail}"
                captured = False
                singularity = False
                raw_score = 0.0
                norm_score = 0.0
        else:
            verdict = "RE"
            if not message:
                tail = f": {sol_stderr[-200:]}" if sol_stderr else ""
                message = f"Solver exited with code {sol_rc_final}{tail}"
            captured = False
            singularity = False
            raw_score = 0.0
            norm_score = 0.0

        return InteractiveRunResult(
            verdict=verdict,
            message=message,
            strategy=strategy,
            seed=seed,
            P=int(data.get("P", 0)),
            Q=int(data.get("Q", 0)),
            P_red=int(data.get("P_red", 0)),
            Q_red=int(data.get("Q_red", 0)),
            q=float(data.get("q", 0.0)),
            captured=captured,
            singularity=singularity,
            raw_score=raw_score,
            norm_score=norm_score,
            turns=int(data.get("turns", 0)),
            traps_placed=int(data.get("traps_placed", 0)),
            bounces=int(data.get("bounces", 0)),
            prime_flashes=int(data.get("prime_flashes", 0)),
            solver_cpu_ms=round(sol_cpu_peak, 2),
            solver_active_ms=round(sol_active_ms, 2),
            interactor_ms=round(inter_ms, 2),
            wall_ms=round(wall_ms, 2),
            peak_mem_mb=round(sol_mem_peak, 2),
            solver_returncode=sol_rc_final,
            interactor_returncode=int_rc_final,
            solver_stderr=sol_stderr,
        )
