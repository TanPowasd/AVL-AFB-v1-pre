# -*- coding: utf-8 -*-
"""《压缩奇点》纯 C99 + AVX2 原生 AOT JIT 编译执行器（engine/vm_jit.py v3）。

关键优化（100% 原始步数/边界/失配语义等价）：
  - 零 C++ STL、零 <windows.h> 头文件（仅 2 行 QPC extern 声明），gcc -O3 -march=native 编译延迟降低 ~5×
  - 全寄存器驻留（base / C / p / p_min / p_max / rem 全部不取地址，仅在冷路径回退时同步，杜绝栈溢出与内存重载）
  - 内联 AVX2 32 字节并行扫描（stride=1/2/4 寻零、stride=1/2 清零寻零、32 比特/周期输出比对）
  - 零除法热路径步数预算检查（a * k <= rem 短路）+ 内联 AVX2 清零（无 msvcrt!memset 调用）+ d==1 进位链短路
  - 64 KiB L2 常驻动态扩容纸带 + 64 字节两端护卫区 + 精确冷路径回退
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time
from typing import Any

import vm_ref

JIT_MAX_PROG_LEN = 32768


def _compile_ir(prog: str) -> tuple[list[dict[str, Any]], list[int]]:
    n = len(prog)
    match = [0] * n
    st: list[int] = []
    for i, c in enumerate(prog):
        if c == "[":
            st.append(i)
        elif c == "]":
            j = st.pop()
            match[j] = i
            match[i] = j

    def analyze_linear(lo: int, hi: int) -> tuple[int, int, int, list[int], bool]:
        cur = mn = mx = 0
        par: dict[int, int] = {}
        for k in range(lo, hi):
            ch = prog[k]
            if ch == ">":
                cur += 1
                if cur > mx:
                    mx = cur
            elif ch == "<":
                cur -= 1
                if cur < mn:
                    mn = cur
            elif ch == "+":
                par[cur] = par.get(cur, 0) ^ 1
        flips = sorted(off for off, v in par.items() if v)
        return cur, mn, mx, flips, (0 in flips)

    ops: list[dict[str, Any]] = []
    bstack: list[int] = []
    i = 0
    while i < n:
        c = prog[i]
        if c == ".":
            j = i + 1
            while j < n and prog[j] == ".":
                j += 1
            ops.append({"kind": "EMIT", "lo": i, "hi": j, "arg": j - i})
            i = j
            continue
        if c in (">", "<", "+"):
            j = i + 1
            cap = min(n, i + 4096)
            while j < cap and prog[j] in (">", "<", "+"):
                j += 1
            delta, mn, mx, flips, _ = analyze_linear(i, j)
            ops.append({
                "kind": "LINEAR", "lo": i, "hi": j, "delta": delta,
                "min_off": mn, "max_off": mx, "flips": flips, "raw_steps": j - i,
            })
            i = j
            continue
        if c == "[":
            close = match[i]
            blen = close - i - 1
            if blen == 0:
                ops.append({"kind": "INF_LOOP", "lo": i, "hi": close + 1})
                i = close + 1
                continue
            if blen == 1 and prog[i + 1] == "+":
                ops.append({"kind": "CLEAR", "lo": i, "hi": close + 1})
                i = close + 1
                continue
            body = prog[i + 1:close]
            if blen <= 4096 and all(ch not in "[]." for ch in body):
                if body[0] in (">", "<") and all(ch == body[0] for ch in body):
                    ops.append({
                        "kind": "SEEK_R" if body[0] == ">" else "SEEK_L",
                        "lo": i, "hi": close + 1, "stride": blen,
                    })
                    i = close + 1
                    continue
                if blen >= 2 and body[0] == "+" and body[1] in (">", "<") and all(ch == body[1] for ch in body[1:]):
                    ops.append({
                        "kind": "CLEAR_SEEK_R" if body[1] == ">" else "CLEAR_SEEK_L",
                        "lo": i, "hi": close + 1, "stride": blen - 1,
                    })
                    i = close + 1
                    continue
                delta, mn, mx, flips, fz = analyze_linear(i + 1, close)
                if delta == 0:
                    ops.append({
                        "kind": "IF_BALANCED" if fz else "INF_BALANCED",
                        "lo": i, "hi": close + 1, "min_off": mn, "max_off": mx,
                        "flips": flips, "raw_steps": blen + (2 if fz else 1),
                    })
                    i = close + 1
                    continue
            bstack.append(len(ops))
            ops.append({"kind": "JZ", "lo": i, "hi": i + 1, "target": 0})
            i += 1
            continue
        open_idx = bstack.pop()
        close_idx = len(ops)
        ops[open_idx]["target"] = close_idx + 1
        ops.append({"kind": "JNZ", "lo": i, "hi": i + 1, "target": open_idx + 1})
        i += 1
    return ops, match


def _emit_flips_code(flips: list[int], indent: str, table_defs: list[str], tid_box: list[int]) -> list[str]:
    """生成最优翻转代码：短表直接内联，等差区间用向量化循环，长不规则表用静态数组避免 O(N) 编译器膨胀。"""
    n = len(flips)
    if n == 0:
        return []
    if n <= 16:
        return [f"{indent}base[p + ({off})] ^= 1;" for off in flips]
    step = flips[1] - flips[0]
    if step > 0 and all(flips[k] - flips[k - 1] == step for k in range(2, n)):
        return [
            f"{indent}for (int64_t _k = {flips[0]}; _k <= {flips[-1]}; _k += {step}) base[p + _k] ^= 1;"
        ]
    tid = tid_box[0]
    tid_box[0] += 1
    vals = ",".join(str(x) for x in flips)
    table_defs.append(f"static const int32_t FLIP_TBL_{tid}[{n}] = {{{vals}}};")
    return [f"{indent}for (size_t _k = 0; _k < {n}ULL; ++_k) base[p + FLIP_TBL_{tid}[_k]] ^= 1;"]


def _emit_jit_c(prog: str, ops: list[dict[str, Any]], match: list[int]) -> str:
    """生成零 STL、零 <windows.h>、全寄存器驻留的纯 C99 + AVX-512/AVX2 原生验证器源码。"""
    counts = [prog.count(ch) for ch in "><+.[]"]
    prog_lit = json.dumps(prog)
    match_lit = ",".join(str(x) for x in match) if match else "0"

    table_defs: list[str] = []
    tid_box = [0]
    body: list[str] = []
    for idx, op in enumerate(ops):
        k = op["kind"]
        lo, hi = op["lo"], op["hi"]
        body.append(f"L_{idx}:;")
        if k == "LINEAR":
            d, mn, mx, rs = op["delta"], op["min_off"], op["max_off"], op["raw_steps"]
            flip_lines = _emit_flips_code(op["flips"], "    ", table_defs, tid_box)
            body.append(f"  if (LIKELY(p + ({mn}) >= -C && p + ({mx}) <= C && {rs}ULL <= rem)) {{")
            body.extend(flip_lines)
            if mn < 0:
                body.append(f"    if (p + ({mn}) < p_min) p_min = p + ({mn});")
            if mx > 0:
                body.append(f"    if (p + ({mx}) > p_max) p_max = p + ({mx});")
            if d != 0:
                body.append(f"    p += ({d});")
            body.append(f"    rem -= {rs}ULL;")
            body.append(f"  }} else if ({rs}ULL <= rem && ensure_cap(&st, p + ({mn}), p + ({mx}))) {{")
            body.append("    base = st.base; C = st.cap;")
            body.extend(flip_lines)
            if mn < 0:
                body.append(f"    if (p + ({mn}) < p_min) p_min = p + ({mn});")
            if mx > 0:
                body.append(f"    if (p + ({mx}) > p_max) p_max = p + ({mx});")
            if d != 0:
                body.append(f"    p += ({d});")
            body.append(f"    rem -= {rs}ULL;")
            body.append(f"  }} else {{ COLD_CALL({lo}, {hi}); }}")
        elif k == "EMIT":
            cnt = op["arg"]
            body.append(f"  EMIT_OP({cnt}ULL);")
        elif k == "CLEAR":
            body.append("  if (UNLIKELY(rem == 0)) { set_err(&st, \"STEP_LIMIT_EXCEEDED\", \"Exceeded step limit\"); goto L_END; }")
            body.append("  if (base[p] == 0) { --rem; } else if (LIKELY(rem >= 3ULL)) { base[p] = 0; rem -= 3ULL; } else { rem = 0; set_err(&st, \"STEP_LIMIT_EXCEEDED\", \"Exceeded step limit\"); goto L_END; }")
        elif k == "INF_LOOP":
            body.append("  if (UNLIKELY(rem == 0)) { set_err(&st, \"STEP_LIMIT_EXCEEDED\", \"Exceeded step limit\"); goto L_END; }")
            body.append("  --rem; if (UNLIKELY(base[p] != 0)) { rem = 0; set_err(&st, \"STEP_LIMIT_EXCEEDED\", \"Infinite empty loop\"); goto L_END; }")
        elif k == "IF_BALANCED":
            mn, mx, rs = op["min_off"], op["max_off"], op["raw_steps"]
            flip_lines = _emit_flips_code(op["flips"], "      ", table_defs, tid_box)
            body.append("  if (UNLIKELY(rem == 0)) { set_err(&st, \"STEP_LIMIT_EXCEEDED\", \"Exceeded step limit\"); goto L_END; }")
            body.append("  if (base[p] == 0) { --rem; } else {")
            body.append(f"    if (LIKELY(p + ({mn}) >= -C && p + ({mx}) <= C && {rs}ULL <= rem)) {{")
            body.extend(flip_lines)
            if mn < 0:
                body.append(f"      if (p + ({mn}) < p_min) p_min = p + ({mn});")
            if mx > 0:
                body.append(f"      if (p + ({mx}) > p_max) p_max = p + ({mx});")
            body.append(f"      rem -= {rs}ULL;")
            body.append(f"    }} else if ({rs}ULL <= rem && ensure_cap(&st, p + ({mn}), p + ({mx}))) {{")
            body.append("      base = st.base; C = st.cap;")
            body.extend(flip_lines)
            if mn < 0:
                body.append(f"      if (p + ({mn}) < p_min) p_min = p + ({mn});")
            if mx > 0:
                body.append(f"      if (p + ({mx}) > p_max) p_max = p + ({mx});")
            body.append(f"      rem -= {rs}ULL;")
            body.append(f"    }} else {{ COLD_CALL({lo}, {hi}); }}")
            body.append("  }")
        elif k == "INF_BALANCED":
            mn, mx, cyc = op["min_off"], op["max_off"], op["raw_steps"]
            body.append("  if (UNLIKELY(rem == 0)) { set_err(&st, \"STEP_LIMIT_EXCEEDED\", \"Exceeded step limit\"); goto L_END; }")
            body.append("  if (base[p] == 0) { --rem; } else {")
            body.append(f"    if (!ensure_cap(&st, p + ({mn}), p + ({mx}))) {{ COLD_CALL({lo}, {hi}); }} else {{")
            body.append(f"      base = st.base; C = st.cap; --rem; uint64_t full = rem / {cyc}ULL;")
            body.append("      if (full > 0) {")
            if mn < 0:
                body.append(f"        if (p + ({mn}) < p_min) p_min = p + ({mn});")
            if mx > 0:
                body.append(f"        if (p + ({mx}) > p_max) p_max = p + ({mx});")
            if op["flips"]:
                body.append("        if (full & 1ULL) {")
                body.extend(_emit_flips_code(op["flips"], "          ", table_defs, tid_box))
                body.append("        }")
            body.append(f"        rem -= full * {cyc}ULL;")
            body.append("      }")
            body.append(f"      COLD_CALL({lo + 1}, {hi});")
            body.append("      set_err(&st, \"STEP_LIMIT_EXCEEDED\", \"Exceeded step limit\"); goto L_END;")
            body.append("    }")
            body.append("  }")
        elif k == "SEEK_R":
            s = op["stride"]
            if s == 1:
                body.append(f"  SEEK_R1_OP({lo}, {hi});")
            elif s == 2:
                body.append(f"  SEEK_R2_OP({lo}, {hi});")
            elif s == 4:
                body.append(f"  SEEK_R4_OP({lo}, {hi});")
            else:
                body.append(f"  SEEK_RG_OP({s}ULL, {lo}, {hi});")
        elif k == "SEEK_L":
            s = op["stride"]
            if s == 1:
                body.append(f"  SEEK_L1_OP({lo}, {hi});")
            elif s == 2:
                body.append(f"  SEEK_L2_OP({lo}, {hi});")
            elif s == 4:
                body.append(f"  SEEK_L4_OP({lo}, {hi});")
            else:
                body.append(f"  SEEK_LG_OP({s}ULL, {lo}, {hi});")
        elif k == "CLEAR_SEEK_R":
            s = op["stride"]
            if s == 1:
                body.append(f"  CSEEK_R1_OP({lo}, {hi});")
            elif s == 2:
                body.append(f"  CSEEK_R2_OP({lo}, {hi});")
            else:
                body.append(f"  CSEEK_RG_OP({s}ULL, {lo}, {hi});")
        elif k == "CLEAR_SEEK_L":
            s = op["stride"]
            if s == 1:
                body.append(f"  CSEEK_L1_OP({lo}, {hi});")
            elif s == 2:
                body.append(f"  CSEEK_L2_OP({lo}, {hi});")
            else:
                body.append(f"  CSEEK_LG_OP({s}ULL, {lo}, {hi});")
        elif k == "JZ":
            tgt = op["target"]
            body.append("  if (UNLIKELY(rem == 0)) { set_err(&st, \"STEP_LIMIT_EXCEEDED\", \"Exceeded step limit\"); goto L_END; }")
            body.append(f"  --rem; if (base[p] == 0) goto L_{tgt};")
        elif k == "JNZ":
            tgt = op["target"]
            body.append("  if (UNLIKELY(++wd >= 32000000ULL)) { wd = 0; if (now_sec() - t0 > wall_limit_s) { set_err(&st, \"VM_TIMEOUT\", \"JIT VM timeout\"); goto L_END; } }")
            body.append("  if (UNLIKELY(rem == 0)) { set_err(&st, \"STEP_LIMIT_EXCEEDED\", \"Exceeded step limit\"); goto L_END; }")
            body.append(f"  --rem; if (base[p] != 0) goto L_{tgt};")

    body.append(f"L_{len(ops)}:")
    body_code = "\n".join(body)
    tables_code = "\n".join(table_defs)

    return f"""/* Auto-generated pure-C99 register-resident AVX-512/AVX2 AOT JIT verifier (v4) */
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#if !defined(_WIN32)
#include <time.h>
#else
__declspec(dllimport) int __stdcall QueryPerformanceCounter(int64_t* lpPerformanceCount);
__declspec(dllimport) int __stdcall QueryPerformanceFrequency(int64_t* lpFrequency);
#endif
#if defined(__AVX512BW__) || defined(__AVX2__)
#include <immintrin.h>
#endif

