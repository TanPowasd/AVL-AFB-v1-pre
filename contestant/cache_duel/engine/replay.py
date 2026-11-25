# -*- coding: utf-8 -*-
"""cache_duel 对局回放录制、Belady OPT 轨迹与一致性校验（afb-cache-duel-replay/1）。

设计要点：
- 直接复用 engine.py 的 Bot、belady_misses、role_seed、is_key 与 MatchResult，不修改引擎源码。
- 记录紧凑的逐步轨迹（keys / hits / evicts / opt_hits / opt_evicts / faults）及周期检查点，
  支持离线播放器即时定位任意步并并排还原防守方与 Belady OPT 的实时缓存状态。
- 所有记录操作均在子进程计时区间之外，不影响单步 1s / 时间池 5s / 总预算 20s 统计。
"""
from __future__ import annotations

import hashlib
import heapq
import json
import os
import time
from typing import Any

try:
    from .engine import Bot, MatchResult, belady_misses, is_key, role_seed
except ImportError:
    from engine import Bot, MatchResult, belady_misses, is_key, role_seed

FORMAT = "afb-cache-duel-replay/1"
CHECKPOINT_STRIDE = 200

ENGINE_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(ENGINE_DIR)


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _safe_rel(path: str, root: str = ROOT) -> str:
    """尽量返回相对题目根目录的 POSIX 路径；跨盘或越出父目录时退回文件名。"""
    abs_path = os.path.abspath(path)
    try:
        rel = os.path.relpath(abs_path, os.path.abspath(root)).replace("\\", "/")
    except ValueError:
        return os.path.basename(abs_path)
    if rel.startswith("../..") or os.path.isabs(rel):
        return os.path.basename(abs_path)
    return rel


def belady_trace(trace: list[int], cap: int) -> tuple[int, list[int], list[int]]:
    """计算离线最优（Belady/MIN）的缺失总数及逐步命中/驱逐序列。

    与 engine.belady_misses 采用完全一致的次出现距离与平手破除规则（nxt[i] = n + i）。
    返回 (opt_misses, opt_hits, opt_evicts)，其中未驱逐记为 -1。
    """
    n = len(trace)
    nxt = [0] * n
    last: dict[int, int] = {}
    for i in range(n - 1, -1, -1):
        nxt[i] = last.get(trace[i], n + i)
        last[trace[i]] = i

    cache: dict[int, int] = {}
    heap: list[tuple[int, int]] = []
    misses = 0
    opt_hits: list[int] = []
    opt_evicts: list[int] = []
    for i, k in enumerate(trace):
        if k in cache:
            cache[k] = nxt[i]
            heapq.heappush(heap, (-nxt[i], k))
            opt_hits.append(1)
            opt_evicts.append(-1)
            continue
        misses += 1
        evicted = -1
        if len(cache) >= cap:
            while True:
                nn, kk = heapq.heappop(heap)
                if kk in cache and cache[kk] == -nn:
                    del cache[kk]
                    evicted = kk
                    break
        cache[k] = nxt[i]
        heapq.heappush(heap, (-nxt[i], k))
        opt_hits.append(0)
        opt_evicts.append(evicted)
    return misses, opt_hits, opt_evicts


def _build_checkpoints(
    keys: list[int],
    hits: list[int],
    evicts: list[int],
    opt_hits: list[int],
    opt_evicts: list[int],
    stride: int = CHECKPOINT_STRIDE,
) -> list[dict[str, Any]]:
    """按固定步距生成累计指标与槽位顺序缓存快照，便于播放器 O(1) 跳转。"""
    n = len(keys)
    if n == 0:
        return []
    stride = max(1, int(stride))
    def_slots: list[int] = []
    opt_slots: list[int] = []
    def_misses = 0
    opt_misses = 0
    checkpoints: list[dict[str, Any]] = []

    for t in range(n):
        k = keys[t]
        if not hits[t]:
            def_misses += 1
            ev = evicts[t]
            if ev >= 0:
                def_slots[def_slots.index(ev)] = k
            else:
                def_slots.append(k)
        if not opt_hits[t]:
            opt_misses += 1
            oev = opt_evicts[t]
            if oev >= 0:
                opt_slots[opt_slots.index(oev)] = k
            else:
                opt_slots.append(k)

        if t == 0 or (t + 1) % stride == 0 or t == n - 1:
            checkpoints.append({
                "t": t,
                "def_misses": def_misses,
                "opt_misses": opt_misses,
                "def_score": round(opt_misses / def_misses, 6) if def_misses else 1.0,
                "def_cache": list(def_slots),
                "opt_cache": list(opt_slots),
            })
    return checkpoints


