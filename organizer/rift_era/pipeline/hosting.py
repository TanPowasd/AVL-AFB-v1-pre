"""Seat process launchers for the scoring pipeline (host/IPC layer only).

Three transports satisfy ``engine.ipc.ManagedProcess`` (``process`` with three
pipes, ``pause``/``resume``/``close``):

* ``cgroup`` – the engine's own production ``Sandbox`` (bwrap + delegated
  cgroup v2 + seccomp). Selected only when a delegated cgroup is passed.
* ``bwrap`` – :class:`BwrapProcess`: bubblewrap namespaces (user/pid/net/ipc/uts),
  read-only runtime and submission, 64 MiB tmpfs work dir, rlimits set by a
  trusted entry script before user code runs. No cgroup, so no CPU/PID quota and
  no kernel freezer: pause/resume stop the whole process tree with SIGSTOP.
* ``none`` – :class:`PlainProcess`: an ordinary child process with the same
  interpreter flags and rlimits. **Not isolated.** Results carry
  ``sandbox: "none"`` and can never be official.

``select_launcher('auto')`` uses bwrap when the binary is present and works,
otherwise falls back to ``none`` and says so in its label. Thinking time is
charged by ``StrategySession``/``StrategyClock`` exactly as in the engine
(0.5 s base window, 450 s time_pool, 3600 s per match), whatever the transport.
"""

from __future__ import annotations

from dataclasses import dataclass
import itertools
import os
from pathlib import Path
import secrets
import shutil
import signal
import subprocess
import sys
import sysconfig
import tempfile
import time
if os.name == 'nt':
    import ctypes
    from ctypes import wintypes
    import msvcrt

SANDBOX_MODES = ('auto', 'bwrap', 'none', 'cgroup', 'windows')

# Per-seat limits applied by the trusted entry (bwrap) or preexec (none).
MEMORY_LIMIT_BYTES = 1024**3          # 1 GiB address space, as the cgroup profile
FILE_SIZE_LIMIT_BYTES = 64 * 1024**2  # matches the 64 MiB work tmpfs
OPEN_FILES_LIMIT = 128
WORK_TMPFS_BYTES = 64 * 1024**2
MAX_WORK_BYTES = WORK_TMPFS_BYTES
_PROFILE_SEQ = itertools.count(1)

_ENTRY = r'''
import os, runpy, sys
mem, fsize, nofile = (int(x) for x in sys.argv[2:5])
try:
    import resource
except ImportError:
    resource = None
if resource is not None:
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    resource.setrlimit(resource.RLIMIT_NOFILE, (nofile, nofile))
    resource.setrlimit(resource.RLIMIT_FSIZE, (fsize, fsize))
    if mem:
        resource.setrlimit(resource.RLIMIT_AS, (mem, mem))
root = sys.argv[1]
sys.path.insert(0, root)
sys.argv = [os.path.join(root, 'main.py')]
runpy.run_path(sys.argv[0], run_name='__main__')
'''


def _seat_env(path_dir: str, work_dir: str | None = None) -> dict:
    env = {'PATH': path_dir, 'PYTHONHASHSEED': '0', 'PYTHONDONTWRITEBYTECODE': '1', 'LC_ALL': 'C'}
    if os.name == 'nt':
        if 'SYSTEMROOT' in os.environ:
            env['SYSTEMROOT'] = os.environ['SYSTEMROOT']
        if work_dir is not None:
            env['TEMP'] = env['TMP'] = work_dir
    return env


def _rmtree_force(path: str | Path) -> None:
    """Remove a directory tree even if it contains read-only files on Windows."""
    def _fix(func, target, _exc):
        try:
            os.chmod(target, 0o700)
            func(target)
        except OSError:
            pass
    try:
        shutil.rmtree(path, onexc=_fix)
    except TypeError:  # Python < 3.12
        shutil.rmtree(path, onerror=lambda f, t, _ei: _fix(f, t, None))
    except OSError:
        pass


def _dir_bytes(path: Path) -> int:
    """Total logical bytes of all files under ``path`` (for disk quota checks)."""
    total = 0
    for root, _, files in os.walk(path):
        for name in files:
            try:
                total += os.stat(os.path.join(root, name), follow_symlinks=False).st_size
            except OSError:
                pass
    return total


def _win_clean_env(work_dir: str) -> dict[str, str]:
    """Minimal, deterministic Windows environment with zero host secrets."""
    sys_root = os.environ.get('SYSTEMROOT', r'C:\Windows')
    win_dir = os.environ.get('WINDIR', sys_root)
    sys_drive = os.environ.get('SYSTEMDRIVE', 'C:')
    comspec = os.environ.get('COMSPEC', os.path.join(sys_root, 'System32', 'cmd.exe'))
    pathext = os.environ.get('PATHEXT', '.COM;.EXE;.BAT;.CMD')
    py_dirs = [str(Path(sys.executable).resolve().parent), str(Path(sys.base_prefix).resolve())]
    path_parts = list(dict.fromkeys([*py_dirs, os.path.join(sys_root, 'System32'), sys_root]))
    return {
        'SYSTEMROOT': sys_root,
        'WINDIR': win_dir,
        'SYSTEMDRIVE': sys_drive,
        'COMSPEC': comspec,
        'PATH': ';'.join(path_parts),
        'PATHEXT': pathext,
        'USERPROFILE': work_dir,
        'LOCALAPPDATA': work_dir,
        'APPDATA': work_dir,
        'TEMP': work_dir,
        'TMP': work_dir,
        'PYTHONHASHSEED': '0',
        'PYTHONUTF8': '1',
        'PYTHONDONTWRITEBYTECODE': '1',
    }


def _win_env_block(env: dict[str, str]):
    """Null-separated, double-null-terminated sorted UTF-16 environment block."""
    items = sorted(env.items(), key=lambda kv: kv[0].upper())
    return ctypes.create_unicode_buffer(''.join(f'{k}={v}\0' for k, v in items) + '\0')


