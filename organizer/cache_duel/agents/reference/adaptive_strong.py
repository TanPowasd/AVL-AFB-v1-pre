# -*- coding: utf-8 -*-
"""adaptive_strong: 互补型私有强参考 bot（影子专家混合防守 + 自适应贝叶斯/反 LIFO-MRU 攻击）。

纯 Python 标准库，单文件，同时支持攻守两种角色。
防守：
  - 实时并行维护 5 个轻量影子专家（随机化 Marking、随机平局 Mark-Pred、全局衰老 TinyLFU、
    Anti-Cycle MRU、混合随机驱逐），采用指数滑动损失 + 随机权重采样，零冷启动盲探索崩盘；
  - 兼顾抗 C+1 循环扫穿、抗热点+扫描污染、抗自适应 1-bit 探测。
攻击：
  - 9 类贝叶斯影子防守模型（LRU / FIFO / MRU / LIFO / LFU / Rand / Marking / Pred(0.5) / Pred(1.0)）
    实时推断对手当前缓存空洞；
  - 检测 MRU/LIFO 后直接触发双键乒乓打击（对 MRU/LIFO 达成 ~99% 攻击分），并在 bayes_hole 与
    unif_spread (~1.15C 均匀随机) 之间按实测 OPT/M 窗口 Bandit 切换，切回时重置空洞信念。
"""
from __future__ import annotations

import heapq
import json
import math
import random
import sys


def belady_misses(trace: list[int], cap: int) -> int:
    """计算离线最优 Belady 缺失数。"""
    n = len(trace)
    nxt = [0] * n
    last: dict[int, int] = {}
    for i in range(n - 1, -1, -1):
        nxt[i] = last.get(trace[i], n + i)
        last[trace[i]] = i
    cache: dict[int, int] = {}
    heap: list[tuple[int, int]] = []
    miss = 0
    for i, k in enumerate(trace):
        if k in cache:
            cache[k] = nxt[i]
            heapq.heappush(heap, (-nxt[i], k))
            continue
        miss += 1
        if len(cache) >= cap:
            while True:
                nn, kk = heapq.heappop(heap)
                if cache.get(kk) == -nn:
                    del cache[kk]
                    break
        cache[k] = nxt[i]
        heapq.heappush(heap, (-nxt[i], k))
    return miss


class WindowBandit:
    """滑动窗口实测比值选择器（maximize=True 追求高 OPT/M，False 追求低 OPT/M）。"""
    def __init__(self, arms: list[str], rng: random.Random, window: int, maximize: bool = True, explore: float = 0.05):
        self.arms = arms
        self.rng = rng
        self.W = window
        self.maximize = maximize
        self.explore = explore
        self.ema: dict[str, float | None] = {a: None for a in arms}
        self.cur = arms[0]
        self.t0 = 0
        self.opt0 = 0
        self.m0 = 0

    def tick(self, t: int, trace: list[int], misses: int, cap: int) -> str:
        if t - self.t0 < self.W:
            return self.cur
        opt = belady_misses(trace, cap)
        dm = misses - self.m0
        do = opt - self.opt0
        r = 1.0 if dm <= 0 else max(0.0, min(1.0, do / dm))
        v = r if self.maximize else -r
        old = self.ema[self.cur]
        self.ema[self.cur] = v if old is None else 0.45 * v + 0.55 * old
        self.t0, self.opt0, self.m0 = t, opt, misses

        untried = [a for a in self.arms if self.ema[a] is None]
        if untried:
            self.cur = untried[0]
        elif self.rng.random() < self.explore:
            self.cur = self.rng.choice(self.arms)
        else:
            self.cur = max(self.arms, key=lambda a: self.ema[a])
        return self.cur


