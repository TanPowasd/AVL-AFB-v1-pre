# -*- coding: utf-8 -*-
"""《双符奇点》（sk_singularity）512 种子动态盲规则/残骸生成器与隐藏正式赛题调度器。

核心设计（方案 A）：
  - 正式评测规模：512 测 = 8 类正交家族 × 64 测/家族（每家族在 N ∈ {14, 18, 21, 24} 上均匀分配各 16 测）。
  - 每次正式评测使用 OS CSPRNG（secrets.randbits(63) + 1）抽取 512 个互异 64-bit 种子。
  - 严格排除 14 个公开练习测规则与强制残骸（含 S <-> K 符号对换同构下均零重合）。
  - 同一批次 512 测内所有 (family, N, a_S, rhs_S, a_K, rhs_K, req) 规范键严格去重。
  - 每条规则对构造式确保非退化（复制放大子 + 删减/投影停机子配对），并对每测按种子施加 50% 精确 S <-> K 对换同构。
"""
from __future__ import annotations

import itertools
import json
from pathlib import Path
import random
import secrets
import sys
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
ENGINE_DIR = ROOT / "engine"
if str(ENGINE_DIR) not in sys.path:
    sys.path.insert(0, str(ENGINE_DIR))
import runner  # noqa: E402

OFFICIAL_SUITE_PATH = ROOT / "hidden" / "official_suite.json"
PRACTICE_SUITE_PATH = ROOT / "config" / "practice_suite.json"

OFFICIAL_FAMILIES: tuple[str, ...] = (
    "F1_RemnantSK",
    "F2_RevSK",
    "F3_SelfWrap",
    "F4_Cross33",
    "F5_Quad42",
    "F6_InjectConst",
    "F7_TriNest",
    "F8_Ar23Twist",
)

FAMILY_PREFIX: dict[str, str] = {
    "F1_RemnantSK": "F1",
    "F2_RevSK": "F2",
    "F3_SelfWrap": "F3",
    "F4_Cross33": "F4",
    "F5_Quad42": "F5",
    "F6_InjectConst": "F6",
    "F7_TriNest": "F7",
    "F8_Ar23Twist": "F8",
}

OFFICIAL_BUDGETS: tuple[int, ...] = (14, 18, 21, 24)
DEFAULT_CASES_PER_FAMILY = 64
QUICK_CASES_PER_FAMILY = 4
OFFICIAL_TOTAL_CASES = len(OFFICIAL_FAMILIES) * DEFAULT_CASES_PER_FAMILY  # 512


def load_raw_official_cases(path: Path = OFFICIAL_SUITE_PATH) -> list[dict[str, Any]]:
    """保留静态官方样例加载接口（供洛谷打包器等静态工具兼容调用）。"""
    data = json.loads(path.read_text(encoding="utf-8"))
    return list(data["cases"])


# ============================================================================
# Public Practice Exclusion Table (exact + S <-> K symmetric closure)
# ============================================================================
def _rule_key(a_s: int, rhs_s: str, a_k: int, rhs_k: str) -> tuple[int, str, int, str]:
    return (int(a_s), rhs_s.strip(), int(a_k), rhs_k.strip())


def _flip_rule_key(rk: tuple[int, str, int, str]) -> tuple[int, str, int, str]:
    a_s, rhs_s, a_k, rhs_k = rk
    return (a_k, runner.swap_sk_str(rhs_k), a_s, runner.swap_sk_str(rhs_s))


def _canonical_rule_key(a_s: int, rhs_s: str, a_k: int, rhs_k: str) -> tuple[int, str, int, str]:
    k0 = _rule_key(a_s, rhs_s, a_k, rhs_k)
    k1 = _flip_rule_key(k0)
    return k0 if k0 <= k1 else k1