if os.name == 'nt':
    _kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
    _ntdll = ctypes.WinDLL('ntdll')
    _userenv = ctypes.WinDLL('userenv', use_last_error=True)
    _advapi32 = ctypes.WinDLL('advapi32', use_last_error=True)
    _CREATE_SUSPENDED = 0x00000004
    _CREATE_UNICODE_ENVIRONMENT = 0x00000400
    _JOB_OBJECT_LIMIT_PROCESS_MEMORY = 0x00000100
    _JOB_OBJECT_LIMIT_JOB_MEMORY = 0x00000200
    _JOB_OBJECT_LIMIT_ACTIVE_PROCESS = 0x00000008
    _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
    _EXTENDED_STARTUPINFO_PRESENT = 0x00080000
    _PROC_THREAD_ATTRIBUTE_SECURITY_CAPABILITIES = 0x00020009
    _STARTF_USESTDHANDLES = 0x00000100
    _HANDLE_FLAG_INHERIT = 0x00000001
    _PROCESS_TERMINATE = 0x0001
    _PROCESS_SUSPEND_RESUME = 0x0800
    _PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    _THREAD_QUERY_LIMITED_INFORMATION = 0x0800
    _TH32CS_SNAPPROCESS = 0x00000002
    _TH32CS_SNAPTHREAD = 0x00000004
    _INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value
    _STILL_ACTIVE = 259

    # Explicit 64-bit Win32 prototypes to prevent pointer truncation.
    _kernel32.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
    _kernel32.CreateJobObjectW.restype = wintypes.HANDLE
    _kernel32.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
    _kernel32.SetInformationJobObject.restype = wintypes.BOOL
    _kernel32.QueryInformationJobObject.argtypes = [
        wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)
    ]
    _kernel32.QueryInformationJobObject.restype = wintypes.BOOL
    _kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    _kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
    _kernel32.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
    _kernel32.TerminateJobObject.restype = wintypes.BOOL
    _kernel32.CreatePipe.argtypes = [
        ctypes.POINTER(wintypes.HANDLE), ctypes.POINTER(wintypes.HANDLE), ctypes.c_void_p, wintypes.DWORD
    ]
    _kernel32.CreatePipe.restype = wintypes.BOOL
    _kernel32.SetHandleInformation.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.DWORD]
    _kernel32.SetHandleInformation.restype = wintypes.BOOL
    _kernel32.InitializeProcThreadAttributeList.argtypes = [
        ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, ctypes.POINTER(ctypes.c_size_t)
    ]
    _kernel32.InitializeProcThreadAttributeList.restype = wintypes.BOOL
    _kernel32.UpdateProcThreadAttribute.argtypes = [
        ctypes.c_void_p, wintypes.DWORD, ctypes.c_size_t, ctypes.c_void_p,
        ctypes.c_size_t, ctypes.c_void_p, ctypes.c_void_p
    ]
    _kernel32.UpdateProcThreadAttribute.restype = wintypes.BOOL
    _kernel32.DeleteProcThreadAttributeList.argtypes = [ctypes.c_void_p]
    _kernel32.DeleteProcThreadAttributeList.restype = None
    _kernel32.CreateProcessW.argtypes = [
        wintypes.LPCWSTR, wintypes.LPWSTR, ctypes.c_void_p, ctypes.c_void_p,
        wintypes.BOOL, wintypes.DWORD, ctypes.c_void_p, wintypes.LPCWSTR,
        ctypes.c_void_p, ctypes.c_void_p
    ]
    _kernel32.CreateProcessW.restype = wintypes.BOOL
    _kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    _kernel32.WaitForSingleObject.restype = wintypes.DWORD
    _kernel32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    _kernel32.GetExitCodeProcess.restype = wintypes.BOOL
    _kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    _kernel32.CloseHandle.restype = wintypes.BOOL
    _kernel32.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
    _kernel32.TerminateProcess.restype = wintypes.BOOL
    _kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    _kernel32.LocalFree.restype = ctypes.c_void_p
    _kernel32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    _kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    _kernel32.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.c_void_p]
    _kernel32.Process32FirstW.restype = wintypes.BOOL
    _kernel32.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.c_void_p]
    _kernel32.Process32NextW.restype = wintypes.BOOL
    _kernel32.Thread32First.argtypes = [wintypes.HANDLE, ctypes.c_void_p]
    _kernel32.Thread32First.restype = wintypes.BOOL
    _kernel32.Thread32Next.argtypes = [wintypes.HANDLE, ctypes.c_void_p]
    _kernel32.Thread32Next.restype = wintypes.BOOL
    _kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    _kernel32.OpenProcess.restype = wintypes.HANDLE
    _kernel32.OpenThread.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    _kernel32.OpenThread.restype = wintypes.HANDLE

    _userenv.CreateAppContainerProfile.argtypes = [
        wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.LPCWSTR,
        ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(ctypes.c_void_p)
    ]
    _userenv.CreateAppContainerProfile.restype = ctypes.c_long
    _userenv.DeleteAppContainerProfile.argtypes = [wintypes.LPCWSTR]
    _userenv.DeleteAppContainerProfile.restype = ctypes.c_long

    _advapi32.ConvertSidToStringSidW.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)]
    _advapi32.ConvertSidToStringSidW.restype = wintypes.BOOL
    _advapi32.FreeSid.argtypes = [ctypes.c_void_p]
    _advapi32.FreeSid.restype = ctypes.c_void_p

    _ntdll.NtQueryInformationThread.argtypes = [
        wintypes.HANDLE, ctypes.c_ulong, ctypes.c_void_p, ctypes.c_ulong, ctypes.c_void_p
    ]
    _ntdll.NtQueryInformationThread.restype = ctypes.c_long
    _ntdll.NtSuspendProcess.argtypes = [wintypes.HANDLE]
    _ntdll.NtSuspendProcess.restype = ctypes.c_long
    _ntdll.NtResumeProcess.argtypes = [wintypes.HANDLE]
    _ntdll.NtResumeProcess.restype = ctypes.c_long

    class _SECURITY_ATTRIBUTES(ctypes.Structure):
        _fields_ = [('nLength', wintypes.DWORD), ('lpSecurityDescriptor', ctypes.c_void_p), ('bInheritHandle', wintypes.BOOL)]

    class _SECURITY_CAPABILITIES(ctypes.Structure):
        _fields_ = [('AppContainerSid', ctypes.c_void_p), ('Capabilities', ctypes.c_void_p),
                    ('CapabilityCount', wintypes.DWORD), ('Reserved', wintypes.DWORD)]

    class _STARTUPINFOW(ctypes.Structure):
        _fields_ = [
            ('cb', wintypes.DWORD), ('lpReserved', wintypes.LPWSTR), ('lpDesktop', wintypes.LPWSTR),
            ('lpTitle', wintypes.LPWSTR), ('dwX', wintypes.DWORD), ('dwY', wintypes.DWORD),
            ('dwXSize', wintypes.DWORD), ('dwYSize', wintypes.DWORD), ('dwXCountChars', wintypes.DWORD),
            ('dwYCountChars', wintypes.DWORD), ('dwFillAttribute', wintypes.DWORD), ('dwFlags', wintypes.DWORD),
            ('wShowWindow', wintypes.WORD), ('cbReserved2', wintypes.WORD), ('lpReserved2', ctypes.c_void_p),
            ('hStdInput', wintypes.HANDLE), ('hStdOutput', wintypes.HANDLE), ('hStdError', wintypes.HANDLE),
        ]

    class _STARTUPINFOEXW(ctypes.Structure):
        _fields_ = [('StartupInfo', _STARTUPINFOW), ('lpAttributeList', ctypes.c_void_p)]

    class _PROCESS_INFORMATION(ctypes.Structure):
        _fields_ = [('hProcess', wintypes.HANDLE), ('hThread', wintypes.HANDLE),
                    ('dwProcessId', wintypes.DWORD), ('dwThreadId', wintypes.DWORD)]

    class _IoCounters(ctypes.Structure):
        _fields_ = [(n, ctypes.c_ulonglong) for n in (
            'ReadOperationCount', 'WriteOperationCount', 'OtherOperationCount',
            'ReadTransferCount', 'WriteTransferCount', 'OtherTransferCount')]

    class _JobBasicLimits(ctypes.Structure):
        _fields_ = [('PerProcessUserTimeLimit', ctypes.c_longlong), ('PerJobUserTimeLimit', ctypes.c_longlong),
                    ('LimitFlags', wintypes.DWORD), ('MinimumWorkingSetSize', ctypes.c_size_t),
                    ('MaximumWorkingSetSize', ctypes.c_size_t), ('ActiveProcessLimit', wintypes.DWORD),
                    ('Affinity', ctypes.c_size_t), ('PriorityClass', wintypes.DWORD),
                    ('SchedulingClass', wintypes.DWORD)]

    class _JobExtendedLimits(ctypes.Structure):
        _fields_ = [('BasicLimitInformation', _JobBasicLimits), ('IoInfo', _IoCounters),
                    ('ProcessMemoryLimit', ctypes.c_size_t), ('JobMemoryLimit', ctypes.c_size_t),
                    ('PeakProcessMemoryUsed', ctypes.c_size_t), ('PeakJobMemoryUsed', ctypes.c_size_t)]

    class _ProcessEntry32W(ctypes.Structure):
        _fields_ = [('dwSize', wintypes.DWORD), ('cntUsage', wintypes.DWORD),
                    ('th32ProcessID', wintypes.DWORD), ('th32DefaultHeapID', ctypes.c_size_t),
                    ('th32ModuleID', wintypes.DWORD), ('cntThreads', wintypes.DWORD),
                    ('th32ParentProcessID', wintypes.DWORD), ('pcPriClassBase', ctypes.c_long),
                    ('dwFlags', wintypes.DWORD), ('szExeFile', ctypes.c_wchar * 260)]

    class _ThreadEntry32(ctypes.Structure):
        _fields_ = [('dwSize', wintypes.DWORD), ('cntUsage', wintypes.DWORD),
                    ('th32ThreadID', wintypes.DWORD), ('th32OwnerProcessID', wintypes.DWORD),
                    ('tpBasePri', ctypes.c_long), ('tpDeltaPri', ctypes.c_long), ('dwFlags', wintypes.DWORD)]

    def _win_create_job(memory_limit: int, active_process_limit: int = 64) -> int:
        job = _kernel32.CreateJobObjectW(None, None)
        if not job:
            raise OSError(ctypes.get_last_error(), 'CreateJobObjectW failed')
        info = _JobExtendedLimits()
        flags = _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if memory_limit > 0:
            flags |= _JOB_OBJECT_LIMIT_PROCESS_MEMORY | _JOB_OBJECT_LIMIT_JOB_MEMORY
            info.ProcessMemoryLimit = memory_limit
            info.JobMemoryLimit = memory_limit
        if active_process_limit > 0:
            flags |= _JOB_OBJECT_LIMIT_ACTIVE_PROCESS
            info.BasicLimitInformation.ActiveProcessLimit = active_process_limit
        info.BasicLimitInformation.LimitFlags = flags
        if not _kernel32.SetInformationJobObject(ctypes.c_void_p(job), 9, ctypes.byref(info), ctypes.sizeof(info)):
            err = ctypes.get_last_error()
            _kernel32.CloseHandle(ctypes.c_void_p(job))
            raise OSError(err, 'SetInformationJobObject failed')
        return int(job)

    def _win_job_pids(job: int) -> list[int]:
        cap = 64
        while True:
            class _PidList(ctypes.Structure):
                _fields_ = [('NumberOfAssignedProcesses', wintypes.DWORD),
                            ('NumberOfProcessIdsInList', wintypes.DWORD),
                            ('ProcessIdList', ctypes.c_size_t * cap)]
            buf = _PidList()
            if _kernel32.QueryInformationJobObject(ctypes.c_void_p(job), 3, ctypes.byref(buf), ctypes.sizeof(buf), None):
                return [int(buf.ProcessIdList[i]) for i in range(buf.NumberOfProcessIdsInList)]
            if ctypes.get_last_error() == 234:  # ERROR_MORE_DATA
                cap *= 2
                continue
            return []

    def _win_tree_pids(root: int) -> list[int]:
        snap = _kernel32.CreateToolhelp32Snapshot(_TH32CS_SNAPPROCESS, 0)
        if snap == _INVALID_HANDLE_VALUE or not snap:
            return [root]
        children: dict[int, list[int]] = {}
        alive: set[int] = set()
        try:
            entry = _ProcessEntry32W()
            entry.dwSize = ctypes.sizeof(entry)
            ok = _kernel32.Process32FirstW(ctypes.c_void_p(snap), ctypes.byref(entry))
            while ok:
                pid, ppid = int(entry.th32ProcessID), int(entry.th32ParentProcessID)
                alive.add(pid)
                if pid != ppid:
                    children.setdefault(ppid, []).append(pid)
                ok = _kernel32.Process32NextW(ctypes.c_void_p(snap), ctypes.byref(entry))
        finally:
            _kernel32.CloseHandle(ctypes.c_void_p(snap))
        if root not in alive and root not in children:
            return []
        seen, stack = [], [root]
        while stack:
            pid = stack.pop()
            if pid in seen:
                continue
            seen.append(pid)
            stack.extend(children.get(pid, ()))
        return seen

    def _win_proc_state(pid: int) -> str | None:
        handle = _kernel32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            return None
        try:
            code = wintypes.DWORD()
            if not _kernel32.GetExitCodeProcess(ctypes.c_void_p(handle), ctypes.byref(code)) or code.value != _STILL_ACTIVE:
                return None
        finally:
            _kernel32.CloseHandle(ctypes.c_void_p(handle))
        snap = _kernel32.CreateToolhelp32Snapshot(_TH32CS_SNAPTHREAD, 0)
        if snap == _INVALID_HANDLE_VALUE or not snap:
            return 'R'
        found = 0
        try:
            te = _ThreadEntry32()
            te.dwSize = ctypes.sizeof(te)
            ok = _kernel32.Thread32First(ctypes.c_void_p(snap), ctypes.byref(te))
            while ok:
                if int(te.th32OwnerProcessID) == pid:
                    th = _kernel32.OpenThread(_THREAD_QUERY_LIMITED_INFORMATION, False, te.th32ThreadID)
                    if th:
                        try:
                            cnt = wintypes.ULONG()
                            st = _ntdll.NtQueryInformationThread(
                                ctypes.c_void_p(th), 35, ctypes.byref(cnt), ctypes.sizeof(cnt), None
                            ) & 0xffffffff
                            if st == 0:
                                found += 1
                                if cnt.value == 0:
                                    return 'R'
                        finally:
                            _kernel32.CloseHandle(ctypes.c_void_p(th))
                ok = _kernel32.Thread32Next(ctypes.c_void_p(snap), ctypes.byref(te))
        finally:
            _kernel32.CloseHandle(ctypes.c_void_p(snap))
        return 'T' if found > 0 else None

    def _win_suspend_or_resume(pids: list[int], *, suspend: bool) -> None:
        fn = _ntdll.NtSuspendProcess if suspend else _ntdll.NtResumeProcess
        for pid in pids:
            handle = _kernel32.OpenProcess(_PROCESS_SUSPEND_RESUME, False, pid)
            if handle:
                try:
                    fn(ctypes.c_void_p(handle))
                finally:
                    _kernel32.CloseHandle(ctypes.c_void_p(handle))


