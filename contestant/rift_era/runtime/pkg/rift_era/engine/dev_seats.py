"""Per-seat strategy selection for the development ``match``/``strategy-match`` CLI.

A seat is ``none`` (no orders), ``simple`` (the in-tree reference SimpleBot) or
an external submission: a directory holding ``main.py`` or a submission zip,
exactly the contestant format. External seats always go through the engine's
own ``StrategySession`` + ``MatchDriver`` pipeline (paging, window tags,
strategy clocks, private replay).

Transport: with a delegated cgroup the production ``Sandbox`` launches each
seat. Without one, ``DevProcess`` runs ``main.py`` as a plain child process
(same ``-S``/``PYTHONHASHSEED=0``/``sys.path`` layout as the sandbox entry) for
development only; such a run is never official.
"""

from __future__ import annotations

from contextlib import ExitStack, contextmanager
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile
import time
if os.name == 'nt':
    import ctypes
    from ctypes import wintypes

from .errors import InfrastructureFault

BUILTIN_SEATS = ('none', 'simple')
SEAT_COUNT = 8

_SIMPLE_MAIN = 'import sdk\nsdk.run_simple_bot()\n'
_DEV_ENTRY = ('import os, runpy, sys\n'
              'root = sys.argv[1]\n'
              'sys.path.insert(0, root)\n'
              'sys.argv = [os.path.join(root, "main.py")]\n'
              'runpy.run_path(sys.argv[0], run_name="__main__")\n')


def parse_seat(spec: str) -> str | Path:
    """``none``/``simple`` or an existing submission directory/zip path."""
    if spec in BUILTIN_SEATS:
        return spec
    path = Path(spec)
    if path.is_dir():
        if not (path / 'main.py').is_file():
            raise ValueError(f'bot directory has no main.py entry: {spec}')
        return path.resolve()
    if path.is_file() and path.suffix == '.zip':
        return path.resolve()
    raise ValueError(f'unknown bot {spec!r}: expected none, simple, a directory with main.py or a .zip')


def resolve_seats(bot: str | None = None, seats=None) -> tuple:
    """Eight seat specs. ``seats`` may be eight tokens or comma-separated text."""
    if seats:
        tokens = [t.strip() for item in seats for t in str(item).split(',') if t.strip()]
        if bot is not None and bot != 'none':
            raise ValueError('use either --bot or --seats, not both')
        if len(tokens) != SEAT_COUNT:
            raise ValueError(f'--seats needs exactly {SEAT_COUNT} entries (none, simple or a bot path)')
        return tuple(parse_seat(t) for t in tokens)
    return (parse_seat(bot or 'none'),) * SEAT_COUNT


def needs_driver(seats) -> bool:
    return any(isinstance(seat, Path) for seat in seats)


if os.name == 'nt':
    _kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
    _ntdll = ctypes.WinDLL('ntdll')
    _CREATE_SUSPENDED = 0x00000004
    _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
    _PROCESS_SUSPEND_RESUME = 0x0800

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

    def _win_create_job() -> int:
        job = _kernel32.CreateJobObjectW(None, None)
        if not job:
            raise OSError(ctypes.get_last_error(), 'CreateJobObjectW failed')
        info = _JobExtendedLimits()
        info.BasicLimitInformation.LimitFlags = _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
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

    def _win_suspend_or_resume(pids: list[int], *, suspend: bool) -> None:
        fn = _ntdll.NtSuspendProcess if suspend else _ntdll.NtResumeProcess
        for pid in pids:
            handle = _kernel32.OpenProcess(_PROCESS_SUSPEND_RESUME, False, pid)
            if handle:
                try:
                    fn(ctypes.c_void_p(handle))
                finally:
                    _kernel32.CloseHandle(ctypes.c_void_p(handle))