def play_with_replay(
    attacker_path: str,
    defender_path: str,
    cache_size: int,
    universe: int,
    length: int,
    seed: int,
    attacker_label: str | None = None,
    defender_label: str | None = None,
    engine_sha: str | None = None,
) -> tuple[MatchResult, dict[str, Any]]:
    """运行与 engine.play() 完全一致的对局循环，并同步生成结构化回放数据。

    返回 (MatchResult, replay_dict)。
    """
    cfg = {"cache_size": cache_size, "universe": universe, "length": length, "seed": seed}
    att = Bot(attacker_path, {"role": "attacker", **cfg, "seed": role_seed(seed, "attacker")})
    dfd = Bot(defender_path, {"role": "defender", **cfg, "seed": role_seed(seed, "defender")})

    cache: set[int] = set()
    insert_order: list[int] = []
    trace: list[int] = []
    hits: list[int] = []
    evicts: list[int] = []
    att_fault_steps: list[int] = []
    def_fault_steps: list[int] = []
    misses = 0
    last_hit = None
    att_faults = def_faults = 0

    try:
        for t in range(length):
            a = att.ask({"t": t, "last_hit": last_hit, "time_pool": round(att.pool, 3)})
            k = a.get("key") if isinstance(a, dict) else None
            if not is_key(k) or not (0 <= k < universe):
                att_faults += 1
                att_fault_steps.append(t)
                k = 0
            trace.append(k)
            hit = k in cache
            need_evict = (not hit) and len(cache) >= cache_size
            d = dfd.ask({
                "t": t,
                "key": k,
                "hit": hit,
                "need_evict": need_evict,
                "time_pool": round(dfd.pool, 3),
            })
            evicted = -1
            if not hit:
                misses += 1
                if need_evict:
                    e = d.get("evict") if isinstance(d, dict) else None
                    if not is_key(e) or e not in cache:
                        def_faults += 1
                        def_fault_steps.append(t)
                        e = insert_order[-1]
                    cache.discard(e)
                    insert_order.remove(e)
                    evicted = e
                cache.add(k)
                insert_order.append(k)
            hits.append(1 if hit else 0)
            evicts.append(evicted)
            last_hit = hit
    finally:
        att.close({"t": length, "end": True})
        dfd.close({"t": length, "end": True})

    opt, opt_hits, opt_evicts = belady_trace(trace, cache_size)
    opt_ref = belady_misses(trace, cache_size)
    if opt != opt_ref:
        raise RuntimeError(f"Belady OPT 轨迹计数与引擎不一致: {opt} != {opt_ref}")

    def_score = opt / misses if misses else 1.0
    faults = {
        "attacker": att_faults,
        "defender": def_faults,
        "attacker_dead": att.dead,
        "defender_dead": dfd.dead,
    }
    result = MatchResult(
        attacker=attacker_path,
        defender=defender_path,
        config=cfg,
        def_misses=misses,
        opt_misses=opt,
        def_score=def_score,
        faults=faults,
    )

    checkpoints = _build_checkpoints(trace, hits, evicts, opt_hits, opt_evicts, CHECKPOINT_STRIDE)
    att_name = attacker_label or os.path.splitext(os.path.basename(attacker_path))[0]
    def_name = defender_label or os.path.splitext(os.path.basename(defender_path))[0]
    eng_sha = engine_sha or sha256_file(os.path.join(ENGINE_DIR, "engine.py"))

    data: dict[str, Any] = {
        "format": FORMAT,
        "meta": {
            "format": FORMAT,
            "problem": "cache_duel",
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "engine_sha256": eng_sha,
            "seed": seed,
            "config": {
                "cache_size": cache_size,
                "universe": universe,
                "length": length,
            },
            "attacker": {
                "label": att_name,
                "file": _safe_rel(attacker_path, ROOT),
                "file_sha256": sha256_file(attacker_path),
            },
            "defender": {
                "label": def_name,
                "file": _safe_rel(defender_path, ROOT),
                "file_sha256": sha256_file(defender_path),
            },
            "def_misses": misses,
            "opt_misses": opt,
            "def_score": def_score,
            "att_score": 1.0 - def_score,
            "faults": dict(faults),
            "checkpoint_stride": CHECKPOINT_STRIDE,
        },
        "keys": trace,
        "hits": hits,
        "evicts": evicts,
        "att_faults": att_fault_steps,
        "def_faults": def_fault_steps,
        "opt_hits": opt_hits,
        "opt_evicts": opt_evicts,
        "checkpoints": checkpoints,
    }
    return result, data