def tree_pids(root: int, proc: Path = Path('/proc')) -> list[int]:
    """``root`` and every descendant, read from /proc/<pid>/task/<tid>/children."""
    if os.name == 'nt' and proc == Path('/proc'):
        return _win_tree_pids(root)
    seen, stack = [], [root]
    while stack:
        pid = stack.pop()
        if pid in seen:
            continue
        seen.append(pid)
        try:
            tasks = list((proc / str(pid) / 'task').iterdir())
        except OSError:
            continue
        for task in tasks:
            try:
                stack.extend(int(x) for x in (task / 'children').read_text().split())
            except (OSError, ValueError):
                pass
    return seen


def _proc_state(pid: int, proc: Path = Path('/proc')) -> str | None:
    if os.name == 'nt' and proc == Path('/proc'):
        return _win_proc_state(pid)
    try:
        return (proc / str(pid) / 'stat').read_text().rsplit(')', 1)[1].split()[0]
    except (OSError, IndexError):
        return None


def _signal_tree(root: int, sig: int, *, settle_seconds: float = 5.0) -> None:
    pids = tree_pids(root)
    # Stop leaves first so a parent cannot respawn between signals.
    for pid in (reversed(pids) if sig == signal.SIGSTOP else pids):
        try:
            os.kill(pid, sig)
        except ProcessLookupError:
            pass
    if sig != signal.SIGSTOP:
        return
    # SIGSTOP is asynchronous: do not start the next seat's clock until every
    # task of this seat is actually stopped (or gone).
    deadline = time.monotonic() + settle_seconds
    while time.monotonic() < deadline:
        if all(_proc_state(pid) in (None, 'T', 't', 'Z', 'X') for pid in pids):
            return
        time.sleep(0.001)
    raise RuntimeError('seat process tree did not stop')