def _load_public_exclusions() -> tuple[frozenset[tuple[int, str, int, str]], frozenset[str]]:
    data = json.loads(PRACTICE_SUITE_PATH.read_text(encoding="utf-8"))
    rule_keys: set[tuple[int, str, int, str]] = set()
    req_keys: set[str] = set()
    for c in data["cases"]:
        rk = _rule_key(c["a_S"], c["rhs_S"], c["a_K"], c["rhs_K"])
        rule_keys.add(rk)
        rule_keys.add(_flip_rule_key(rk))
        req = str(c.get("req", "-")).strip()
        if req and req != "-":
            req_keys.add(req)
            req_keys.add(runner.swap_sk_str(req))
    return frozenset(rule_keys), frozenset(req_keys)


PUBLIC_RULE_KEYS, PUBLIC_REQ_KEYS = _load_public_exclusions()


# ============================================================================
# Pure-Python Fast Bounded Normal-Order Halting Check (for E_req validation)
# ============================================================================
def _parse_tree_tokens(s: str) -> Any:
    toks = s.replace("(", " ( ").replace(")", " ) ").split()
    pos = 0

    def rec() -> Any:
        nonlocal pos
        t = toks[pos]
        pos += 1
        if t == "(":
            a = rec()
            b = rec()
            assert toks[pos] == ")"
            pos += 1
            return (a, b)
        return t

    return rec()


def _inst_rhs_tree(node: Any, args: list[Any]) -> Any:
    if isinstance(node, tuple):
        return (_inst_rhs_tree(node[0], args), _inst_rhs_tree(node[1], args))
    if node in ("S", "K"):
        return node
    return args[int(node)]


def _expr_halts_within(
    a_s: int,
    rhs_s: str,
    a_k: int,
    rhs_k: str,
    expr_str: str,
    max_steps: int = 32,
    max_size: int = 256,
) -> bool:
    """确认强制残骸子树 E_req 本身在目标规则下快速合法停机（确保起步求解器亦可 100% 合法停机）。"""
    ar = {"S": a_s, "K": a_k}
    rt = {"S": _parse_tree_tokens(rhs_s), "K": _parse_tree_tokens(rhs_k)}
    cur = _parse_tree_tokens(expr_str)

    def tree_size(t: Any, cap: int) -> int:
        if not isinstance(t, tuple):
            return 1
        ls = tree_size(t[0], cap)
        if ls >= cap:
            return cap
        return ls + tree_size(t[1], cap - ls)

    def step_no(t: Any) -> tuple[bool, Any]:
        if not isinstance(t, tuple):
            return False, t
        # Check root redex
        args: list[Any] = []
        head = t
        while isinstance(head, tuple):
            args.append(head[1])
            head = head[0]
        if len(args) == ar[head]:
            args.reverse()
            return True, _inst_rhs_tree(rt[head], args)
        fired_l, nl = step_no(t[0])
        if fired_l:
            return True, (nl, t[1])
        fired_r, nr = step_no(t[1])
        if fired_r:
            return True, (t[0], nr)
        return False, t

    for _ in range(max_steps):
        fired, nxt = step_no(cur)
        if not fired:
            return True
        if tree_size(nxt, max_size + 1) > max_size:
            return False
        cur = nxt
    return False


# ============================================================================
# Constructive Family Rule Pools (strictly disjoint across families & public!)
# ============================================================================
def _fmt_4_shape(shape_id: int, a: str, b: str, c: str, d: str) -> str:
    if shape_id == 0:
        return f"(({a} {b}) ({c} {d}))"
    if shape_id == 1:
        return f"((({a} {b}) {c}) {d})"
    if shape_id == 2:
        return f"(({a} ({b} {c})) {d})"
    if shape_id == 3:
        return f"({a} (({b} {c}) {d}))"
    return f"({a} ({b} ({c} {d})))"


def _fmt_5_shape(shape_id: int, a: str, b: str, c: str, d: str, e: str) -> str:
    if shape_id == 0:
        return f"(({a} ({b} {c})) ({d} {e}))"
    if shape_id == 1:
        return f"((({a} {b}) {c}) ({d} {e}))"
    if shape_id == 2:
        return f"(({a} {b}) (({c} {d}) {e}))"
    if shape_id == 3:
        return f"(({a} {b}) ({c} ({d} {e})))"
    if shape_id == 4:
        return f"((({a} {b}) ({c} {d})) {e})"
    return f"({a} (({b} {c}) ({d} {e})))"