#define GUARD_PAD 128
#if defined(__GNUC__) || defined(__clang__)
#define LIKELY(x)   __builtin_expect(!!(x), 1)
#define UNLIKELY(x) __builtin_expect(!!(x), 0)
#define NOINLINE    __attribute__((noinline))
#else
#define LIKELY(x)   (x)
#define UNLIKELY(x) (x)
#define NOINLINE
#endif

static const char PROG_STR[] = {prog_lit};
static const size_t PROG_LEN = {len(prog)}ULL;
static const size_t MATCH_ARR[{max(1, len(prog))}] = {{{match_lit}}};
{tables_code}

static double now_sec(void) {{
#if defined(_WIN32)
    static int64_t freq = 0;
    int64_t cnt = 0;
    if (freq == 0) QueryPerformanceFrequency(&freq);
    QueryPerformanceCounter(&cnt);
    return (double)cnt / (double)freq;
#else
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return (double)ts.tv_sec + (double)ts.tv_nsec * 1e-9;
#endif
}}

typedef struct {{
    uint8_t* raw_buf;
    uint8_t* base;
    int64_t cap;
    int64_t B;
    const char* target;
    size_t L;
    int run_only;
    char* out_bits;
    size_t out_cap;
    size_t out_written;
    const char* verdict;
    char msg[256];
    int64_t p, p_min, p_max;
    uint64_t rem;
    size_t out_pos;
}} State;

static NOINLINE void set_err(State* st, const char* v, const char* m) {{
    st->verdict = v;
    snprintf(st->msg, sizeof(st->msg), "%s", m);
}}

