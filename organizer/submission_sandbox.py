# -*- coding: utf-8 -*-
"""用 bubblewrap（Linux）或 Windows 原生双层沙箱（Windows Job Object + AppContainer）隔离正式评测中的提交。

提交只能看到系统运行库和自己的文件，看不到 organizer/、隐藏数据和参考对手，
也没有网络。参考对手和基线是可信代码，不进沙箱。

Python 题的引擎都用 sys.executable 启动提交，所以评测进程只需替换这个值，
不用改冻结的引擎。C++ 题由 common.py 读取 AVL_SANDBOX_WRAP 后走同一套隔离。
"""
from __future__ import annotations

import ctypes
from ctypes import wintypes
import hashlib
import json
import os
import pathlib
import runpy
import shutil
import subprocess
import sys
import tempfile
import time
from typing import Any

ROOT = os.path.dirname(os.path.abspath(__file__))
CPP_ENV = "AVL_SANDBOX_WRAP"
MEM_ENV = "AVL_SANDBOX_MEM_MB"
CPU_LIMIT_ENV = "AVL_SANDBOX_CPU_LIMIT_MS"
WALL_LIMIT_ENV = "AVL_SANDBOX_WALL_LIMIT_MS"
METRICS_ENV = "AVL_SANDBOX_METRICS_FILE"
MAX_PROCS_ENV = "AVL_SANDBOX_MAX_PROCS"
REQUIRE_APPCONTAINER_ENV = "AVL_SANDBOX_REQUIRE_APPCONTAINER"
DEFAULT_MEMORY_MB = 1024
MEMORY_MB = {"mem_recall": 2048, "ghost_rabbit": 512, "sk_singularity": 512, "minimum_effort": 128}
MAX_OPEN_FILES = 256
EXTRA_TASKS = 256
TRUSTED_DIRS = ("engine", os.path.join("agents", "baselines"), os.path.join("agents", "reference"))
OPTIONS_WITH_VALUE = ("-X", "-W")

REAL_PYTHON = os.path.realpath(getattr(sys, "_base_executable", sys.executable))

# ---------------------------------------------------------------------------
# Linux bubblewrap 实现
# ---------------------------------------------------------------------------

def _common_linux() -> list[str]:
    binds = ["/usr", "/bin", "/sbin", "/lib", "/lib32", "/lib64", "/etc/ld.so.cache", "/etc/alternatives"]
    prefix = os.path.realpath(sys.base_prefix)
    if prefix != "/usr" and not prefix.startswith("/usr/"):
        binds.append(prefix)
    args = ["bwrap", "--unshare-user", "--unshare-all", "--uid", "65534", "--gid", "65534",
            "--die-with-parent", "--new-session", "--cap-drop", "ALL"]
    for path in binds:
        args += ["--ro-bind-try", path, path]
    args += ["--proc", "/proc", "--dev", "/dev", "--clearenv",
             "--setenv", "PATH", "/usr/bin:/bin", "--setenv", "HOME", "/tmp", "--setenv", "LANG", "C.UTF-8"]
    return args


def _limit_process_linux(memory_mb: int) -> None:
    import resource
    memory = memory_mb * 1024 * 1024
    with open("/proc/loadavg", encoding="ascii") as f:
        tasks = int(f.read().split()[3].split("/")[1])
    resource.setrlimit(resource.RLIMIT_AS, (memory, memory))
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    resource.setrlimit(resource.RLIMIT_NOFILE, (MAX_OPEN_FILES, MAX_OPEN_FILES))
    resource.setrlimit(resource.RLIMIT_NPROC, (tasks + EXTRA_TASKS, tasks + EXTRA_TASKS))


# ---------------------------------------------------------------------------
# Windows 原生 Win32 Job Object & AppContainer 定义
# ---------------------------------------------------------------------------

JOB_OBJECT_LIMIT_JOB_TIME = 0x00000004
JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
JOB_OBJECT_LIMIT_JOB_MEMORY = 0x00000200
JOB_OBJECT_LIMIT_PROCESS_MEMORY = 0x00000100
JOB_OBJECT_LIMIT_ACTIVE_PROCESS = 0x00000008

CREATE_SUSPENDED = 0x00000004
EXTENDED_STARTUPINFO_PRESENT = 0x00080000
PROC_THREAD_ATTRIBUTE_SECURITY_CAPABILITIES = 0x00020009
STARTF_USESTDHANDLES = 0x00000100


class _JOBOBJECT_BASIC_ACCOUNTING_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("TotalUserTime", ctypes.c_int64),
        ("TotalKernelTime", ctypes.c_int64),
        ("ThisPeriodTotalUserTime", ctypes.c_int64),
        ("ThisPeriodTotalKernelTime", ctypes.c_int64),
        ("TotalPageFaultCount", wintypes.DWORD),
        ("TotalProcesses", wintypes.DWORD),
        ("ActiveProcesses", wintypes.DWORD),
        ("TotalTerminatedProcesses", wintypes.DWORD),
    ]


class _IO_COUNTERS(ctypes.Structure):
    _fields_ = [(n, ctypes.c_ulonglong) for n in (
        'ReadOperationCount', 'WriteOperationCount', 'OtherOperationCount',
        'ReadTransferCount', 'WriteTransferCount', 'OtherTransferCount')]


class _JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
    _fields_ = [
        ('PerProcessUserTimeLimit', ctypes.c_int64),
        ('PerJobUserTimeLimit', ctypes.c_int64),
        ('LimitFlags', wintypes.DWORD),
        ('MinimumWorkingSetSize', ctypes.c_size_t),
        ('MaximumWorkingSetSize', ctypes.c_size_t),
        ('ActiveProcessLimit', wintypes.DWORD),
        ('Affinity', ctypes.c_size_t),
        ('PriorityClass', wintypes.DWORD),
        ('SchedulingClass', wintypes.DWORD),
    ]


class _JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
    _fields_ = [
        ('BasicLimitInformation', _JOBOBJECT_BASIC_LIMIT_INFORMATION),
        ('IoInfo', _IO_COUNTERS),
        ('ProcessMemoryLimit', ctypes.c_size_t),
        ('JobMemoryLimit', ctypes.c_size_t),
        ('PeakProcessMemoryUsed', ctypes.c_size_t),
        ('PeakJobMemoryUsed', ctypes.c_size_t),
    ]


class _SECURITY_CAPABILITIES(ctypes.Structure):
    _fields_ = [
        ("AppContainerSid", ctypes.c_void_p),
        ("Capabilities", ctypes.c_void_p),
        ("CapabilityCount", wintypes.DWORD),
        ("Reserved", wintypes.DWORD),
    ]


class _STARTUPINFOW(ctypes.Structure):
    _fields_ = [
        ("cb", wintypes.DWORD),
        ("lpReserved", wintypes.LPWSTR),
        ("lpDesktop", wintypes.LPWSTR),
        ("lpTitle", wintypes.LPWSTR),
        ("dwX", wintypes.DWORD),
        ("dwY", wintypes.DWORD),
        ("dwXSize", wintypes.DWORD),
        ("dwYSize", wintypes.DWORD),
        ("dwXCountChars", wintypes.DWORD),
        ("dwYCountChars", wintypes.DWORD),
        ("dwFillAttribute", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("wShowWindow", wintypes.WORD),
        ("cbReserved2", wintypes.WORD),
        ("lpReserved2", ctypes.c_void_p),
        ("hStdInput", wintypes.HANDLE),
        ("hStdOutput", wintypes.HANDLE),
        ("hStdError", wintypes.HANDLE),
    ]


class _STARTUPINFOEXW(ctypes.Structure):
    _fields_ = [
        ("StartupInfo", _STARTUPINFOW),
        ("lpAttributeList", ctypes.c_void_p),
    ]


class _PROCESS_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("hProcess", wintypes.HANDLE),
        ("hThread", wintypes.HANDLE),
        ("dwProcessId", wintypes.DWORD),
        ("dwThreadId", wintypes.DWORD),
    ]


