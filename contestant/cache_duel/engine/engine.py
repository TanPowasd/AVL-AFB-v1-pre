"""cache_duel 对战引擎：一局 = 一个 attacker bot 对一个 defender bot。

协议见 README.md。每个 bot 是一个 Python 文件，
以子进程运行，stdin/stdout 每行一个 UTF-8 JSON。
"""
from __future__ import annotations

import hashlib
import json
import os
import queue
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field

STEP_TIMEOUT = 1.0      # 单步硬超时（秒）
TIME_POOL = 5.0         # 时间池（秒）：独立于单步，单步超出 1s 的部分从池扣，池空后超 1s 即超时
TOTAL_BUDGET = 20.0     # 每个 bot 每局总思考时间（秒）
MAX_LINE_BYTES = 65536  # 单行回复字节硬上限，防止恶意超大输出耗尽裁判内存
CLOSE_WAIT_S = 2.0      # 发送 end 后等待子进程及沙箱包装器优雅退出与清理的最大时长


class BotError(Exception):
    pass


class Bot:
    def __init__(self, path: str, init: dict):
        self.path = path
        self.used = 0.0
        self.pool = TIME_POOL
        self.dead = False
        self.faults = 0
        env = {
            **os.environ,
            "PYTHONHASHSEED": "0",
            "PYTHONUTF8": "1",
            "PYTHONIOENCODING": "utf-8",
        }
        self.proc = subprocess.Popen(
            [sys.executable, "-u", "-X", "utf8", os.path.abspath(path)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            bufsize=0,
            env=env,
            cwd=os.path.dirname(os.path.abspath(path)),
        )
        self.q: queue.Queue = queue.Queue(maxsize=4)
        self._reader_thread = threading.Thread(target=self._reader, daemon=True)
        self._reader_thread.start()
        hs = self.ask(init)  # 握手：bot 需回一行合法 JSON 对象（如 {}）
        if not self.dead and not isinstance(hs, dict):
            self.dead = True
            self.kill()

    def _put_event(self, kind: str, payload) -> bool:
        try:
            self.q.put((kind, payload), timeout=0.5)
            return True
        except queue.Full:
            return False

    def _reader(self):  # 跨平台（Windows 管道不支持 select），带单行字节硬限与 UTF-8 异常兜底
        stdout = self.proc.stdout
        if stdout is None:
            self._put_event("eof", None)
            return
        try:
            while True:
                raw = stdout.readline(MAX_LINE_BYTES + 1)
                if not raw:
                    break
                if len(raw) > MAX_LINE_BYTES and not raw.endswith(b"\n"):
                    self._put_event("oversize", None)
                    return
                try:
                    line = raw.decode("utf-8")
                except UnicodeDecodeError:
                    self._put_event("bad_utf8", None)
                    return
                if not self._put_event("line", line):
                    # 队列积压满：说明子进程无视协议狂刷输出
                    return
        except Exception:
            pass
        self._put_event("eof", None)

    def ask(self, msg: dict):
        if self.dead:
            return None
        try:
            # 发消息前队列若已有未请求的滞留输出，说明子进程提前多行抢跑/刷屏，直接判死
            if not self.q.empty():
                raise BotError("desync")
            payload = (json.dumps(msg, separators=(",", ":")) + "\n").encode("utf-8")
            self.proc.stdin.write(payload)
            self.proc.stdin.flush()
            t0 = time.perf_counter()
            limit = min(STEP_TIMEOUT + self.pool, max(0.0, TOTAL_BUDGET - self.used))
            try:
                kind, line = self.q.get(timeout=limit)
            except queue.Empty:
                raise BotError("timeout")
            dt = time.perf_counter() - t0
            self.used += dt
            self.pool = max(0.0, self.pool - max(0.0, dt - STEP_TIMEOUT))
            if kind != "line" or not line:
                raise BotError(kind)
            return json.loads(line)
        except (BotError, OSError, ValueError, RecursionError):
            self.dead = True
            self.kill()
            return None

    def _close_pipes(self):
        for stream in (self.proc.stdin, self.proc.stdout):
            if stream is not None:
                try:
                    stream.close()
                except Exception:
                    pass

    def close(self, end_msg: dict | None = None):
        """对局结束时优雅通知并等待退出，保证沙箱与 AppContainer Profile 完整清理，不污染 dead 标记。"""
        try:
            if not self.dead and end_msg is not None and self.proc.stdin is not None:
                try:
                    payload = (json.dumps(end_msg, separators=(",", ":")) + "\n").encode("utf-8")
                    self.proc.stdin.write(payload)
                    self.proc.stdin.flush()
                except Exception:
                    pass
                try:
                    self.proc.stdin.close()
                except Exception:
                    pass
                try:
                    self.proc.wait(timeout=CLOSE_WAIT_S)
                    return
                except subprocess.TimeoutExpired:
                    pass
            self.kill()
        finally:
            self._close_pipes()

    def kill(self):
        try:
            if self.proc.poll() is None:
                self.proc.kill()
        except Exception:
            pass
        try:
            self.proc.wait(timeout=CLOSE_WAIT_S)
        except Exception:
            pass
        self._close_pipes()


def belady_misses(trace: list[int], cap: int) -> int:
    """离线最优（Belady/MIN）缺失数。"""
    import heapq
    if cap <= 0:
        return len(trace)
    n = len(trace)
    nxt = [0] * n
    last: dict[int, int] = {}
    for i in range(n - 1, -1, -1):
        nxt[i] = last.get(trace[i], n + i)  # 不再出现时给一个唯一的大值
        last[trace[i]] = i
    cache: dict[int, int] = {}  # key -> 下一次出现位置
    heap: list[tuple[int, int]] = []  # (-next, key)，惰性删除
    misses = 0
    for i, k in enumerate(trace):
        if k in cache:
            cache[k] = nxt[i]
            heapq.heappush(heap, (-nxt[i], k))
            continue
        misses += 1
        if len(cache) >= cap:
            while True:
                nn, kk = heapq.heappop(heap)
                if kk in cache and cache[kk] == -nn:
                    del cache[kk]
                    break
        cache[k] = nxt[i]
        heapq.heappush(heap, (-nxt[i], k))
    return misses


def role_seed(seed: int, role: str) -> int:
    """按角色派生种子，一方无法从自己收到的种子推出对方的随机流。"""
    h = hashlib.sha256(f"{seed}|{role}".encode()).hexdigest()
    return int(h[:8], 16)


def is_key(v) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


@dataclass
class MatchResult:
    attacker: str
    defender: str
    config: dict
    def_misses: int
    opt_misses: int
    def_score: float          # OPT缺失 / 防守方缺失 ∈ (0,1]，越大防守越好
    faults: dict = field(default_factory=dict)


def play(attacker_path: str, defender_path: str, cache_size: int, universe: int,
         length: int, seed: int) -> MatchResult:
    cfg = {"cache_size": cache_size, "universe": universe, "length": length, "seed": seed}
    att = Bot(attacker_path, {"role": "attacker", **cfg, "seed": role_seed(seed, "attacker")})
    dfd = Bot(defender_path, {"role": "defender", **cfg, "seed": role_seed(seed, "defender")})
    cache: set[int] = set()
    insert_order: list[int] = []  # 用于非法驱逐时的惩罚：驱逐最新放入的
    trace: list[int] = []
    misses = 0
    last_hit = None
    att_faults = def_faults = 0
    try:
        for t in range(length):
            a = att.ask({"t": t, "last_hit": last_hit, "time_pool": round(att.pool, 3)})
            k = a.get("key") if isinstance(a, dict) else None
            if not is_key(k) or not (0 <= k < universe):
                att_faults += 1
                k = 0  # 攻击方失误：退化为常量键 0，杜绝借助犯规白嫖循环扫描攻击
            trace.append(k)
            hit = k in cache
            need_evict = (not hit) and len(cache) >= cache_size
            d = dfd.ask({"t": t, "key": k, "hit": hit, "need_evict": need_evict, "time_pool": round(dfd.pool, 3)})
            if not hit:
                misses += 1
                if need_evict:
                    e = d.get("evict") if isinstance(d, dict) else None
                    if not is_key(e) or e not in cache:
                        def_faults += 1
                        e = insert_order[-1]  # 惩罚：驱逐最新放入的
                    cache.discard(e)
                    insert_order.remove(e)
                cache.add(k)
                insert_order.append(k)
            last_hit = hit
    finally:
        att.close({"t": length, "end": True})
        dfd.close({"t": length, "end": True})
    opt = belady_misses(trace, cache_size)
    return MatchResult(
        attacker=attacker_path, defender=defender_path, config=cfg,
        def_misses=misses, opt_misses=opt, def_score=opt / misses if misses else 1.0,
        faults={"attacker": att_faults, "defender": def_faults,
                "attacker_dead": att.dead, "defender_dead": dfd.dead},
    )