static NOINLINE int ensure_cap(State* st, int64_t lo, int64_t hi) {{
    if (lo < -st->B || hi > st->B) return 0;
    int64_t a = lo < 0 ? -lo : lo;
    int64_t b = hi < 0 ? -hi : hi;
    int64_t need = a > b ? a : b;
    if (need <= st->cap) return 1;
    int64_t ncap = st->cap ? st->cap : 32768LL;
    while (ncap < need && ncap < st->B) {{
        ncap *= 2;
        if (ncap > st->B) ncap = st->B;
    }}
    uint8_t* nbuf = (uint8_t*)calloc((size_t)(2 * ncap + 1 + 2 * GUARD_PAD), 1);
    if (!nbuf) return 0;
    if (st->raw_buf) {{
        memcpy(nbuf + GUARD_PAD + ncap - st->cap, st->raw_buf + GUARD_PAD, (size_t)(2 * st->cap + 1));
        free(st->raw_buf);
    }}
    st->raw_buf = nbuf;
    st->cap = ncap;
    st->base = nbuf + GUARD_PAD + ncap;
    return 1;
}}

static NOINLINE int grow_next_cap(State* st, int64_t min_add) {{
    int64_t ncap = st->cap * 2;
    if (ncap < st->cap + min_add) ncap = st->cap + min_add;
    if (ncap > st->B) ncap = st->B;
    return ensure_cap(st, -ncap, ncap);
}}

static inline uint64_t scan_r1(const uint8_t* base, int64_t p, uint64_t limit) {{
    uint64_t d = 0;
    const uint8_t* ptr = base + p + 1;
#if defined(__AVX512BW__)
    const __m512i z = _mm512_setzero_si512();
    while (d + 64 <= limit) {{
        uint64_t m = (uint64_t)_mm512_cmpeq_epi8_mask(_mm512_loadu_si512(ptr + d), z);
        if (m) return d + (uint64_t)__builtin_ctzll(m) + 1;
        d += 64;
    }}
    if (d < limit) {{
        uint32_t rem = (uint32_t)(limit - d);
        uint64_t m = (uint64_t)_mm512_cmpeq_epi8_mask(_mm512_loadu_si512(ptr + d), z);
        m &= (1ULL << rem) - 1ULL;
        if (m) return d + (uint64_t)__builtin_ctzll(m) + 1;
    }}
    return 0;
#elif defined(__AVX2__)
    const __m256i z = _mm256_setzero_si256();
    while (d + 32 <= limit) {{
        __m256i v = _mm256_loadu_si256((const __m256i*)(ptr + d));
        uint32_t m = (uint32_t)_mm256_movemask_epi8(_mm256_cmpeq_epi8(v, z));
        if (m) return d + (uint64_t)__builtin_ctz(m) + 1;
        d += 32;
    }}
    if (d < limit) {{
        __m256i v = _mm256_loadu_si256((const __m256i*)(ptr + d));
        uint32_t m = (uint32_t)_mm256_movemask_epi8(_mm256_cmpeq_epi8(v, z));
        m &= (1U << (limit - d)) - 1U;
        if (m) return d + (uint64_t)__builtin_ctz(m) + 1;
    }}
    return 0;
#else
    if (limit > 0) {{
        const void* f = memchr(ptr, 0, (size_t)limit);
        if (f) return (uint64_t)((const uint8_t*)f - ptr) + 1;
    }}
    return 0;
#endif
}}

static inline uint64_t scan_l1(const uint8_t* base, int64_t p, uint64_t limit) {{
    uint64_t d = 0;
#if defined(__AVX512BW__)
    const __m512i z = _mm512_setzero_si512();
    while (d + 64 <= limit) {{
        uint64_t m = (uint64_t)_mm512_cmpeq_epi8_mask(_mm512_loadu_si512(base + p - (int64_t)d - 64), z);
        if (m) return d + (uint64_t)__builtin_clzll(m) + 1;
        d += 64;
    }}
    if (d < limit) {{
        uint32_t rem = (uint32_t)(limit - d);
        uint64_t m = (uint64_t)_mm512_cmpeq_epi8_mask(_mm512_loadu_si512(base + p - (int64_t)d - 64), z);
        m &= ~((1ULL << (64U - rem)) - 1ULL);
        if (m) return d + (uint64_t)__builtin_clzll(m) + 1;
    }}
    return 0;
#elif defined(__AVX2__)
    const __m256i z = _mm256_setzero_si256();
    while (d + 32 <= limit) {{
        __m256i v = _mm256_loadu_si256((const __m256i*)(base + p - (int64_t)d - 32));
        uint32_t m = (uint32_t)_mm256_movemask_epi8(_mm256_cmpeq_epi8(v, z));
        if (m) return d + (uint64_t)__builtin_clz(m) + 1;
        d += 32;
    }}
    if (d < limit) {{
        uint32_t rem = (uint32_t)(limit - d);
        __m256i v = _mm256_loadu_si256((const __m256i*)(base + p - (int64_t)d - 32));
        uint32_t m = (uint32_t)_mm256_movemask_epi8(_mm256_cmpeq_epi8(v, z));
        m &= ~((1U << (32U - rem)) - 1U);
        if (m) return d + (uint64_t)__builtin_clz(m) + 1;
    }}
    return 0;
#else
    for (uint64_t k = 1; k <= limit; ++k) if (base[p - (int64_t)k] == 0) return k;
    return 0;
#endif
}}

static inline uint64_t scan_r2(const uint8_t* base, int64_t p, uint64_t limit) {{
    uint64_t d = 0;
    const uint8_t* ptr = base + p + 2;
#if defined(__AVX512BW__)
    const __m512i z = _mm512_setzero_si512();
    while (d + 32 <= limit) {{
        uint64_t m = (uint64_t)_mm512_cmpeq_epi8_mask(_mm512_loadu_si512(ptr + (d << 1)), z) & 0x5555555555555555ULL;
        if (m) return d + (uint64_t)(__builtin_ctzll(m) >> 1) + 1;
        d += 32;
    }}
    if (d < limit) {{
        uint32_t rem = (uint32_t)(limit - d);
        uint64_t m = (uint64_t)_mm512_cmpeq_epi8_mask(_mm512_loadu_si512(ptr + (d << 1)), z) & 0x5555555555555555ULL;
        m &= (1ULL << (rem << 1)) - 1ULL;
        if (m) return d + (uint64_t)(__builtin_ctzll(m) >> 1) + 1;
    }}
    return 0;
#elif defined(__AVX2__)
    const __m256i z = _mm256_setzero_si256();
    while (d + 16 <= limit) {{
        __m256i v = _mm256_loadu_si256((const __m256i*)(ptr + (d << 1)));
        uint32_t m = (uint32_t)_mm256_movemask_epi8(_mm256_cmpeq_epi8(v, z)) & 0x55555555U;
        if (m) return d + (uint64_t)(__builtin_ctz(m) >> 1) + 1;
        d += 16;
    }}
    if (d < limit) {{
        uint32_t rem = (uint32_t)(limit - d);
        __m256i v = _mm256_loadu_si256((const __m256i*)(ptr + (d << 1)));
        uint32_t m = (uint32_t)_mm256_movemask_epi8(_mm256_cmpeq_epi8(v, z)) & 0x55555555U;
        m &= (1U << (rem << 1)) - 1U;
        if (m) return d + (uint64_t)(__builtin_ctz(m) >> 1) + 1;
    }}
    return 0;
#else
    for (uint64_t k = 1; k <= limit; ++k) if (base[p + (int64_t)(k << 1)] == 0) return k;
    return 0;
#endif
}}

