"""Bounded, nonblocking strategy JSONL sessions over a managed kernel sandbox.

Only already-filtered observations enter this module. It has no world state,
seed, score collector or access to other seats. The production constructor always
uses Sandbox.start; raw processes are accepted only by the transport component
so its pipe behavior can be tested independently of host cgroup delegation.
"""

from __future__ import annotations

from array import array
from collections import deque
from dataclasses import dataclass
import os
from pathlib import Path
import selectors
import time
from typing import Protocol
if os.name == 'nt':
    import ctypes
    import errno
    import msvcrt
else:
    import fcntl
    import termios

from .determinism import canonical
from .errors import InfrastructureFault, RuleViolation
from .evaluation import StrategyClock
from .numeric import Q, floor
from .protocol import Framer, MAX_LINE, MAX_SNAPSHOT, WindowReader, decode_json, paginate
from .sandbox import Sandbox


class ManagedProcess(Protocol):
    process: object
    def pause(self) -> None: ...
    def resume(self) -> None: ...
    def close(self) -> None: ...


@dataclass(frozen=True)
class TransportLimits:
    initialization_seconds: Q = Q(15)
    delivery_seconds: Q = Q(30)
    stderr_bytes: int = 4 * 1024**2

    def __post_init__(self):
        if self.initialization_seconds <= 0 or self.delivery_seconds <= 0 or not 0 < self.stderr_bytes <= 4 * 1024**2:
            raise ValueError('invalid transport limits')


@dataclass(frozen=True)
class Decision:
    window: int | None
    orders: tuple[dict, ...]
    status: str
    errors: tuple[str, ...]
    think_ns: int
    delivery_ns: int
    custodial: bool


class StderrTail:
    """Private diagnostic tail. Never attach it to another seat or a replay."""

    def __init__(self, limit: int):
        self.limit, self.size, self.dropped = limit, 0, 0
        self.chunks = deque()

    def append(self, chunk: bytes) -> None:
        self.chunks.append(chunk)
        self.size += len(chunk)
        while self.size > self.limit:
            oldest = self.chunks.popleft()
            remove = min(len(oldest), self.size - self.limit)
            self.size -= remove
            self.dropped += remove
            if remove < len(oldest):
                self.chunks.appendleft(oldest[remove:])

    def read(self) -> bytes:
        return b''.join(self.chunks)


def downstream_pages(message: dict) -> tuple[bytes, ...]:
    raw = canonical(message)
    if len(raw) > MAX_SNAPSHOT:
        raise InfrastructureFault('filtered snapshot exceeds 16MiB')
    pages = (raw,) if len(raw) <= MAX_LINE else paginate(message)
    return tuple(page + b'\n' for page in pages)