def _has_bare_self_pair(rhs: str) -> bool:
    """剔除形如 (0 0)/(1 1)/(2 2)/(3 3) 的裸自应用对，避免绝大多数输入瞬间死循环导致退化。"""
    return ("(0 0)" in rhs) or ("(1 1)" in rhs) or ("(2 2)" in rhs) or ("(3 3)" in rhs)


def _build_f1_pool() -> list[tuple[int, str, int, str]]:
    """F1_RemnantSK: (3, 2) 平衡跨半区高放大复制子 + 2元投影子（与强制残骸 E_req 组合，排除所有公开规则）。"""
    out: list[tuple[int, str, int, str]] = []
    seen: set[tuple[int, str, int, str]] = set()
    # Emphasize dup in {2, 1} and include strong left-skewed forms so F1 has rich rule diversity + high amplification
    for dup in ("2", "1"):
        others = [v for v in ("0", "1", "2") if v != dup]
        for o_left, o_right in ((others[0], others[1]), (others[1], others[0])):
            for lp in ((o_left, dup), (dup, o_left)):
                for rp in ((o_right, dup), (dup, o_right)):
                    for rhs_s in (
                        f"(({lp[0]} {lp[1]}) ({rp[0]} {rp[1]}))",
                        f"((({lp[0]} {lp[1]}) {rp[0]}) {rp[1]})",
                    ):
                        if _has_bare_self_pair(rhs_s):
                            continue
                        for rhs_k in ("0", "1"):
                            rk = _rule_key(3, rhs_s, 2, rhs_k)
                            if rk in PUBLIC_RULE_KEYS:
                                continue
                            ck = _canonical_rule_key(*rk)
                            if ck not in seen:
                                seen.add(ck)
                                out.append(rk)
    return out


def _build_f2_pool() -> list[tuple[int, str, int, str]]:
    """F2_RevSK: (3, 2) 4叶全变量复制+多拓扑/置换盲规则（无 E_req，排除公开规则与裸自应用退化对）。"""
    out: list[tuple[int, str, int, str]] = []
    seen: set[tuple[int, str, int, str]] = set()
    for shape_id in (0, 1, 2, 3):
        for dup in ("0", "1", "2"):
            others = [v for v in ("0", "1", "2") if v != dup]
            bag = [dup, dup, others[0], others[1]]
            for perm in sorted(set(itertools.permutations(bag))):
                a, b, c, d = perm
                rhs_s = _fmt_4_shape(shape_id, a, b, c, d)
                if _has_bare_self_pair(rhs_s):
                    continue
                for rhs_k in ("0", "1"):
                    rk = _rule_key(3, rhs_s, 2, rhs_k)
                    if rk in PUBLIC_RULE_KEYS:
                        continue
                    ck = _canonical_rule_key(*rk)
                    if ck not in seen:
                        seen.add(ck)
                        out.append(rk)
    return out


def _build_f3_pool() -> list[tuple[int, str, int, str]]:
    """F3_SelfWrap: (3, 2) 5叶双变量各复制2次（{2,2,1}）深度自包裹高放大家族。"""
    out: list[tuple[int, str, int, str]] = []
    seen: set[tuple[int, str, int, str]] = set()
    for shape_id in (0, 1, 2, 3, 4, 5):
        for single in ("0", "1", "2"):
            dups = [v for v in ("0", "1", "2") if v != single]
            bag = [dups[0], dups[0], dups[1], dups[1], single]
            for perm in sorted(set(itertools.permutations(bag))):
                a, b, c, d, e = perm
                rhs_s = _fmt_5_shape(shape_id, a, b, c, d, e)
                if _has_bare_self_pair(rhs_s):
                    continue
                for rhs_k in ("0", "1"):
                    rk = _rule_key(3, rhs_s, 2, rhs_k)
                    if rk in PUBLIC_RULE_KEYS:
                        continue
                    ck = _canonical_rule_key(*rk)
                    if ck not in seen:
                        seen.add(ck)
                        out.append(rk)
    return out