static inline uint64_t scan_l2(const uint8_t* base, int64_t p, uint64_t limit) {{
    uint64_t d = 0;
#if defined(__AVX512BW__)
    const __m512i z = _mm512_setzero_si512();
    while (d + 32 <= limit) {{
        uint64_t m = (uint64_t)_mm512_cmpeq_epi8_mask(_mm512_loadu_si512(base + p - (int64_t)(d << 1) - 64), z) & 0x5555555555555555ULL;
        if (m) return d + (uint64_t)(__builtin_clzll(m) >> 1) + 1;
        d += 32;
    }}
    if (d < limit) {{
        uint32_t rem = (uint32_t)(limit - d);
        uint64_t m = (uint64_t)_mm512_cmpeq_epi8_mask(_mm512_loadu_si512(base + p - (int64_t)(d << 1) - 64), z) & 0x5555555555555555ULL;
        m &= ~((1ULL << (64U - (rem << 1))) - 1ULL);
        if (m) return d + (uint64_t)(__builtin_clzll(m) >> 1) + 1;
    }}
    return 0;
#elif defined(__AVX2__)
    const __m256i z = _mm256_setzero_si256();
    while (d + 16 <= limit) {{
        __m256i v = _mm256_loadu_si256((const __m256i*)(base + p - (int64_t)(d << 1) - 32));
        uint32_t m = (uint32_t)_mm256_movemask_epi8(_mm256_cmpeq_epi8(v, z)) & 0x55555555U;
        if (m) return d + (uint64_t)(__builtin_clz(m) >> 1) + 1;
        d += 16;
    }}
    if (d < limit) {{
        uint32_t rem = (uint32_t)(limit - d);
        __m256i v = _mm256_loadu_si256((const __m256i*)(base + p - (int64_t)(d << 1) - 32));
        uint32_t m = (uint32_t)_mm256_movemask_epi8(_mm256_cmpeq_epi8(v, z)) & 0x55555555U;
        m &= ~((1U << (32U - (rem << 1))) - 1U);
        if (m) return d + (uint64_t)(__builtin_clz(m) >> 1) + 1;
    }}
    return 0;
#else
    for (uint64_t k = 1; k <= limit; ++k) if (base[p - (int64_t)(k << 1)] == 0) return k;
    return 0;
#endif
}}

static inline uint64_t scan_r4(const uint8_t* base, int64_t p, uint64_t limit) {{
    uint64_t d = 0;
    const uint8_t* ptr = base + p + 4;
#if defined(__AVX512BW__)
    const __m512i z = _mm512_setzero_si512();
    while (d + 16 <= limit) {{
        uint64_t m = (uint64_t)_mm512_cmpeq_epi8_mask(_mm512_loadu_si512(ptr + (d << 2)), z) & 0x1111111111111111ULL;
        if (m) return d + (uint64_t)(__builtin_ctzll(m) >> 2) + 1;
        d += 16;
    }}
    if (d < limit) {{
        uint32_t rem = (uint32_t)(limit - d);
        uint64_t m = (uint64_t)_mm512_cmpeq_epi8_mask(_mm512_loadu_si512(ptr + (d << 2)), z) & 0x1111111111111111ULL;
        m &= (1ULL << (rem << 2)) - 1ULL;
        if (m) return d + (uint64_t)(__builtin_ctzll(m) >> 2) + 1;
    }}
    return 0;
#elif defined(__AVX2__)
    const __m256i z = _mm256_setzero_si256();
    while (d + 8 <= limit) {{
        __m256i v = _mm256_loadu_si256((const __m256i*)(ptr + (d << 2)));
        uint32_t m = (uint32_t)_mm256_movemask_epi8(_mm256_cmpeq_epi8(v, z)) & 0x11111111U;
        if (m) return d + (uint64_t)(__builtin_ctz(m) >> 2) + 1;
        d += 8;
    }}
    if (d < limit) {{
        uint32_t rem = (uint32_t)(limit - d);
        __m256i v = _mm256_loadu_si256((const __m256i*)(ptr + (d << 2)));
        uint32_t m = (uint32_t)_mm256_movemask_epi8(_mm256_cmpeq_epi8(v, z)) & 0x11111111U;
        m &= (1U << (rem << 2)) - 1U;
        if (m) return d + (uint64_t)(__builtin_ctz(m) >> 2) + 1;
    }}
    return 0;
#else
    for (uint64_t k = 1; k <= limit; ++k) if (base[p + (int64_t)(k << 2)] == 0) return k;
    return 0;
#endif
}}

static inline uint64_t scan_l4(const uint8_t* base, int64_t p, uint64_t limit) {{
    uint64_t d = 0;
#if defined(__AVX512BW__)
    const __m512i z = _mm512_setzero_si512();
    while (d + 16 <= limit) {{
        uint64_t m = (uint64_t)_mm512_cmpeq_epi8_mask(_mm512_loadu_si512(base + p - (int64_t)(d << 2) - 64), z) & 0x1111111111111111ULL;
        if (m) return d + (uint64_t)(__builtin_clzll(m) >> 2) + 1;
        d += 16;
    }}
    if (d < limit) {{
        uint32_t rem = (uint32_t)(limit - d);
        uint64_t m = (uint64_t)_mm512_cmpeq_epi8_mask(_mm512_loadu_si512(base + p - (int64_t)(d << 2) - 64), z) & 0x1111111111111111ULL;
        m &= ~((1ULL << (64U - (rem << 2))) - 1ULL);
        if (m) return d + (uint64_t)(__builtin_clzll(m) >> 2) + 1;
    }}
    return 0;
#elif defined(__AVX2__)
    const __m256i z = _mm256_setzero_si256();
    while (d + 8 <= limit) {{
        __m256i v = _mm256_loadu_si256((const __m256i*)(base + p - (int64_t)(d << 2) - 32));
        uint32_t m = (uint32_t)_mm256_movemask_epi8(_mm256_cmpeq_epi8(v, z)) & 0x11111111U;
        if (m) return d + (uint64_t)(__builtin_clz(m) >> 2) + 1;
        d += 8;
    }}
    if (d < limit) {{
        uint32_t rem = (uint32_t)(limit - d);
        __m256i v = _mm256_loadu_si256((const __m256i*)(base + p - (int64_t)(d << 2) - 32));
        uint32_t m = (uint32_t)_mm256_movemask_epi8(_mm256_cmpeq_epi8(v, z)) & 0x11111111U;
        m &= ~((1U << (32U - (rem << 2))) - 1U);
        if (m) return d + (uint64_t)(__builtin_clz(m) >> 2) + 1;
    }}
    return 0;
#else
    for (uint64_t k = 1; k <= limit; ++k) if (base[p - (int64_t)(k << 2)] == 0) return k;
    return 0;
#endif
}}

static inline void clear_s1(uint8_t* ptr, uint64_t count) {{
    if (LIKELY(count == 1)) {{ ptr[0] = 0; return; }}
    uint64_t i = 0;
#if defined(__AVX512BW__)
    if (count >= 64) {{
        const __m512i z = _mm512_setzero_si512();
        while (i + 64 <= count) {{
            _mm512_storeu_si512(ptr + i, z);
            i += 64;
        }}
        if (i < count) _mm512_storeu_si512(ptr + count - 64, z);
        return;
    }}
#endif
#if defined(__AVX2__)
    if (count >= 32) {{
        const __m256i z = _mm256_setzero_si256();
        while (i + 32 <= count) {{
            _mm256_storeu_si256((__m256i*)(ptr + i), z);
            i += 32;
        }}
        if (i < count) _mm256_storeu_si256((__m256i*)(ptr + count - 32), z);
        return;
    }}
#endif
    while (i + 8 <= count) {{
        uint64_t z64 = 0;
        memcpy(ptr + i, &z64, 8);
        i += 8;
    }}
    while (i < count) {{ ptr[i++] = 0; }}
}}

static inline void clear_s2(uint8_t* ptr, uint64_t count) {{
    if (LIKELY(count == 1)) {{ ptr[0] = 0; return; }}
    uint64_t i = 0;
#if defined(__AVX512BW__)
    const __m512i keep_odd512 = _mm512_set1_epi16((short)0xFF00);
    while (i + 32 <= count) {{
        void* p512 = ptr + (i << 1);
        _mm512_storeu_si512(p512, _mm512_and_si512(_mm512_loadu_si512(p512), keep_odd512));
        i += 32;
    }}
#endif
#if defined(__AVX2__)
    const __m256i keep_odd = _mm256_set1_epi16((short)0xFF00);
    while (i + 16 <= count) {{
        __m256i* p256 = (__m256i*)(ptr + (i << 1));
        _mm256_storeu_si256(p256, _mm256_and_si256(_mm256_loadu_si256(p256), keep_odd));
        i += 16;
    }}
#endif
    while (i < count) {{ ptr[i << 1] = 0; ++i; }}
}}

static inline uint64_t find_mm(const char* ptr, uint64_t n, char bit) {{
    uint64_t i = 0;
#if defined(__AVX512BW__)
    const __m512i pat512 = _mm512_set1_epi8(bit);
    while (i + 64 <= n) {{
        uint64_t m = (uint64_t)_mm512_cmpneq_epi8_mask(_mm512_loadu_si512(ptr + i), pat512);
        if (m) return i + (uint64_t)__builtin_ctzll(m);
        i += 64;
    }}
#endif
#if defined(__AVX2__)
    const __m256i pat = _mm256_set1_epi8(bit);
    while (i + 32 <= n) {{
        __m256i v = _mm256_loadu_si256((const __m256i*)(ptr + i));
        uint32_t m = (uint32_t)_mm256_movemask_epi8(_mm256_cmpeq_epi8(v, pat));
        if (m != 0xFFFFFFFFU) return i + (uint64_t)__builtin_ctz(~m);
        i += 32;
    }}
#endif
    while (i < n) {{ if (ptr[i] != bit) return i; ++i; }}
    return n;
}}