class DevProcess:
    """Unsandboxed child satisfying ``ipc.ManagedProcess`` (development only)."""

    def __init__(self, submission: Path):
        self.work = tempfile.TemporaryDirectory(prefix='rift-dev-seat-')
        env = {'PATH': os.environ.get('PATH', '/usr/bin:/bin'), 'PYTHONHASHSEED': '0',
               'PYTHONDONTWRITEBYTECODE': '1', 'LC_ALL': 'C'}
        self._job = 0
        if os.name == 'nt':
            if 'SYSTEMROOT' in os.environ:
                env['SYSTEMROOT'] = os.environ['SYSTEMROOT']
            env['TEMP'] = env['TMP'] = self.work.name
            self._job = _win_create_job()
            try:
                self.process = subprocess.Popen(
                    [sys.executable, '-S', '-u', '-c', _DEV_ENTRY, str(submission)],
                    cwd=self.work.name, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE, bufsize=0, env=env, creationflags=_CREATE_SUSPENDED)
                if not _kernel32.AssignProcessToJobObject(ctypes.c_void_p(self._job),
                                                          ctypes.c_void_p(int(self.process._handle))):
                    err = ctypes.get_last_error()
                    self.process.kill()
                    self.process.wait(timeout=5)
                    raise OSError(err, 'AssignProcessToJobObject failed')
                _ntdll.NtResumeProcess(ctypes.c_void_p(int(self.process._handle)))
            except Exception:
                _kernel32.CloseHandle(ctypes.c_void_p(self._job))
                self._job = 0
                self.work.cleanup()
                raise
        else:
            self.process = subprocess.Popen([sys.executable, '-S', '-u', '-c', _DEV_ENTRY, str(submission)],
                                            cwd=self.work.name, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                            stderr=subprocess.PIPE, bufsize=0, env=env)
        self.input_capacity_bytes = 0
        self.closed = False

    def pause(self):
        if self.process.poll() is None:
            if os.name == 'nt':
                if self._job:
                    _win_suspend_or_resume(list(reversed(_win_job_pids(self._job))), suspend=True)
                return
            try:
                os.kill(self.process.pid, signal.SIGSTOP)
                os.waitpid(self.process.pid, os.WUNTRACED)
            except (ProcessLookupError, ChildProcessError):
                pass

    def resume(self):
        if self.process.poll() is None:
            if os.name == 'nt':
                if self._job:
                    _win_suspend_or_resume(_win_job_pids(self._job), suspend=False)
                return
            try:
                os.kill(self.process.pid, signal.SIGCONT)
            except ProcessLookupError:
                pass

    def close(self):
        if self.closed:
            return
        self.closed = True
        if os.name == 'nt':
            if self._job:
                _kernel32.TerminateJobObject(ctypes.c_void_p(self._job), 1)
            elif self.process.poll() is None:
                self.process.kill()
            try:
                self.process.wait(timeout=5)
                if self._job:
                    deadline = time.monotonic() + 5
                    while time.monotonic() < deadline and _win_job_pids(self._job):
                        time.sleep(0.005)
            finally:
                if self._job:
                    _kernel32.CloseHandle(ctypes.c_void_p(self._job))
                    self._job = 0
                for stream in (self.process.stdin, self.process.stdout, self.process.stderr):
                    if stream is not None:
                        stream.close()
                self.work.cleanup()
            return
        if self.process.poll() is None:
            self.resume()
            self.process.kill()
        self.process.wait(timeout=5)
        for stream in (self.process.stdin, self.process.stdout, self.process.stderr):
            if stream is not None:
                stream.close()
        self.work.cleanup()


@contextmanager
def materialized(seat, stack: ExitStack):
    """Yield a directory with ``main.py`` for one non-``none`` seat."""
    if seat == 'simple':
        with tempfile.TemporaryDirectory(prefix='rift-simple-seat-') as temp:
            root = Path(temp)
            shutil.copyfile(Path(__file__).resolve().parent.parent / 'sdk.py', root / 'sdk.py')
            (root / 'main.py').write_text(_SIMPLE_MAIN, encoding='utf-8')
            yield root
        return
    if isinstance(seat, Path) and seat.suffix == '.zip' and seat.is_file():
        from .archive import extracted_submission
        with extracted_submission(seat) as root:
            yield root
        return
    if isinstance(seat, Path):
        yield seat
        return
    raise InfrastructureFault(f'seat {seat!r} has no submission')


def seat_label(seat) -> str:
    return seat if isinstance(seat, str) else str(seat)


def run_seated_match(engine, seats, *, replay: Path | None = None, launcher=None, official: bool = False,
                     manifest: dict | None = None, finalize=None) -> dict:
    """Drive the non-``none`` seats of ``engine`` through ``MatchDriver``.

    ``launcher`` is a preflighted ``Sandbox``; ``None`` selects ``DevProcess``
    (refused for an official run).
    """
    from .driver import MatchDriver, economic_development_init, economic_development_messages
    from .economy import Economy
    from .ipc import StrategySession
    from .match import MATCH_PROFILE, finalize_match
    if official and launcher is None:
        raise InfrastructureFault('an official run requires the kernel sandbox (--cgroup)')
    owners = sorted(engine.state.initial.known)
    if len(seats) != len(owners):
        raise InfrastructureFault('one seat specification per nation is required')
    economy = Economy(engine.state)
    transport = 'sandbox' if launcher is not None else 'unsandboxed_dev_pipe'
    with ExitStack() as stack:
        temp = stack.enter_context(tempfile.TemporaryDirectory(prefix='rift-seated-replay-'))
        sessions = {}
        for owner, seat in zip(owners, seats):
            if seat == 'none':
                continue
            root = stack.enter_context(materialized(seat, stack))
            if launcher is not None:
                session = StrategySession.launch(launcher, root)
            else:
                managed = DevProcess(root)
                try:
                    session = StrategySession(managed)
                except Exception:
                    managed.close()
                    raise
            sessions[owner] = stack.enter_context(session)
        if not sessions:
            raise InfrastructureFault('a seated match needs at least one strategy seat')
        driver = MatchDriver(engine, sessions,
            init_messages={p: economic_development_init(economy, p, engine.clock) | {'scope': MATCH_PROFILE}
                           for p in sessions},
            observe=lambda p, t: economic_development_messages(economy, p, t, engine.clock),
            finalize=finalize or (lambda: finalize_match(engine)),
            replay=replay or Path(temp) / 'replay.jsonl', official=official,
            manifest=(manifest or {'profile': MATCH_PROFILE, 'rules_hash': engine.state.catalog.fingerprint})
            | {'transport': transport})
        report = driver.run()
    return report | {'transport': transport,
                     'seats': {owner: seat_label(seat) for owner, seat in zip(owners, seats)}}