def _build_f4_pool() -> list[tuple[int, str, int, str]]:
    """F4_Cross33: (3, 3) 对称双三元耦合家族（S 为 4叶复制子，K 为 2叶删减应用子 (u v)）。"""
    out: list[tuple[int, str, int, str]] = []
    seen: set[tuple[int, str, int, str]] = set()
    k_templates = ["(0 1)", "(0 2)", "(1 0)", "(1 2)", "(2 0)", "(2 1)"]
    for shape_id in (0, 1, 2, 3):
        for dup in ("0", "1", "2"):
            others = [v for v in ("0", "1", "2") if v != dup]
            bag = [dup, dup, others[0], others[1]]
            for perm in sorted(set(itertools.permutations(bag))):
                a, b, c, d = perm
                rhs_s = _fmt_4_shape(shape_id, a, b, c, d)
                if _has_bare_self_pair(rhs_s):
                    continue
                for rhs_k in k_templates:
                    rk = _rule_key(3, rhs_s, 3, rhs_k)
                    if rk in PUBLIC_RULE_KEYS:
                        continue
                    ck = _canonical_rule_key(*rk)
                    if ck not in seen:
                        seen.add(ck)
                        out.append(rk)
    return out


def _build_f5_pool() -> list[tuple[int, str, int, str]]:
    """F5_Quad42: (4, 2) 四元高元数复制子家族（5叶双复制与6叶三广播形态）。"""
    out: list[tuple[int, str, int, str]] = []
    seen: set[tuple[int, str, int, str]] = set()
    # 5a: 6-leaf triple-broadcast quaternary templates (((a d) (b d)) (c d)) and ((a d) ((b d) (c d)))
    for perm in itertools.permutations(("0", "1", "2", "3")):
        a, b, c, d = perm
        for rhs_s in (
            f"((({a} {d}) ({b} {d})) ({c} {d}))",
            f"(({a} {d}) (({b} {d}) ({c} {d})))",
            f"((({d} {a}) ({b} {d})) ({c} {d}))",
        ):
            for rhs_k in ("0", "1"):
                rk = _rule_key(4, rhs_s, 2, rhs_k)
                if rk in PUBLIC_RULE_KEYS:
                    continue
                ck = _canonical_rule_key(*rk)
                if ck not in seen:
                    seen.add(ck)
                    out.append(rk)
    # 5b: 5-leaf quaternary duplicators using all 4 args with one arg duplicated across branches
    for dup in ("0", "1", "2", "3"):
        others = [v for v in ("0", "1", "2", "3") if v != dup]
        for p in itertools.permutations(others):
            a, b, c = p
            for rhs_s in (
                f"(({a} {dup}) (({b} {c}) {dup}))",
                f"((({a} {b}) {dup}) ({c} {dup}))",
                f"(({a} ({b} {dup})) ({c} {dup}))",
                f"((({a} {dup}) {b}) ({c} {dup}))",
                f"(({dup} {a}) ({b} ({c} {dup})))",
                f"(({a} {dup}) ({b} ({dup} {c})))",
            ):
                for rhs_k in ("0", "1"):
                    rk = _rule_key(4, rhs_s, 2, rhs_k)
                    if rk in PUBLIC_RULE_KEYS:
                        continue
                    ck = _canonical_rule_key(*rk)
                    if ck not in seen:
                        seen.add(ck)
                        out.append(rk)
    return out