static NOINLINE int cold_run(State* st, size_t ip, size_t end_ip) {{
    int64_t p = st->p, mn = st->p_min, mx = st->p_max;
    uint64_t rem = st->rem;
    size_t out_pos = st->out_pos;
    while (ip < end_ip) {{
        if (rem == 0) {{ set_err(st, "STEP_LIMIT_EXCEEDED", "Exceeded step limit"); goto FAIL; }}
        char c = PROG_STR[ip];
        --rem;
        switch (c) {{
            case '>':
                ++p;
                if (p > st->B) {{ set_err(st, "TAPE_OUT_OF_BOUNDS", "Head exceeded +B"); goto FAIL; }}
                if (p > st->cap) ensure_cap(st, p, p);
                if (p > mx) mx = p;
                ++ip; break;
            case '<':
                --p;
                if (p < -st->B) {{ set_err(st, "TAPE_OUT_OF_BOUNDS", "Head exceeded -B"); goto FAIL; }}
                if (p < -st->cap) ensure_cap(st, p, p);
                if (p < mn) mn = p;
                ++ip; break;
            case '+': st->base[p] ^= 1; ++ip; break;
            case '.': {{
                char bit = st->base[p] ? '1' : '0';
                if (st->run_only) {{
                    if (st->out_written < st->out_cap) st->out_bits[st->out_written++] = bit;
                    ++out_pos; ++ip;
                }} else {{
                    if (out_pos >= st->L) {{ set_err(st, "OUTPUT_MISMATCH", "Output length exceeded L"); goto FAIL; }}
                    if (st->target[out_pos] != bit) {{ set_err(st, "OUTPUT_MISMATCH", "Bit mismatch"); goto FAIL; }}
                    ++out_pos; ++ip;
                }}
                break;
            }}
            case '[': ip = (st->base[p] == 0) ? (MATCH_ARR[ip] + 1) : (ip + 1); break;
            case ']': ip = (st->base[p] != 0) ? (MATCH_ARR[ip] + 1) : (ip + 1); break;
        }}
    }}
    st->p = p; st->p_min = mn; st->p_max = mx; st->rem = rem; st->out_pos = out_pos;
    return 1;
FAIL:
    st->p = p; st->p_min = mn; st->p_max = mx; st->rem = rem; st->out_pos = out_pos;
    return 0;
}}

static NOINLINE int do_emit_cold(State* st, uint64_t k) {{
    uint64_t rem = st->rem;
    if (rem == 0) {{ set_err(st, "STEP_LIMIT_EXCEEDED", "Exceeded step limit"); return 0; }}
    char bit = st->base[st->p] ? '1' : '0';
    if (st->run_only) {{
        uint64_t take = k <= rem ? k : rem;
        if (st->out_written < st->out_cap) {{
            size_t room = st->out_cap - st->out_written;
            size_t n = take < room ? (size_t)take : room;
            memset(st->out_bits + st->out_written, bit, n);
            st->out_written += n;
        }}
        st->out_pos += (size_t)take;
        if (k <= rem) {{ st->rem = rem - k; return 1; }}
        st->rem = 0; set_err(st, "STEP_LIMIT_EXCEEDED", "Exceeded step limit"); return 0;
    }}
    size_t out_pos = st->out_pos;
    uint64_t avail = (out_pos < st->L) ? (uint64_t)(st->L - out_pos) : 0ULL;
    uint64_t chk = k < avail ? k : avail;
    uint64_t mm = (chk == 1) ? (st->target[out_pos] == bit ? 1ULL : 0ULL) : find_mm(st->target + out_pos, chk, bit);
    if (mm == chk && k <= avail) {{
        if (k <= rem) {{ st->out_pos = out_pos + (size_t)k; st->rem = rem - k; return 1; }}
        st->out_pos = out_pos + (size_t)rem; st->rem = 0; set_err(st, "STEP_LIMIT_EXCEEDED", "Exceeded step limit"); return 0;
    }}
    uint64_t need = mm + 1;
    if (need <= rem) {{
        st->out_pos = out_pos + (size_t)mm; st->rem = rem - need;
        set_err(st, "OUTPUT_MISMATCH", (st->out_pos >= st->L) ? "Output length exceeded L" : "Bit mismatch");
        return 0;
    }}
    st->out_pos = out_pos + (size_t)rem; st->rem = 0; set_err(st, "STEP_LIMIT_EXCEEDED", "Exceeded step limit"); return 0;
}}

#define COLD_CALL(lo_, hi_) do {{ \
    st.p = p; st.p_min = p_min; st.p_max = p_max; st.rem = rem; st.out_pos = out_pos; \
    int _ok = cold_run(&st, (lo_), (hi_)); \
    p = st.p; p_min = st.p_min; p_max = st.p_max; rem = st.rem; out_pos = st.out_pos; \
    base = st.base; C = st.cap; \
    if (!_ok) goto L_END; \
}} while (0)

#define EMIT_OP(k_) do {{ \
    if (LIKELY(!run_only && (k_) == 1ULL && out_pos < target_L && rem >= 1ULL && target_ptr[out_pos] == (base[p] ? '1' : '0'))) {{ \
        ++out_pos; --rem; \
    }} else {{ \
        st.p = p; st.rem = rem; st.out_pos = out_pos; \
        int _ok = do_emit_cold(&st, (k_)); \
        rem = st.rem; out_pos = st.out_pos; \
        if (!_ok) goto L_END; \
    }} \
}} while (0)

#define SEEK_R1_OP(lo_, hi_) do {{ \
    if (UNLIKELY(rem == 0)) {{ set_err(&st, "STEP_LIMIT_EXCEEDED", "Exceeded step limit"); goto L_END; }} \
    --rem; \
    if (base[p] != 0) {{ \
        for (;;) {{ \
            uint64_t _a = (uint64_t)(C - p); \
            uint64_t _lim = LIKELY((_a << 1) <= rem) ? _a : (rem >> 1); \
            if (_lim > 0) {{ \
                uint64_t _d = scan_r1(base, p, _lim); \
                if (LIKELY(_d > 0)) {{ p += (int64_t)_d; if (p > p_max) p_max = p; rem -= _d << 1; break; }} \
                p += (int64_t)_lim; if (p > p_max) p_max = p; rem -= _lim << 1; \
            }} \
            if (C < B && rem >= 2 && (C - p) < 1 && grow_next_cap(&st, 64)) {{ base = st.base; C = st.cap; continue; }} \
            COLD_CALL((lo_) + 1, (hi_)); break; \
        }} \
    }} \
}} while (0)

#define SEEK_L1_OP(lo_, hi_) do {{ \
    if (UNLIKELY(rem == 0)) {{ set_err(&st, "STEP_LIMIT_EXCEEDED", "Exceeded step limit"); goto L_END; }} \
    --rem; \
    if (base[p] != 0) {{ \
        for (;;) {{ \
            uint64_t _a = (uint64_t)(p + C); \
            uint64_t _lim = LIKELY((_a << 1) <= rem) ? _a : (rem >> 1); \
            if (_lim > 0) {{ \
                uint64_t _d = scan_l1(base, p, _lim); \
                if (LIKELY(_d > 0)) {{ p -= (int64_t)_d; if (p < p_min) p_min = p; rem -= _d << 1; break; }} \
                p -= (int64_t)_lim; if (p < p_min) p_min = p; rem -= _lim << 1; \
            }} \
            if (C < B && rem >= 2 && (p + C) < 1 && grow_next_cap(&st, 64)) {{ base = st.base; C = st.cap; continue; }} \
            COLD_CALL((lo_) + 1, (hi_)); break; \
        }} \
    }} \
}} while (0)