if os.name == 'nt':
    from ctypes import wintypes  # noqa: F401
    _ntdll = ctypes.WinDLL('ntdll')
    _kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
    _FILE_PIPE_CONNECTED_STATE = 3
    _TIMER_HIGH_RES = 0x00000002
    _TIMER_ALL_ACCESS = 0x001F0003

    class _IoStatusBlock(ctypes.Structure):
        _fields_ = [('Status', ctypes.c_void_p), ('Information', ctypes.c_size_t)]

    class _FilePipeLocalInfo(ctypes.Structure):
        _fields_ = [
            ('NamedPipeType', ctypes.c_ulong),
            ('NamedPipeConfiguration', ctypes.c_ulong),
            ('MaximumInstances', ctypes.c_ulong),
            ('CurrentInstances', ctypes.c_ulong),
            ('InboundQuota', ctypes.c_ulong),
            ('ReadDataAvailable', ctypes.c_ulong),
            ('OutboundQuota', ctypes.c_ulong),
            ('WriteQuotaAvailable', ctypes.c_ulong),
            ('NamedPipeState', ctypes.c_ulong),
            ('NamedPipeEnd', ctypes.c_ulong),
        ]

    def _win_pipe_info(fd: int) -> _FilePipeLocalInfo:
        handle = msvcrt.get_osfhandle(fd)
        iosb = _IoStatusBlock()
        info = _FilePipeLocalInfo()
        status = _ntdll.NtQueryInformationFile(
            ctypes.c_void_p(handle), ctypes.byref(iosb), ctypes.byref(info), ctypes.sizeof(info), 24
        ) & 0xffffffff
        if status != 0:
            raise OSError(errno.EBADF, f'NtQueryInformationFile status 0x{status:08x}')
        return info

    class _WinPipeSelector:
        """Nonblocking pipe readiness poller matching selectors.DefaultSelector on Windows."""

        def __init__(self):
            self._keys: dict[int, selectors.SelectorKey] = {}
            self._blocked_write: dict[int, int] = {}
            self._timer = _kernel32.CreateWaitableTimerExW(None, None, _TIMER_HIGH_RES, _TIMER_ALL_ACCESS)

        def close(self) -> None:
            self._keys.clear()
            self._blocked_write.clear()
            if self._timer:
                _kernel32.CloseHandle(ctypes.c_void_p(self._timer))
                self._timer = 0

        def __enter__(self):
            return self

        def __exit__(self, *args):
            self.close()

        def _sleep(self, seconds: float) -> None:
            if seconds <= 0:
                return
            if self._timer:
                due = ctypes.c_longlong(-max(1, int(seconds * 10_000_000)))
                if _kernel32.SetWaitableTimer(ctypes.c_void_p(self._timer), ctypes.byref(due), 0, None, None, False):
                    _kernel32.WaitForSingleObject(ctypes.c_void_p(self._timer), max(1, int(seconds * 1000) + 5))
                    return
            time.sleep(seconds)

        def register(self, fileobj: int, events: int, data=None) -> selectors.SelectorKey:
            fd = fileobj if isinstance(fileobj, int) else fileobj.fileno()
            key = selectors.SelectorKey(fileobj=fileobj, fd=fd, events=events, data=data)
            self._keys[fd] = key
            self._blocked_write.pop(fd, None)
            return key

        def unregister(self, fileobj: int) -> selectors.SelectorKey:
            fd = fileobj if isinstance(fileobj, int) else fileobj.fileno()
            self._blocked_write.pop(fd, None)
            return self._keys.pop(fd)

        def note_write_blocked(self, fd: int) -> None:
            try:
                self._blocked_write[fd] = int(_win_pipe_info(fd).WriteQuotaAvailable)
            except OSError:
                self._blocked_write.pop(fd, None)

        def _poll_once(self) -> list[tuple[selectors.SelectorKey, int]]:
            ready: list[tuple[selectors.SelectorKey, int]] = []
            for key in list(self._keys.values()):
                try:
                    info = _win_pipe_info(key.fd)
                except OSError:
                    self._blocked_write.pop(key.fd, None)
                    ready.append((key, key.events))
                    continue
                mask = 0
                if key.events & selectors.EVENT_READ:
                    if info.ReadDataAvailable > 0 or info.NamedPipeState != _FILE_PIPE_CONNECTED_STATE:
                        mask |= selectors.EVENT_READ
                if key.events & selectors.EVENT_WRITE:
                    blocked_quota = self._blocked_write.get(key.fd)
                    if info.NamedPipeState != _FILE_PIPE_CONNECTED_STATE:
                        self._blocked_write.pop(key.fd, None)
                        mask |= selectors.EVENT_WRITE
                    elif blocked_quota is None or int(info.WriteQuotaAvailable) > blocked_quota:
                        self._blocked_write.pop(key.fd, None)
                        mask |= selectors.EVENT_WRITE
                if mask:
                    ready.append((key, mask))
            return ready

        def select(self, timeout: float | None = None) -> list[tuple[selectors.SelectorKey, int]]:
            ready = self._poll_once()
            if ready or (timeout is not None and timeout <= 0):
                return ready
            deadline = None if timeout is None else time.monotonic() + timeout
            while True:
                remaining = 0.0005 if deadline is None else deadline - time.monotonic()
                if remaining <= 0:
                    self._blocked_write.clear()
                    return self._poll_once()
                self._sleep(min(remaining, 0.0005))
                self._blocked_write.clear()
                ready = self._poll_once()
                if ready:
                    return ready

    _PipeSelector = _WinPipeSelector
else:
    _PipeSelector = selectors.DefaultSelector


def _write_pipe(fd: int, data) -> int:
    try:
        return os.write(fd, data)
    except (BlockingIOError, BrokenPipeError):
        raise
    except OSError as exc:
        if os.name == 'nt' and exc.errno == errno.EINVAL:
            raise BrokenPipeError(errno.EPIPE, 'pipe closed') from exc
        raise