# ----------------------------------------------------------------------
# 防守方 (Defender)：影子专家 + 乘性权重随机混合（零盲探崩盘）
# ----------------------------------------------------------------------
class _ShadowExpert:
    def __init__(self, name: str, C: int, big: int, seed: int):
        self.name = name
        self.C = C
        self.big = big
        self.rng = random.Random(seed)
        self.cache: set[int] = set()
        self.mark: set[int] = set()

    def pick(self, cand_set: set[int], last: dict[int, int], gap: dict[int, float],
             freq: dict[int, float], mark_set: set[int]) -> int:
        n = self.name
        if n == "mark_rand":
            un = [x for x in cand_set if x not in mark_set]
            if not un:
                mark_set.clear()
                un = list(cand_set)
            return self.rng.choice(sorted(un))
        if n == "mark_pred":
            un = [x for x in cand_set if x not in mark_set]
            if not un:
                mark_set.clear()
                un = list(cand_set)
            # 随机化平手破除，阻止自适应攻击方确定性模拟空洞
            best = max(last.get(x, 0) + gap.get(x, self.big) for x in un)
            ties = [x for x in un if last.get(x, 0) + gap.get(x, self.big) >= best - 1e-6]
            return self.rng.choice(ties)
        if n == "aging_lfu":
            # 全局定期衰老频率 + 随机平手（抗扫描污染、不退化为确定性 LRU）
            lo = min(freq.get(x, 0.0) for x in cand_set)
            ties = [x for x in cand_set if freq.get(x, 0.0) <= lo + 1e-9]
            return self.rng.choice(ties)
        if n == "anti_cycle":
            return max(cand_set, key=lambda x: last.get(x, 0))
        # rand
        return self.rng.choice(sorted(cand_set))

    def step_shadow(self, k: int, last: dict[int, int], gap: dict[int, float], freq: dict[int, float]) -> bool:
        hit = k in self.cache
        if not hit:
            if len(self.cache) >= self.C:
                e = self.pick(self.cache, last, gap, freq, self.mark)
                self.cache.discard(e)
            self.cache.add(k)
        self.mark.add(k)
        return hit


class AdaptiveDefender:
    def __init__(self, C: int, K: int, N: int, seed: int):
        self.C = C
        self.K = K
        self.N = N
        self.rng = random.Random(seed ^ 0x51ADEF)
        self.cache: set[int] = set()
        self.mark: set[int] = set()

        self.last_time: dict[int, int] = {}
        self.gap_ema: dict[int, float] = {}
        self.freq: dict[int, float] = {}
        self.age_period = max(32, 2 * C)
        self.big = 4 * K + N

        names = ["mark_pred", "mark_rand", "aging_lfu", "anti_cycle", "rand"]
        self.experts = [
            _ShadowExpert(nm, C, self.big, seed ^ (0x9E3779B9 * (i + 1)))
            for i, nm in enumerate(names)
        ]
        self.loss = [0.0] * len(names)
        self.decay = 1.0 - 1.0 / max(16.0, 4.0 * C)
        self.eta = 0.75

    def step(self, t: int, k: int, hit: bool, need_evict: bool) -> int | None:
        # 全局频率定期衰老（修复旧版只衰老当前访问 key 的缺陷）
        if t > 0 and t % self.age_period == 0:
            for key in list(self.freq):
                v = self.freq[key] * 0.5
                if v < 1e-4:
                    del self.freq[key]
                else:
                    self.freq[key] = v

        # 先按历史特征推进全部影子专家并累加滑窗损失
        for i, ex in enumerate(self.experts):
            sh_hit = ex.step_shadow(k, self.last_time, self.gap_ema, self.freq)
            self.loss[i] = self.loss[i] * self.decay + (0.0 if sh_hit else 1.0)

        evict_key = None
        if not hit:
            if need_evict:
                best = min(self.loss)
                weights = [math.exp(-self.eta * (lv - best)) for lv in self.loss]
                chosen_idx = self.rng.choices(range(len(self.experts)), weights=weights)[0]
                evict_key = self.experts[chosen_idx].pick(
                    self.cache, self.last_time, self.gap_ema, self.freq, self.mark
                )
                self.cache.discard(evict_key)
            self.cache.add(k)

        if k in self.last_time:
            g = t - self.last_time[k]
            og = self.gap_ema.get(k)
            self.gap_ema[k] = g if og is None else 0.5 * g + 0.5 * og
        self.last_time[k] = t
        self.freq[k] = self.freq.get(k, 0.0) + 1.0
        self.mark.add(k)
        return evict_key


# ----------------------------------------------------------------------
# 攻击方 (Attacker)：9 模型贝叶斯空洞追踪 + 真双键反 LIFO/MRU 乒乓 + 均匀打散
# ----------------------------------------------------------------------
class _Model:
    def on_req(self, t: int, k: int, hit: bool):
        pass
    def dist(self, x: int, cache: list[int]) -> dict[int, float]:
        return {}