def _create_win_job(memory_mb: int, cpu_limit_ms: int | None = None, active_process_limit: int = 64) -> int:
    """创建并配置匿名 Windows Job Object（支持内存硬限、进程数上限与可选整组用户态 CPU 硬限）。"""
    kernel32 = ctypes.windll.kernel32
    hJob = kernel32.CreateJobObjectW(None, None)
    if not hJob:
        raise OSError(ctypes.GetLastError(), "CreateJobObjectW failed")
    info = _JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
    limit_flags = (
        JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE |
        JOB_OBJECT_LIMIT_JOB_MEMORY |
        JOB_OBJECT_LIMIT_PROCESS_MEMORY |
        JOB_OBJECT_LIMIT_ACTIVE_PROCESS
    )
    if cpu_limit_ms is not None and cpu_limit_ms > 0:
        limit_flags |= JOB_OBJECT_LIMIT_JOB_TIME
        # 100ns 为单位；加 500ms 余量供外层优先完成精确采样再强杀
        info.BasicLimitInformation.PerJobUserTimeLimit = int(cpu_limit_ms + 500) * 10000
    info.BasicLimitInformation.LimitFlags = limit_flags
    info.BasicLimitInformation.ActiveProcessLimit = max(1, int(active_process_limit))
    limit_bytes = memory_mb * 1024 * 1024
    info.ProcessMemoryLimit = limit_bytes
    info.JobMemoryLimit = limit_bytes

    JobObjectExtendedLimitInformation = 9
    ok = kernel32.SetInformationJobObject(
        hJob, JobObjectExtendedLimitInformation,
        ctypes.byref(info), ctypes.sizeof(info)
    )
    if not ok:
        err = ctypes.GetLastError()
        kernel32.CloseHandle(hJob)
        raise OSError(err, "SetInformationJobObject failed")
    return int(hJob)


def _query_win_job_metrics(hJob: int) -> tuple[float, float]:
    """查询 Windows Job Object 整棵进程树的累计 CPU (ms) 与峰值提交内存 (MiB)。"""
    kernel32 = ctypes.windll.kernel32
    cpu_ms = 0.0
    peak_mb = 0.0
    try:
        acct = _JOBOBJECT_BASIC_ACCOUNTING_INFORMATION()
        if kernel32.QueryInformationJobObject(
            wintypes.HANDLE(hJob), 1, ctypes.byref(acct), ctypes.sizeof(acct), None
        ):
            cpu_ms = max(0.0, (acct.TotalUserTime + acct.TotalKernelTime) / 10000.0)
    except Exception:
        pass
    try:
        ext = _JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
        if kernel32.QueryInformationJobObject(
            wintypes.HANDLE(hJob), 9, ctypes.byref(ext), ctypes.sizeof(ext), None
        ):
            peak_bytes = max(int(ext.PeakJobMemoryUsed), int(ext.PeakProcessMemoryUsed))
            peak_mb = max(0.0, peak_bytes / (1024.0 * 1024.0))
    except Exception:
        pass
    return cpu_ms, peak_mb


def _real_win_localappdata() -> str:
    """读取宿主真实 LOCALAPPDATA 目录，不受外层裁剪环境变量影响。"""
    if sys.platform != "win32":
        return ""
    try:
        buf = ctypes.create_unicode_buffer(260)
        # CSIDL_LOCAL_APPDATA = 0x001C
        hr = ctypes.windll.shell32.SHGetFolderPathW(None, 0x001C, None, 0, buf)
        if hr == 0 and buf.value:
            return buf.value
    except Exception:
        pass
    return os.environ.get("LOCALAPPDATA", "")


def _probe_job_object() -> bool:
    if sys.platform != "win32":
        return False
    try:
        hJob = _create_win_job(DEFAULT_MEMORY_MB)
        ctypes.windll.kernel32.CloseHandle(hJob)
        return True
    except Exception:
        return False


def _probe_appcontainer() -> bool:
    if sys.platform != "win32":
        return False
    try:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        userenv = ctypes.WinDLL("userenv", use_last_error=True)
        if not hasattr(userenv, "CreateAppContainerProfile") or not hasattr(userenv, "DeleteAppContainerProfile"):
            return False
        userenv.CreateAppContainerProfile.argtypes = [
            wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.LPCWSTR,
            ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(ctypes.c_void_p)
        ]
        userenv.CreateAppContainerProfile.restype = ctypes.HRESULT
        userenv.DeleteAppContainerProfile.argtypes = [wintypes.LPCWSTR]
        userenv.DeleteAppContainerProfile.restype = ctypes.HRESULT

        profile_name = f"avl_probe_{os.getpid()}"
        sid = ctypes.c_void_p()
        h_mtx = _win_appcontainer_lock(kernel32)
        try:
            userenv.DeleteAppContainerProfile(profile_name)
            hr = userenv.CreateAppContainerProfile(profile_name, profile_name, profile_name, None, 0, ctypes.byref(sid))
        finally:
            _win_appcontainer_unlock(kernel32, h_mtx)
        if hr != 0:
            return False

        # 检查是否能创建 AttributeList 并启动测试进程
        size = ctypes.c_size_t(0)
        kernel32.InitializeProcThreadAttributeList(None, 1, 0, ctypes.byref(size))
        attr_buf = (ctypes.c_char * size.value)()
        p_attr = ctypes.cast(attr_buf, ctypes.c_void_p)
        if not kernel32.InitializeProcThreadAttributeList(p_attr, 1, 0, ctypes.byref(size)):
            h_mtx = _win_appcontainer_lock(kernel32)
            try:
                userenv.DeleteAppContainerProfile(profile_name)
            finally:
                _win_appcontainer_unlock(kernel32, h_mtx)
            return False

        sec_caps = _SECURITY_CAPABILITIES()
        sec_caps.AppContainerSid = sid
        sec_caps.Capabilities = None
        sec_caps.CapabilityCount = 0
        sec_caps.Reserved = 0
        kernel32.UpdateProcThreadAttribute(
            p_attr, 0, PROC_THREAD_ATTRIBUTE_SECURITY_CAPABILITIES,
            ctypes.byref(sec_caps), ctypes.sizeof(sec_caps), None, None
        )

        si = _STARTUPINFOEXW()
        si.StartupInfo.cb = ctypes.sizeof(_STARTUPINFOEXW)
        si.lpAttributeList = p_attr
        pi = _PROCESS_INFORMATION()

        # 确保 Python 与 C++ 运行库目录拥有 AppContainer 读权限
        _ensure_base_prefix_acl()

        # 显式传入包含宿主真实 LOCALAPPDATA 的 Unicode 环境块，防止裁剪 env 触发 ERROR_ENVVAR_NOT_FOUND(203)
        probe_env = {
            "SYSTEMROOT": os.environ.get("SYSTEMROOT", r"C:\Windows"),
            "WINDIR": os.environ.get("WINDIR", os.environ.get("SYSTEMROOT", r"C:\Windows")),
            "SYSTEMDRIVE": os.environ.get("SYSTEMDRIVE", "C:"),
            "PATH": os.environ.get("PATH", r"C:\Windows\system32;C:\Windows"),
        }
        local_app = _real_win_localappdata()
        if local_app:
            probe_env["LOCALAPPDATA"] = local_app
        env_str = "".join(f"{k}={v}\0" for k, v in sorted(probe_env.items(), key=lambda x: x[0].upper())) + "\0"
        env_buf = ctypes.create_unicode_buffer(env_str)

        cmd = f'"{REAL_PYTHON}" -I -c "pass"'
        created = kernel32.CreateProcessW(
            None, cmd, None, None, False,
            EXTENDED_STARTUPINFO_PRESENT | 0x00000400, ctypes.byref(env_buf), None,
            ctypes.byref(si.StartupInfo), ctypes.byref(pi)
        )
        ok = False
        if created:
            kernel32.WaitForSingleObject(pi.hProcess, 3000)
            code = wintypes.DWORD()
            kernel32.GetExitCodeProcess(pi.hProcess, ctypes.byref(code))
            kernel32.CloseHandle(pi.hProcess)
            kernel32.CloseHandle(pi.hThread)
            ok = (code.value == 0)
        kernel32.DeleteProcThreadAttributeList(p_attr)
        h_mtx = _win_appcontainer_lock(kernel32)
        try:
            userenv.DeleteAppContainerProfile(profile_name)
        finally:
            _win_appcontainer_unlock(kernel32, h_mtx)
        return ok
    except Exception:
        return False