def _build_f6_pool() -> list[tuple[int, str, int, str]]:
    """F6_InjectConst: RHS 显式注入字面常量 S/K 的双子形态家族（(3,3) 删减注入 S + (3,2) 右枝常量注入，均衡配比）。"""
    out_a: list[tuple[int, str, int, str]] = []
    out_b: list[tuple[int, str, int, str]] = []
    seen: set[tuple[int, str, int, str]] = set()
    # 6a: (3, 3) high-amplification dup=2 S + 3-leaf dropping K injecting S on right branch (like original F6)
    s_dup2: list[str] = [
        "((0 2) (1 2))",
        "((1 2) (0 2))",
        "((2 0) (1 2))",
        "((0 2) (2 1))",
        "((1 2) (2 0))",
        "((0 (1 2)) 2)",
        "((1 (0 2)) 2)",
        "((0 2) ((1 2) 2))",
        "((1 2) ((0 2) 2))",
    ]
    k_const_33: list[str] = []
    for u, v in (("0", "1"), ("0", "2"), ("1", "0"), ("1", "2"), ("2", "0"), ("2", "1")):
        k_const_33.append(f"({u} (S {v}))")
        k_const_33.append(f"({u} ({v} S))")
        k_const_33.append(f"((S {u}) {v})")
    for rhs_s in s_dup2:
        for rhs_k in k_const_33:
            rk = _rule_key(3, rhs_s, 3, rhs_k)
            if rk in PUBLIC_RULE_KEYS:
                continue
            ck = _canonical_rule_key(*rk)
            if ck not in seen:
                seen.add(ck)
                out_a.append(rk)
    # 6b: (3, 2) 5-leaf S with clean left head (a d) and literal K/S injected on right branch + 2-arg K projection
    for dup in ("2", "1"):
        others = [v for v in ("0", "1", "2") if v != dup]
        for a, b in ((others[0], others[1]), (others[1], others[0])):
            for const in ("K", "S"):
                for rhs_s in (
                    f"(({a} {dup}) ({b} ({const} {dup})))",
                    f"(({a} {dup}) ({b} ({dup} {const})))",
                    f"(({a} {dup}) (({const} {b}) {dup}))",
                    f"(({a} {dup}) (({b} {const}) {dup}))",
                    f"(({dup} {a}) ({b} ({const} {dup})))",
                    f"(({a} {dup}) ({dup} ({const} {b})))",
                ):
                    for rhs_k in ("0", "1"):
                        rk = _rule_key(3, rhs_s, 2, rhs_k)
                        if rk in PUBLIC_RULE_KEYS:
                            continue
                        ck = _canonical_rule_key(*rk)
                        if ck not in seen:
                            seen.add(ck)
                            out_b.append(rk)
    # Interleave 6a and 6b so both (3,3) and (3,2) constant-injection sub-modes appear ~50/50
    out: list[tuple[int, str, int, str]] = []
    m = min(len(out_a), len(out_b))
    for i in range(m):
        out.append(out_a[i])
        out.append(out_b[i])
    out.extend(out_a[m:])
    out.extend(out_b[m:])
    return out


def _build_f7_pool() -> list[tuple[int, str, int, str]]:
    """F7_TriNest: (3, 2) 5叶单变量三重嵌套复制（{3,1,1}）超高放大家族（剔除裸自应用对）。"""
    out: list[tuple[int, str, int, str]] = []
    seen: set[tuple[int, str, int, str]] = set()
    for shape_id in (0, 1, 2, 3, 4, 5):
        for tri in ("0", "1", "2"):
            others = [v for v in ("0", "1", "2") if v != tri]
            bag = [tri, tri, tri, others[0], others[1]]
            for perm in sorted(set(itertools.permutations(bag))):
                a, b, c, d, e = perm
                rhs_s = _fmt_5_shape(shape_id, a, b, c, d, e)
                if _has_bare_self_pair(rhs_s):
                    continue
                for rhs_k in ("0", "1"):
                    rk = _rule_key(3, rhs_s, 2, rhs_k)
                    if rk in PUBLIC_RULE_KEYS:
                        continue
                    ck = _canonical_rule_key(*rk)
                    if ck not in seen:
                        seen.add(ck)
                        out.append(rk)
    return out