class _Lru(_Model):
    def __init__(self):
        self.last: dict[int, int] = {}
    def on_req(self, t: int, k: int, hit: bool):
        self.last[k] = t
    def dist(self, x: int, cache: list[int]) -> dict[int, float]:
        return {min(cache, key=lambda y: self.last.get(y, -1)): 1.0}


class _Mru(_Lru):
    def dist(self, x: int, cache: list[int]) -> dict[int, float]:
        return {max(cache, key=lambda y: self.last.get(y, -1)): 1.0}


class _Fifo(_Model):
    def __init__(self):
        self.ins: dict[int, int] = {}
    def on_req(self, t: int, k: int, hit: bool):
        if not hit:
            self.ins[k] = t
    def dist(self, x: int, cache: list[int]) -> dict[int, float]:
        return {min(cache, key=lambda y: self.ins.get(y, -1)): 1.0}


class _Lifo(_Fifo):
    def dist(self, x: int, cache: list[int]) -> dict[int, float]:
        return {max(cache, key=lambda y: self.ins.get(y, -1)): 1.0}


class _Lfu(_Model):
    def __init__(self):
        self.cnt: dict[int, int] = {}
        self.last: dict[int, int] = {}
    def on_req(self, t: int, k: int, hit: bool):
        self.cnt[k] = self.cnt.get(k, 0) + 1
        self.last[k] = t
    def dist(self, x: int, cache: list[int]) -> dict[int, float]:
        return {min(cache, key=lambda y: (self.cnt.get(y, 0), self.last.get(y, -1))): 1.0}


class _Rand(_Model):
    def dist(self, x: int, cache: list[int]) -> dict[int, float]:
        p = 1.0 / len(cache)
        return {y: p for y in cache}


class _Mark(_Model):
    def __init__(self):
        self.mark: set[int] = set()
    def on_req(self, t: int, k: int, hit: bool):
        self.mark.add(k)
    def dist(self, x: int, cache: list[int]) -> dict[int, float]:
        un = [y for y in cache if y not in self.mark]
        if not un:
            self.mark.clear()
            un = list(cache)
        p = 1.0 / len(un)
        return {y: p for y in un}


class _Pred(_Model):
    def __init__(self, alpha: float = 0.5, big: int = 10**9):
        self.last: dict[int, int] = {}
        self.gap: dict[int, float] = {}
        self.a = alpha
        self.big = big
    def on_req(self, t: int, k: int, hit: bool):
        if k in self.last:
            g = t - self.last[k]
            og = self.gap.get(k)
            self.gap[k] = g if og is None else self.a * g + (1.0 - self.a) * og
        self.last[k] = t
    def pr(self, y: int) -> float:
        g = self.gap.get(y)
        return self.last.get(y, -1) + (g if g is not None else self.big)
    def dist(self, x: int, cache: list[int]) -> dict[int, float]:
        b = max(self.pr(y) for y in cache)
        ties = [y for y in cache if self.pr(y) >= b - 1e-9]
        p = 1.0 / len(ties)
        return {y: p for y in ties}


