"""strong：参考强 bot。
防守 = 影子专家（LRU/MRU/LFU/FIFO/预测 Belady/随机 Marking）+ 乘性权重随机选专家。
攻击 = 只用 C+1 个 key；同时模拟若干种对手（LRU/FIFO/MRU/LFU/预测/随机）的缓存，
       用命中反馈挑出最像的那个模型，请求它认为"不在缓存里"的 key；都不像时用概率信念。"""
import json, sys, random, math
from collections import OrderedDict

init = json.loads(sys.stdin.readline())
ROLE, C, K = init["role"], init["cache_size"], init["universe"]
rng = random.Random(init["seed"] * 7919 + 101)
print("{}", flush=True)


# ---------------- 通用：可模拟的确定性策略 ----------------
class Policy:
    """在自己的影子缓存上跑，也能对任意缓存给出驱逐建议。"""
    def __init__(self, name):
        self.name = name
        self.cache = set()
        self.last = {}      # key -> 最近访问时间
        self.ins = {}       # key -> 放入时间
        self.freq = {}      # key -> 访问次数
        self.gap = {}       # key -> 最近一次间隔
        self.marked = set()
        self.r = random.Random(rng.random())

    def pick(self, cand, t, last, ins, freq, gap, marked):
        n = self.name
        if n == "lru":
            return min(cand, key=lambda x: last[x])
        if n == "mru":
            return max(cand, key=lambda x: last[x])
        if n == "fifo":
            return min(cand, key=lambda x: ins[x])
        if n == "lfu":
            return min(cand, key=lambda x: (freq[x], last[x]))
        if n == "pred":   # 预测下一次出现 = 上次 + 间隔，踢最晚的
            return max(cand, key=lambda x: (last[x] + gap.get(x, 10 ** 9), -last[x]))
        # marking
        un = [x for x in cand if x not in marked]
        if not un:
            marked.clear(); un = list(cand)
        return self.r.choice(un)

    def access(self, k, t):
        """在影子缓存上执行一次访问，返回是否命中。"""
        hit = k in self.cache
        if not hit:
            if len(self.cache) >= C:
                e = self.pick(self.cache, t, self.last, self.ins, self.freq, self.gap, self.marked)
                self.cache.discard(e)
            self.cache.add(k); self.ins[k] = t
        if k in self.last:
            self.gap[k] = t - self.last[k]
        self.last[k] = t
        self.freq[k] = self.freq.get(k, 0) + 1
        self.marked.add(k)
        return hit

    def absent(self, universe):
        return [x for x in universe if x not in self.cache]


def out(o):
    sys.stdout.write(json.dumps(o) + "\n"); sys.stdout.flush()


# ---------------- 防守 ----------------
def defend():
    names = ["lru", "mru", "lfu", "fifo", "pred", "marking"]
    experts = [Policy(n) for n in names]
    loss = [0.0] * len(names)
    DECAY = 1.0 - 1.0 / (4 * C)
    ETA = 0.6
    cache, last, ins, freq, gap, marked = set(), {}, {}, {}, {}, set()
    me = Policy("marking")   # 只借用 pick
    for line in sys.stdin:
        m = json.loads(line)
        if m.get("end"):
            out({}); break
        t, k = m["t"], m["key"]
        for i, ex in enumerate(experts):
            loss[i] = loss[i] * DECAY + (0.0 if ex.access(k, t) else 1.0)
        o = {}
        if not m["hit"]:
            if m["need_evict"]:
                best = min(loss)
                w = [math.exp(-ETA * (l - best)) for l in loss]
                i = rng.choices(range(len(names)), weights=w)[0]
                me.name = names[i]
                e = me.pick(cache, t, last, ins, freq, gap, marked)
                cache.discard(e); o["evict"] = e
            cache.add(k); ins[k] = t
        if k in last:
            gap[k] = t - last[k]
        last[k] = t
        freq[k] = freq.get(k, 0) + 1
        marked.add(k)
        out(o)


# ---------------- 攻击 ----------------
def attack():
    U = list(range(C + 1))
    names = ["lru", "fifo", "mru", "lfu", "pred"]
    models = [Policy(n) for n in names]
    err = [0.0] * len(names)
    DECAY = 0.97
    pred = [None] * len(names)     # 各模型对上一请求的命中预测
    p = {x: 0.0 for x in U}        # 概率信念：key 在对手缓存里的概率
    prev = None
    filled = 0
    for line in sys.stdin:
        m = json.loads(line)
        if m.get("end"):
            out({}); break
        t, lh = m["t"], m["last_hit"]
        if lh is not None:
            for i in range(len(names)):
                if pred[i] is not None:
                    err[i] = err[i] * DECAY + (0.0 if pred[i] == lh else 1.0)
            if lh is False:
                filled += 1
        # 选 key
        if filled < C + 1 and t < 3 * (C + 1):
            k = U[t % (C + 1)]               # 先把缓存装满
        else:
            bi = min(range(len(names)), key=lambda i: err[i])
            if err[bi] < 1.5:
                ab = [x for x in models[bi].absent(U) if x != prev]
                k = rng.choice(ab) if ab else None
            else:
                k = None
            if k is None:
                lo = min(p[x] for x in U if x != prev)
                cand = [x for x in U if x != prev and p[x] <= lo + 1e-9]
                k = rng.choice(cand)
        # 更新各模型（它们预测这一步是否命中）
        for i, md in enumerate(models):
            pred[i] = md.access(k, t)
        # 概率信念：被请求的一定在；若它此前可能不在，其他 key 按比例被踢
        miss_prob = 1.0 - p[k]
        others = [x for x in U if x != k]
        s = sum(p[x] for x in others)
        if s > C - 1 + 1e-9 and s > 0:      # 缓存已满才会驱逐
            f = 1.0 - miss_prob / s
            for x in others:
                p[x] *= f
        p[k] = 1.0
        prev = k
        out({"key": k})


if ROLE == "attacker":
    attack()
else:
    defend()