def _queued_input(fd: int) -> int:
    if os.name == 'nt':
        try:
            info = _win_pipe_info(fd)
        except OSError as exc:
            raise InfrastructureFault('cannot attest strategy input delivery') from exc
        return max(0, int(info.OutboundQuota) - int(info.WriteQuotaAvailable))
    # Linux exposes unread pipe bytes on either endpoint. Unlike successful
    # write(), zero here proves that the complete frame left the kernel pipe.
    count = array('i', [0])
    try:
        fcntl.ioctl(fd, termios.FIONREAD, count, True)
    except OSError as exc:
        raise InfrastructureFault('cannot attest strategy input delivery') from exc
    return count[0]


def input_pipe_capacity(clock: StrategyClock) -> int:
    """Bound outstanding unread frames by the actual timeout/time-pool rules.

    An accepted response must have drained its input. An unread frame can only
    survive a full timeout. With the default clock, custody starts at timeout 16.
    Twice the logical snapshot limit covers base64 and every paging envelope.
    Capacity reserves pipe slots, not resident pages for nonexistent snapshots.
    """
    probe = StrategyClock(clock.budget)
    count = 0
    while not probe.custodial:
        if probe.allowance <= 0:
            break
        probe.charge(max(1, floor(probe.allowance * 10**9)), timed_out=True)
        count += 1
    required = max(1, count) * 2 * MAX_SNAPSHOT
    return 1 << (required - 1).bit_length()