def save_replay(path: str, data: dict[str, Any]) -> tuple[str, int]:
    """以紧凑 UTF-8 JSON 保存回放，返回 (path, byte_size)。"""
    os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(payload)
    return path, len(payload.encode("utf-8"))


def verify_against_engine(
    data: dict[str, Any],
    result: MatchResult | dict[str, Any] | None = None,
) -> tuple[bool, str]:
    """校验回放的格式、逐步缓存模拟、Belady OPT 轨迹、检查点及与引擎结果的一致性。"""
    if not isinstance(data, dict):
        return False, "回放根节点不是 JSON 对象"
    if data.get("format") != FORMAT:
        return False, f"格式不匹配: {data.get('format')!r} != {FORMAT!r}"

    meta = data.get("meta")
    if not isinstance(meta, dict):
        return False, "缺少 meta 字段"
    cfg = meta.get("config")
    if not isinstance(cfg, dict):
        return False, "缺少 meta.config"

    try:
        cap = int(cfg["cache_size"])
        universe = int(cfg["universe"])
        length = int(cfg["length"])
    except (KeyError, TypeError, ValueError) as exc:
        return False, f"meta.config 参数无效: {exc}"

    if cap < 1 or universe <= cap or length < 0:
        return False, f"非法配置参数: C={cap}, K={universe}, N={length}"

    keys = data.get("keys")
    hits = data.get("hits")
    evicts = data.get("evicts")
    opt_hits = data.get("opt_hits")
    opt_evicts = data.get("opt_evicts")
    att_faults = data.get("att_faults")
    def_faults = data.get("def_faults")

    for name, arr in (
        ("keys", keys),
        ("hits", hits),
        ("evicts", evicts),
        ("opt_hits", opt_hits),
        ("opt_evicts", opt_evicts),
    ):
        if not isinstance(arr, list) or len(arr) != length:
            got_len = len(arr) if isinstance(arr, list) else type(arr).__name__
            return False, f"{name} 长度不匹配: {got_len} != {length}"

    if not isinstance(att_faults, list) or not isinstance(def_faults, list):
        return False, "att_faults / def_faults 必须为列表"

    meta_faults = meta.get("faults") or {}
    if len(att_faults) != int(meta_faults.get("attacker", -1)):
        return False, f"att_faults 长度({len(att_faults)}) != meta.faults.attacker({meta_faults.get('attacker')})"
    if len(def_faults) != int(meta_faults.get("defender", -1)):
        return False, f"def_faults 长度({len(def_faults)}) != meta.faults.defender({meta_faults.get('defender')})"

    cp_map: dict[int, dict[str, Any]] = {}
    for cp in data.get("checkpoints") or []:
        if not isinstance(cp, dict) or "t" not in cp:
            return False, "checkpoints 条目格式无效"
        cp_map[int(cp["t"])] = cp

    # 1. 重放防守方缓存，验证每步 hit/miss 与驱逐合法性
    def_cache: set[int] = set()
    sim_def_misses = 0
    opt_cache: set[int] = set()
    sim_opt_misses = 0

    for t in range(length):
        k = keys[t]
        if not is_key(k) or not (0 <= k < universe):
            return False, f"步 {t} 请求 key 非法: {k!r}"

        h = hits[t]
        ev = evicts[t]
        if h not in (0, 1):
            return False, f"步 {t} hits 值非法: {h!r}"
        expected_hit = 1 if k in def_cache else 0
        if h != expected_hit:
            return False, f"步 {t} 防守命中不一致: 记录={h}, 模拟={expected_hit} (key={k})"
        if h == 1:
            if ev != -1:
                return False, f"步 {t} 命中时不应驱逐: evict={ev}"
        else:
            sim_def_misses += 1
            if len(def_cache) >= cap:
                if not is_key(ev) or ev not in def_cache:
                    return False, f"步 {t} 防守驱逐非法: evict={ev!r} 不在缓存中"
                def_cache.remove(ev)
            else:
                if ev != -1:
                    return False, f"步 {t} 缓存未满时不应驱逐: evict={ev}"
            def_cache.add(k)

        oh = opt_hits[t]
        oev = opt_evicts[t]
        if oh not in (0, 1):
            return False, f"步 {t} opt_hits 值非法: {oh!r}"
        expected_oh = 1 if k in opt_cache else 0
        if oh != expected_oh:
            return False, f"步 {t} OPT 命中不一致: 记录={oh}, 模拟={expected_oh}"
        if oh == 1:
            if oev != -1:
                return False, f"步 {t} OPT 命中时不应驱逐: opt_evict={oev}"
        else:
            sim_opt_misses += 1
            if len(opt_cache) >= cap:
                if not is_key(oev) or oev not in opt_cache:
                    return False, f"步 {t} OPT 驱逐非法: opt_evict={oev!r} 不在缓存中"
                opt_cache.remove(oev)
            else:
                if oev != -1:
                    return False, f"步 {t} OPT 缓存未满时不应驱逐: opt_evict={oev}"
            opt_cache.add(k)

        if t in cp_map:
            cp = cp_map[t]
            if int(cp.get("def_misses", -1)) != sim_def_misses:
                return False, f"检查点 t={t} def_misses 不一致: {cp.get('def_misses')} != {sim_def_misses}"
            if int(cp.get("opt_misses", -1)) != sim_opt_misses:
                return False, f"检查点 t={t} opt_misses 不一致: {cp.get('opt_misses')} != {sim_opt_misses}"
            if "def_cache" in cp and set(cp["def_cache"]) != def_cache:
                return False, f"检查点 t={t} def_cache 集合不一致"
            if "opt_cache" in cp and set(cp["opt_cache"]) != opt_cache:
                return False, f"检查点 t={t} opt_cache 集合不一致"

    # 2. 用 engine.belady_misses + belady_trace 交叉校验离线最优
    opt_ref = belady_misses(keys, cap)
    opt_chk, opt_hits_chk, opt_evicts_chk = belady_trace(keys, cap)
    if opt_chk != opt_ref or sim_opt_misses != opt_ref:
        return False, f"Belady OPT 缺失数不一致: trace={sim_opt_misses}, engine={opt_ref}"
    if opt_hits != opt_hits_chk or opt_evicts != opt_evicts_chk:
        return False, "Belady OPT 逐步命中/驱逐序列与重算结果不一致"

    if sim_def_misses != int(meta.get("def_misses", -1)):
        return False, f"meta.def_misses({meta.get('def_misses')}) != 模拟缺失({sim_def_misses})"
    if opt_ref != int(meta.get("opt_misses", -1)):
        return False, f"meta.opt_misses({meta.get('opt_misses')}) != OPT({opt_ref})"

    expected_score = opt_ref / sim_def_misses if sim_def_misses else 1.0
    if abs(float(meta.get("def_score", -1.0)) - expected_score) > 1e-9:
        return False, f"meta.def_score({meta.get('def_score')}) != {expected_score}"
    if abs(float(meta.get("att_score", -1.0)) - (1.0 - expected_score)) > 1e-9:
        return False, f"meta.att_score({meta.get('att_score')}) != {1.0 - expected_score}"

    # 3. 与外部 MatchResult（若提供）核对
    if result is not None:
        r_def = result["def_misses"] if isinstance(result, dict) else result.def_misses
        r_opt = result["opt_misses"] if isinstance(result, dict) else result.opt_misses
        r_score = result["def_score"] if isinstance(result, dict) else result.def_score
        r_faults = result["faults"] if isinstance(result, dict) else result.faults
        if r_def != sim_def_misses:
            return False, f"防守缺失与引擎结果不一致: replay={sim_def_misses} != engine={r_def}"
        if r_opt != opt_ref:
            return False, f"OPT 缺失与引擎结果不一致: replay={opt_ref} != engine={r_opt}"
        if abs(float(r_score) - expected_score) > 1e-9:
            return False, f"def_score 与引擎结果不一致: replay={expected_score} != engine={r_score}"
        if r_faults != meta_faults:
            return False, f"faults 与引擎结果不一致: replay={meta_faults} != engine={r_faults}"

    return True, (
        f"一致（N={length}, M_def={sim_def_misses}, OPT={opt_ref}, "
        f"def_score={expected_score:.4f}）"
    )