_CACHED_WINDOWS_SANDBOX: str | None = None
_BASE_PREFIX_ACL_CHECKED: bool = False


def _user_id() -> str:
    if hasattr(os, "getuid"):
        return str(os.getuid())
    return os.environ.get("USERNAME", "user")


def _get_probe_cache_path() -> str:
    prefix_hash = hashlib.sha256(sys.base_prefix.encode()).hexdigest()[:12]
    cache_dir = tempfile.gettempdir()
    if sys.platform == "win32":
        local_app = _real_win_localappdata()
        if local_app:
            cand = os.path.join(local_app, "Temp")
            if os.path.isdir(cand):
                cache_dir = cand
    return os.path.join(cache_dir, f"avl_sbx_probe_{_user_id()}_{prefix_hash}.json")


def _read_probe_cache() -> str | None:
    try:
        p = _get_probe_cache_path()
        if os.path.isfile(p):
            data = json.loads(pathlib.Path(p).read_text(encoding="utf-8"))
            if data.get("base_prefix") == sys.base_prefix and data.get("sandbox_type"):
                return str(data["sandbox_type"])
    except Exception:
        pass
    return None


def _write_probe_cache(sbx_type: str) -> None:
    try:
        p = _get_probe_cache_path()
        pathlib.Path(p).write_text(
            json.dumps({"sandbox_type": sbx_type, "base_prefix": sys.base_prefix, "real_python": REAL_PYTHON}),
            encoding="utf-8"
        )
    except Exception:
        pass


def _ensure_dir_appcontainer_rx(target_dir: str) -> None:
    if not target_dir or not os.path.isdir(target_dir):
        return
    try:
        res = subprocess.run(["icacls", target_dir], capture_output=True, text=True, errors="replace", timeout=2)
        if res.returncode == 0 and ("S-1-15-2-1" in res.stdout or "ALL APPLICATION PACKAGES" in res.stdout):
            return
        subprocess.run(["icacls", target_dir, "/grant", "*S-1-15-2-1:(OI)(CI)(RX)"],
                       capture_output=True, timeout=5)
    except Exception:
        pass


def _ensure_base_prefix_acl() -> None:
    global _BASE_PREFIX_ACL_CHECKED
    if _BASE_PREFIX_ACL_CHECKED:
        return
    _ensure_dir_appcontainer_rx(sys.base_prefix)
    gpp = shutil.which(os.environ.get("CXX", "g++"))
    if gpp:
        gpp_bin = os.path.dirname(os.path.realpath(gpp))
        gpp_root = os.path.dirname(gpp_bin)
        # 若 g++ 位于独立工具链目录（如 mingw64/bin），一并校验其根/bin 权限以支持 AppContainer 下运行与编译
        _ensure_dir_appcontainer_rx(gpp_root if os.path.basename(gpp_bin).lower() == "bin" else gpp_bin)
    _BASE_PREFIX_ACL_CHECKED = True


def probe_windows_sandbox(force: bool = False) -> str:
    """自动探测 Windows 沙箱支持层级：优先 AppContainer + Job，受限时回退至 Job Object。
    支持内存缓存、环境变量及持久化 JSON 缓存加速。"""
    global _CACHED_WINDOWS_SANDBOX
    if not force:
        if _CACHED_WINDOWS_SANDBOX:
            return _CACHED_WINDOWS_SANDBOX
        env_val = os.environ.get("AVL_SANDBOX_TYPE")
        if env_val in ("windows_appcontainer_job", "windows_job_isolated"):
            _CACHED_WINDOWS_SANDBOX = env_val
            return env_val
        cached = _read_probe_cache()
        if cached in ("windows_appcontainer_job", "windows_job_isolated"):
            _CACHED_WINDOWS_SANDBOX = cached
            os.environ["AVL_SANDBOX_TYPE"] = cached
            return cached

    if not _probe_job_object():
        res = ""
    elif _probe_appcontainer():
        res = "windows_appcontainer_job"
    else:
        res = "windows_job_isolated"

    _CACHED_WINDOWS_SANDBOX = res
    if res:
        os.environ["AVL_SANDBOX_TYPE"] = res
        _write_probe_cache(res)
    return res


# ---------------------------------------------------------------------------
# 状态与接口
# ---------------------------------------------------------------------------

SANDBOX_TYPE = "none"


def available() -> bool:
    if sys.platform.startswith("linux"):
        if shutil.which("bwrap") is None:
            return False
        probe = _common_linux() + ["--remount-ro", "/", os.path.realpath(sys.executable), "-I", "-c", "pass"]
        try:
            return subprocess.run(probe, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                  stderr=subprocess.DEVNULL, timeout=30).returncode == 0
        except (OSError, subprocess.SubprocessError):
            return False
    elif sys.platform == "win32":
        return _probe_job_object()
    return False


def official_ready() -> str | bool:
    """返回提交是否隔离。Linux 上无法隔离时中止，Windows 探测双层沙箱。"""
    global SANDBOX_TYPE
    if sys.platform == "win32":
        sbx = probe_windows_sandbox(force=True)
        if sbx != "windows_appcontainer_job":
            raise SystemExit("Windows 正式评分需要 AppContainer 和 Job Object，隔离探针未通过。")
        SANDBOX_TYPE = sbx
        os.environ[REQUIRE_APPCONTAINER_ENV] = "1"
        return sbx
    if not available():
        raise SystemExit("Linux 上的正式评分需要可用的 bubblewrap 沙箱。")
    SANDBOX_TYPE = "bwrap"
    return "bwrap"


# ---------------------------------------------------------------------------
# 跨平台垫片生成
# ---------------------------------------------------------------------------