class StrategySession:
    def __init__(self, managed: ManagedProcess, *, clock: StrategyClock | None = None,
                 limits: TransportLimits = TransportLimits()):
        self.managed, self.process = managed, managed.process
        self.clock = clock or StrategyClock()
        self.limits = limits
        self.framer = Framer()
        self.stderr = StderrTail(limits.stderr_bytes)
        self.initialized = False
        self.next_window = 0
        self.closed = False
        self._win_max_unread = 0
        for stream in (self.process.stdin, self.process.stdout, self.process.stderr):
            if stream is None:
                raise InfrastructureFault('strategy transport requires three pipes')
            os.set_blocking(stream.fileno(), False)
        self.managed.pause()

    def _record_write(self, fd: int, count: int) -> None:
        if os.name != 'nt' or count <= 0:
            return
        try:
            info = _win_pipe_info(fd)
        except OSError:
            self._win_max_unread = 0
            return
        if info.NamedPipeState != _FILE_PIPE_CONNECTED_STATE:
            self._win_max_unread = 0
            return
        raw_used = max(0, int(info.OutboundQuota) - int(info.WriteQuotaAvailable))
        self._win_max_unread = min(self._win_max_unread + count, raw_used)

    def _unread_input(self, fd: int) -> int:
        if os.name != 'nt':
            return _queued_input(fd)
        try:
            info = _win_pipe_info(fd)
        except OSError as exc:
            raise InfrastructureFault('cannot attest strategy input delivery') from exc
        if info.NamedPipeState != _FILE_PIPE_CONNECTED_STATE:
            self._win_max_unread = 0
            return 0
        raw_used = max(0, int(info.OutboundQuota) - int(info.WriteQuotaAvailable))
        if raw_used == 0 or self._win_max_unread == 0 or raw_used > self._win_max_unread:
            self._win_max_unread = 0
            return 0
        self._win_max_unread = raw_used
        return raw_used

    @classmethod
    def launch(cls, sandbox: Sandbox, submission: Path, **kwargs) -> StrategySession:
        budget = kwargs.get('clock') or StrategyClock()
        managed = sandbox.start(submission, start_paused=True, input_capacity_bytes=input_pipe_capacity(budget))
        try:
            return cls(managed, **kwargs)
        except Exception:
            managed.close()
            raise

    def initialize(self, message: dict) -> Decision:
        if self.initialized or self.closed or message.get('type') != 'init':
            raise InfrastructureFault('invalid strategy initialization lifecycle')
        result = self._exchange(message, None, self.limits.initialization_seconds)
        self.initialized = True
        self.clock.charge_initialization(result.think_ns, failed=result.status != 'ready')
        return self._result_with_custody(result)

    def decide(self, observation: dict) -> Decision:
        window = observation.get('window')
        if (not self.initialized or self.closed or observation.get('type') != 'obs'
                or type(window) is not int or window != self.next_window):
            raise InfrastructureFault('out-of-order strategy window')
        self.next_window += 1
        if self.clock.custodial:
            return Decision(window, (), 'custodial', (), 0, 0, True)
        result = self._exchange(observation, window, self.clock.allowance)
        self.clock.charge(result.think_ns, timed_out=result.status == 'timeout',
                          crashed=result.status in ('crashed', 'delivery_failed'))
        return self._result_with_custody(result)

    def _result_with_custody(self, result):
        return Decision(result.window, result.orders, result.status, result.errors,
                        result.think_ns, result.delivery_ns, self.clock.custodial)

    def finish(self, message: dict) -> None:
        if not self.initialized or self.closed or message.get('type') != 'end':
            raise InfrastructureFault('invalid strategy end lifecycle')
        try:
            if not self.clock.custodial:
                self._exchange(message, None, Q(0), expect_response=False)
        finally:
            self.close()

    def _exchange(self, message: dict, window: int | None, allowance: Q, *, expect_response: bool = True) -> Decision:
        pages = downstream_pages(message)  # referee failures precede resume
        reader = WindowReader(window) if window is not None else None
        incoming = self.process.stdin.fileno()
        outgoing = self.process.stdout.fileno()
        diagnostics = self.process.stderr.fileno()
        allowance_ns = max(0, floor(allowance * 10**9))
        delivery_started = time.monotonic_ns()
        delivery_deadline = delivery_started + floor(self.limits.delivery_seconds * 10**9)
        think_started = None
        delivery_ns = 0
        page, offset = 0, 0
        errors, early = [], []
        status, orders, think_ns = 'crashed', (), 0
        buffered = bool(getattr(self.managed, 'input_capacity_bytes', 0))
        deferred_invalid = False
        if buffered:
            if self._unread_input(incoming) + sum(map(len, pages)) > self.managed.input_capacity_bytes:
                raise InfrastructureFault('strategy input backlog exceeded its proved timeout bound')
            # Fill the complete snapshot while ALL contestant threads are
            # frozen. No precomputation can hide inside uncharged page delivery.
            try:
                for encoded in pages:
                    cursor = 0
                    while cursor < len(encoded):
                        wrote = _write_pipe(incoming, memoryview(encoded)[cursor:])
                        self._record_write(incoming, wrote)
                        cursor += wrote
            except BrokenPipeError:
                return Decision(window, (), 'crashed', ('STRATEGY_EXIT',), 0,
                                time.monotonic_ns() - delivery_started, False)
            except BlockingIOError as exc:
                raise InfrastructureFault('reserved strategy pipe could not stage a complete snapshot') from exc
            page = len(pages)
        self.managed.resume()
        if buffered:
            think_started = time.monotonic_ns()
            delivery_ns = think_started - delivery_started
        try:
            with _PipeSelector() as selector:
                if not buffered:
                    selector.register(incoming, selectors.EVENT_WRITE, 'in')
                selector.register(outgoing, selectors.EVENT_READ, 'out')
                selector.register(diagnostics, selectors.EVENT_READ, 'err')
                while True:
                    now = time.monotonic_ns()
                    if not buffered and os.name == 'nt' and page == len(pages) and early:
                        self._win_max_unread = 0
                    if think_started is None and page == len(pages) and self._unread_input(incoming) == 0:
                        think_started = now
                        delivery_ns = now - delivery_started
                        if not expect_response:
                            status = 'ended'
                            break
                    input_drained = not buffered or self._unread_input(incoming) == 0
                    if not expect_response and page == len(pages) and input_drained:
                        status, think_ns = 'ended', 0
                        break
                    deadline = delivery_deadline if think_started is None or not expect_response else think_started + allowance_ns
                    if now >= deadline:
                        status = 'delivery_failed' if think_started is None else 'timeout'
                        errors.append('INPUT_DELIVERY_TIMEOUT' if think_started is None else 'ERR_TIMEOUT')
                        delivery_ns = now - delivery_started if think_started is None else delivery_ns
                        think_ns = 0 if think_started is None else now - think_started
                        self.framer.timeout()
                        break
                    if deferred_invalid and input_drained:
                        status, think_ns = 'invalid', now - think_started
                        break
                    if think_started is not None and early and input_drained:
                        try:
                            for raw in early:
                                if reader is None:
                                    if status == 'ready':
                                        raise RuleViolation('ERR_PIPE_FLOOD')
                                    if decode_json(raw) != {'type': 'ready'}:
                                        raise RuleViolation('ERR_SCHEMA')
                                    status = 'ready'
                                else:
                                    frame = reader.accept(raw)
                                    if frame is None:
                                        errors.append('ERR_STALE_WINDOW')
                                    else:
                                        status, orders = 'orders', tuple(frame['orders'])
                            early.clear()
                            if status in ('orders', 'ready'):
                                think_ns = now - think_started
                                break
                        except RuleViolation as exc:
                            errors.append(exc.code)
                            status, orders, think_ns = 'invalid', (), now - think_started
                            break
                    # After the last write, poll delivery acknowledgement as
                    # well as stdout/stderr. No busy loop on a writable pipe.
                    timeout = (deadline - now) / 1e9
                    if think_started is None and page == len(pages):
                        timeout = min(timeout, .001)
                    if buffered and not input_drained and (early or deferred_invalid):
                        timeout = min(timeout, .001)
                    events = selector.select(timeout)
                    for key, _ in events:
                        if key.data == 'in':
                            try:
                                count = _write_pipe(incoming, memoryview(pages[page])[offset:offset + 65536])
                            except BlockingIOError:
                                if hasattr(selector, 'note_write_blocked'):
                                    selector.note_write_blocked(incoming)
                                continue
                            except BrokenPipeError:
                                status = 'crashed'
                                break
                            self._record_write(incoming, count)
                            offset += count
                            if offset == len(pages[page]):
                                page, offset = page + 1, 0
                                if page == len(pages):
                                    selector.unregister(incoming)
                            continue
                        try:
                            chunk = os.read(key.fd, 65536)
                        except BlockingIOError:
                            continue
                        if not chunk:
                            selector.unregister(key.fd)
                            if key.data == 'out':
                                status = ('ended' if not expect_response and page == len(pages)
                                          and self._unread_input(incoming) == 0 else 'crashed')
                                break
                            continue
                        if key.data == 'err':
                            self.stderr.append(chunk)
                            continue
                        lines, framing_errors = self.framer.feed(chunk)
                        early.extend(lines)
                        if framing_errors or len(early) > 17 or sum(map(len, early)) > 2 * MAX_LINE:
                            errors.extend(framing_errors or ['ERR_PIPE_FLOOD'])
                            if buffered and self._unread_input(incoming):
                                # The program has not consumed the snapshot.
                                # Keep charging the window until it does or its
                                # timeout expires; guessing an early bad frame
                                # cannot accumulate unbounded unread input.
                                deferred_invalid = True
                                early.clear()
                                selector.unregister(outgoing)
                                continue
                            status, orders = 'invalid', ()
                            break
                    else:
                        continue
                    # Broken pipes, EOF, or framing failures end this exchange.
                    ended = time.monotonic_ns()
                    delivery_ns = ended - delivery_started if think_started is None else delivery_ns
                    think_ns = ended - think_started if think_started is not None and expect_response else 0
                    break
        finally:
            self.managed.pause()
            quota_check = getattr(self.managed, 'check_disk_quota', None)
            if callable(quota_check) and not quota_check():
                status, orders = 'crashed', ()
                if 'ERR_DISK_QUOTA' not in errors:
                    errors.append('ERR_DISK_QUOTA')
            if status in ('orders', 'ready') and self._has_extra_stdout():
                status, orders = 'invalid', ()
                errors.append('ERR_PIPE_FLOOD')
            if status in ('invalid', 'crashed', 'delivery_failed') or deferred_invalid:
                self._discard_readable_stdout()
            self._drain_stderr()
        if status == 'crashed':
            errors.append('STRATEGY_EXIT')
        if status == 'invalid' and think_started is None:
            # Delivery was interrupted: the next observation cannot be appended
            # after an incomplete frame. End this process, preserving its plans.
            status = 'delivery_failed'
        return Decision(window, orders, status, tuple(errors), think_ns, delivery_ns, False)

    def _has_extra_stdout(self) -> bool:
        extra = bool(self.framer.buffer)
        while True:
            try:
                chunk = os.read(self.process.stdout.fileno(), 65536)
            except BlockingIOError:
                return extra
            if not chunk:
                return extra
            extra = True
            self.framer.feed(chunk)

    def _discard_readable_stdout(self):
        # All strategy threads are frozen, so draining the finite kernel pipe
        # cannot become an unbounded read loop against a flooding writer.
        self.framer.timeout()
        while True:
            try:
                chunk = os.read(self.process.stdout.fileno(), 65536)
            except BlockingIOError:
                return
            if not chunk:
                return
            self.framer.discard_until_newline = not chunk.endswith(b'\n')

    def _drain_stderr(self):
        while True:
            try:
                chunk = os.read(self.process.stderr.fileno(), 65536)
            except BlockingIOError:
                return
            if not chunk:
                return
            self.stderr.append(chunk)

    def close(self):
        if not self.closed:
            self.managed.close()
            self.closed = True

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