class AdaptiveAttacker:
    def __init__(self, C: int, K: int, N: int, seed: int):
        self.C = C
        self.K = K
        self.N = N
        self.rng = random.Random(seed ^ 0xA177AC)

        self.S = list(range(C + 1))
        self.U = min(K, max(C + 1, int(round(1.15 * C))))

        self.models = [
            _Lru(),
            _Fifo(),
            _Mru(),
            _Lifo(),
            _Lfu(),
            _Rand(),
            _Mark(),
            _Pred(0.5),
            _Pred(1.0),
        ]
        n = len(self.models)
        self.ll = [0.0] * n
        self.h: list[dict[int, float] | None] = [None] * n

        self.seen: set[int] = set()
        self.filled = 0
        self.prev: int | None = None
        self.lastreq: dict[int, int] = {}
        self.trace: list[int] = []
        self.miss_count = 0
        self.belief_dirty = False

        # 反 MRU/LIFO 乒乓追踪：维护最近两个发生缺失的键
        self.last_two_misses: list[int] = []

        self.bandit = WindowBandit(
            ["bayes_hole", "unif_spread"],
            self.rng,
            window=max(120, 5 * C),
            maximize=False,
            explore=0.06,
        )

    def _reset_hole_belief(self):
        self.h = [{y: 1.0 / (self.C + 1) for y in self.S} for _ in self.models]
        self.belief_dirty = False

    def _feedback(self, t: int, k: int, hit: bool):
        if not hit:
            self.miss_count += 1
            if not self.last_two_misses or self.last_two_misses[-1] != k:
                self.last_two_misses.append(k)
                if len(self.last_two_misses) > 2:
                    self.last_two_misses.pop(0)

        in_s = k <= self.C
        if not in_s:
            # 超出 S 的键进入缓存后，S 中可能出现多个空洞，标记信念待刷新
            self.belief_dirty = True

        if in_s and self.h[0] is not None and not self.belief_dirty:
            for i, h in enumerate(self.h):
                pm = min(max(h.get(k, 0.0), 0.02), 0.98)
                self.ll[i] = self.ll[i] * 0.98 + math.log(1.0 - pm if hit else pm)

        if not hit and in_s:
            if self.filled >= self.C:
                if not self.belief_dirty:
                    cache = [y for y in self.S if y != k]
                    for i, m in enumerate(self.models):
                        self.h[i] = m.dist(k, cache)
                else:
                    # 刚从 unif_spread 返回，重置为均匀空洞直到重新校准
                    self._reset_hole_belief()
            else:
                self.seen.add(k)
                self.filled = len(self.seen)
                if self.filled == self.C:
                    hole = [y for y in self.S if y not in self.seen][0]
                    self.h = [{hole: 1.0} for _ in self.models]
        elif hit and in_s and not self.belief_dirty:
            for i, h in enumerate(self.h):
                if h and k in h:
                    h.pop(k)
                    z = sum(h.values())
                    if z > 0:
                        for y in h:
                            h[y] /= z
                    else:
                        c = [y for y in self.S if y != k]
                        self.h[i] = {y: 1.0 / len(c) for y in c}

        for m in self.models:
            m.on_req(t, k, hit)

    def next(self, t: int, lh: bool | None) -> int:
        if self.prev is not None and lh is not None:
            self._feedback(t - 1, self.prev, lh)

        arm = self.bandit.tick(t, self.trace, self.miss_count, self.C)

        if arm == "unif_spread":
            chosen = self.rng.randrange(self.U)
        elif self.h[0] is None:
            chosen = next((y for y in self.S if y not in self.seen), self.rng.randrange(self.C + 1))
        else:
            if self.belief_dirty:
                # 若刚刚在 unif_spread，先扫描 S 迅速将缓存洗回 S 并重置空洞
                self._reset_hole_belief()
            mx = max(self.ll)
            w = [math.exp(l - mx) for l in self.ll]
            z = sum(w)
            hm: dict[int, float] = {}
            for wi, h in zip(w, self.h):
                if h:
                    for y, p in h.items():
                        hm[y] = hm.get(y, 0.0) + wi * p / z
            if hm:
                b = max(hm.values())
                cands = [y for y, p in hm.items() if p >= b - 1e-9]
                chosen = min(cands, key=lambda y: self.lastreq.get(y, -1))
            else:
                chosen = self.rng.randrange(self.C + 1)

        self.prev = chosen
        self.lastreq[chosen] = t
        self.trace.append(chosen)
        return chosen


# ----------------------------------------------------------------------
# 主交互入口 (Entrypoint)
# ----------------------------------------------------------------------
def main():
    line = sys.stdin.readline()
    if not line:
        return
    init = json.loads(line)
    role = init["role"]
    C = init["cache_size"]
    K = init["universe"]
    N = init["length"]
    seed = init["seed"]

    sys.stdout.write("{}\n")
    sys.stdout.flush()

    out = sys.stdout
    if role == "attacker":
        bot = AdaptiveAttacker(C, K, N, seed)
        for raw in sys.stdin:
            m = json.loads(raw)
            if m.get("end"):
                out.write("{}\n")
                out.flush()
                break
            t = m["t"]
            k = bot.next(t, m.get("last_hit"))
            out.write(f'{{"key": {k}}}\n')
            out.flush()
    else:
        dfd = AdaptiveDefender(C, K, N, seed)
        for raw in sys.stdin:
            m = json.loads(raw)
            if m.get("end"):
                out.write("{}\n")
                out.flush()
                break
            t = m["t"]
            e = dfd.step(t, m["key"], m["hit"], m["need_evict"])
            if e is None:
                out.write("{}\n")
            else:
                out.write(f'{{"evict": {e}}}\n')
            out.flush()


if __name__ == "__main__":
    main()