def _build_f8_pool() -> list[tuple[int, str, int, str]]:
    """F8_Ar23Twist: (2, 3) 反转元数扭曲家族（二元 3/4 叶无裸自应用复制子 + 三元 1/2 叶删减投影子）。"""
    out: list[tuple[int, str, int, str]] = []
    seen: set[tuple[int, str, int, str]] = set()
    # Non-degenerate 3-leaf & 4-leaf binary duplicators (no bare (0 0) or (1 1) self-pairs!)
    s_templates: list[str] = [
        "(1 (0 1))",
        "((0 1) 1)",
        "(0 (1 0))",
        "((1 0) 0)",
        "(1 (1 0))",
        "((0 1) 0)",
        "(0 (0 1))",
        "((1 0) 1)",
        "((0 1) (1 0))",
        "((1 0) (0 1))",
        "((0 1) (0 1))",
        "((1 0) (1 0))",
        "(((0 1) 1) 0)",
        "(((1 0) 0) 1)",
        "(((0 1) 0) 1)",
        "(((1 0) 1) 0)",
        "(1 ((0 1) 0))",
        "(0 ((1 0) 1))",
        "(1 ((1 0) 1))",
        "(0 ((0 1) 0))",
        "((0 (1 0)) 1)",
        "((1 (0 1)) 0)",
        "((1 (1 0)) 1)",
        "((0 (0 1)) 0)",
    ]
    k_templates: list[str] = [
        "(2 0)", "(0 2)", "(1 2)", "(2 1)", "(0 1)", "(1 0)",
        "0", "1", "2",
    ]
    for rhs_s in s_templates:
        for rhs_k in k_templates:
            rk = _rule_key(2, rhs_s, 3, rhs_k)
            if rk in PUBLIC_RULE_KEYS:
                continue
            ck = _canonical_rule_key(*rk)
            if ck not in seen:
                seen.add(ck)
                out.append(rk)
    return out


FAMILY_POOLS: dict[str, list[tuple[int, str, int, str]]] = {
    "F1_RemnantSK": _build_f1_pool(),
    "F2_RevSK": _build_f2_pool(),
    "F3_SelfWrap": _build_f3_pool(),
    "F4_Cross33": _build_f4_pool(),
    "F5_Quad42": _build_f5_pool(),
    "F6_InjectConst": _build_f6_pool(),
    "F7_TriNest": _build_f7_pool(),
    "F8_Ar23Twist": _build_f8_pool(),
}


def _gen_random_sexp(leaves: int, rng: random.Random) -> str:
    if leaves == 1:
        return "S" if rng.getrandbits(1) else "K"
    l_sz = rng.randint(1, leaves - 1)
    return f"({_gen_random_sexp(l_sz, rng)} {_gen_random_sexp(leaves - l_sz, rng)})"


def _synthesize_remnant(
    a_s: int,
    rhs_s: str,
    a_k: int,
    rhs_k: str,
    budget_n: int,
    rng: random.Random,
    *,
    min_leaves: int = 4,
    max_leaves: int = 7,
) -> str:
    """动态合成 4..min(max_leaves, N-6) 叶非平凡强制残骸 E_req（含 S 与 K、非公开、可快速停机）。"""
    hi = min(max_leaves, budget_n - 6)
    lo = min(min_leaves, hi)
    # Scale remnant complexity gently with N so N=14 prefers 4-5 and N=24 prefers 6-7
    if budget_n <= 14:
        choices = [4, 4, 5]
    elif budget_n <= 18:
        choices = [4, 5, 5, 6]
    elif budget_n <= 21:
        choices = [5, 6, 6, 7]
    else:
        choices = [5, 6, 7, 7]
    choices = [x for x in choices if lo <= x <= hi] or [hi]

    for _ in range(128):
        lv = rng.choice(choices)
        expr = _gen_random_sexp(lv, rng)
        if "S" not in expr or "K" not in expr:
            continue
        if expr in PUBLIC_REQ_KEYS:
            continue
        if _expr_halts_within(a_s, rhs_s, a_k, rhs_k, expr, max_steps=32, max_size=256):
            return expr
    # Guaranteed safe fallback (always halting since head spine has <= 1 arg < min arity 2)
    fallback = "((K S) (S K))" if budget_n <= 14 else "((S (K S)) (K (S K)))"
    return fallback