#define SEEK_R2_OP(lo_, hi_) do {{ \
    if (UNLIKELY(rem == 0)) {{ set_err(&st, "STEP_LIMIT_EXCEEDED", "Exceeded step limit"); goto L_END; }} \
    --rem; \
    if (base[p] != 0) {{ \
        for (;;) {{ \
            uint64_t _a = (uint64_t)(C - p) >> 1; \
            uint64_t _lim = LIKELY(_a * 3ULL <= rem) ? _a : (rem / 3ULL); \
            if (_lim > 0) {{ \
                uint64_t _d = scan_r2(base, p, _lim); \
                if (LIKELY(_d > 0)) {{ p += (int64_t)(_d << 1); if (p > p_max) p_max = p; rem -= _d * 3ULL; break; }} \
                p += (int64_t)(_lim << 1); if (p > p_max) p_max = p; rem -= _lim * 3ULL; \
            }} \
            if (C < B && rem >= 3 && (C - p) < 2 && grow_next_cap(&st, 64)) {{ base = st.base; C = st.cap; continue; }} \
            COLD_CALL((lo_) + 1, (hi_)); break; \
        }} \
    }} \
}} while (0)

#define SEEK_L2_OP(lo_, hi_) do {{ \
    if (UNLIKELY(rem == 0)) {{ set_err(&st, "STEP_LIMIT_EXCEEDED", "Exceeded step limit"); goto L_END; }} \
    --rem; \
    if (base[p] != 0) {{ \
        for (;;) {{ \
            uint64_t _a = (uint64_t)(p + C) >> 1; \
            uint64_t _lim = LIKELY(_a * 3ULL <= rem) ? _a : (rem / 3ULL); \
            if (_lim > 0) {{ \
                uint64_t _d = scan_l2(base, p, _lim); \
                if (LIKELY(_d > 0)) {{ p -= (int64_t)(_d << 1); if (p < p_min) p_min = p; rem -= _d * 3ULL; break; }} \
                p -= (int64_t)(_lim << 1); if (p < p_min) p_min = p; rem -= _lim * 3ULL; \
            }} \
            if (C < B && rem >= 3 && (p + C) < 2 && grow_next_cap(&st, 64)) {{ base = st.base; C = st.cap; continue; }} \
            COLD_CALL((lo_) + 1, (hi_)); break; \
        }} \
    }} \
}} while (0)

#define SEEK_R4_OP(lo_, hi_) do {{ \
    if (UNLIKELY(rem == 0)) {{ set_err(&st, "STEP_LIMIT_EXCEEDED", "Exceeded step limit"); goto L_END; }} \
    --rem; \
    if (base[p] != 0) {{ \
        for (;;) {{ \
            uint64_t _a = (uint64_t)(C - p) >> 2; \
            uint64_t _lim = LIKELY(_a * 5ULL <= rem) ? _a : (rem / 5ULL); \
            if (_lim > 0) {{ \
                uint64_t _d = scan_r4(base, p, _lim); \
                if (LIKELY(_d > 0)) {{ p += (int64_t)(_d << 2); if (p > p_max) p_max = p; rem -= _d * 5ULL; break; }} \
                p += (int64_t)(_lim << 2); if (p > p_max) p_max = p; rem -= _lim * 5ULL; \
            }} \
            if (C < B && rem >= 5 && (C - p) < 4 && grow_next_cap(&st, 64)) {{ base = st.base; C = st.cap; continue; }} \
            COLD_CALL((lo_) + 1, (hi_)); break; \
        }} \
    }} \
}} while (0)

#define SEEK_L4_OP(lo_, hi_) do {{ \
    if (UNLIKELY(rem == 0)) {{ set_err(&st, "STEP_LIMIT_EXCEEDED", "Exceeded step limit"); goto L_END; }} \
    --rem; \
    if (base[p] != 0) {{ \
        for (;;) {{ \
            uint64_t _a = (uint64_t)(p + C) >> 2; \
            uint64_t _lim = LIKELY(_a * 5ULL <= rem) ? _a : (rem / 5ULL); \
            if (_lim > 0) {{ \
                uint64_t _d = scan_l4(base, p, _lim); \
                if (LIKELY(_d > 0)) {{ p -= (int64_t)(_d << 2); if (p < p_min) p_min = p; rem -= _d * 5ULL; break; }} \
                p -= (int64_t)(_lim << 2); if (p < p_min) p_min = p; rem -= _lim * 5ULL; \
            }} \
            if (C < B && rem >= 5 && (p + C) < 4 && grow_next_cap(&st, 64)) {{ base = st.base; C = st.cap; continue; }} \
            COLD_CALL((lo_) + 1, (hi_)); break; \
        }} \
    }} \
}} while (0)

#define CSEEK_R1_OP(lo_, hi_) do {{ \
    if (UNLIKELY(rem == 0)) {{ set_err(&st, "STEP_LIMIT_EXCEEDED", "Exceeded step limit"); goto L_END; }} \
    --rem; \
    if (base[p] != 0) {{ \
        if (LIKELY(p + 1 <= C && rem >= 3ULL) && base[p + 1] == 0) {{ \
            base[p] = 0; ++p; if (p > p_max) p_max = p; rem -= 3ULL; \
        }} else {{ \
            for (;;) {{ \
                uint64_t _a = (uint64_t)(C - p); \
                uint64_t _lim = LIKELY(_a * 3ULL <= rem) ? _a : (rem / 3ULL); \
                if (_lim > 0) {{ \
                    uint64_t _d = scan_r1(base, p, _lim); \
                    if (LIKELY(_d > 0)) {{ clear_s1(base + p, _d); p += (int64_t)_d; if (p > p_max) p_max = p; rem -= _d * 3ULL; break; }} \
                    clear_s1(base + p, _lim); p += (int64_t)_lim; if (p > p_max) p_max = p; rem -= _lim * 3ULL; \
                }} \
                if (C < B && rem >= 3 && (C - p) < 1 && grow_next_cap(&st, 64)) {{ base = st.base; C = st.cap; continue; }} \
                COLD_CALL((lo_) + 1, (hi_)); break; \
            }} \
        }} \
    }} \
}} while (0)

#define CSEEK_L1_OP(lo_, hi_) do {{ \
    if (UNLIKELY(rem == 0)) {{ set_err(&st, "STEP_LIMIT_EXCEEDED", "Exceeded step limit"); goto L_END; }} \
    --rem; \
    if (base[p] != 0) {{ \
        if (LIKELY(p - 1 >= -C && rem >= 3ULL) && base[p - 1] == 0) {{ \
            base[p] = 0; --p; if (p < p_min) p_min = p; rem -= 3ULL; \
        }} else {{ \
            for (;;) {{ \
                uint64_t _a = (uint64_t)(p + C); \
                uint64_t _lim = LIKELY(_a * 3ULL <= rem) ? _a : (rem / 3ULL); \
                if (_lim > 0) {{ \
                    uint64_t _d = scan_l1(base, p, _lim); \
                    if (LIKELY(_d > 0)) {{ clear_s1(base + p - (int64_t)_d + 1, _d); p -= (int64_t)_d; if (p < p_min) p_min = p; rem -= _d * 3ULL; break; }} \
                    clear_s1(base + p - (int64_t)_lim + 1, _lim); p -= (int64_t)_lim; if (p < p_min) p_min = p; rem -= _lim * 3ULL; \
                }} \
                if (C < B && rem >= 3 && (p + C) < 1 && grow_next_cap(&st, 64)) {{ base = st.base; C = st.cap; continue; }} \
                COLD_CALL((lo_) + 1, (hi_)); break; \
            }} \
        }} \
    }} \
}} while (0)

#define CSEEK_R2_OP(lo_, hi_) do {{ \
    if (UNLIKELY(rem == 0)) {{ set_err(&st, "STEP_LIMIT_EXCEEDED", "Exceeded step limit"); goto L_END; }} \
    --rem; \
    if (base[p] != 0) {{ \
        if (LIKELY(p + 2 <= C && rem >= 4ULL) && base[p + 2] == 0) {{ \
            base[p] = 0; p += 2; if (p > p_max) p_max = p; rem -= 4ULL; \
        }} else {{ \
            for (;;) {{ \
                uint64_t _a = (uint64_t)(C - p) >> 1; \
                uint64_t _lim = LIKELY((_a << 2) <= rem) ? _a : (rem >> 2); \
                if (_lim > 0) {{ \
                    uint64_t _d = scan_r2(base, p, _lim); \
                    if (LIKELY(_d > 0)) {{ clear_s2(base + p, _d); p += (int64_t)(_d << 1); if (p > p_max) p_max = p; rem -= _d << 2; break; }} \
                    clear_s2(base + p, _lim); p += (int64_t)(_lim << 1); if (p > p_max) p_max = p; rem -= _lim << 2; \
                }} \
                if (C < B && rem >= 4 && (C - p) < 2 && grow_next_cap(&st, 64)) {{ base = st.base; C = st.cap; continue; }} \
                COLD_CALL((lo_) + 1, (hi_)); break; \
            }} \
        }} \
    }} \
}} while (0)