def _compile_win_shim(target_exe: str, script_path: str, mode: str, out_exe: str) -> None:
    """使用系统 g++ 编译极简 C 转发可执行文件，用于在 Windows 上透明转回本模块。"""
    c_src = f"""#define _UNICODE
#define UNICODE
#include <windows.h>
#include <wchar.h>

static void append_quoted(wchar_t *buf, size_t max_len, const wchar_t *arg) {{
    int need_quote = (wcschr(arg, L' ') != NULL || wcschr(arg, L'\\t') != NULL || wcschr(arg, L'\\"') != NULL || arg[0] == L'\\0');
    if (!need_quote) {{
        wcsncat(buf, arg, max_len - wcslen(buf) - 1);
        return;
    }}
    wcsncat(buf, L"\\"", max_len - wcslen(buf) - 1);
    for (const wchar_t *ptr = arg; *ptr; ptr++) {{
        int backslashes = 0;
        while (*ptr == L'\\\\') {{
            backslashes++;
            ptr++;
        }}
        if (*ptr == L'\\0') {{
            for (int i = 0; i < backslashes * 2; i++) wcsncat(buf, L"\\\\", max_len - wcslen(buf) - 1);
            break;
        }} else if (*ptr == L'\\"') {{
            for (int i = 0; i < backslashes * 2 + 1; i++) wcsncat(buf, L"\\\\", max_len - wcslen(buf) - 1);
            wcsncat(buf, L"\\"", max_len - wcslen(buf) - 1);
        }} else {{
            for (int i = 0; i < backslashes; i++) wcsncat(buf, L"\\\\", max_len - wcslen(buf) - 1);
            wchar_t s[2] = {{*ptr, 0}};
            wcsncat(buf, s, max_len - wcslen(buf) - 1);
        }}
    }}
    wcsncat(buf, L"\\"", max_len - wcslen(buf) - 1);
}}

int wmain(int argc, wchar_t *argv[]) {{
    wchar_t cmd[32768] = {{0}};
    append_quoted(cmd, 32768, LR"({target_exe})");
    wcsncat(cmd, L" -I -S ", 32768 - wcslen(cmd) - 1);
    append_quoted(cmd, 32768, LR"({script_path})");
    wcsncat(cmd, L" ", 32768 - wcslen(cmd) - 1);
    append_quoted(cmd, 32768, LR"({mode})");
    for (int i = 1; i < argc; i++) {{
        wcsncat(cmd, L" ", 32768 - wcslen(cmd) - 1);
        append_quoted(cmd, 32768, argv[i]);
    }}
    STARTUPINFOW si;
    PROCESS_INFORMATION pi;
    ZeroMemory(&si, sizeof(si));
    si.cb = sizeof(si);
    si.dwFlags = STARTF_USESTDHANDLES;
    si.hStdInput = GetStdHandle(STD_INPUT_HANDLE);
    si.hStdOutput = GetStdHandle(STD_OUTPUT_HANDLE);
    si.hStdError = GetStdHandle(STD_ERROR_HANDLE);
    ZeroMemory(&pi, sizeof(pi));
    HANDLE hJob = CreateJobObjectW(NULL, NULL);
    if (hJob) {{
        JOBOBJECT_EXTENDED_LIMIT_INFORMATION jeli;
        ZeroMemory(&jeli, sizeof(jeli));
        jeli.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;
        SetInformationJobObject(hJob, JobObjectExtendedLimitInformation, &jeli, sizeof(jeli));
    }}
    DWORD cflags = hJob ? CREATE_SUSPENDED : 0;
    if (!CreateProcessW(NULL, cmd, NULL, NULL, TRUE, cflags, NULL, NULL, &si, &pi)) {{
        if (hJob) CloseHandle(hJob);
        return 1;
    }}
    if (hJob) {{
        AssignProcessToJobObject(hJob, pi.hProcess);
        ResumeThread(pi.hThread);
    }}
    WaitForSingleObject(pi.hProcess, INFINITE);
    DWORD code = 0;
    GetExitCodeProcess(pi.hProcess, &code);
    CloseHandle(pi.hProcess);
    CloseHandle(pi.hThread);
    if (hJob) CloseHandle(hJob);
    return (int)code;
}}
"""
    pid_tag = f"{os.getpid()}_{hashlib.sha256(os.urandom(8)).hexdigest()[:8]}"
    tmp_c = f"{out_exe}.{pid_tag}.c"
    tmp_exe = f"{out_exe}.{pid_tag}.exe"
    with open(tmp_c, "w", encoding="utf-8") as f:
        f.write(c_src)
    try:
        subprocess.run(["g++", "-O2", "-municode", "-s", tmp_c, "-o", tmp_exe],
                       check=True, capture_output=True)
        try:
            os.replace(tmp_exe, out_exe)
        except OSError:
            # 另一并发进程已完成原子替换且正在执行 out_exe，直接复用已存在的完好二进制
            if not (os.path.isfile(out_exe) and os.path.getsize(out_exe) > 0):
                raise
    finally:
        for p in (tmp_c, tmp_exe):
            try:
                if os.path.exists(p):
                    os.unlink(p)
            except OSError:
                pass


def _wrapper(mode: str) -> str:
    """生成转回本模块的可执行脚本或原生可执行垫片。放在临时目录。"""
    script_file = os.path.abspath(__file__)
    target_py = REAL_PYTHON
    if sys.platform == "win32":
        sig = f"v3_atomic_job|{target_py}|{script_file}|{mode}"
        digest = hashlib.sha256(sig.encode()).hexdigest()[:16]
        path = os.path.join(tempfile.gettempdir(), f"avl_sandbox_{_user_id()}_{digest}{mode.replace('-', '_')}.exe")
        if not os.path.isfile(path) or os.path.getsize(path) == 0:
            _compile_win_shim(target_py, script_file, mode, path)
        return path

    text = f'#!/bin/sh\nexec "{target_py}" -I -S "{script_file}" {mode} "$@"\n'
    digest = hashlib.sha256(text.encode()).hexdigest()[:16]
    path = os.path.join(tempfile.gettempdir(), f"avl_sandbox_{_user_id()}_{digest}{mode.replace('-', '_')}")
    if not os.path.isfile(path):
        with tempfile.NamedTemporaryFile("w", dir=tempfile.gettempdir(), delete=False) as f:
            f.write(text)
        os.chmod(f.name, 0o755)
        os.replace(f.name, path)
    return path


def install(problem: str) -> None:
    """让之后用 sys.executable 启动的提交都进入沙箱。"""
    os.environ[MEM_ENV] = str(MEMORY_MB.get(problem, DEFAULT_MEMORY_MB))
    if sys.platform == "win32":
        sbx = probe_windows_sandbox()
        if sbx:
            os.environ["AVL_SANDBOX_TYPE"] = sbx
    sys.executable = _wrapper("--python")


def enable_cpp() -> None:
    if sys.platform == "win32":
        sbx = probe_windows_sandbox()
        if sbx:
            os.environ["AVL_SANDBOX_TYPE"] = sbx
    os.environ[CPP_ENV] = _wrapper("--cpp")


def _trusted(path: str) -> bool:
    try:
        relative = os.path.relpath(os.path.realpath(path), os.path.realpath(ROOT))
        parts = relative.split(os.sep, 1)
        return len(parts) == 2 and not relative.startswith("..") and parts[1].startswith(tuple(d + os.sep for d in TRUSTED_DIRS))
    except (ValueError, OSError):
        return False


def _script_index(argv: list[str]) -> int | None:
    index = 0
    while index < len(argv):
        arg = argv[index]
        if arg in ("-c", "-m", "-"):
            return None
        if arg in OPTIONS_WITH_VALUE:
            index += 2
        elif arg.startswith("-"):
            index += 1
        else:
            return index
    return None


# ---------------------------------------------------------------------------
# Windows 下执行候选提交
# ---------------------------------------------------------------------------

