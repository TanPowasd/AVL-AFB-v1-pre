"""Pipeline launchers: unsandboxed fallback (real processes) and bwrap path (mocked)."""

import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

from organizer.rift_era.pipeline import hosting
from organizer.rift_era.pipeline.hosting import (BwrapMounts, BwrapProcess, PlainProcess, bwrap_command,
                                                 select_launcher, tree_pids)

PROBE_MAIN = '''import os, sys
line = sys.stdin.readline()
if os.name == 'nt':
    import ctypes
    from ctypes import wintypes
    class _Io(ctypes.Structure):
        _fields_ = [(n, ctypes.c_ulonglong) for n in ('a', 'b', 'c', 'd', 'e', 'f')]
    class _Basic(ctypes.Structure):
        _fields_ = [('u', ctypes.c_longlong), ('j', ctypes.c_longlong), ('flags', wintypes.DWORD),
                    ('minw', ctypes.c_size_t), ('maxw', ctypes.c_size_t), ('ap', wintypes.DWORD),
                    ('aff', ctypes.c_size_t), ('pri', wintypes.DWORD), ('sch', wintypes.DWORD)]
    class _Ext(ctypes.Structure):
        _fields_ = [('basic', _Basic), ('io', _Io), ('pmem', ctypes.c_size_t), ('jmem', ctypes.c_size_t),
                    ('ppmem', ctypes.c_size_t), ('pjmem', ctypes.c_size_t)]
    info = _Ext()
    ctypes.WinDLL('kernel32').QueryInformationJobObject(None, 9, ctypes.byref(info), ctypes.sizeof(info), None)
    soft, nofile = info.pmem, 128
else:
    import resource
    soft = resource.getrlimit(resource.RLIMIT_AS)[0]
    nofile = resource.getrlimit(resource.RLIMIT_NOFILE)[0]
print(line.strip(), soft, nofile, sys.path[0], flush=True)
sys.stdin.readline()
'''


def _state(pid: int):  # None once the process is gone
    return hosting._proc_state(pid)


class FallbackTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.root = Path(self.dir.name)
        (self.root / 'main.py').write_text(PROBE_MAIN)

    def tearDown(self):
        self.dir.cleanup()

    def test_auto_without_bwrap_labels_none(self):
        launcher = select_launcher('auto', which=lambda name: None, win_probe=lambda: "")
        self.assertEqual(launcher.sandbox, 'none')
        self.assertIn('not installed' if os.name != 'nt' else 'unavailable', launcher.reason)
        self.assertIs(launcher.start, PlainProcess)

    def test_auto_with_broken_bwrap_falls_back(self):
        launcher = select_launcher('auto', which=lambda name: '/usr/bin/bwrap',
                                   probe=lambda path: (False, 'No permissions to create new namespace'),
                                   win_probe=lambda: "")
        self.assertEqual(launcher.sandbox, 'none')
        self.assertIn('unusable' if os.name != 'nt' else 'unavailable', launcher.reason)

    def test_auto_on_windows_selects_windows_sandbox(self):
        if os.name == 'nt':
            launcher = select_launcher('auto')
            self.assertIn(launcher.sandbox, ('windows_appcontainer_job', 'windows_job_isolated'))
            self.assertIn('Windows native', launcher.reason)

    def test_windows_mode_with_mock_probe(self):
        if os.name == 'nt':
            launcher = select_launcher('windows', win_probe=lambda: 'windows_appcontainer_job')
            self.assertEqual(launcher.sandbox, 'windows_appcontainer_job')
            launcher_job = select_launcher('windows', win_probe=lambda: 'windows_job_isolated')
            self.assertEqual(launcher_job.sandbox, 'windows_job_isolated')
            with self.assertRaises(RuntimeError):
                select_launcher('windows', win_probe=lambda: '')

    @unittest.skipUnless(os.name == 'nt', 'Windows only')
    def test_windows_seat_process(self):
        from organizer.rift_era.pipeline.hosting import WindowsSeatProcess
        with tempfile.TemporaryDirectory() as tmp:
            bot_dir = Path(tmp)
            (bot_dir / 'main.py').write_text(
                'import sys\nline = sys.stdin.readline()\nprint(line.strip(), sys.path[0], flush=True)\nsys.stdin.readline()\n'
            )
            seat = WindowsSeatProcess(bot_dir)
            try:
                self.assertIn(seat.sandbox, ('windows_appcontainer_job', 'windows_job_isolated'))
                pid = seat.process.pid
                seat.pause()
                self.assertEqual(_state(pid), 'T')
                seat.resume()
                self.assertNotEqual(_state(pid), 'T')
                seat.process.stdin.write(b'hello\n')
                seat.process.stdin.flush()
                words = seat.process.stdout.readline().decode().rstrip('\r\n').split(None, 1)
                self.assertEqual(words[0], 'hello')
                if seat.sandbox == 'windows_appcontainer_job':
                    self.assertNotEqual(Path(words[1]).resolve(), bot_dir.resolve())
                    self.assertTrue((Path(words[1]) / 'main.py').is_file())
                else:
                    self.assertEqual(Path(words[1]).resolve(), bot_dir.resolve())
            finally:
                seat.close()
            self.assertIsNotNone(seat.process.poll())

    def test_explicit_bwrap_never_silently_degrades(self):
        with self.assertRaises(RuntimeError):
            select_launcher('bwrap', which=lambda name: None)
        with self.assertRaises(RuntimeError):
            select_launcher('bwrap', which=lambda name: '/x/bwrap', probe=lambda path: (False, 'denied'))

    def test_cgroup_mode_requires_directory(self):
        with self.assertRaises(ValueError):
            select_launcher('cgroup')

    def test_plain_process_runs_main_with_limits_and_pause(self):
        seat = PlainProcess(self.root)
        try:
            pid = seat.process.pid
            seat.pause()
            self.assertEqual(_state(pid), 'T')
            seat.resume()
            self.assertNotEqual(_state(pid), 'T')
            seat.process.stdin.write(b'hello\n')
            os.set_blocking(seat.process.stdout.fileno(), True)
            words = seat.process.stdout.readline().decode().rstrip('\r\n').split(None, 3)
            self.assertEqual(words[0], 'hello')
            self.assertEqual(int(words[1]), hosting.MEMORY_LIMIT_BYTES)
            self.assertEqual(int(words[2]), hosting.OPEN_FILES_LIMIT)
            self.assertEqual(Path(words[3]).resolve(), self.root.resolve())
        finally:
            seat.close()
        self.assertIsNotNone(seat.process.poll())

    @unittest.skipUnless(os.name == 'nt', 'Windows sharing violation')
    def test_close_retries_work_dir_cleanup(self):
        seat = PlainProcess(self.root)
        work = Path(seat.work.name)
        cleanup = seat.work.cleanup
        with mock.patch.object(seat.work, 'cleanup', side_effect=[PermissionError(13, 'busy', str(work), 32), None]) as mocked:
            seat.close()
        self.assertEqual(mocked.call_count, 2)
        cleanup()
        self.assertFalse(work.exists())

    def test_close_kills_descendants(self):
        (self.root / 'main.py').write_text(
            'import subprocess, sys, time\n'
            'child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])\n'
            'print(child.pid, flush=True)\n'
            'time.sleep(60)\n')
        seat = PlainProcess(self.root)
        try:
            os.set_blocking(seat.process.stdout.fileno(), True)
            child = int(seat.process.stdout.readline())
            self.assertIn(child, tree_pids(seat.process.pid))
            seat.pause()
            self.assertEqual(_state(child), 'T')
        finally:
            seat.close()
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if _state(child) in ('Z', None):
                break
            time.sleep(0.05)
        else:
            os.kill(child, signal.SIGTERM if os.name == 'nt' else signal.SIGKILL)
            self.fail('descendant survived close()')