#define CSEEK_L2_OP(lo_, hi_) do {{ \
    if (UNLIKELY(rem == 0)) {{ set_err(&st, "STEP_LIMIT_EXCEEDED", "Exceeded step limit"); goto L_END; }} \
    --rem; \
    if (base[p] != 0) {{ \
        if (LIKELY(p - 2 >= -C && rem >= 4ULL) && base[p - 2] == 0) {{ \
            base[p] = 0; p -= 2; if (p < p_min) p_min = p; rem -= 4ULL; \
        }} else {{ \
            for (;;) {{ \
                uint64_t _a = (uint64_t)(p + C) >> 1; \
                uint64_t _lim = LIKELY((_a << 2) <= rem) ? _a : (rem >> 2); \
                if (_lim > 0) {{ \
                    uint64_t _d = scan_l2(base, p, _lim); \
                    if (LIKELY(_d > 0)) {{ clear_s2(base + p - (int64_t)((_d - 1) << 1), _d); p -= (int64_t)(_d << 1); if (p < p_min) p_min = p; rem -= _d << 2; break; }} \
                    clear_s2(base + p - (int64_t)((_lim - 1) << 1), _lim); p -= (int64_t)(_lim << 1); if (p < p_min) p_min = p; rem -= _lim << 2; \
                }} \
                if (C < B && rem >= 4 && (p + C) < 2 && grow_next_cap(&st, 64)) {{ base = st.base; C = st.cap; continue; }} \
                COLD_CALL((lo_) + 1, (hi_)); break; \
            }} \
        }} \
    }} \
}} while (0)

#define SEEK_RG_OP(s_, lo_, hi_) do {{ \
    if (UNLIKELY(rem == 0)) {{ set_err(&st, "STEP_LIMIT_EXCEEDED", "Exceeded step limit"); goto L_END; }} \
    --rem; \
    if (base[p] != 0) {{ \
        for (;;) {{ \
            uint64_t _a = (uint64_t)(C - p) / (s_); \
            uint64_t _lim = LIKELY(_a * ((s_) + 1ULL) <= rem) ? _a : (rem / ((s_) + 1ULL)); \
            int64_t _q = p; int _hit = 0; \
            for (uint64_t _d = 1; _d <= _lim; ++_d) {{ \
                _q += (int64_t)(s_); \
                if (base[_q] == 0) {{ p = _q; if (p > p_max) p_max = p; rem -= _d * ((s_) + 1ULL); _hit = 1; break; }} \
            }} \
            if (_hit) break; \
            p = _q; if (p > p_max) p_max = p; rem -= _lim * ((s_) + 1ULL); \
            if (C < B && rem >= ((s_) + 1ULL) && (uint64_t)(C - p) < (s_) && grow_next_cap(&st, (int64_t)(s_))) {{ base = st.base; C = st.cap; continue; }} \
            COLD_CALL((lo_) + 1, (hi_)); break; \
        }} \
    }} \
}} while (0)

#define SEEK_LG_OP(s_, lo_, hi_) do {{ \
    if (UNLIKELY(rem == 0)) {{ set_err(&st, "STEP_LIMIT_EXCEEDED", "Exceeded step limit"); goto L_END; }} \
    --rem; \
    if (base[p] != 0) {{ \
        for (;;) {{ \
            uint64_t _a = (uint64_t)(p + C) / (s_); \
            uint64_t _lim = LIKELY(_a * ((s_) + 1ULL) <= rem) ? _a : (rem / ((s_) + 1ULL)); \
            int64_t _q = p; int _hit = 0; \
            for (uint64_t _d = 1; _d <= _lim; ++_d) {{ \
                _q -= (int64_t)(s_); \
                if (base[_q] == 0) {{ p = _q; if (p < p_min) p_min = p; rem -= _d * ((s_) + 1ULL); _hit = 1; break; }} \
            }} \
            if (_hit) break; \
            p = _q; if (p < p_min) p_min = p; rem -= _lim * ((s_) + 1ULL); \
            if (C < B && rem >= ((s_) + 1ULL) && (uint64_t)(p + C) < (s_) && grow_next_cap(&st, (int64_t)(s_))) {{ base = st.base; C = st.cap; continue; }} \
            COLD_CALL((lo_) + 1, (hi_)); break; \
        }} \
    }} \
}} while (0)

#define CSEEK_RG_OP(s_, lo_, hi_) do {{ \
    if (UNLIKELY(rem == 0)) {{ set_err(&st, "STEP_LIMIT_EXCEEDED", "Exceeded step limit"); goto L_END; }} \
    --rem; \
    if (base[p] != 0) {{ \
        for (;;) {{ \
            uint64_t _a = (uint64_t)(C - p) / (s_); \
            uint64_t _lim = LIKELY(_a * ((s_) + 2ULL) <= rem) ? _a : (rem / ((s_) + 2ULL)); \
            int64_t _q = p; int _hit = 0; \
            for (uint64_t _d = 1; _d <= _lim; ++_d) {{ \
                base[_q] = 0; _q += (int64_t)(s_); \
                if (base[_q] == 0) {{ p = _q; if (p > p_max) p_max = p; rem -= _d * ((s_) + 2ULL); _hit = 1; break; }} \
            }} \
            if (_hit) break; \
            p = _q; if (p > p_max) p_max = p; rem -= _lim * ((s_) + 2ULL); \
            if (C < B && rem >= ((s_) + 2ULL) && (uint64_t)(C - p) < (s_) && grow_next_cap(&st, (int64_t)(s_))) {{ base = st.base; C = st.cap; continue; }} \
            COLD_CALL((lo_) + 1, (hi_)); break; \
        }} \
    }} \
}} while (0)

#define CSEEK_LG_OP(s_, lo_, hi_) do {{ \
    if (UNLIKELY(rem == 0)) {{ set_err(&st, "STEP_LIMIT_EXCEEDED", "Exceeded step limit"); goto L_END; }} \
    --rem; \
    if (base[p] != 0) {{ \
        for (;;) {{ \
            uint64_t _a = (uint64_t)(p + C) / (s_); \
            uint64_t _lim = LIKELY(_a * ((s_) + 2ULL) <= rem) ? _a : (rem / ((s_) + 2ULL)); \
            int64_t _q = p; int _hit = 0; \
            for (uint64_t _d = 1; _d <= _lim; ++_d) {{ \
                base[_q] = 0; _q -= (int64_t)(s_); \
                if (base[_q] == 0) {{ p = _q; if (p < p_min) p_min = p; rem -= _d * ((s_) + 2ULL); _hit = 1; break; }} \
            }} \
            if (_hit) break; \
            p = _q; if (p < p_min) p_min = p; rem -= _lim * ((s_) + 2ULL); \
            if (C < B && rem >= ((s_) + 2ULL) && (uint64_t)(p + C) < (s_) && grow_next_cap(&st, (int64_t)(s_))) {{ base = st.base; C = st.cap; continue; }} \
            COLD_CALL((lo_) + 1, (hi_)); break; \
        }} \
    }} \
}} while (0)