def _clean_win_env(sandbox_dir: str) -> dict[str, str]:
    return {
        "SYSTEMROOT": os.environ.get("SYSTEMROOT", "C:\\Windows"),
        "WINDIR": os.environ.get("WINDIR", os.environ.get("SYSTEMROOT", "C:\\Windows")),
        "SYSTEMDRIVE": os.environ.get("SYSTEMDRIVE", "C:"),
        "COMSPEC": os.environ.get("COMSPEC", "C:\\Windows\\system32\\cmd.exe"),
        "USERPROFILE": sandbox_dir,
        "LOCALAPPDATA": sandbox_dir,
        "APPDATA": sandbox_dir,
        "TEMP": sandbox_dir,
        "TMP": sandbox_dir,
        "PATH": os.environ.get("PATH", "C:\\Windows\\system32;C:\\Windows"),
        "PATHEXT": os.environ.get("PATHEXT", ".COM;.EXE;.BAT;.CMD"),
        "PYTHONHASHSEED": "0",
        "PYTHONUTF8": "1",
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONIOENCODING": "utf-8",
    }


def _grant_win_dir_sid(path: str, psid: ctypes.c_void_p) -> bool:
    """进程内 Win32 Security API（~0.7 ms）为沙箱目录授予指定 AppContainer SID 完整控制权（含对象/容器继承），免去启动 icacls.exe 的 ~150 ms 开销。"""
    SE_FILE_OBJECT = 1
    DACL_SECURITY_INFORMATION = 0x00000004
    GRANT_ACCESS = 1
    TRUSTEE_IS_SID = 0
    TRUSTEE_IS_UNKNOWN = 0
    OBJECT_INHERIT_ACE = 0x1
    CONTAINER_INHERIT_ACE = 0x2
    FILE_ALL_ACCESS = 0x001F01FF

    class _TRUSTEE_W(ctypes.Structure):
        _fields_ = [
            ("pMultipleTrustee", ctypes.c_void_p),
            ("MultipleTrusteeOperation", wintypes.DWORD),
            ("TrusteeForm", wintypes.DWORD),
            ("TrusteeType", wintypes.DWORD),
            ("ptstrName", ctypes.c_void_p),
        ]

    class _EXPLICIT_ACCESS_W(ctypes.Structure):
        _fields_ = [
            ("grfAccessPermissions", wintypes.DWORD),
            ("grfAccessMode", wintypes.DWORD),
            ("grfInheritance", wintypes.DWORD),
            ("Trustee", _TRUSTEE_W),
        ]

    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    advapi32.GetNamedSecurityInfoW.argtypes = [
        wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
        ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(ctypes.c_void_p),
    ]
    advapi32.GetNamedSecurityInfoW.restype = wintypes.DWORD
    advapi32.SetEntriesInAclW.argtypes = [
        wintypes.ULONG, ctypes.POINTER(_EXPLICIT_ACCESS_W),
        ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p),
    ]
    advapi32.SetEntriesInAclW.restype = wintypes.DWORD
    advapi32.SetNamedSecurityInfoW.argtypes = [
        wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
        ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p,
    ]
    advapi32.SetNamedSecurityInfoW.restype = wintypes.DWORD
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p

    old_dacl = ctypes.c_void_p()
    sec_desc = ctypes.c_void_p()
    rc = advapi32.GetNamedSecurityInfoW(
        path, SE_FILE_OBJECT, DACL_SECURITY_INFORMATION,
        None, None, ctypes.byref(old_dacl), None, ctypes.byref(sec_desc),
    )
    if rc != 0:
        return False
    new_dacl = ctypes.c_void_p()
    try:
        ea = _EXPLICIT_ACCESS_W()
        ea.grfAccessPermissions = FILE_ALL_ACCESS
        ea.grfAccessMode = GRANT_ACCESS
        ea.grfInheritance = OBJECT_INHERIT_ACE | CONTAINER_INHERIT_ACE
        ea.Trustee.pMultipleTrustee = None
        ea.Trustee.MultipleTrusteeOperation = 0
        ea.Trustee.TrusteeForm = TRUSTEE_IS_SID
        ea.Trustee.TrusteeType = TRUSTEE_IS_UNKNOWN
        ea.Trustee.ptstrName = psid
        rc = advapi32.SetEntriesInAclW(1, ctypes.byref(ea), old_dacl, ctypes.byref(new_dacl))
        if rc != 0:
            return False
        rc = advapi32.SetNamedSecurityInfoW(
            path, SE_FILE_OBJECT, DACL_SECURITY_INFORMATION,
            None, None, new_dacl, None,
        )
        return rc == 0
    finally:
        if new_dacl.value:
            kernel32.LocalFree(new_dacl)
        if sec_desc.value:
            kernel32.LocalFree(sec_desc)


def _win_appcontainer_lock(kernel32: ctypes.WinDLL) -> int | None:
    """获取跨进程命名互斥锁，串行化 AppContainer 注册表 Profile 增删，消除高并发竞争。"""
    try:
        kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
        kernel32.CreateMutexW.restype = wintypes.HANDLE
        kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        kernel32.WaitForSingleObject.restype = wintypes.DWORD
        h = kernel32.CreateMutexW(None, False, f"Local\\AVL_AppContainer_Lock_{_user_id()}")
        if h:
            kernel32.WaitForSingleObject(h, 10000)
            return int(h)
    except Exception:
        pass
    return None


def _win_appcontainer_unlock(kernel32: ctypes.WinDLL, h_mutex: int | None) -> None:
    if not h_mutex:
        return
    try:
        kernel32.ReleaseMutex.argtypes = [wintypes.HANDLE]
        kernel32.ReleaseMutex.restype = wintypes.BOOL
        kernel32.ReleaseMutex(ctypes.c_void_p(h_mutex))
    except Exception:
        pass
    try:
        kernel32.CloseHandle(ctypes.c_void_p(h_mutex))
    except Exception:
        pass


def _write_metrics_atomic(metrics_file: str | None, payload: dict[str, Any]) -> None:
    if not metrics_file:
        return
    try:
        tmp = f"{metrics_file}.tmp.{os.getpid()}"
        pathlib.Path(tmp).write_text(json.dumps(payload), encoding="utf-8")
        os.replace(tmp, metrics_file)
    except Exception:
        pass