# ============================================================================
# Seed Sampling & 512-Case Official Dynamic Schedule Builder
# ============================================================================
def sample_official_seeds(
    n_cases: int,
    *,
    master_seed: int | None = None,
    explicit_seeds: list[int] | None = None,
) -> tuple[list[int], str]:
    """抽取 n_cases 个互异 64-bit 正整数种子（正式模式使用 OS CSPRNG）。"""
    if n_cases < 1:
        raise ValueError("n_cases must be positive")
    if explicit_seeds is not None:
        if len(explicit_seeds) != n_cases:
            raise ValueError(f"--seeds 需要恰好 {n_cases} 个种子，实际收到 {len(explicit_seeds)} 个")
        if any(s <= 0 for s in explicit_seeds):
            raise ValueError("--seeds 中的全部种子必须为正整数")
        if len(set(explicit_seeds)) != n_cases:
            raise ValueError("--seeds 中不能包含重复种子")
        return [int(s) for s in explicit_seeds], "explicit_seeds"

    seeds: list[int] = []
    seen: set[int] = set()
    if master_seed is not None:
        rng = random.Random(int(master_seed))
        while len(seeds) < n_cases:
            s = rng.getrandbits(63) + 1
            if s not in seen:
                seen.add(s)
                seeds.append(s)
        return seeds, "fixed_master_seed"

    while len(seeds) < n_cases:
        s = secrets.randbits(63) + 1
        if s not in seen:
            seen.add(s)
            seeds.append(s)
    return seeds, "os_csprng"


def canonical_case_signature(case: dict[str, Any]) -> tuple[Any, ...]:
    """规范化测试点唯一签名（对精确 S <-> K 符号对换不变，用于去重校验）。"""
    c0 = (
        str(case["family"]),
        int(case["N"]),
        int(case["a_S"]),
        str(case["rhs_S"]),
        int(case["a_K"]),
        str(case["rhs_K"]),
        str(case.get("req", "-")),
    )
    fl = runner.apply_sk_flip(case, True)
    c1 = (
        str(fl["family"]),
        int(fl["N"]),
        int(fl["a_S"]),
        str(fl["rhs_S"]),
        int(fl["a_K"]),
        str(fl["rhs_K"]),
        str(fl.get("req", "-")),
    )
    return c0 if c0 <= c1 else c1