class BwrapMockTest(unittest.TestCase):
    MOUNTS = BwrapMounts(Path('/opt/py/bin/python3.12'), Path('/opt/py/lib/python3.12'),
                         (Path('/lib/libc.so.6'), Path('/lib/libm.so.6')))

    def test_command_isolates_and_mounts_read_only(self):
        argv = bwrap_command(Path('/srv/sub'), Path('/tmp/e/entry.py'), self.MOUNTS, bwrap='/usr/bin/bwrap')
        self.assertEqual(argv[0], '/usr/bin/bwrap')
        for flag in ('--unshare-user', '--unshare-pid', '--unshare-net', '--unshare-ipc', '--die-with-parent',
                     '--clearenv', '--new-session'):
            self.assertIn(flag, argv)
        pairs = list(zip(argv, argv[1:], argv[2:]))
        self.assertIn(('--ro-bind', '/srv/sub', '/submission'), pairs)
        self.assertIn(('--ro-bind', '/tmp/e/entry.py', '/trusted/entry.py'), pairs)
        self.assertIn(('--ro-bind', '/lib/libc.so.6', '/lib/libc.so.6'), pairs)
        self.assertNotIn('--bind', argv)  # nothing writable from the host
        self.assertIn(('--size', str(hosting.WORK_TMPFS_BYTES), '--tmpfs'), pairs)
        tail = argv[argv.index('/trusted/entry.py', argv.index('--chdir')):]
        self.assertEqual(tail[:2], ['/trusted/entry.py', '/submission'])
        self.assertEqual(int(tail[2]), hosting.MEMORY_LIMIT_BYTES)

    def test_select_and_launch_bwrap_with_mocked_binary(self):
        calls = []

        class FakePopen:
            def __init__(self, argv, **kwargs):
                calls.append((argv, kwargs))
                self.pid = 999999
                self.stdin = self.stdout = self.stderr = None

            def poll(self):
                return 0

            def wait(self, timeout=None):
                return 0

        with mock.patch.object(hosting, 'bwrap_mounts', return_value=self.MOUNTS):
            launcher = select_launcher('auto', which=lambda name: '/usr/bin/bwrap', probe=lambda p: (True, ''))
        self.assertEqual(launcher.sandbox, 'bwrap')
        self.assertEqual(launcher.describe()['sandbox'], 'bwrap')
        with mock.patch.object(hosting, 'BwrapProcess',
                               side_effect=lambda sub, **kw: BwrapProcess(sub, popen=FakePopen, **kw)):
            seat = launcher.start(Path('/srv/sub'))
        argv, kwargs = calls[0]
        self.assertEqual(argv[0], '/usr/bin/bwrap')
        self.assertTrue(kwargs['start_new_session'])
        self.assertEqual(kwargs['env'], {'PATH': '/usr/bin:/bin', 'LC_ALL': 'C'})
        self.assertEqual(seat.sandbox, 'bwrap')
        entry = Path(argv[argv.index('/trusted/entry.py') - 1])
        self.assertIn('RLIMIT_AS', entry.read_text())
        seat.close()
        self.assertFalse(entry.exists())  # private work dir cleaned

    def test_probe_reports_namespace_failure(self):
        failed = subprocess.CompletedProcess([], 1, b'', b'bwrap: No permissions to create new namespace')
        with mock.patch.object(hosting.subprocess, 'run', return_value=failed):
            ok, why = hosting.bwrap_works('/usr/bin/bwrap')
        self.assertFalse(ok)
        self.assertIn('namespace', why)



SLOW_BOT = """import sys, time
sys.path.insert(0, sys.argv[0].rsplit('/', 1)[0])
from sdk import Client
c = Client()
c.receive()
c.ready()
while True:
    m = c.receive()
    if m.get('type') == 'end':
        break
    time.sleep(0.25)
    c.orders(m['window'], [])
"""


class TimeBudgetOnFallbackTest(unittest.TestCase):
    """The engine's StrategyClock rules hold on the pipeline launcher (scaled-down budget)."""

    def test_timeouts_drain_pool_then_custody(self):
        from fractions import Fraction as Q
        from organizer.rift_era.engine.evaluation import StrategyClock, TimeBudget
        from organizer.rift_era.engine.ipc import StrategySession
        rift = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / 'main.py').write_text(SLOW_BOT)
            (root / 'sdk.py').write_bytes((rift / 'sdk.py').read_bytes())
            budget = TimeBudget(base_window_seconds=Q(1, 20), maximum_window_seconds=Q(1, 10),
                                initial_pool_seconds=Q(1, 10), total_seconds=Q(10))
            session = StrategySession(PlainProcess(root), clock=StrategyClock(budget))
            try:
                ready = session.initialize({'type': 'init', 'owner': 'n0'})
                self.assertEqual(ready.status, 'ready')
                statuses = []
                for window in range(4):
                    result = session.decide({'type': 'obs', 'window': window, 'tick': 5 * window})
                    statuses.append(result.status)
                    if result.custodial:
                        break
            finally:
                session.close()
        # window 0: allowance .05+.1 → .1 cap → timeout drains the .1 s pool → custody
        self.assertEqual(statuses[0], 'timeout')
        self.assertTrue(session.clock.custodial)
        self.assertEqual(session.clock.pool_seconds, 0)
        self.assertGreaterEqual(session.clock.timeouts, 1)


class OrphanTest(unittest.TestCase):
    def test_seat_dies_with_its_launcher(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / 'main.py').write_text('import time\ntime.sleep(60)\n')
            code = ('import sys, time; sys.path.insert(0, sys.argv[1]);'
                    'from organizer.rift_era.pipeline.hosting import PlainProcess;'
                    'from pathlib import Path; seat = PlainProcess(Path(sys.argv[2]));'
                    'print(seat.process.pid, flush=True); time.sleep(60)')
            repo = Path(__file__).resolve().parents[3]
            launcher = subprocess.Popen([sys.executable, '-c', code, str(repo), str(root)], stdout=subprocess.PIPE)
            seat_pid = int(launcher.stdout.readline())
            launcher.kill()
            launcher.wait()
            launcher.stdout.close()
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline and _state(seat_pid) not in ('Z', None):
                time.sleep(0.05)
            self.assertIn(_state(seat_pid), ('Z', None))




if __name__ == '__main__':
    unittest.main()