def _run_in_win_sandbox(
    cmd: list[str],
    cwd: str,
    memory_mb: int,
    env: dict[str, str],
    *,
    cpu_limit_ms: int | None = None,
    wall_limit_ms: int | None = None,
    metrics_file: str | None = None,
    active_process_limit: int = 64,
) -> int:
    """在 Windows Job Object（可选每实例独立 SID 的 AppContainer）内执行命令、转发 IO 并精确采集进程树指标。"""
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    ntdll = ctypes.WinDLL("ntdll", use_last_error=True)
    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    userenv = ctypes.WinDLL("userenv", use_last_error=True)

    advapi32.ConvertSidToStringSidW.argtypes = [ctypes.c_void_p, ctypes.POINTER(wintypes.LPWSTR)]
    advapi32.ConvertSidToStringSidW.restype = wintypes.BOOL
    advapi32.FreeSid.argtypes = [ctypes.c_void_p]
    advapi32.FreeSid.restype = ctypes.c_void_p
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p
    userenv.CreateAppContainerProfile.argtypes = [
        wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.LPCWSTR,
        ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(ctypes.c_void_p),
    ]
    userenv.CreateAppContainerProfile.restype = ctypes.c_long
    userenv.DeleteAppContainerProfile.argtypes = [wintypes.LPCWSTR]
    userenv.DeleteAppContainerProfile.restype = ctypes.c_long
    kernel32.GetStdHandle.argtypes = [wintypes.DWORD]
    kernel32.GetStdHandle.restype = wintypes.HANDLE
    kernel32.InitializeProcThreadAttributeList.argtypes = [ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, ctypes.POINTER(ctypes.c_size_t)]
    kernel32.InitializeProcThreadAttributeList.restype = wintypes.BOOL
    kernel32.UpdateProcThreadAttribute.argtypes = [ctypes.c_void_p, wintypes.DWORD, ctypes.c_size_t, ctypes.c_void_p, ctypes.c_size_t, ctypes.c_void_p, ctypes.c_void_p]
    kernel32.UpdateProcThreadAttribute.restype = wintypes.BOOL
    kernel32.DeleteProcThreadAttributeList.argtypes = [ctypes.c_void_p]
    kernel32.CreateProcessW.argtypes = [
        wintypes.LPCWSTR, wintypes.LPWSTR, ctypes.c_void_p, ctypes.c_void_p,
        wintypes.BOOL, wintypes.DWORD, ctypes.c_void_p, wintypes.LPCWSTR,
        ctypes.c_void_p, ctypes.POINTER(_PROCESS_INFORMATION),
    ]
    kernel32.CreateProcessW.restype = wintypes.BOOL
    kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
    kernel32.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
    kernel32.TerminateJobObject.restype = wintypes.BOOL
    kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel32.WaitForSingleObject.restype = wintypes.DWORD
    kernel32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    kernel32.GetExitCodeProcess.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    kernel32.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
    kernel32.TerminateProcess.restype = wintypes.BOOL
    ntdll.NtResumeProcess.argtypes = [wintypes.HANDLE]
    ntdll.NtResumeProcess.restype = ctypes.c_long

    hJob = _create_win_job(memory_mb, cpu_limit_ms=cpu_limit_ms, active_process_limit=active_process_limit)

    # 优先尝试按实例隔离 SID 的 AppContainer
    p_attr = None
    attr_buf = None
    attr_initialized = False
    sec_caps = None
    profile_name = None
    use_appcontainer = False
    sid = ctypes.c_void_p()
    if probe_windows_sandbox() == "windows_appcontainer_job":
        try:
            _ensure_base_prefix_acl()
            profile_name = f"avl_sbx_{os.getpid()}_{hashlib.sha256(cwd.encode()).hexdigest()[:8]}"
            # 预先创建 AppContainer 重定向的 AC\Temp 目录，使 GetTempPathW / g++ 临时文件正常工作
            try:
                os.makedirs(os.path.join(cwd, "Packages", profile_name, "AC", "Temp"), exist_ok=True)
            except OSError:
                pass
            hr = -1
            h_mtx = _win_appcontainer_lock(kernel32)
            try:
                userenv.DeleteAppContainerProfile(profile_name)
                hr = userenv.CreateAppContainerProfile(profile_name, profile_name, profile_name, None, 0, ctypes.byref(sid))
            finally:
                _win_appcontainer_unlock(kernel32, h_mtx)
            if hr == 0 and sid.value:
                acl_ok = _grant_win_dir_sid(cwd, sid)
                if not acl_ok:
                    str_ptr = wintypes.LPWSTR()
                    if advapi32.ConvertSidToStringSidW(sid, ctypes.byref(str_ptr)) and str_ptr.value:
                        sid_str = str_ptr.value
                        kernel32.LocalFree(str_ptr)
                        acl_res = subprocess.run(
                            ["icacls", cwd, "/grant", f"*{sid_str}:(OI)(CI)(F)"],
                            capture_output=True, timeout=15,
                        )
                        acl_ok = (acl_res.returncode == 0)
                if acl_ok:
                    size = ctypes.c_size_t(0)
                    kernel32.InitializeProcThreadAttributeList(None, 1, 0, ctypes.byref(size))
                    attr_buf = (ctypes.c_char * size.value)()
                    p_attr = ctypes.cast(attr_buf, ctypes.c_void_p)
                    attr_initialized = bool(kernel32.InitializeProcThreadAttributeList(p_attr, 1, 0, ctypes.byref(size)))
                    if attr_initialized:
                        sec_caps = _SECURITY_CAPABILITIES()
                        sec_caps.AppContainerSid = sid
                        sec_caps.Capabilities = None
                        sec_caps.CapabilityCount = 0
                        sec_caps.Reserved = 0
                        if kernel32.UpdateProcThreadAttribute(
                            p_attr, 0, PROC_THREAD_ATTRIBUTE_SECURITY_CAPABILITIES,
                            ctypes.byref(sec_caps), ctypes.sizeof(sec_caps), None, None
                        ):
                            use_appcontainer = True
        except Exception:
            use_appcontainer = False

    def cleanup_appcontainer() -> None:
        if p_attr and attr_initialized:
            kernel32.DeleteProcThreadAttributeList(p_attr)
        if sid.value:
            advapi32.FreeSid(sid)
        if profile_name:
            mutex = _win_appcontainer_lock(kernel32)
            try:
                userenv.DeleteAppContainerProfile(profile_name)
            finally:
                _win_appcontainer_unlock(kernel32, mutex)

    if os.environ.get(REQUIRE_APPCONTAINER_ENV) == "1" and not use_appcontainer:
        kernel32.CloseHandle(hJob)
        cleanup_appcontainer()
        raise RuntimeError("AppContainer launch setup failed in official scoring")

    cmd_line = subprocess.list2cmdline(cmd)
    flags = CREATE_SUSPENDED | 0x00000400  # CREATE_UNICODE_ENVIRONMENT
    if use_appcontainer and p_attr:
        flags |= EXTENDED_STARTUPINFO_PRESENT
        si = _STARTUPINFOEXW()
        si.StartupInfo.cb = ctypes.sizeof(_STARTUPINFOEXW)
        si.StartupInfo.dwFlags = STARTF_USESTDHANDLES
        si.StartupInfo.hStdInput = kernel32.GetStdHandle(0xFFFFFFF6)   # STD_INPUT_HANDLE (-10)
        si.StartupInfo.hStdOutput = kernel32.GetStdHandle(0xFFFFFFF5)  # STD_OUTPUT_HANDLE (-11)
        si.StartupInfo.hStdError = kernel32.GetStdHandle(0xFFFFFFF4)   # STD_ERROR_HANDLE (-12)
        si.lpAttributeList = p_attr
        psi = ctypes.byref(si.StartupInfo)
    else:
        si_std = _STARTUPINFOW()
        si_std.cb = ctypes.sizeof(_STARTUPINFOW)
        si_std.dwFlags = STARTF_USESTDHANDLES
        si_std.hStdInput = kernel32.GetStdHandle(0xFFFFFFF6)
        si_std.hStdOutput = kernel32.GetStdHandle(0xFFFFFFF5)
        si_std.hStdError = kernel32.GetStdHandle(0xFFFFFFF4)
        psi = ctypes.cast(ctypes.byref(si_std), ctypes.c_void_p)

    pi = _PROCESS_INFORMATION()

    # 显式传入干净的 Unicode 环境块（AppContainer 下亦适用，零宿主机环境变量泄漏）
    sorted_items = sorted(env.items(), key=lambda x: x[0].upper())
    env_str = "".join(f"{k}={v}\0" for k, v in sorted_items) + "\0"
    env_buf = ctypes.create_unicode_buffer(env_str)

    created = kernel32.CreateProcessW(
        None, cmd_line, None, None, True,
        flags,
        ctypes.byref(env_buf), cwd, psi, ctypes.byref(pi)
    )

    if not created:
        err = ctypes.get_last_error()
        kernel32.CloseHandle(hJob)
        cleanup_appcontainer()
        raise OSError(err, f"CreateProcessW failed for {cmd[0]}")

    timed_out = False
    cpu_exceeded = False
    exit_code = 1
    cpu_ms = 0.0
    peak_mem_mb = 0.0
    try:
        if not kernel32.AssignProcessToJobObject(hJob, pi.hProcess):
            err = ctypes.get_last_error()
            kernel32.TerminateProcess(pi.hProcess, 1)
            raise OSError(err, "AssignProcessToJobObject failed before resume")
        if ntdll.NtResumeProcess(pi.hProcess) != 0:
            kernel32.TerminateJobObject(hJob, 1)
            raise RuntimeError("NtResumeProcess failed")
        t_start = time.perf_counter()
        WAIT_OBJECT_0 = 0x00000000
        poll_needed = bool(cpu_limit_ms or wall_limit_ms or metrics_file)
        if not poll_needed:
            kernel32.WaitForSingleObject(pi.hProcess, 0xFFFFFFFF)
        else:
            last_flush = 0.0
            while True:
                wait_rc = kernel32.WaitForSingleObject(pi.hProcess, 25)
                cpu_ms, peak_mem_mb = _query_win_job_metrics(hJob)
                now = time.perf_counter()
                elapsed_ms = (now - t_start) * 1000.0
                if wait_rc == WAIT_OBJECT_0:
                    break
                if cpu_limit_ms is not None and cpu_ms > float(cpu_limit_ms) + 80.0:
                    timed_out = True
                    cpu_exceeded = True
                    kernel32.TerminateJobObject(hJob, 124)
                    kernel32.WaitForSingleObject(pi.hProcess, 1000)
                    cpu_ms, peak_mem_mb = _query_win_job_metrics(hJob)
                    break
                if wall_limit_ms is not None and elapsed_ms > float(wall_limit_ms):
                    timed_out = True
                    kernel32.TerminateJobObject(hJob, 124)
                    kernel32.WaitForSingleObject(pi.hProcess, 1000)
                    cpu_ms, peak_mem_mb = _query_win_job_metrics(hJob)
                    break
                if metrics_file and (now - last_flush) >= 0.08:
                    last_flush = now
                    _write_metrics_atomic(metrics_file, {
                        "cpu_ms": round(cpu_ms, 2),
                        "peak_mem_mb": round(peak_mem_mb, 2),
                        "timed_out": False,
                        "cpu_exceeded": False,
                        "appcontainer": use_appcontainer,
                        "running": True,
                    })
        cpu_ms, peak_mem_mb = _query_win_job_metrics(hJob)
        code = wintypes.DWORD()
        kernel32.GetExitCodeProcess(pi.hProcess, ctypes.byref(code))
        exit_code = int(code.value)
        # Job Object PerJobUserTimeLimit 触发时返回 STATUS_QUOTA_EXCEEDED / 0xC0000044 或 exit_code 124
        if cpu_limit_ms is not None and (cpu_ms > float(cpu_limit_ms) or exit_code in (0xC0000044, 0x00000718)):
            timed_out = True
            cpu_exceeded = True
        mem_limit_hit = exit_code in (0xC0000017, 0xC000012D)
        _write_metrics_atomic(metrics_file, {
            "cpu_ms": round(cpu_ms, 2),
            "peak_mem_mb": round(peak_mem_mb, 2),
            "timed_out": timed_out,
            "cpu_exceeded": cpu_exceeded,
            "mem_limit_hit": mem_limit_hit,
            "exit_code": exit_code,
            "appcontainer": use_appcontainer,
            "running": False,
        })
        return exit_code
    finally:
        kernel32.CloseHandle(pi.hProcess)
        kernel32.CloseHandle(pi.hThread)
        kernel32.CloseHandle(hJob)
        cleanup_appcontainer()
        if _CACHED_WINDOWS_SANDBOX:
            os.environ["AVL_SANDBOX_TYPE"] = _CACHED_WINDOWS_SANDBOX