class _TreeProcess:
    """Shared pause/resume/close for launchers without a cgroup freezer."""

    sandbox = 'none'

    def __init__(self, process: subprocess.Popen, work: tempfile.TemporaryDirectory | None, *, job: int = 0):
        self.process = process
        self.work = work
        self._job = job
        self.input_capacity_bytes = 0
        self.closed = False

    def pause(self) -> None:
        if not self.closed and self.process.poll() is None:
            if os.name == 'nt':
                pids = _win_job_pids(self._job) if self._job else tree_pids(self.process.pid)
                _win_suspend_or_resume(list(reversed(pids)), suspend=True)
                deadline = time.monotonic() + 5.0
                while time.monotonic() < deadline:
                    if all(_proc_state(pid) in (None, 'T') for pid in pids):
                        return
                    time.sleep(0.001)
                raise RuntimeError('seat process tree did not stop')
            _signal_tree(self.process.pid, signal.SIGSTOP)

    def resume(self) -> None:
        if not self.closed and self.process.poll() is None:
            if os.name == 'nt':
                pids = _win_job_pids(self._job) if self._job else tree_pids(self.process.pid)
                _win_suspend_or_resume(pids, suspend=False)
                return
            _signal_tree(self.process.pid, signal.SIGCONT)

    def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        if os.name == 'nt':
            if self._job:
                _kernel32.TerminateJobObject(ctypes.c_void_p(self._job), 1)
            elif self.process.poll() is None:
                for pid in tree_pids(self.process.pid):
                    handle = _kernel32.OpenProcess(_PROCESS_TERMINATE, False, pid)
                    if handle:
                        _kernel32.TerminateProcess(ctypes.c_void_p(handle), 1)
                        _kernel32.CloseHandle(ctypes.c_void_p(handle))
            try:
                self.process.wait(timeout=10)
                if self._job:
                    deadline = time.monotonic() + 5.0
                    while time.monotonic() < deadline and _win_job_pids(self._job):
                        time.sleep(0.005)
            finally:
                if self._job:
                    _kernel32.CloseHandle(ctypes.c_void_p(self._job))
                    self._job = 0
                for stream in (self.process.stdin, self.process.stdout, self.process.stderr):
                    if stream is not None:
                        stream.close()
                if self.work is not None:
                    deadline = time.monotonic() + 2.0
                    while True:
                        try:
                            self.work.cleanup()
                            break
                        except PermissionError as exc:
                            # The last Windows process can still hold the cwd during kernel teardown.
                            if exc.winerror not in (5, 32) or time.monotonic() >= deadline:
                                raise
                            time.sleep(0.02)
            return
        if self.process.poll() is None:
            pids = tree_pids(self.process.pid)
            _signal_tree(self.process.pid, signal.SIGCONT)
            for pid in pids:
                try:
                    os.kill(pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
        try:
            self.process.wait(timeout=10)
        finally:
            for stream in (self.process.stdin, self.process.stdout, self.process.stderr):
                if stream is not None:
                    stream.close()
            if self.work is not None:
                self.work.cleanup()


def _limit_preexec(memory: int, fsize: int, nofile: int):
    def apply():
        import resource
        os.setsid()
        try:  # die with the match runner even if it is SIGKILLed by a watchdog (Linux PR_SET_PDEATHSIG)
            import ctypes
            ctypes.CDLL(None, use_errno=True).prctl(1, signal.SIGKILL, 0, 0, 0)
        except (OSError, AttributeError):
            pass
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
        resource.setrlimit(resource.RLIMIT_NOFILE, (nofile, nofile))
        resource.setrlimit(resource.RLIMIT_FSIZE, (fsize, fsize))
        if memory:
            resource.setrlimit(resource.RLIMIT_AS, (memory, memory))
    return apply


class PlainProcess(_TreeProcess):
    """Unsandboxed seat (fallback). Same flags as the sandbox entry; rlimits only."""

    sandbox = 'none'

    def __init__(self, submission: Path, *, memory_limit: int = MEMORY_LIMIT_BYTES):
        work = tempfile.TemporaryDirectory(prefix='rift-seat-')
        argv = [sys.executable, '-S', '-u', '-c', _ENTRY, str(Path(submission).resolve()),
                str(memory_limit), str(FILE_SIZE_LIMIT_BYTES), str(OPEN_FILES_LIMIT)]
        if os.name == 'nt':
            job = _win_create_job(memory_limit)
            try:
                process = subprocess.Popen(
                    argv, cwd=work.name, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                    bufsize=0, env=_seat_env(os.environ.get('PATH', ''), work.name), creationflags=_CREATE_SUSPENDED)
                if not _kernel32.AssignProcessToJobObject(ctypes.c_void_p(job), ctypes.c_void_p(int(process._handle))):
                    err = ctypes.get_last_error()
                    process.kill()
                    process.wait(timeout=5)
                    raise OSError(err, 'AssignProcessToJobObject failed')
                _ntdll.NtResumeProcess(ctypes.c_void_p(int(process._handle)))
            except Exception:
                _kernel32.CloseHandle(ctypes.c_void_p(job))
                work.cleanup()
                raise
            super().__init__(process, work, job=job)
        else:
            process = subprocess.Popen(
                argv, cwd=work.name, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                bufsize=0, env=_seat_env(os.environ.get('PATH', '/usr/bin:/bin')),
                preexec_fn=_limit_preexec(0, FILE_SIZE_LIMIT_BYTES, OPEN_FILES_LIMIT))
            super().__init__(process, work)


if os.name == 'nt':
    class _WinProcess:
        """Subprocess-like wrapper around a Win32 process created via CreateProcessW."""

        def __init__(self, pi: _PROCESS_INFORMATION, sin, sout, serr):
            self._pi = pi
            self.pid = int(pi.dwProcessId)
            self.stdin = sin
            self.stdout = sout
            self.stderr = serr
            self._handle = int(pi.hProcess)
            self._returncode: int | None = None

        def poll(self) -> int | None:
            if self._returncode is not None:
                return self._returncode
            code = wintypes.DWORD()
            if not _kernel32.GetExitCodeProcess(ctypes.c_void_p(self._handle), ctypes.byref(code)):
                return None
            if code.value == _STILL_ACTIVE:
                if _kernel32.WaitForSingleObject(ctypes.c_void_p(self._handle), 0) != 0:
                    return None
            self._returncode = int(code.value)
            return self._returncode

        def wait(self, timeout: float | None = None) -> int:
            if self._returncode is not None:
                return self._returncode
            ms = 0xFFFFFFFF if timeout is None else max(0, int(timeout * 1000))
            res = _kernel32.WaitForSingleObject(ctypes.c_void_p(self._handle), ms)
            if res == 0x00000102:  # WAIT_TIMEOUT
                raise subprocess.TimeoutExpired(str(self.pid), timeout)
            code = wintypes.DWORD()
            _kernel32.GetExitCodeProcess(ctypes.c_void_p(self._handle), ctypes.byref(code))
            self._returncode = int(code.value)
            return self._returncode

        def terminate(self) -> None:
            _kernel32.TerminateProcess(ctypes.c_void_p(self._handle), 1)

        def kill(self) -> None:
            _kernel32.TerminateProcess(ctypes.c_void_p(self._handle), 1)

        def close_handles(self) -> None:
            if getattr(self._pi, 'hProcess', None):
                _kernel32.CloseHandle(ctypes.c_void_p(int(self._pi.hProcess)))
                self._pi.hProcess = None
            if getattr(self._pi, 'hThread', None):
                _kernel32.CloseHandle(ctypes.c_void_p(int(self._pi.hThread)))
                self._pi.hThread = None


class WindowsSeatProcess(_TreeProcess):
    """Windows dual-layer sandboxed seat: per-seat AppContainer + Job Object for
    untrusted candidate seats, Job Object with -I -S for trusted reference seats."""

    sandbox = 'windows_job_isolated'

    def __init__(self, submission: Path, *, memory_limit: int = MEMORY_LIMIT_BYTES,
                 sandbox_type: str = 'windows_appcontainer_job',
                 require_appcontainer: bool | None = None,
                 max_work_bytes: int = MAX_WORK_BYTES):
        if os.name != 'nt':
            raise NotImplementedError('WindowsSeatProcess is only supported on Windows')
        from .match_runner import bots_root
        sub_path = Path(submission).resolve()
        try:
            b_root = bots_root().resolve()
            trusted = (sub_path == b_root or b_root in sub_path.parents)
        except Exception:
            trusted = False

        self.trusted = trusted
        self.require_appcontainer = (
            (sandbox_type == 'windows_appcontainer_job')
            if require_appcontainer is None else bool(require_appcontainer)
        )
        self.max_work_bytes = int(max_work_bytes)
        self._quota_exceeded = False
        self._profile_name: str | None = None
        self._is_win_proc: bool = False

        seat_temp = Path(tempfile.mkdtemp(prefix='rift-seat-'))
        work_dir = seat_temp / 'work'
        work_dir.mkdir()
        self._seat_temp: Path | None = seat_temp
        self._work_dir: Path | None = work_dir

        if trusted or sandbox_type == 'windows_job_isolated':
            self.sandbox = 'windows_job_isolated'
            argv = [sys.executable, '-I', '-S', '-u', '-c', _ENTRY, str(sub_path),
                    str(memory_limit), str(FILE_SIZE_LIMIT_BYTES), str(OPEN_FILES_LIMIT)]
            job = _win_create_job(memory_limit, active_process_limit=64)
            try:
                process = subprocess.Popen(
                    argv, cwd=str(work_dir), stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                    bufsize=0, env=_win_clean_env(str(work_dir)), creationflags=_CREATE_SUSPENDED)
                if not _kernel32.AssignProcessToJobObject(job, int(process._handle)):
                    err = ctypes.get_last_error()
                    process.kill()
                    process.wait(timeout=5)
                    raise OSError(err, 'AssignProcessToJobObject failed')
                _ntdll.NtResumeProcess(int(process._handle))
            except Exception:
                _kernel32.CloseHandle(job)
                _rmtree_force(seat_temp)
                self._seat_temp = None
                self._work_dir = None
                raise
            super().__init__(process, None, job=job)
            return

        # Untrusted candidate seat: private read-only copy + unique per-seat AppContainer SID
        bot_dir = seat_temp / 'bot'
        appcontainer_err: Exception | None = None
        job = 0
        profile_name: str | None = None
        proc = None
        sid = ctypes.c_void_p()
        p_attr = None
        attr_initialized = False
        hChildIn = hParentIn = hParentOut = hChildOut = hParentErr = hChildErr = None
        pi = _PROCESS_INFORMATION()
        try:
            shutil.copytree(sub_path, bot_dir, ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
            for root_d, dirs, files in os.walk(bot_dir):
                for d in dirs:
                    try:
                        os.chmod(os.path.join(root_d, d), 0o700)
                    except OSError:
                        pass
                for f in files:
                    try:
                        os.chmod(os.path.join(root_d, f), 0o600)
                    except OSError:
                        pass

            profile_name = f'avl_rift_{os.getpid()}_{next(_PROFILE_SEQ)}_{secrets.token_hex(4)}'
            _userenv.DeleteAppContainerProfile(profile_name)
            hr = _userenv.CreateAppContainerProfile(profile_name, profile_name, profile_name, None, 0, ctypes.byref(sid))
            if hr != 0 or not sid.value:
                raise OSError(f'CreateAppContainerProfile failed (hr=0x{hr & 0xffffffff:08x})')

            str_ptr = ctypes.c_void_p()
            if not _advapi32.ConvertSidToStringSidW(sid, ctypes.byref(str_ptr)) or not str_ptr.value:
                raise OSError(ctypes.get_last_error(), 'ConvertSidToStringSidW failed')
            try:
                sid_str = ctypes.wstring_at(str_ptr.value)
            finally:
                _kernel32.LocalFree(str_ptr)

            # Grant ONLY this seat's unique AppContainer SID; never grant *S-1-15-2-1
            # and never touch ACLs on the caller's original submission_root.
            for target_dir, perm in ((work_dir, '(OI)(CI)(F)'), (bot_dir, '(OI)(CI)(RX)')):
                res = subprocess.run(
                    ['icacls', str(target_dir), '/grant', f'*{sid_str}:{perm}'],
                    capture_output=True, text=True, timeout=15,
                )
                if res.returncode != 0:
                    raise RuntimeError(f'icacls failed for {target_dir}: {(res.stderr or res.stdout).strip()[:300]}')

            size = ctypes.c_size_t(0)
            _kernel32.InitializeProcThreadAttributeList(None, 1, 0, ctypes.byref(size))
            if not size.value:
                raise OSError(ctypes.get_last_error(), 'InitializeProcThreadAttributeList size query failed')
            attr_buf = (ctypes.c_char * size.value)()
            p_attr = ctypes.cast(attr_buf, ctypes.c_void_p)
            if not _kernel32.InitializeProcThreadAttributeList(p_attr, 1, 0, ctypes.byref(size)):
                raise OSError(ctypes.get_last_error(), 'InitializeProcThreadAttributeList failed')
            attr_initialized = True

            sec_caps = _SECURITY_CAPABILITIES()
            sec_caps.AppContainerSid = sid
            sec_caps.Capabilities = None
            sec_caps.CapabilityCount = 0
            sec_caps.Reserved = 0
            if not _kernel32.UpdateProcThreadAttribute(
                p_attr, 0, _PROC_THREAD_ATTRIBUTE_SECURITY_CAPABILITIES,
                ctypes.byref(sec_caps), ctypes.sizeof(sec_caps), None, None
            ):
                raise OSError(ctypes.get_last_error(), 'UpdateProcThreadAttribute failed')

            sa = _SECURITY_ATTRIBUTES()
            sa.nLength = ctypes.sizeof(_SECURITY_ATTRIBUTES)
            sa.bInheritHandle = True
            sa.lpSecurityDescriptor = None

            hChildIn, hParentIn = wintypes.HANDLE(), wintypes.HANDLE()
            if not _kernel32.CreatePipe(ctypes.byref(hChildIn), ctypes.byref(hParentIn), ctypes.byref(sa), 0):
                raise OSError(ctypes.get_last_error(), 'CreatePipe(stdin) failed')
            if not _kernel32.SetHandleInformation(hParentIn, _HANDLE_FLAG_INHERIT, 0):
                raise OSError(ctypes.get_last_error(), 'SetHandleInformation(stdin) failed')

            hParentOut, hChildOut = wintypes.HANDLE(), wintypes.HANDLE()
            if not _kernel32.CreatePipe(ctypes.byref(hParentOut), ctypes.byref(hChildOut), ctypes.byref(sa), 0):
                raise OSError(ctypes.get_last_error(), 'CreatePipe(stdout) failed')
            if not _kernel32.SetHandleInformation(hParentOut, _HANDLE_FLAG_INHERIT, 0):
                raise OSError(ctypes.get_last_error(), 'SetHandleInformation(stdout) failed')

            hParentErr, hChildErr = wintypes.HANDLE(), wintypes.HANDLE()
            if not _kernel32.CreatePipe(ctypes.byref(hParentErr), ctypes.byref(hChildErr), ctypes.byref(sa), 0):
                raise OSError(ctypes.get_last_error(), 'CreatePipe(stderr) failed')
            if not _kernel32.SetHandleInformation(hParentErr, _HANDLE_FLAG_INHERIT, 0):
                raise OSError(ctypes.get_last_error(), 'SetHandleInformation(stderr) failed')

            si = _STARTUPINFOEXW()
            si.StartupInfo.cb = ctypes.sizeof(_STARTUPINFOEXW)
            si.StartupInfo.dwFlags = _STARTF_USESTDHANDLES
            si.StartupInfo.hStdInput = hChildIn.value
            si.StartupInfo.hStdOutput = hChildOut.value
            si.StartupInfo.hStdError = hChildErr.value
            si.lpAttributeList = p_attr

            argv = [sys.executable, '-I', '-S', '-u', '-c', _ENTRY, str(bot_dir),
                    str(memory_limit), str(FILE_SIZE_LIMIT_BYTES), str(OPEN_FILES_LIMIT)]
            cmd_buf = ctypes.create_unicode_buffer(subprocess.list2cmdline(argv))
            env_buf = _win_env_block(_win_clean_env(str(work_dir)))

            created = _kernel32.CreateProcessW(
                None, cmd_buf, None, None, True,
                _EXTENDED_STARTUPINFO_PRESENT | _CREATE_UNICODE_ENVIRONMENT | _CREATE_SUSPENDED,
                env_buf, str(work_dir), ctypes.byref(si.StartupInfo), ctypes.byref(pi)
            )
            if not created:
                raise OSError(ctypes.get_last_error(), 'CreateProcessW(AppContainer) failed')

            # Child inherited its pipe ends; parent closes its copies immediately.
            for h in (hChildIn, hChildOut, hChildErr):
                _kernel32.CloseHandle(h)
            hChildIn = hChildOut = hChildErr = None

            job = _win_create_job(memory_limit, active_process_limit=64)
            if not _kernel32.AssignProcessToJobObject(job, pi.hProcess):
                err = ctypes.get_last_error()
                _kernel32.TerminateProcess(pi.hProcess, 1)
                raise OSError(err, 'AssignProcessToJobObject failed')
            _ntdll.NtResumeProcess(pi.hProcess)

            fd_in = msvcrt.open_osfhandle(hParentIn.value, os.O_WRONLY)
            hParentIn = None
            sin = open(fd_in, 'wb', buffering=0)
            fd_out = msvcrt.open_osfhandle(hParentOut.value, os.O_RDONLY)
            hParentOut = None
            sout = open(fd_out, 'rb', buffering=0)
            fd_err = msvcrt.open_osfhandle(hParentErr.value, os.O_RDONLY)
            hParentErr = None
            serr = open(fd_err, 'rb', buffering=0)

            proc = _WinProcess(pi, sin, sout, serr)
            self._is_win_proc = True
            self._profile_name = profile_name
            self.sandbox = 'windows_appcontainer_job'
        except Exception as exc:
            appcontainer_err = exc
            for h in (hChildIn, hParentIn, hChildOut, hParentOut, hChildErr, hParentErr):
                if h and h.value:
                    _kernel32.CloseHandle(h)
            if pi.hProcess:
                _kernel32.TerminateProcess(pi.hProcess, 1)
                _kernel32.CloseHandle(pi.hProcess)
            if pi.hThread:
                _kernel32.CloseHandle(pi.hThread)
            if job:
                _kernel32.CloseHandle(job)
                job = 0
            if profile_name:
                try:
                    _userenv.DeleteAppContainerProfile(profile_name)
                except Exception:
                    pass
                profile_name = None
        finally:
            if attr_initialized and p_attr is not None:
                _kernel32.DeleteProcThreadAttributeList(p_attr)
            if sid.value:
                _advapi32.FreeSid(sid)

        if proc is not None:
            super().__init__(proc, None, job=job)
            return

        if self.require_appcontainer:
            _rmtree_force(seat_temp)
            self._seat_temp = None
            self._work_dir = None
            raise RuntimeError(f'Windows AppContainer isolation required but failed: {appcontainer_err}') from appcontainer_err

        # Explicit non-AppContainer fallback (only when require_appcontainer=False)
        self.sandbox = 'windows_job_isolated'
        argv = [sys.executable, '-I', '-S', '-u', '-c', _ENTRY, str(sub_path),
                str(memory_limit), str(FILE_SIZE_LIMIT_BYTES), str(OPEN_FILES_LIMIT)]
        job = _win_create_job(memory_limit, active_process_limit=64)
        try:
            process = subprocess.Popen(
                argv, cwd=str(work_dir), stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                bufsize=0, env=_win_clean_env(str(work_dir)), creationflags=_CREATE_SUSPENDED)
            if not _kernel32.AssignProcessToJobObject(job, int(process._handle)):
                err = ctypes.get_last_error()
                process.kill()
                process.wait(timeout=5)
                raise OSError(err, 'AssignProcessToJobObject failed')
            _ntdll.NtResumeProcess(int(process._handle))
        except Exception:
            _kernel32.CloseHandle(job)
            _rmtree_force(seat_temp)
            self._seat_temp = None
            self._work_dir = None
            raise
        super().__init__(process, None, job=job)

    def check_disk_quota(self) -> bool:
        """Enforce writable work-directory quota (64 MiB); terminate seat if exceeded."""
        if self._quota_exceeded:
            return False
        if self._work_dir is not None and _dir_bytes(self._work_dir) > self.max_work_bytes:
            self._quota_exceeded = True
            if self._job:
                _kernel32.TerminateJobObject(self._job, 1)
            elif self.process.poll() is None:
                self.process.kill()
            return False
        return True

    def pause(self) -> None:
        super().pause()
        self.check_disk_quota()

    def close(self) -> None:
        try:
            super().close()
        finally:
            if self._is_win_proc and hasattr(self.process, 'close_handles'):
                self.process.close_handles()
            if self._profile_name:
                try:
                    _userenv.DeleteAppContainerProfile(self._profile_name)
                except Exception:
                    pass
                self._profile_name = None
            if self._seat_temp is not None:
                _rmtree_force(self._seat_temp)
                self._seat_temp = None
                self._work_dir = None


@dataclass(frozen=True)
class BwrapMounts:
    python: Path
    stdlib: Path
    libraries: tuple[Path, ...]


def bwrap_mounts() -> BwrapMounts:
    """Trusted interpreter, stdlib (without site-packages) and its shared libraries."""
    executable = Path(sys.executable).resolve()
    stdlib = Path(sysconfig.get_path('stdlib')).resolve()
    libraries: set[Path] = set()
    for binary in [executable, *sorted((stdlib / 'lib-dynload').glob('*.so'))]:
        result = subprocess.run(['ldd', str(binary)], capture_output=True, text=True, timeout=10,
                                env={'PATH': '/usr/bin:/bin', 'LC_ALL': 'C'})
        if result.returncode:
            continue  # statically linked or not an ELF the loader lists
        for line in result.stdout.splitlines():
            parts = line.strip().split()
            path = parts[2] if len(parts) > 2 and parts[1] == '=>' else parts[0] if parts else ''
            if path.startswith('/'):
                libraries.add(Path(path))
    return BwrapMounts(executable, stdlib, tuple(sorted(libraries)))


def _bwrap_host_path(path: Path, *, resolve: bool = False) -> str:
    p = Path(path)
    if resolve and (os.name != 'nt' or p.drive or not p.root):
        p = p.resolve()
    return p.as_posix()


def bwrap_command(submission: Path, entry: Path, mounts: BwrapMounts, *, bwrap: str = 'bwrap',
                  memory_limit: int = MEMORY_LIMIT_BYTES) -> list[str]:
    """The exact argv used for a bwrap seat (pure function; unit-tested with mocks)."""
    py = _bwrap_host_path(mounts.python)
    stdlib = _bwrap_host_path(mounts.stdlib)
    args = [bwrap, '--unshare-user', '--unshare-pid', '--unshare-net', '--unshare-ipc', '--unshare-uts',
            '--die-with-parent', '--new-session', '--cap-drop', 'ALL', '--clearenv',
            '--setenv', 'PATH', _bwrap_host_path(mounts.python.parent), '--setenv', 'PYTHONHASHSEED', '0',
            '--setenv', 'PYTHONDONTWRITEBYTECODE', '1', '--setenv', 'LC_ALL', 'C',
            '--ro-bind', py, py,
            '--ro-bind', stdlib, stdlib]
    for library in mounts.libraries:
        lib = _bwrap_host_path(library)
        args += ['--ro-bind', lib, lib]
    for excluded in ('site-packages', 'dist-packages'):
        if (mounts.stdlib / excluded).exists():
            args += ['--tmpfs', _bwrap_host_path(mounts.stdlib / excluded)]
    args += ['--ro-bind', _bwrap_host_path(submission, resolve=True), '/submission',
             '--ro-bind', _bwrap_host_path(entry), '/trusted/entry.py',
             '--proc', '/proc', '--dev', '/dev',
             '--size', str(WORK_TMPFS_BYTES), '--tmpfs', '/work', '--dir', '/work/tmp',
             '--symlink', '/work/tmp', '/tmp', '--chdir', '/work',
             py, '-S', '-u', '/trusted/entry.py', '/submission',
             str(memory_limit), str(FILE_SIZE_LIMIT_BYTES), str(OPEN_FILES_LIMIT)]
    return args


class BwrapProcess(_TreeProcess):
    """bubblewrap seat without cgroup: namespaces + ro mounts + rlimits."""

    sandbox = 'bwrap'

    def __init__(self, submission: Path, *, bwrap: str = 'bwrap', mounts: BwrapMounts | None = None,
                 memory_limit: int = MEMORY_LIMIT_BYTES, popen=subprocess.Popen):
        work = tempfile.TemporaryDirectory(prefix='rift-bwrap-')
        entry = Path(work.name) / 'entry.py'
        entry.write_text(_ENTRY, encoding='utf-8')
        argv = bwrap_command(submission, entry, mounts or bwrap_mounts(), bwrap=bwrap, memory_limit=memory_limit)
        self.argv = argv
        process = popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, bufsize=0,
                        env={'PATH': '/usr/bin:/bin', 'LC_ALL': 'C'}, start_new_session=True)
        super().__init__(process, work)


def bwrap_works(bwrap: str) -> tuple[bool, str]:
    """Probe that unprivileged user namespaces actually work on this host."""
    try:
        result = subprocess.run([bwrap, '--unshare-user', '--unshare-pid', '--unshare-net', '--die-with-parent',
                                 '--ro-bind', '/', '/', '/bin/true'], capture_output=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, f'{type(exc).__name__}: {exc}'
    if result.returncode:
        return False, result.stderr.decode('utf-8', 'replace').strip()[:300]
    return True, ''


@dataclass
class Launcher:
    """What the match runner needs: a factory and an honest label."""
    sandbox: str                       # 'cgroup' | 'bwrap' | 'none'
    reason: str
    start: object = None               # callable(submission_dir) -> ManagedProcess, or None for cgroup
    cgroup_sandbox: object = None      # engine Sandbox when sandbox == 'cgroup'

    def describe(self) -> dict:
        return {'sandbox': self.sandbox, 'reason': self.reason}


def select_launcher(mode: str = 'auto', *, cgroup: Path | None = None, which=shutil.which,
                    probe=bwrap_works, win_probe=None) -> Launcher:
    """Choose a transport. ``auto``: bwrap or Windows native sandbox if present and functional, else none (labelled)."""
    if mode not in SANDBOX_MODES:
        raise ValueError(f'sandbox mode must be one of {SANDBOX_MODES}')
    if mode == 'cgroup' or cgroup is not None:
        if cgroup is None:
            raise ValueError('--sandbox cgroup needs --cgroup <delegated cgroup v2 dir>')
        from ..engine.sandbox import Sandbox, runtime_mounts
        sandbox = Sandbox(Path(cgroup))
        sandbox.preflight()
        sandbox.mounts = runtime_mounts()
        return Launcher('cgroup', f'engine Sandbox on {cgroup}', None, sandbox)
    if mode == 'none':
        return Launcher('none', 'requested --sandbox none', PlainProcess)

    if mode == 'windows':
        if win_probe is None:
            try:
                import submission_sandbox
            except ImportError:
                from organizer import submission_sandbox
            win_probe = submission_sandbox.probe_windows_sandbox
        sbx = win_probe()
        if sbx:
            req_ac = (sbx == 'windows_appcontainer_job')
            return Launcher(sbx, f'Windows native dual-layer sandbox ({sbx})',
                            lambda sub, _sbx=sbx, _req=req_ac: WindowsSeatProcess(
                                sub, sandbox_type=_sbx, require_appcontainer=_req))
        raise RuntimeError('--sandbox windows requested but Windows sandbox is unavailable')

    path = which('bwrap')
    if path is not None:
        ok, why = probe(path)
        if ok:
            mounts = bwrap_mounts()
            return Launcher('bwrap', f'bubblewrap at {path} (no cgroup quota)',
                            lambda submission: BwrapProcess(submission, bwrap=path, mounts=mounts))
        if mode == 'bwrap':
            raise RuntimeError(f'bubblewrap present but unusable: {why}')
    elif mode == 'bwrap':
        raise RuntimeError('--sandbox bwrap requested but bubblewrap is not installed')

    if os.name == 'nt':
        if win_probe is None:
            try:
                import submission_sandbox
            except ImportError:
                from organizer import submission_sandbox
            win_probe = submission_sandbox.probe_windows_sandbox
        sbx = win_probe()
        if sbx:
            req_ac = (sbx == 'windows_appcontainer_job')
            return Launcher(sbx, f'Windows native dual-layer sandbox ({sbx})',
                            lambda sub, _sbx=sbx, _req=req_ac: WindowsSeatProcess(
                                sub, sandbox_type=_sbx, require_appcontainer=_req))
        return Launcher('none', 'Windows sandbox unavailable; unsandboxed fallback', PlainProcess)

    return Launcher('none', 'bubblewrap not installed; unsandboxed fallback', PlainProcess)