int main(int argc, char** argv) {{
    double t0 = now_sec();
    const char* target_path = (argc >= 2) ? argv[1] : "-";
    uint64_t M = 3000000000000ULL;
    int64_t B = 10000000LL;
    double wall_limit_s = 600.0;
    int run_only = (strcmp(target_path, "-") == 0);
    size_t max_out_bits = 2000000ULL;

    for (int i = 2; i < argc; ++i) {{
        if (strcmp(argv[i], "--step-limit") == 0 && i + 1 < argc) M = strtoull(argv[++i], NULL, 10);
        else if (strcmp(argv[i], "--tape-bound") == 0 && i + 1 < argc) B = strtoll(argv[++i], NULL, 10);
        else if (strcmp(argv[i], "--cpu-limit-s") == 0 && i + 1 < argc) wall_limit_s = strtod(argv[++i], NULL);
        else if (strcmp(argv[i], "--run-only") == 0) run_only = 1;
        else if (strcmp(argv[i], "--max-out-bits") == 0 && i + 1 < argc) max_out_bits = (size_t)strtoull(argv[++i], NULL, 10);
    }}

    State st;
    memset(&st, 0, sizeof(st));
    st.B = B;
    st.run_only = run_only;
    st.verdict = "AC";
    strcpy(st.msg, "OK");

    char* target_buf = NULL;
    if (!run_only) {{
        FILE* f = fopen(target_path, "rb");
        if (!f) return 3;
        fseek(f, 0, SEEK_END);
        long sz = ftell(f);
        fseek(f, 0, SEEK_SET);
        if (sz <= 0) {{ fclose(f); return 3; }}
        target_buf = (char*)malloc((size_t)sz + 2);
        if (!target_buf || fread(target_buf, 1, (size_t)sz, f) != (size_t)sz) {{ fclose(f); return 3; }}
        target_buf[sz] = '\\0';
        fclose(f);
        char* nl = strchr(target_buf, '\\n');
        if (!nl) return 3;
        st.L = (size_t)strtoull(target_buf, NULL, 10);
        st.target = nl + 1;
    }} else {{
        st.out_cap = max_out_bits;
        st.out_bits = (char*)malloc(max_out_bits + 1);
    }}

    int64_t init_cap = B < 32768LL ? B : 32768LL;
    st.cap = init_cap;
    st.raw_buf = (uint8_t*)calloc((size_t)(2 * init_cap + 1 + 2 * GUARD_PAD), 1);
    st.base = st.raw_buf + GUARD_PAD + init_cap;

    uint8_t* base = st.base;
    int64_t C = st.cap;
    const char* target_ptr = st.target;
    const size_t target_L = st.L;
    int64_t p = 0, p_min = 0, p_max = 0;
    uint64_t rem = M, wd = 0;
    size_t out_pos = 0;

{body_code}

    if (!run_only && out_pos != target_L) {{
        set_err(&st, "OUTPUT_MISMATCH", "Output too short");
    }}

L_END:
    double elapsed_ms = (now_sec() - t0) * 1000.0;
    uint64_t steps = M - rem;
    printf("{{\\"verdict\\":\\"%s\\",\\"message\\":\\"%s\\",\\"prog_len\\":%llu,\\"ir_ops\\":%d,"
           "\\"steps\\":%llu,\\"out_len\\":%llu,\\"target_len\\":%llu,"
           "\\"p_final\\":%lld,\\"p_min\\":%lld,\\"p_max\\":%lld,\\"elapsed_ms\\":%.4f,"
#if defined(__AVX512BW__)
           "\\"engine\\":\\"jit_c99_avx512\\","
#elif defined(__AVX2__)
           "\\"engine\\":\\"jit_c99_avx2\\","
#else
           "\\"engine\\":\\"jit_c99\\","
#endif
           "\\"op_counts\\":{{\\">\\":{counts[0]},\\"<\\":{counts[1]},\\"+\\":{counts[2]},"
           "\\".\\":{counts[3]},\\"[\\":{counts[4]},\\"]\\":{counts[5]}}}",
           st.verdict, st.msg, (unsigned long long)PROG_LEN, {len(ops)},
           (unsigned long long)steps, (unsigned long long)out_pos, (unsigned long long)target_L,
           (long long)p, (long long)p_min, (long long)p_max, elapsed_ms);
    if (run_only) {{
        st.out_bits[st.out_written] = '\\0';
        printf(",\\"out_bits\\":\\"%s\\"", st.out_bits);
    }}
    printf("}}\\n");
    free(st.raw_buf);
    free(target_buf);
    free(st.out_bits);
    return 0;
}}
"""


def compile_and_run_jit(
    target_input_bytes: bytes | None,
    program_bytes: bytes,
    *,
    step_limit: int = 3 * 10**12,
    tape_bound: int = 10**7,
    max_prog_len: int | None = None,
    cpu_limit_s: float = 600.0,
    run_only: bool = False,
    max_out_bits: int = 2_000_000,
) -> dict[str, Any]:
    """将程序 P 编译为纯 C99 + AVX2 原生机器码并精确校验或运行。"""
    prog_str = vm_ref.strip_single_trailing_newline(program_bytes.decode("utf-8", errors="surrogateescape"))
    L = 0
    if not run_only and target_input_bytes is not None:
        first_nl = target_input_bytes.find(b"\n")
        if first_nl > 0:
            L = int(target_input_bytes[:first_nl].strip())
    eff_max = max_prog_len if max_prog_len is not None else (max(len(prog_str), 10**7) if run_only else 2 * L + 100)
    ok, verdict, msg, _ = vm_ref.validate_program(prog_str, eff_max)
    if not ok:
        return {
            "verdict": verdict,
            "message": msg,
            "prog_len": len(prog_str),
            "steps": 0,
            "out_len": 0,
            "target_len": L,
            "p_final": 0,
            "p_min": 0,
            "p_max": 0,
            "elapsed_ms": 0.0,
            "engine": "jit_c99_avx2",
            "op_counts": {k: prog_str.count(k) for k in "><+.[]"},
        }
    if len(prog_str) > JIT_MAX_PROG_LEN:
        raise ValueError(f"Program length {len(prog_str)} exceeds JIT limit {JIT_MAX_PROG_LEN}")

    ops, match = _compile_ir(prog_str)
    c_src = _emit_jit_c(prog_str, ops, match)

    digest = hashlib.sha256(c_src.encode("utf-8")).hexdigest()[:16]
    ext = ".exe" if os.name == "nt" else ""
    uid = os.getuid() if hasattr(os, "getuid") else os.environ.get("USERNAME", "win")
    cache_exe = Path(tempfile.gettempdir()) / f"afb_cs_jitc4_{uid}_{digest}{ext}"

    compile_ms = 0.0
    if not (cache_exe.is_file() and cache_exe.stat().st_size > 0):
        cc = os.environ.get("CC") or ("gcc" if shutil.which("gcc") else os.environ.get("CXX", "g++"))
        with tempfile.TemporaryDirectory(prefix="afb_cs_jit_build_") as tmp:
            src_p = Path(tmp) / "jit.c"
            exe_p = Path(tmp) / f"jit{ext}"
            src_p.write_text(c_src, encoding="utf-8", newline="\n")
            tc0 = time.perf_counter()
            last_err = ""
            for flags in (
                ["-std=gnu11", "-O3", "-march=native", "-funroll-loops", "-pipe"],
                ["-std=gnu11", "-O3", "-pipe"],
            ):
                proc = subprocess.run(
                    [cc, *flags, str(src_p), "-o", str(exe_p)],
                    capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60,
                )
                if proc.returncode == 0 and exe_p.is_file():
                    break
                last_err = (proc.stdout or "") + "\n" + (proc.stderr or "")
            else:
                raise RuntimeError(f"JIT compile failed ({cc}):\n{last_err}")
            compile_ms = (time.perf_counter() - tc0) * 1000.0
            try:
                shutil.copy2(exe_p, cache_exe)
            except OSError:
                if not cache_exe.is_file():
                    raise

    with tempfile.TemporaryDirectory(prefix="afb_cs_jit_run_") as tmp:
        t_arg = "-"
        if not run_only and target_input_bytes is not None:
            tp = Path(tmp) / "target.in"
            tp.write_bytes(target_input_bytes)
            t_arg = str(tp)
        cmd = [
            str(cache_exe), t_arg,
            "--step-limit", str(int(step_limit)),
            "--tape-bound", str(int(tape_bound)),
            "--cpu-limit-s", str(float(cpu_limit_s)),
        ]
        if run_only:
            cmd.extend(["--run-only", "--max-out-bits", str(int(max_out_bits))])
        wall_timeout = max(5.0, float(cpu_limit_s) + 15.0)
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=wall_timeout)
        except subprocess.TimeoutExpired:
            return {
                "verdict": "VM_TIMEOUT",
                "message": f"JIT VM exceeded watchdog ({cpu_limit_s:.0f}s)",
                "prog_len": len(prog_str),
                "steps": step_limit,
                "out_len": 0,
                "target_len": L,
                "p_final": 0,
                "p_min": 0,
                "p_max": 0,
                "elapsed_ms": wall_timeout * 1000.0,
                "compile_ms": round(compile_ms, 2),
                "engine": "jit_c99_avx2",
                "op_counts": {k: prog_str.count(k) for k in "><+.[]"},
            }
        if proc.returncode != 0 or not proc.stdout.strip():
            raise RuntimeError(f"JIT binary failed (rc={proc.returncode}): {proc.stderr}")
        res = json.loads(proc.stdout.strip().splitlines()[-1])
        res["compile_ms"] = round(compile_ms, 2)
        return res