def _exec_python_win(argv: list[str]) -> None:
    index = _script_index(argv)
    if index is None:
        sys.exit(subprocess.call([REAL_PYTHON] + argv))

    script = argv[index]
    inner = list(argv)
    kagg_loader = os.path.join(ROOT, "kaggriculture", "engine", "loader.py")
    ai_pkg_entry = os.path.join(ROOT, "ai_training", "engine", "pkg_entry.py")

    is_kagg = (os.path.realpath(script) == os.path.realpath(kagg_loader) and argv[index + 1:index + 2] == ["--worker"])
    is_ai_pkg = (os.path.realpath(script) == os.path.realpath(ai_pkg_entry) and len(argv) > index + 1)
    if is_kagg:
        submission = argv[index + 2]
    elif is_ai_pkg:
        submission = argv[index + 1]
    else:
        submission = script

    if _trusted(submission):
        sys.exit(subprocess.call([REAL_PYTHON] + argv))

    # 候选提交：复制到独立临时目录并隔离运行
    sandbox_dir = tempfile.mkdtemp(prefix="avl_win_sbx_")
    try:
        if is_kagg or is_ai_pkg:
            # 由专用包装脚本载入（支持目录包/多文件模块与包内只读数据）
            dst_entry = os.path.join(sandbox_dir, os.path.basename(script))
            shutil.copy2(script, dst_entry)
            inner[index] = dst_entry

            sub_arg_idx = index + 2 if is_kagg else index + 1
            dst_sub = os.path.join(sandbox_dir, os.path.basename(submission.rstrip("\\/")) or "pkg")
            if os.path.isdir(submission):
                shutil.copytree(submission, dst_sub)
            else:
                shutil.copy2(submission, dst_sub)
            inner[sub_arg_idx] = dst_sub
        else:
            dst_sub = os.path.join(sandbox_dir, os.path.basename(submission.rstrip("\\/")) or "sub")
            if os.path.isdir(submission):
                shutil.copytree(submission, dst_sub)
            else:
                shutil.copy2(submission, dst_sub)
            inner[index] = dst_sub

        # 注意：不得使用 -I（其隐含 -E，会丢弃 _clean_win_env 注入的 PYTHONUTF8=1 与 PYTHONHASHSEED=0）；
        # _clean_win_env 已彻底清空宿主环境变量，改用 -P -s -B -X utf8 同时保证路径/用户站点/字节码隔离与 UTF-8 + 固定哈希种子。
        cmd = [REAL_PYTHON, "-P", "-s", "-B", "-X", "utf8"] + inner
        mem = int(os.environ.get(MEM_ENV, DEFAULT_MEMORY_MB))
        cpu_env = os.environ.get(CPU_LIMIT_ENV, "").strip()
        wall_env = os.environ.get(WALL_LIMIT_ENV, "").strip()
        procs_env = os.environ.get(MAX_PROCS_ENV, "").strip()
        cpu_lim = int(float(cpu_env)) if cpu_env else None
        wall_lim = int(float(wall_env)) if wall_env else None
        max_procs = int(procs_env) if procs_env else 64
        env = _clean_win_env(sandbox_dir)
        code = _run_in_win_sandbox(
            cmd,
            cwd=sandbox_dir,
            memory_mb=mem,
            env=env,
            cpu_limit_ms=cpu_lim,
            wall_limit_ms=wall_lim,
            active_process_limit=max_procs,
        )
        sys.exit(code)
    finally:
        try:
            shutil.rmtree(sandbox_dir, ignore_errors=True)
        except Exception:
            pass