def build_official_schedule(
    *,
    seed: int | None = None,
    explicit_seeds: list[int] | None = None,
    enable_flip: bool = True,
    families: list[str] | None = None,
    cases_per_family: int = DEFAULT_CASES_PER_FAMILY,
    quick: bool = False,
    case_names: list[str] | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """按 512 种子动态生成 8 家族 × 64 测隐藏盲规则/残骸测试集（全批严格去重 + 排除公开规则 + 50% S<->K 同构）。"""
    if quick:
        cases_per_family = QUICK_CASES_PER_FAMILY
    if cases_per_family < 1:
        raise ValueError("cases_per_family must be positive")

    chosen_fams = list(OFFICIAL_FAMILIES)
    if families:
        unknown = [f for f in families if f not in FAMILY_POOLS]
        if unknown:
            raise ValueError(f"Unknown official families: {unknown}; valid={list(OFFICIAL_FAMILIES)}")
        chosen_fams = [f for f in OFFICIAL_FAMILIES if f in set(families)]

    # Build (family, idx_in_fam, budget_N) slots evenly distributed across OFFICIAL_BUDGETS
    slots: list[tuple[str, int, int]] = []
    for fam in chosen_fams:
        for j in range(cases_per_family):
            if cases_per_family % len(OFFICIAL_BUDGETS) == 0:
                per_b = cases_per_family // len(OFFICIAL_BUDGETS)
                budget = OFFICIAL_BUDGETS[j // per_b]
            else:
                budget = OFFICIAL_BUDGETS[j % len(OFFICIAL_BUDGETS)]
            slots.append((fam, j, budget))

    seeds, sampling_mode = sample_official_seeds(
        len(slots),
        master_seed=seed,
        explicit_seeds=explicit_seeds,
    )

    seen_case_sigs: set[tuple[Any, ...]] = set()
    seen_rules_by_fam: dict[str, set[tuple[int, str, int, str]]] = {f: set() for f in chosen_fams}
    scheduled: list[dict[str, Any]] = []

    for (fam, j, budget), case_seed in zip(slots, seeds):
        pool = FAMILY_POOLS[fam]
        prefix = FAMILY_PREFIX[fam]
        name = f"{prefix}_{j + 1:02d}"
        fam_rules_used = seen_rules_by_fam[fam]
        prefer_unique_rule_in_fam = (fam != "F1_RemnantSK" and len(pool) >= cases_per_family)

        chosen_case: dict[str, Any] | None = None
        for attempt in range(4096):
            mix = (
                (case_seed * 0x9E3779B97F4A7C15)
                ^ (attempt * 0xBF58476D1CE4E5B9)
                ^ (j * 0x94D049BB133111EB)
                ^ 0x243F6A8885A308D3
            ) & ((1 << 64) - 1)
            rng = random.Random(mix)
            a_s, rhs_s, a_k, rhs_k = pool[rng.randrange(len(pool))]
            rk = _rule_key(a_s, rhs_s, a_k, rhs_k)
            if rk in PUBLIC_RULE_KEYS:
                continue
            ck = _canonical_rule_key(*rk)
            # In first 64 attempts, also try to keep rules distinct across all 64 cases of F2..F8!
            if prefer_unique_rule_in_fam and attempt < 64 and ck in fam_rules_used:
                continue

            req = "-"
            if fam == "F1_RemnantSK":
                req = _synthesize_remnant(a_s, rhs_s, a_k, rhs_k, budget, rng, min_leaves=4, max_leaves=7)
            elif fam == "F8_Ar23Twist" and (rng.randrange(4) == 0):
                # ~25% of F8_Ar23Twist cases also combine a non-trivial 4..6-leaf halting remnant
                req = _synthesize_remnant(a_s, rhs_s, a_k, rhs_k, budget, rng, min_leaves=4, max_leaves=6)

            cand = {
                "name": name,
                "family": fam,
                "seed": int(case_seed),
                "N": int(budget),
                "a_S": int(a_s),
                "rhs_S": rhs_s,
                "a_K": int(a_k),
                "rhs_K": rhs_k,
                "req": req,
                "max_nodes": runner.DEFAULT_MAX_NODES,
                "time_limit_ms": runner.SOLVER_TIME_LIMIT_MS,
                "l_ref": 50.0,
            }
            sig = canonical_case_signature(cand)
            if sig in seen_case_sigs:
                continue
            seen_case_sigs.add(sig)
            fam_rules_used.add(ck)
            flip_rng = random.Random(case_seed ^ 0x517CC1B727220A95)
            flip = bool(flip_rng.getrandbits(1)) if enable_flip else False
            chosen_case = runner.apply_sk_flip(cand, flip)
            break

        if chosen_case is None:
            raise RuntimeError(f"Failed to synthesize unique non-public case for {fam} #{j + 1} (N={budget})")
        scheduled.append(chosen_case)

    if case_names:
        name_set = set(case_names)
        scheduled = [c for c in scheduled if c["name"] in name_set]

    sampling_info: dict[str, Any] = {
        "mode": sampling_mode,
        "master_seed": int(seed) if seed is not None else None,
        "total_cases": len(scheduled),
        "cases_per_family": cases_per_family,
        "families": chosen_fams,
        "enable_flip": bool(enable_flip),
        "seeds": [int(c["seed"]) for c in scheduled],
    }
    return sampling_info, scheduled