def _exec_python_linux(argv: list[str]) -> None:
    real = os.path.realpath(sys.executable)
    index = _script_index(argv)
    if index is None:
        os.execv(real, [real] + argv)
    script = argv[index]
    binds, inner = [], list(argv)
    kagg_loader = os.path.join(ROOT, "kaggriculture", "engine", "loader.py")
    ai_pkg_entry = os.path.join(ROOT, "ai_training", "engine", "pkg_entry.py")
    if os.path.realpath(script) == os.path.realpath(kagg_loader) and argv[index + 1:index + 2] == ["--worker"]:
        submission = argv[index + 2]
        inner[index] = "/sandbox/loader.py"
        inner[index + 2] = "/work/" + os.path.basename(submission.rstrip("/"))
        binds += ["--ro-bind", script, "/sandbox/loader.py"]
    elif os.path.realpath(script) == os.path.realpath(ai_pkg_entry) and len(argv) > index + 1:
        submission = argv[index + 1]
        if _trusted(submission):
            os.execv(real, [real] + argv)
        inner[index] = "/sandbox/pkg_entry.py"
        inner[index + 1] = "/work/" + os.path.basename(submission.rstrip("/"))
        binds += ["--ro-bind", script, "/sandbox/pkg_entry.py"]
    elif _trusted(script):
        os.execv(real, [real] + argv)
    else:
        submission = script
        inner[index] = "/work/" + os.path.basename(submission.rstrip("/"))
    command = _common_linux() + binds + [
        "--ro-bind", os.path.realpath(submission), "/work/" + os.path.basename(submission.rstrip("/")),
        "--chdir", "/work", "--setenv", "PYTHONHASHSEED", "0", "--setenv", "PYTHONUTF8", "1",
        "--setenv", "PYTHONDONTWRITEBYTECODE", "1", "--remount-ro", "/dev", "--remount-ro", "/",
        real, "-P", "-s", "-B", "-X", "utf8"] + inner
    _limit_process_linux(int(os.environ.get(MEM_ENV, DEFAULT_MEMORY_MB)))
    os.execvp("bwrap", command)


def _exec_python(argv: list[str]) -> None:
    if sys.platform == "win32":
        _exec_python_win(argv)
    else:
        _exec_python_linux(argv)


def _exec_compile(cmd: list[str]) -> None:
    """编译隔离：在 AppContainer（Windows）或 bubblewrap（Linux）内隔离编译，防止 #embed/.incbin/#include 窃取宿主机隐藏文件。"""
    if sys.platform == "win32":
        temp_dir = tempfile.mkdtemp(prefix="avl_win_cpp_")
        try:
            compiler = shutil.which(cmd[0]) or cmd[0]
            staged_cmd: list[str] = [compiler]
            orig_out: str | None = None
            staged_out = os.path.join(temp_dir, "out.exe")
            src_idx = 0
            inc_idx = 0
            i = 1
            while i < len(cmd):
                arg = cmd[i]
                if arg == "-o" and i + 1 < len(cmd):
                    orig_out = cmd[i + 1]
                    staged_cmd.extend(["-o", staged_out])
                    i += 2
                    continue
                if arg == "-I" and i + 1 < len(cmd):
                    inc_src = cmd[i + 1]
                    inc_dst = os.path.join(temp_dir, f"inc_{inc_idx}")
                    inc_idx += 1
                    if os.path.isdir(inc_src):
                        shutil.copytree(inc_src, inc_dst)
                    else:
                        os.makedirs(inc_dst, exist_ok=True)
                    staged_cmd.extend(["-I", inc_dst])
                    i += 2
                    continue
                if not arg.startswith("-") and os.path.isfile(arg):
                    sdir = os.path.join(temp_dir, f"src_{src_idx}")
                    src_idx += 1
                    os.makedirs(sdir, exist_ok=True)
                    sdst = os.path.join(sdir, os.path.basename(arg))
                    shutil.copy2(arg, sdst)
                    staged_cmd.append(sdst)
                    i += 1
                    continue
                staged_cmd.append(arg)
                i += 1

            env = _clean_win_env(temp_dir)
            env["TMPDIR"] = temp_dir
            code = _run_in_win_sandbox(staged_cmd, cwd=temp_dir, memory_mb=2048, env=env, wall_limit_ms=60000)
            if code == 0 and orig_out and os.path.isfile(staged_out):
                os.makedirs(os.path.dirname(os.path.abspath(orig_out)), exist_ok=True)
                shutil.copy2(staged_out, orig_out)
            sys.exit(code)
        finally:
            try:
                shutil.rmtree(temp_dir, ignore_errors=True)
            except Exception:
                pass
    else:
        output = cmd[cmd.index("-o") + 1]
        binds = ["--tmpfs", "/tmp"]
        for path in cmd[1:]:
            if os.path.isfile(path):
                binds += ["--ro-bind", path, path]
        for index, arg in enumerate(cmd):
            if arg == "-I":
                binds += ["--ro-bind", cmd[index + 1], cmd[index + 1]]
        binds += ["--bind", os.path.dirname(output), os.path.dirname(output)]
        os.execvp("bwrap", _common_linux() + binds + ["--setenv", "TMPDIR", "/tmp", "--chdir", "/"] + cmd)


def _exec_binary(workdir: str, launcher: str, fd: str, exe: str) -> None:
    if sys.platform == "win32":
        dst_exe = os.path.join(workdir, os.path.basename(exe) or "solution.exe")
        if os.path.normcase(os.path.abspath(exe)) != os.path.normcase(os.path.abspath(dst_exe)):
            shutil.copy2(exe, dst_exe)
        target = [dst_exe]
        if launcher and launcher != "None":
            target = [launcher, fd, dst_exe]
        mem = int(os.environ.get(MEM_ENV, DEFAULT_MEMORY_MB))
        # Job Object 硬上限给 +64 MiB 缓冲区，使 (mem, mem+64] 区间分配可被精确采集为 PeakJobMemoryUsed > mem 从而准确判 MLE
        job_mem = mem + 64
        cpu_env = os.environ.get(CPU_LIMIT_ENV, "").strip()
        wall_env = os.environ.get(WALL_LIMIT_ENV, "").strip()
        cpu_lim = int(float(cpu_env)) if cpu_env else None
        wall_lim = int(float(wall_env)) if wall_env else None
        metrics_file = os.environ.get(METRICS_ENV) or None
        env = _clean_win_env(workdir)
        code = _run_in_win_sandbox(
            target,
            cwd=workdir,
            memory_mb=job_mem,
            env=env,
            cpu_limit_ms=cpu_lim,
            wall_limit_ms=wall_lim,
            metrics_file=metrics_file,
        )
        sys.exit(code)
    else:
        mem = int(os.environ.get(MEM_ENV, DEFAULT_MEMORY_MB))
        _limit_process_linux(mem + 64)
        binds = ["--bind", workdir, "/work", "--chdir", "/work", "--setenv", "TMPDIR", "/work",
                 "--ro-bind", exe, "/sandbox/solution"]
        target = ["/sandbox/solution"]
        if launcher and launcher != "None":
            binds += ["--ro-bind", launcher, "/sandbox/launcher"]
            target = ["/sandbox/launcher", fd, "/sandbox/solution"]
        os.execvp("bwrap", _common_linux() + binds + ["--remount-ro", "/dev", "--remount-ro", "/"] + target)


def _launch(problem: str, script: str, args: list[str]) -> None:
    install(problem)
    sys.argv = [script] + args
    sys.path.insert(0, os.path.dirname(os.path.abspath(script)))
    runpy.run_path(script, run_name="__main__")


def main() -> None:
    mode, args = sys.argv[1], sys.argv[2:]
    if mode == "--python":
        _exec_python(args)
    elif mode == "--cpp" and args[0] == "--compile":
        _exec_compile(args[1:])
    elif mode == "--cpp":
        _exec_binary(*args[1:])
    elif mode == "--launch":
        _launch(args[0], args[1], args[2:])
    else:
        raise SystemExit("unknown mode: " + mode)


if __name__ == "__main__":
    main()
