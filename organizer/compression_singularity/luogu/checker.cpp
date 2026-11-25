// Luogu Special Judge (testlib.h, strict C++14 -fno-asm -O2)
// for "The Compression Singularity" (compression_singularity).
//
// Features (100% raw-step-exact & OOB-exact):
//   - Strict raw output validation matching official vm.cpp (single optional \n or \r\n,
//     no internal/leading/trailing whitespace, alphabet ><+.[], matched brackets, |P| <= 2L+100)
//   - Zero-allocation streaming fast path for bracket-free programs
//   - 32-byte direct-threaded HotOp with HALT sentinel & cold OpMeta
//   - 64 KiB L2-resident dynamically-growing tape with 64-byte zero guard padding
//   - Portable 64-bit SWAR + memchr macro-step acceleration for [>], [<], [>>], [<<],
//     [>>>>], [<<<<], [+>], [+<], [+>>], [+<<], [+], [], IF_BALANCED, INF_BALANCED (O(1) anti-DoS),
//     and contiguous EMIT verification
//   - Built-in wall-clock soft watchdog (3.5s) -> clean WA("VM_TIMEOUT") instead of Luogu UKE
//   - Partial scoring via testlib quitp(NormScore / 100) with RawScore reported in feedback

#include "testlib.h"

#include <algorithm>
#include <chrono>
#include <cmath>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <string>
#include <vector>

namespace {

static const uint64_t DEFAULT_STEP_LIMIT = 2000000000ULL;  // 2 * 10^9 raw steps (safe for Luogu SPJ)
static const int64_t DEFAULT_TAPE_BOUND = 10000000LL;      // |p| <= 10^7
static const double DEFAULT_WALL_LIMIT_S = 3.5;            // Soft watchdog to avoid Luogu UKE
static const int64_t GUARD_PAD = 64;

#if defined(__GNUC__) || defined(__clang__)
#define VM_LIKELY(x)   __builtin_expect(!!(x), 1)
#define VM_UNLIKELY(x) __builtin_expect(!!(x), 0)
#else
#define VM_LIKELY(x)   (x)
#define VM_UNLIKELY(x) (x)
#endif

enum class OpKind : uint8_t {
    RIGHT = 0,
    LEFT = 1,
    FLIP = 2,
    LINEAR = 3,
    EMIT = 4,
    JZ = 5,
    JNZ = 6,
    CLEAR = 7,
    INF_LOOP = 8,
    SEEK_R1 = 9,
    SEEK_L1 = 10,
    SEEK_R2 = 11,
    SEEK_L2 = 12,
    SEEK_R4 = 13,
    SEEK_L4 = 14,
    SEEK_R = 15,
    SEEK_L = 16,
    CLEAR_SEEK_R1 = 17,
    CLEAR_SEEK_L1 = 18,
    CLEAR_SEEK_R2 = 19,
    CLEAR_SEEK_L2 = 20,
    CLEAR_SEEK_R = 21,
    CLEAR_SEEK_L = 22,
    IF_BALANCED = 23,
    INF_BALANCED = 24,
    HALT = 25
};

struct HotOp {
    void* handler;
    int64_t arg;
    uint32_t raw_steps;
    int16_t min_off;
    int16_t max_off;
    uint32_t aux_begin;
    uint32_t aux_end;
};

struct OpMeta {
    uint32_t src_lo;
    uint32_t src_hi;
    OpKind kind;
};

struct VerifyResult {
    std::string verdict;
    std::string message;
    uint64_t steps;
    size_t out_len;
    int64_t p_final;
    int64_t p_min;
    int64_t p_max;
    VerifyResult() : steps(0), out_len(0), p_final(0), p_min(0), p_max(0) {}
};

struct TapeWorkspace {
    std::vector<uint8_t> buf;
    int64_t cap;
    std::vector<HotOp> ops;
    std::vector<OpMeta> meta;
    std::vector<int32_t> flip_offsets;
    std::vector<size_t> match;
    std::vector<size_t> bstack;
    std::vector<int32_t> scratch_flips;

    TapeWorkspace() : cap(0) {}

    void prepare(int64_t B) {
        int64_t init_cap = std::min<int64_t>(B, 32768LL);
        cap = init_cap;
        buf.assign(static_cast<size_t>(2 * cap + 1 + 2 * GUARD_PAD), 0);
    }

    uint8_t* base_ptr() { return buf.data() + GUARD_PAD + cap; }

    void grow_to(int64_t need_abs, int64_t B, int64_t p_min, int64_t p_max) {
        int64_t new_cap = cap ? cap : 32768LL;
        while (new_cap < need_abs && new_cap < B) {
            new_cap = std::min<int64_t>(B, new_cap * 2);
        }
        if (new_cap <= cap) return;
        std::vector<uint8_t> nbuf(static_cast<size_t>(2 * new_cap + 1 + 2 * GUARD_PAD), 0);
        int64_t lo = std::max(-cap, p_min);
        int64_t hi = std::min(cap, p_max);
        if (lo <= hi) {
            std::memcpy(
                nbuf.data() + GUARD_PAD + new_cap + lo,
                buf.data() + GUARD_PAD + cap + lo,
                static_cast<size_t>(hi - lo + 1)
            );
        }
        buf.swap(nbuf);
        cap = new_cap;
    }
};

// Portable SWAR / memchr scanners (zero AVX/inline-asm, strict C++14 -fno-asm -O2)
static inline uint64_t scan_right_zero_s1(const uint8_t* base, int64_t p, uint64_t limit) {
    if (limit == 0) return 0;
    const uint8_t* ptr = base + p + 1;
    const void* f = std::memchr(ptr, 0, static_cast<size_t>(limit));
    if (f) return static_cast<uint64_t>(static_cast<const uint8_t*>(f) - ptr) + 1;
    return 0;
}

static inline uint64_t scan_left_zero_s1(const uint8_t* base, int64_t p, uint64_t limit) {
    uint64_t d = 0;
    while (d + 8 <= limit) {
        uint64_t w;
        std::memcpy(&w, base + p - static_cast<int64_t>(d) - 8, 8);
        if (w != 0x0101010101010101ULL) {
            for (uint64_t k = 1; k <= 8; ++k) {
                if (base[p - static_cast<int64_t>(d + k)] == 0) return d + k;
            }
        }
        d += 8;
    }
    for (uint64_t k = d + 1; k <= limit; ++k) {
        if (base[p - static_cast<int64_t>(k)] == 0) return k;
    }
    return 0;
}

static inline uint64_t scan_right_zero_s2(const uint8_t* base, int64_t p, uint64_t limit) {
    uint64_t d = 0;
    const uint8_t* ptr = base + p + 2;
    while (d + 4 <= limit) {
        uint64_t w;
        std::memcpy(&w, ptr + (d << 1), 8);
        if ((w & 0x00FF00FF00FF00FFULL) != 0x0001000100010001ULL) {
            for (uint64_t k = 0; k < 4; ++k) {
                if (ptr[(d + k) << 1] == 0) return d + k + 1;
            }
        }
        d += 4;
    }
    for (uint64_t k = d + 1; k <= limit; ++k) {
        if (base[p + static_cast<int64_t>(k << 1)] == 0) return k;
    }
    return 0;
}

static inline uint64_t scan_left_zero_s2(const uint8_t* base, int64_t p, uint64_t limit) {
    uint64_t d = 0;
    while (d + 4 <= limit) {
        uint64_t w;
        std::memcpy(&w, base + p - static_cast<int64_t>(d << 1) - 8, 8);
        if ((w & 0x00FF00FF00FF00FFULL) != 0x0001000100010001ULL) {
            for (uint64_t k = 1; k <= 4; ++k) {
                if (base[p - static_cast<int64_t>((d + k) << 1)] == 0) return d + k;
            }
        }
        d += 4;
    }
    for (uint64_t k = d + 1; k <= limit; ++k) {
        if (base[p - static_cast<int64_t>(k << 1)] == 0) return k;
    }
    return 0;
}

static inline uint64_t scan_right_zero_s4(const uint8_t* base, int64_t p, uint64_t limit) {
    for (uint64_t k = 1; k <= limit; ++k) {
        if (base[p + static_cast<int64_t>(k << 2)] == 0) return k;
    }
    return 0;
}

static inline uint64_t scan_left_zero_s4(const uint8_t* base, int64_t p, uint64_t limit) {
    for (uint64_t k = 1; k <= limit; ++k) {
        if (base[p - static_cast<int64_t>(k << 2)] == 0) return k;
    }
    return 0;
}

static inline void clear_s1_fast(uint8_t* ptr, uint64_t count) {
    if (VM_LIKELY(count == 1)) { ptr[0] = 0; return; }
    if (count >= 32) { std::memset(ptr, 0, static_cast<size_t>(count)); return; }
    uint64_t i = 0;
    uint64_t z64 = 0;
    while (i + 8 <= count) { std::memcpy(ptr + i, &z64, 8); i += 8; }
    while (i < count) { ptr[i++] = 0; }
}

static inline void clear_stride2_right(uint8_t* ptr, uint64_t count) {
    if (VM_LIKELY(count == 1)) { ptr[0] = 0; return; }
    uint64_t i = 0;
    while (i + 4 <= count) {
        uint64_t w;
        std::memcpy(&w, ptr + (i << 1), 8);
        w &= 0xFF00FF00FF00FF00ULL;
        std::memcpy(ptr + (i << 1), &w, 8);
        i += 4;
    }
    while (i < count) { ptr[i << 1] = 0; ++i; }
}

static inline uint64_t find_first_mismatch_fast(const char* ptr, uint64_t n, char bit) {
    uint64_t i = 0;
    const uint64_t pat64 = (bit == '1') ? 0x3131313131313131ULL : 0x3030303030303030ULL;
    while (i + 8 <= n) {
        uint64_t w;
        std::memcpy(&w, ptr + i, 8);
        if (w != pat64) {
            for (uint64_t k = 0; k < 8; ++k) {
                if (ptr[i + k] != bit) return i + k;
            }
        }
        i += 8;
    }
    while (i < n) {
        if (ptr[i] != bit) return i;
        ++i;
    }
    return n;
}

static bool validate_and_compile(
    const std::string& prog,
    size_t max_prog_len,
    TapeWorkspace& ws,
    std::string& verdict,
    std::string& message
) {
    if (prog.empty()) {
        verdict = "SYNTAX_ERROR";
        message = "Empty program";
        return false;
    }
    if (prog.size() > max_prog_len) {
        verdict = "PROGRAM_TOO_LONG";
        message = "Program length exceeds 2*L+100";
        return false;
    }
    const size_t n = prog.size();
    const char* pstr = prog.data();
    ws.bstack.clear();

    size_t n_open = 0, n_close = 0;
    for (size_t i = 0; i < n; ++i) {
        switch (pstr[i]) {
            case '>': case '<': case '+': case '.': break;
            case '[': ++n_open; break;
            case ']': ++n_close; break;
            default:
                verdict = "SYNTAX_ERROR";
                message = "Illegal character (byte " + std::to_string(static_cast<unsigned char>(pstr[i])) +
                          ") at index " + std::to_string(i);
                return false;
        }
    }
    if (n_open != n_close) {
        for (size_t i = 0; i < n; ++i) {
            if (pstr[i] == '[') ws.bstack.push_back(i);
            else if (pstr[i] == ']') {
                if (ws.bstack.empty()) {
                    verdict = "SYNTAX_ERROR";
                    message = "Unmatched ']' at index " + std::to_string(i);
                    return false;
                }
                ws.bstack.pop_back();
            }
        }
        verdict = "SYNTAX_ERROR";
        message = "Unmatched '[' at index " + std::to_string(ws.bstack.back());
        return false;
    }

    if (n_open == 0) {
        ws.match.clear();
        ws.ops.clear();
        ws.meta.clear();
        return true;
    }

    ws.match.resize(n);
    for (size_t i = 0; i < n; ++i) {
        if (pstr[i] == '[') {
            ws.bstack.push_back(i);
        } else if (pstr[i] == ']') {
            if (ws.bstack.empty()) {
                verdict = "SYNTAX_ERROR";
                message = "Unmatched ']' at index " + std::to_string(i);
                return false;
            }
            size_t open = ws.bstack.back();
            ws.bstack.pop_back();
            ws.match[open] = i;
            ws.match[i] = open;
        }
    }

    std::vector<HotOp>& ops = ws.ops;
    std::vector<OpMeta>& meta = ws.meta;
    std::vector<int32_t>& flip_offsets = ws.flip_offsets;
    ops.clear();
    meta.clear();
    flip_offsets.clear();
    ops.reserve(n + 16);
    meta.reserve(n + 16);

    auto emit_op = [&](OpKind kind, uint32_t slo, uint32_t shi, int64_t arg,
                       int16_t mn, int16_t mx, uint32_t ab, uint32_t ae, uint32_t rs) {
        HotOp h;
        h.handler = nullptr;
        h.arg = arg;
        h.raw_steps = rs;
        h.min_off = mn;
        h.max_off = mx;
        h.aux_begin = ab;
        h.aux_end = ae;
        ops.push_back(h);
        OpMeta m;
        m.src_lo = slo;
        m.src_hi = shi;
        m.kind = kind;
        meta.push_back(m);
    };

    ws.bstack.clear();

    auto analyze_linear = [&](size_t lo, size_t hi, int64_t& delta, int16_t& min_off, int16_t& max_off,
                              uint32_t& aux_b, uint32_t& aux_e, bool& flips_zero) {
        std::vector<int32_t>& sf = ws.scratch_flips;
        sf.clear();
        int64_t cur = 0, mn = 0, mx = 0;
        for (size_t k = lo; k < hi; ++k) {
            char ch = pstr[k];
            if (ch == '>') { ++cur; if (cur > mx) mx = cur; }
            else if (ch == '<') { --cur; if (cur < mn) mn = cur; }
            else { sf.push_back(static_cast<int32_t>(cur)); }
        }
        delta = cur;
        min_off = static_cast<int16_t>(mn);
        max_off = static_cast<int16_t>(mx);
        aux_b = static_cast<uint32_t>(flip_offsets.size());
        flips_zero = false;
        if (!sf.empty()) {
            std::sort(sf.begin(), sf.end());
            size_t idx = 0;
            while (idx < sf.size()) {
                size_t nxt = idx + 1;
                while (nxt < sf.size() && sf[nxt] == sf[idx]) ++nxt;
                if ((nxt - idx) & 1ULL) {
                    flip_offsets.push_back(sf[idx]);
                    if (sf[idx] == 0) flips_zero = true;
                }
                idx = nxt;
            }
        }
        aux_e = static_cast<uint32_t>(flip_offsets.size());
    };

    size_t i = 0;
    while (i < n) {
        char c = pstr[i];
        if (c == '.') {
            size_t j = i + 1;
            while (j < n && pstr[j] == '.') ++j;
            emit_op(OpKind::EMIT, static_cast<uint32_t>(i), static_cast<uint32_t>(j),
                    static_cast<int64_t>(j - i), 0, 0, 0, 0, static_cast<uint32_t>(j - i));
            i = j;
            continue;
        }
        if (c == '>' || c == '<' || c == '+') {
            size_t j = i + 1;
            const size_t cap = std::min(n, i + 4096);
            bool uniform = true;
            while (j < cap && (pstr[j] == '>' || pstr[j] == '<' || pstr[j] == '+')) {
                if (pstr[j] != c) uniform = false;
                ++j;
            }
            const uint32_t cnt = static_cast<uint32_t>(j - i);
            if (uniform) {
                if (c == '+') {
                    emit_op(OpKind::FLIP, static_cast<uint32_t>(i), static_cast<uint32_t>(j),
                            static_cast<int64_t>(cnt & 1U), 0, 0, 0, 0, cnt);
                } else if (c == '>') {
                    emit_op(OpKind::RIGHT, static_cast<uint32_t>(i), static_cast<uint32_t>(j),
                            static_cast<int64_t>(cnt), 0, static_cast<int16_t>(cnt), 0, 0, cnt);
                } else {
                    emit_op(OpKind::LEFT, static_cast<uint32_t>(i), static_cast<uint32_t>(j),
                            static_cast<int64_t>(cnt), static_cast<int16_t>(-static_cast<int32_t>(cnt)), 0, 0, 0, cnt);
                }
            } else {
                int64_t delta = 0;
                int16_t mn = 0, mx = 0;
                uint32_t ab = 0, ae = 0;
                bool fz = false;
                analyze_linear(i, j, delta, mn, mx, ab, ae, fz);
                emit_op(OpKind::LINEAR, static_cast<uint32_t>(i), static_cast<uint32_t>(j),
                        delta, mn, mx, ab, ae, cnt);
            }
            i = j;
            continue;
        }
        if (c == '[') {
            size_t close = ws.match[i];
            const size_t body_len = close - i - 1;
            if (body_len == 0) {
                emit_op(OpKind::INF_LOOP, static_cast<uint32_t>(i), static_cast<uint32_t>(close + 1), 0, 0, 0, 0, 0, 1);
                i = close + 1;
                continue;
            }
            if (body_len == 1 && pstr[i + 1] == '+') {
                emit_op(OpKind::CLEAR, static_cast<uint32_t>(i), static_cast<uint32_t>(close + 1), 0, 0, 0, 0, 0, 3);
                i = close + 1;
                continue;
            }
            bool simple_no_io = true;
            for (size_t k = i + 1; k < close; ++k) {
                char ch = pstr[k];
                if (ch == '[' || ch == ']' || ch == '.') { simple_no_io = false; break; }
            }
            if (simple_no_io && body_len <= 4096) {
                char first = pstr[i + 1];
                if (first == '>' || first == '<') {
                    bool pure_move = true;
                    for (size_t k = i + 2; k < close; ++k) {
                        if (pstr[k] != first) { pure_move = false; break; }
                    }
                    if (pure_move) {
                        int64_t s = static_cast<int64_t>(body_len);
                        OpKind sk = (first == '>')
                            ? (s == 1 ? OpKind::SEEK_R1 : (s == 2 ? OpKind::SEEK_R2 : (s == 4 ? OpKind::SEEK_R4 : OpKind::SEEK_R)))
                            : (s == 1 ? OpKind::SEEK_L1 : (s == 2 ? OpKind::SEEK_L2 : (s == 4 ? OpKind::SEEK_L4 : OpKind::SEEK_L)));
                        emit_op(sk, static_cast<uint32_t>(i), static_cast<uint32_t>(close + 1),
                                s, 0, 0, 0, 0, static_cast<uint32_t>(s + 1));
                        i = close + 1;
                        continue;
                    }
                }
                if (body_len >= 2 && first == '+') {
                    char dir = pstr[i + 2];
                    if (dir == '>' || dir == '<') {
                        bool pure_cs = true;
                        for (size_t k = i + 3; k < close; ++k) {
                            if (pstr[k] != dir) { pure_cs = false; break; }
                        }
                        if (pure_cs) {
                            int64_t s = static_cast<int64_t>(body_len - 1);
                            OpKind ck = (dir == '>')
                                ? (s == 1 ? OpKind::CLEAR_SEEK_R1 : (s == 2 ? OpKind::CLEAR_SEEK_R2 : OpKind::CLEAR_SEEK_R))
                                : (s == 1 ? OpKind::CLEAR_SEEK_L1 : (s == 2 ? OpKind::CLEAR_SEEK_L2 : OpKind::CLEAR_SEEK_L));
                            emit_op(ck, static_cast<uint32_t>(i), static_cast<uint32_t>(close + 1),
                                    s, 0, 0, 0, 0, static_cast<uint32_t>(s + 2));
                            i = close + 1;
                            continue;
                        }
                    }
                }
                int64_t delta = 0;
                int16_t mn = 0, mx = 0;
                uint32_t ab = 0, ae = 0;
                bool fz = false;
                analyze_linear(i + 1, close, delta, mn, mx, ab, ae, fz);
                if (delta == 0) {
                    emit_op(fz ? OpKind::IF_BALANCED : OpKind::INF_BALANCED,
                            static_cast<uint32_t>(i), static_cast<uint32_t>(close + 1),
                            static_cast<int64_t>(body_len + 1), mn, mx, ab, ae,
                            static_cast<uint32_t>(body_len + (fz ? 2 : 1)));
                    i = close + 1;
                    continue;
                }
            }
            ws.bstack.push_back(ops.size());
            emit_op(OpKind::JZ, static_cast<uint32_t>(i), static_cast<uint32_t>(i + 1), 0, 0, 0, 0, 0, 1);
            ++i;
            continue;
        }
        size_t open_pc = ws.bstack.back();
        ws.bstack.pop_back();
        size_t close_pc = ops.size();
        ops[open_pc].arg = static_cast<int64_t>(close_pc + 1);
        emit_op(OpKind::JNZ, static_cast<uint32_t>(i), static_cast<uint32_t>(i + 1),
                static_cast<int64_t>(open_pc + 1), 0, 0, 0, 0, 1);
        ++i;
    }
    emit_op(OpKind::HALT, static_cast<uint32_t>(n), static_cast<uint32_t>(n), 0, 0, 0, 0, 0, 0);
    return true;
}

static VerifyResult run_flat_stream(
    const std::string& target,
    const std::string& prog,
    uint64_t M,
    int64_t B,
    TapeWorkspace& ws
) {
    VerifyResult res;
    ws.prepare(B);
    uint8_t* base = ws.base_ptr();
    const size_t L = target.size();
    const char* const target_ptr = target.data();
    const char* const pstr = prog.data();
    const size_t n = prog.size();
    uint64_t rem = M;
    int64_t p = 0, p_min = 0, p_max = 0;
    size_t out_pos = 0;

    auto done = [&](const std::string& v, const std::string& msg) -> VerifyResult {
        res.verdict = v;
        res.message = msg;
        res.steps = M - rem;
        res.out_len = out_pos;
        res.p_final = p;
        res.p_min = p_min;
        res.p_max = p_max;
        return res;
    };

    for (size_t i = 0; i < n; ++i) {
        if (VM_UNLIKELY(rem == 0)) {
            return done("STEP_LIMIT_EXCEEDED", "Exceeded step limit " + std::to_string(M));
        }
        const char c = pstr[i];
        --rem;
        if (c == '.') {
            const char bit = base[p] ? '1' : '0';
            if (VM_UNLIKELY(out_pos >= L)) {
                return done("OUTPUT_MISMATCH", "Output length exceeded L=" + std::to_string(L));
            }
            if (VM_UNLIKELY(target_ptr[out_pos] != bit)) {
                return done("OUTPUT_MISMATCH", "Bit mismatch at index " + std::to_string(out_pos) +
                                               ": expected '" + target_ptr[out_pos] + "', got '" + bit + "'");
            }
            ++out_pos;
        } else if (c == '+') {
            base[p] ^= 1;
        } else if (c == '>') {
            ++p;
            if (VM_UNLIKELY(p > B)) {
                return done("TAPE_OUT_OF_BOUNDS", "Head position " + std::to_string(p) + " exceeded +" + std::to_string(B));
            }
            if (VM_UNLIKELY(p > ws.cap)) { ws.grow_to(p, B, p_min, p_max); base = ws.base_ptr(); }
            if (p > p_max) p_max = p;
        } else {
            --p;
            if (VM_UNLIKELY(p < -B)) {
                return done("TAPE_OUT_OF_BOUNDS", "Head position " + std::to_string(p) + " exceeded -" + std::to_string(B));
            }
            if (VM_UNLIKELY(p < -ws.cap)) { ws.grow_to(-p, B, p_min, p_max); base = ws.base_ptr(); }
            if (p < p_min) p_min = p;
        }
    }
    if (out_pos != L) {
        return done("OUTPUT_MISMATCH", "Output too short: got " + std::to_string(out_pos) + ", expected " + std::to_string(L));
    }
    return done("AC", "OK");
}

static VerifyResult run_vm_core(
    const std::string& target,
    const std::string& prog,
    uint64_t M,
    int64_t B,
    double wall_limit_s,
    TapeWorkspace& ws,
    std::chrono::steady_clock::time_point t_start
) {
    VerifyResult res;
    ws.prepare(B);
    uint8_t* base = ws.base_ptr();
    int64_t C = std::min(ws.cap, B);

    const size_t L = target.size();
    const char* const target_ptr = target.data();
    const char* const prog_ptr = prog.data();
    uint64_t rem = M;
    int64_t p = 0, p_min = 0, p_max = 0;
    size_t out_pos = 0;

    const size_t n_real_ops = ws.ops.size() - 1;
    HotOp* const op_arr = ws.ops.data();
    const OpMeta* const meta_arr = ws.meta.data();
    const int32_t* const flips_arr = ws.flip_offsets.data();

    uint64_t wd = 0;
    const uint64_t WD_INTERVAL = 16000000ULL;

    auto ensure_cap = [&](int64_t need_lo, int64_t need_hi) -> bool {
        if (need_lo < -B || need_hi > B) return false;
        int64_t need_abs = std::max(need_lo < 0 ? -need_lo : need_lo, need_hi < 0 ? -need_hi : need_hi);
        if (need_abs > ws.cap) {
            ws.grow_to(need_abs, B, p_min, p_max);
            base = ws.base_ptr();
        }
        C = std::min(ws.cap, B);
        return true;
    };

    auto finish = [&](const std::string& v, const std::string& msg) -> VerifyResult {
        res.verdict = v;
        res.message = msg;
        res.steps = M - rem;
        res.out_len = out_pos;
        res.p_final = p;
        res.p_min = p_min;
        res.p_max = p_max;
        return res;
    };

    auto run_raw_slice = [&](size_t s_ip, size_t end_ip, std::string& out_v, std::string& out_m) -> bool {
        while (s_ip < end_ip) {
            if (rem == 0) {
                out_v = "STEP_LIMIT_EXCEEDED";
                out_m = "Exceeded step limit " + std::to_string(M);
                return false;
            }
            char c = prog_ptr[s_ip];
            --rem;
            switch (c) {
                case '>':
                    ++p;
                    if (p > B) { out_v = "TAPE_OUT_OF_BOUNDS"; out_m = "Head position " + std::to_string(p) + " exceeded +" + std::to_string(B); return false; }
                    if (p > ws.cap) { ws.grow_to(p, B, p_min, p_max); base = ws.base_ptr(); }
                    C = std::min(ws.cap, B);
                    if (p > p_max) p_max = p;
                    ++s_ip;
                    break;
                case '<':
                    --p;
                    if (p < -B) { out_v = "TAPE_OUT_OF_BOUNDS"; out_m = "Head position " + std::to_string(p) + " exceeded -" + std::to_string(B); return false; }
                    if (p < -ws.cap) { ws.grow_to(-p, B, p_min, p_max); base = ws.base_ptr(); }
                    C = std::min(ws.cap, B);
                    if (p < p_min) p_min = p;
                    ++s_ip;
                    break;
                case '+':
                    base[p] ^= 1;
                    ++s_ip;
                    break;
                case '.': {
                    char bit = base[p] ? '1' : '0';
                    if (out_pos >= L) { out_v = "OUTPUT_MISMATCH"; out_m = "Output length exceeded L=" + std::to_string(L); return false; }
                    if (target_ptr[out_pos] != bit) {
                        out_v = "OUTPUT_MISMATCH";
                        out_m = "Bit mismatch at index " + std::to_string(out_pos) + ": expected '" + target_ptr[out_pos] + "', got '" + bit + "'";
                        return false;
                    }
                    ++out_pos;
                    ++s_ip;
                    break;
                }
                case '[':
                    if (base[p] == 0) s_ip = ws.match[s_ip] + 1;
                    else ++s_ip;
                    break;
                case ']':
                    if (base[p] != 0) s_ip = ws.match[s_ip] + 1;
                    else ++s_ip;
                    break;
            }
        }
        return true;
    };

    std::string cold_v, cold_m;

#if defined(__GNUC__) || defined(__clang__)
    static void* const dispatch_tbl[26] = {
        &&L_RIGHT, &&L_LEFT, &&L_FLIP, &&L_LINEAR, &&L_EMIT, &&L_JZ, &&L_JNZ,
        &&L_CLEAR, &&L_INF_LOOP,
        &&L_SEEK_R1, &&L_SEEK_L1, &&L_SEEK_R2, &&L_SEEK_L2, &&L_SEEK_R4, &&L_SEEK_L4, &&L_SEEK_R, &&L_SEEK_L,
        &&L_CLEAR_SEEK_R1, &&L_CLEAR_SEEK_L1, &&L_CLEAR_SEEK_R2, &&L_CLEAR_SEEK_L2, &&L_CLEAR_SEEK_R, &&L_CLEAR_SEEK_L,
        &&L_IF_BALANCED, &&L_INF_BALANCED, &&L_HALT
    };
    for (size_t i = 0; i <= n_real_ops; ++i) {
        OpKind k = meta_arr[i].kind;
        op_arr[i].handler = dispatch_tbl[static_cast<size_t>(k)];
        if (k == OpKind::JZ || k == OpKind::JNZ) {
            op_arr[i].arg = reinterpret_cast<int64_t>(op_arr + static_cast<size_t>(op_arr[i].arg));
        }
    }
    const HotOp* ip = op_arr;

#define VM_NEXT() do { ++ip; goto *ip->handler; } while (0)
#define VM_JUMP(tgt_ptr) do { ip = (tgt_ptr); goto *ip->handler; } while (0)
#define VM_LBL(name) name:
#define CUR_IDX() static_cast<size_t>(ip - op_arr)

    goto *ip->handler;
#else
    const HotOp* ip = op_arr;
#define VM_NEXT() do { ++ip; continue; } while (0)
#define VM_JUMP(tgt_ptr) do { ip = (tgt_ptr); continue; } while (0)
#define VM_LBL(name) case OpKind::name:
#define CUR_IDX() static_cast<size_t>(ip - op_arr)
    while (true) {
        switch (meta_arr[CUR_IDX()].kind) {
#endif

    VM_LBL(L_RIGHT) {
        const int64_t np = p + ip->arg;
        const uint64_t rs = ip->raw_steps;
        if (VM_LIKELY(np <= C && rs <= rem)) {
            p = np; if (p > p_max) p_max = p; rem -= rs; VM_NEXT();
        }
        if (rs <= rem && ensure_cap(p, np)) {
            p = np; if (p > p_max) p_max = p; rem -= rs; VM_NEXT();
        }
        const OpMeta& m = meta_arr[CUR_IDX()];
        if (!run_raw_slice(m.src_lo, m.src_hi, cold_v, cold_m)) return finish(cold_v, cold_m);
        VM_NEXT();
    }

    VM_LBL(L_LEFT) {
        const int64_t np = p - ip->arg;
        const uint64_t rs = ip->raw_steps;
        if (VM_LIKELY(np >= -C && rs <= rem)) {
            p = np; if (p < p_min) p_min = p; rem -= rs; VM_NEXT();
        }
        if (rs <= rem && ensure_cap(np, p)) {
            p = np; if (p < p_min) p_min = p; rem -= rs; VM_NEXT();
        }
        const OpMeta& m = meta_arr[CUR_IDX()];
        if (!run_raw_slice(m.src_lo, m.src_hi, cold_v, cold_m)) return finish(cold_v, cold_m);
        VM_NEXT();
    }

    VM_LBL(L_FLIP) {
        const uint64_t rs = ip->raw_steps;
        if (VM_LIKELY(rs <= rem)) {
            base[p] ^= static_cast<uint8_t>(ip->arg); rem -= rs; VM_NEXT();
        }
        rem = 0;
        return finish("STEP_LIMIT_EXCEEDED", "Exceeded step limit " + std::to_string(M));
    }

    VM_LBL(L_LINEAR) {
        const int64_t lo = p + ip->min_off;
        const int64_t hi = p + ip->max_off;
        const uint64_t rs = ip->raw_steps;
        if (VM_LIKELY(lo >= -C && hi <= C && rs <= rem)) {
            for (uint32_t k = ip->aux_begin; k < ip->aux_end; ++k) base[p + flips_arr[k]] ^= 1;
            if (lo < p_min) p_min = lo;
            if (hi > p_max) p_max = hi;
            p += ip->arg; rem -= rs; VM_NEXT();
        }
        if (rs <= rem && ensure_cap(lo, hi)) {
            for (uint32_t k = ip->aux_begin; k < ip->aux_end; ++k) base[p + flips_arr[k]] ^= 1;
            if (lo < p_min) p_min = lo;
            if (hi > p_max) p_max = hi;
            p += ip->arg; rem -= rs; VM_NEXT();
        }
        const OpMeta& m = meta_arr[CUR_IDX()];
        if (!run_raw_slice(m.src_lo, m.src_hi, cold_v, cold_m)) return finish(cold_v, cold_m);
        VM_NEXT();
    }

    VM_LBL(L_EMIT) {
        const uint64_t k = static_cast<uint64_t>(ip->arg);
        if (VM_UNLIKELY(rem == 0)) return finish("STEP_LIMIT_EXCEEDED", "Exceeded step limit " + std::to_string(M));
        const char bit = base[p] ? '1' : '0';
        if (VM_LIKELY(k == 1 && out_pos < L && target_ptr[out_pos] == bit)) {
            ++out_pos; --rem; VM_NEXT();
        }
        const uint64_t avail = (out_pos < L) ? static_cast<uint64_t>(L - out_pos) : 0ULL;
        const uint64_t check_n = std::min(k, avail);
        const uint64_t mm = (check_n == 1) ? (target_ptr[out_pos] == bit ? 1ULL : 0ULL)
                                           : find_first_mismatch_fast(target_ptr + out_pos, check_n, bit);
        if (VM_LIKELY(mm == check_n && k <= avail)) {
            if (VM_LIKELY(k <= rem)) { out_pos += static_cast<size_t>(k); rem -= k; VM_NEXT(); }
            out_pos += static_cast<size_t>(rem); rem = 0;
            return finish("STEP_LIMIT_EXCEEDED", "Exceeded step limit " + std::to_string(M));
        }
        const uint64_t need = mm + 1;
        if (need <= rem) {
            out_pos += static_cast<size_t>(mm); rem -= need;
            if (out_pos >= L) return finish("OUTPUT_MISMATCH", "Output length exceeded L=" + std::to_string(L));
            return finish("OUTPUT_MISMATCH", "Bit mismatch at index " + std::to_string(out_pos) +
                                             ": expected '" + target_ptr[out_pos] + "', got '" + bit + "'");
        }
        out_pos += static_cast<size_t>(rem); rem = 0;
        return finish("STEP_LIMIT_EXCEEDED", "Exceeded step limit " + std::to_string(M));
    }

    VM_LBL(L_JZ) {
        if (VM_UNLIKELY(rem == 0)) return finish("STEP_LIMIT_EXCEEDED", "Exceeded step limit " + std::to_string(M));
        --rem;
#if defined(__GNUC__) || defined(__clang__)
        VM_JUMP((base[p] == 0) ? reinterpret_cast<const HotOp*>(ip->arg) : (ip + 1));
#else
        VM_JUMP((base[p] == 0) ? (op_arr + static_cast<size_t>(ip->arg)) : (ip + 1));
#endif
    }

    VM_LBL(L_JNZ) {
        if (VM_UNLIKELY(++wd >= WD_INTERVAL)) {
            wd = 0;
            double sec = std::chrono::duration<double>(std::chrono::steady_clock::now() - t_start).count();
            if (sec > wall_limit_s) {
                return finish("VM_TIMEOUT", "VM checker exceeded soft wall limit (" + std::to_string(wall_limit_s) + "s)");
            }
        }
        if (VM_UNLIKELY(rem == 0)) return finish("STEP_LIMIT_EXCEEDED", "Exceeded step limit " + std::to_string(M));
        --rem;
#if defined(__GNUC__) || defined(__clang__)
        VM_JUMP((base[p] != 0) ? reinterpret_cast<const HotOp*>(ip->arg) : (ip + 1));
#else
        VM_JUMP((base[p] != 0) ? (op_arr + static_cast<size_t>(ip->arg)) : (ip + 1));
#endif
    }

    VM_LBL(L_CLEAR) {
        if (VM_UNLIKELY(rem == 0)) return finish("STEP_LIMIT_EXCEEDED", "Exceeded step limit " + std::to_string(M));
        if (base[p] == 0) { --rem; VM_NEXT(); }
        if (VM_LIKELY(rem >= 3)) { base[p] = 0; rem -= 3; VM_NEXT(); }
        rem = 0;
        return finish("STEP_LIMIT_EXCEEDED", "Exceeded step limit " + std::to_string(M));
    }

    VM_LBL(L_INF_LOOP) {
        if (VM_UNLIKELY(rem == 0)) return finish("STEP_LIMIT_EXCEEDED", "Exceeded step limit " + std::to_string(M));
        --rem;
        if (VM_LIKELY(base[p] == 0)) { VM_NEXT(); }
        rem = 0;
        return finish("STEP_LIMIT_EXCEEDED", "Infinite empty loop '[]' on cell=1");
    }

    VM_LBL(L_IF_BALANCED) {
        if (VM_UNLIKELY(rem == 0)) return finish("STEP_LIMIT_EXCEEDED", "Exceeded step limit " + std::to_string(M));
        if (base[p] == 0) { --rem; VM_NEXT(); }
        const int64_t lo = p + ip->min_off;
        const int64_t hi = p + ip->max_off;
        const uint64_t rs = ip->raw_steps;
        if (VM_LIKELY(lo >= -C && hi <= C && rs <= rem)) {
            for (uint32_t k = ip->aux_begin; k < ip->aux_end; ++k) base[p + flips_arr[k]] ^= 1;
            if (lo < p_min) p_min = lo;
            if (hi > p_max) p_max = hi;
            rem -= rs; VM_NEXT();
        }
        if (rs <= rem && ensure_cap(lo, hi)) {
            for (uint32_t k = ip->aux_begin; k < ip->aux_end; ++k) base[p + flips_arr[k]] ^= 1;
            if (lo < p_min) p_min = lo;
            if (hi > p_max) p_max = hi;
            rem -= rs; VM_NEXT();
        }
        const OpMeta& m = meta_arr[CUR_IDX()];
        if (!run_raw_slice(m.src_lo, m.src_hi, cold_v, cold_m)) return finish(cold_v, cold_m);
        VM_NEXT();
    }

    VM_LBL(L_INF_BALANCED) {
        if (VM_UNLIKELY(rem == 0)) return finish("STEP_LIMIT_EXCEEDED", "Exceeded step limit " + std::to_string(M));
        if (base[p] == 0) { --rem; VM_NEXT(); }
        const int64_t lo = p + ip->min_off;
        const int64_t hi = p + ip->max_off;
        const OpMeta& m = meta_arr[CUR_IDX()];
        if (!ensure_cap(lo, hi)) {
            if (!run_raw_slice(m.src_lo, m.src_hi, cold_v, cold_m)) return finish(cold_v, cold_m);
            VM_NEXT();
        }
        --rem;
        const uint64_t cycle = ip->raw_steps;
        const uint64_t full = rem / cycle;
        if (full > 0) {
            if (lo < p_min) p_min = lo;
            if (hi > p_max) p_max = hi;
            if (full & 1ULL) {
                for (uint32_t k = ip->aux_begin; k < ip->aux_end; ++k) base[p + flips_arr[k]] ^= 1;
            }
            rem -= full * cycle;
        }
        if (!run_raw_slice(m.src_lo + 1, m.src_hi, cold_v, cold_m)) return finish(cold_v, cold_m);
        return finish("STEP_LIMIT_EXCEEDED", "Exceeded step limit " + std::to_string(M));
    }

    VM_LBL(L_SEEK_R1) {
        if (VM_UNLIKELY(rem == 0)) return finish("STEP_LIMIT_EXCEEDED", "Exceeded step limit " + std::to_string(M));
        --rem;
        if (base[p] == 0) { VM_NEXT(); }
        while (true) {
            const uint64_t a = static_cast<uint64_t>(C - p);
            const uint64_t limit = VM_LIKELY((a << 1) <= rem) ? a : (rem >> 1);
            if (limit > 0) {
                const uint64_t d = scan_right_zero_s1(base, p, limit);
                if (VM_LIKELY(d > 0)) { p += static_cast<int64_t>(d); if (p > p_max) p_max = p; rem -= d << 1; VM_NEXT(); }
                p += static_cast<int64_t>(limit); if (p > p_max) p_max = p; rem -= limit << 1;
            }
            if (C < B && rem >= 2 && (C - p) < 1) {
                ws.grow_to(std::min(B, std::max<int64_t>(C * 2, C + 64)), B, p_min, p_max);
                base = ws.base_ptr(); C = std::min(ws.cap, B); continue;
            }
            break;
        }
        const OpMeta& m = meta_arr[CUR_IDX()];
        if (!run_raw_slice(m.src_lo + 1, m.src_hi, cold_v, cold_m)) return finish(cold_v, cold_m);
        VM_NEXT();
    }

    VM_LBL(L_SEEK_L1) {
        if (VM_UNLIKELY(rem == 0)) return finish("STEP_LIMIT_EXCEEDED", "Exceeded step limit " + std::to_string(M));
        --rem;
        if (base[p] == 0) { VM_NEXT(); }
        while (true) {
            const uint64_t a = static_cast<uint64_t>(p + C);
            const uint64_t limit = VM_LIKELY((a << 1) <= rem) ? a : (rem >> 1);
            if (limit > 0) {
                const uint64_t d = scan_left_zero_s1(base, p, limit);
                if (VM_LIKELY(d > 0)) { p -= static_cast<int64_t>(d); if (p < p_min) p_min = p; rem -= d << 1; VM_NEXT(); }
                p -= static_cast<int64_t>(limit); if (p < p_min) p_min = p; rem -= limit << 1;
            }
            if (C < B && rem >= 2 && (p + C) < 1) {
                ws.grow_to(std::min(B, std::max<int64_t>(C * 2, C + 64)), B, p_min, p_max);
                base = ws.base_ptr(); C = std::min(ws.cap, B); continue;
            }
            break;
        }
        const OpMeta& m = meta_arr[CUR_IDX()];
        if (!run_raw_slice(m.src_lo + 1, m.src_hi, cold_v, cold_m)) return finish(cold_v, cold_m);
        VM_NEXT();
    }

    VM_LBL(L_SEEK_R2) {
        if (VM_UNLIKELY(rem == 0)) return finish("STEP_LIMIT_EXCEEDED", "Exceeded step limit " + std::to_string(M));
        --rem;
        if (base[p] == 0) { VM_NEXT(); }
        while (true) {
            const uint64_t a = static_cast<uint64_t>(C - p) >> 1;
            const uint64_t limit = VM_LIKELY(a * 3ULL <= rem) ? a : (rem / 3ULL);
            if (limit > 0) {
                const uint64_t d = scan_right_zero_s2(base, p, limit);
                if (VM_LIKELY(d > 0)) { p += static_cast<int64_t>(d << 1); if (p > p_max) p_max = p; rem -= d * 3ULL; VM_NEXT(); }
                p += static_cast<int64_t>(limit << 1); if (p > p_max) p_max = p; rem -= limit * 3ULL;
            }
            if (C < B && rem >= 3 && (C - p) < 2) {
                ws.grow_to(std::min(B, std::max<int64_t>(C * 2, C + 64)), B, p_min, p_max);
                base = ws.base_ptr(); C = std::min(ws.cap, B); continue;
            }
            break;
        }
        const OpMeta& m = meta_arr[CUR_IDX()];
        if (!run_raw_slice(m.src_lo + 1, m.src_hi, cold_v, cold_m)) return finish(cold_v, cold_m);
        VM_NEXT();
    }

    VM_LBL(L_SEEK_L2) {
        if (VM_UNLIKELY(rem == 0)) return finish("STEP_LIMIT_EXCEEDED", "Exceeded step limit " + std::to_string(M));
        --rem;
        if (base[p] == 0) { VM_NEXT(); }
        while (true) {
            const uint64_t a = static_cast<uint64_t>(p + C) >> 1;
            const uint64_t limit = VM_LIKELY(a * 3ULL <= rem) ? a : (rem / 3ULL);
            if (limit > 0) {
                const uint64_t d = scan_left_zero_s2(base, p, limit);
                if (VM_LIKELY(d > 0)) { p -= static_cast<int64_t>(d << 1); if (p < p_min) p_min = p; rem -= d * 3ULL; VM_NEXT(); }
                p -= static_cast<int64_t>(limit << 1); if (p < p_min) p_min = p; rem -= limit * 3ULL;
            }
            if (C < B && rem >= 3 && (p + C) < 2) {
                ws.grow_to(std::min(B, std::max<int64_t>(C * 2, C + 64)), B, p_min, p_max);
                base = ws.base_ptr(); C = std::min(ws.cap, B); continue;
            }
            break;
        }
        const OpMeta& m = meta_arr[CUR_IDX()];
        if (!run_raw_slice(m.src_lo + 1, m.src_hi, cold_v, cold_m)) return finish(cold_v, cold_m);
        VM_NEXT();
    }

    VM_LBL(L_SEEK_R4) {
        if (VM_UNLIKELY(rem == 0)) return finish("STEP_LIMIT_EXCEEDED", "Exceeded step limit " + std::to_string(M));
        --rem;
        if (base[p] == 0) { VM_NEXT(); }
        while (true) {
            const uint64_t a = static_cast<uint64_t>(C - p) >> 2;
            const uint64_t limit = VM_LIKELY(a * 5ULL <= rem) ? a : (rem / 5ULL);
            if (limit > 0) {
                const uint64_t d = scan_right_zero_s4(base, p, limit);
                if (VM_LIKELY(d > 0)) { p += static_cast<int64_t>(d << 2); if (p > p_max) p_max = p; rem -= d * 5ULL; VM_NEXT(); }
                p += static_cast<int64_t>(limit << 2); if (p > p_max) p_max = p; rem -= limit * 5ULL;
            }
            if (C < B && rem >= 5 && (C - p) < 4) {
                ws.grow_to(std::min(B, std::max<int64_t>(C * 2, C + 64)), B, p_min, p_max);
                base = ws.base_ptr(); C = std::min(ws.cap, B); continue;
            }
            break;
        }
        const OpMeta& m = meta_arr[CUR_IDX()];
        if (!run_raw_slice(m.src_lo + 1, m.src_hi, cold_v, cold_m)) return finish(cold_v, cold_m);
        VM_NEXT();
    }

    VM_LBL(L_SEEK_L4) {
        if (VM_UNLIKELY(rem == 0)) return finish("STEP_LIMIT_EXCEEDED", "Exceeded step limit " + std::to_string(M));
        --rem;
        if (base[p] == 0) { VM_NEXT(); }
        while (true) {
            const uint64_t a = static_cast<uint64_t>(p + C) >> 2;
            const uint64_t limit = VM_LIKELY(a * 5ULL <= rem) ? a : (rem / 5ULL);
            if (limit > 0) {
                const uint64_t d = scan_left_zero_s4(base, p, limit);
                if (VM_LIKELY(d > 0)) { p -= static_cast<int64_t>(d << 2); if (p < p_min) p_min = p; rem -= d * 5ULL; VM_NEXT(); }
                p -= static_cast<int64_t>(limit << 2); if (p < p_min) p_min = p; rem -= limit * 5ULL;
            }
            if (C < B && rem >= 5 && (p + C) < 4) {
                ws.grow_to(std::min(B, std::max<int64_t>(C * 2, C + 64)), B, p_min, p_max);
                base = ws.base_ptr(); C = std::min(ws.cap, B); continue;
            }
            break;
        }
        const OpMeta& m = meta_arr[CUR_IDX()];
        if (!run_raw_slice(m.src_lo + 1, m.src_hi, cold_v, cold_m)) return finish(cold_v, cold_m);
        VM_NEXT();
    }

    VM_LBL(L_SEEK_R) {
        if (VM_UNLIKELY(rem == 0)) return finish("STEP_LIMIT_EXCEEDED", "Exceeded step limit " + std::to_string(M));
        --rem;
        if (base[p] == 0) { VM_NEXT(); }
        const uint64_t s = static_cast<uint64_t>(ip->arg);
        while (true) {
            const uint64_t a = static_cast<uint64_t>(C - p) / s;
            const uint64_t limit = VM_LIKELY(a * (s + 1) <= rem) ? a : (rem / (s + 1));
            int64_t q = p;
            for (uint64_t d = 1; d <= limit; ++d) {
                q += static_cast<int64_t>(s);
                if (base[q] == 0) { p = q; if (p > p_max) p_max = p; rem -= d * (s + 1); VM_NEXT(); }
            }
            p = q; if (p > p_max) p_max = p; rem -= limit * (s + 1);
            if (C < B && rem >= (s + 1) && static_cast<uint64_t>(C - p) < s) {
                ws.grow_to(std::min(B, std::max<int64_t>(C * 2, C + static_cast<int64_t>(s))), B, p_min, p_max);
                base = ws.base_ptr(); C = std::min(ws.cap, B); continue;
            }
            break;
        }
        const OpMeta& m = meta_arr[CUR_IDX()];
        if (!run_raw_slice(m.src_lo + 1, m.src_hi, cold_v, cold_m)) return finish(cold_v, cold_m);
        VM_NEXT();
    }

    VM_LBL(L_SEEK_L) {
        if (VM_UNLIKELY(rem == 0)) return finish("STEP_LIMIT_EXCEEDED", "Exceeded step limit " + std::to_string(M));
        --rem;
        if (base[p] == 0) { VM_NEXT(); }
        const uint64_t s = static_cast<uint64_t>(ip->arg);
        while (true) {
            const uint64_t a = static_cast<uint64_t>(p + C) / s;
            const uint64_t limit = VM_LIKELY(a * (s + 1) <= rem) ? a : (rem / (s + 1));
            int64_t q = p;
            for (uint64_t d = 1; d <= limit; ++d) {
                q -= static_cast<int64_t>(s);
                if (base[q] == 0) { p = q; if (p < p_min) p_min = p; rem -= d * (s + 1); VM_NEXT(); }
            }
            p = q; if (p < p_min) p_min = p; rem -= limit * (s + 1);
            if (C < B && rem >= (s + 1) && static_cast<uint64_t>(p + C) < s) {
                ws.grow_to(std::min(B, std::max<int64_t>(C * 2, C + static_cast<int64_t>(s))), B, p_min, p_max);
                base = ws.base_ptr(); C = std::min(ws.cap, B); continue;
            }
            break;
        }
        const OpMeta& m = meta_arr[CUR_IDX()];
        if (!run_raw_slice(m.src_lo + 1, m.src_hi, cold_v, cold_m)) return finish(cold_v, cold_m);
        VM_NEXT();
    }

    VM_LBL(L_CLEAR_SEEK_R1) {
        if (VM_UNLIKELY(rem == 0)) return finish("STEP_LIMIT_EXCEEDED", "Exceeded step limit " + std::to_string(M));
        --rem;
        if (base[p] == 0) { VM_NEXT(); }
        if (VM_LIKELY(p + 1 <= C && rem >= 3ULL) && base[p + 1] == 0) {
            base[p] = 0; ++p; if (p > p_max) p_max = p; rem -= 3ULL; VM_NEXT();
        }
        while (true) {
            const uint64_t a = static_cast<uint64_t>(C - p);
            const uint64_t limit = VM_LIKELY(a * 3ULL <= rem) ? a : (rem / 3ULL);
            if (limit > 0) {
                const uint64_t d = scan_right_zero_s1(base, p, limit);
                if (VM_LIKELY(d > 0)) { clear_s1_fast(base + p, d); p += static_cast<int64_t>(d); if (p > p_max) p_max = p; rem -= d * 3ULL; VM_NEXT(); }
                clear_s1_fast(base + p, limit); p += static_cast<int64_t>(limit); if (p > p_max) p_max = p; rem -= limit * 3ULL;
            }
            if (C < B && rem >= 3 && (C - p) < 1) {
                ws.grow_to(std::min(B, std::max<int64_t>(C * 2, C + 64)), B, p_min, p_max);
                base = ws.base_ptr(); C = std::min(ws.cap, B); continue;
            }
            break;
        }
        const OpMeta& m = meta_arr[CUR_IDX()];
        if (!run_raw_slice(m.src_lo + 1, m.src_hi, cold_v, cold_m)) return finish(cold_v, cold_m);
        VM_NEXT();
    }

    VM_LBL(L_CLEAR_SEEK_L1) {
        if (VM_UNLIKELY(rem == 0)) return finish("STEP_LIMIT_EXCEEDED", "Exceeded step limit " + std::to_string(M));
        --rem;
        if (base[p] == 0) { VM_NEXT(); }
        if (VM_LIKELY(p - 1 >= -C && rem >= 3ULL) && base[p - 1] == 0) {
            base[p] = 0; --p; if (p < p_min) p_min = p; rem -= 3ULL; VM_NEXT();
        }
        while (true) {
            const uint64_t a = static_cast<uint64_t>(p + C);
            const uint64_t limit = VM_LIKELY(a * 3ULL <= rem) ? a : (rem / 3ULL);
            if (limit > 0) {
                const uint64_t d = scan_left_zero_s1(base, p, limit);
                if (VM_LIKELY(d > 0)) { clear_s1_fast(base + p - static_cast<int64_t>(d) + 1, d); p -= static_cast<int64_t>(d); if (p < p_min) p_min = p; rem -= d * 3ULL; VM_NEXT(); }
                clear_s1_fast(base + p - static_cast<int64_t>(limit) + 1, limit); p -= static_cast<int64_t>(limit); if (p < p_min) p_min = p; rem -= limit * 3ULL;
            }
            if (C < B && rem >= 3 && (p + C) < 1) {
                ws.grow_to(std::min(B, std::max<int64_t>(C * 2, C + 64)), B, p_min, p_max);
                base = ws.base_ptr(); C = std::min(ws.cap, B); continue;
            }
            break;
        }
        const OpMeta& m = meta_arr[CUR_IDX()];
        if (!run_raw_slice(m.src_lo + 1, m.src_hi, cold_v, cold_m)) return finish(cold_v, cold_m);
        VM_NEXT();
    }

    VM_LBL(L_CLEAR_SEEK_R2) {
        if (VM_UNLIKELY(rem == 0)) return finish("STEP_LIMIT_EXCEEDED", "Exceeded step limit " + std::to_string(M));
        --rem;
        if (base[p] == 0) { VM_NEXT(); }
        if (VM_LIKELY(p + 2 <= C && rem >= 4ULL) && base[p + 2] == 0) {
            base[p] = 0; p += 2; if (p > p_max) p_max = p; rem -= 4ULL; VM_NEXT();
        }
        while (true) {
            const uint64_t a = static_cast<uint64_t>(C - p) >> 1;
            const uint64_t limit = VM_LIKELY((a << 2) <= rem) ? a : (rem >> 2);
            if (limit > 0) {
                const uint64_t d = scan_right_zero_s2(base, p, limit);
                if (VM_LIKELY(d > 0)) { clear_stride2_right(base + p, d); p += static_cast<int64_t>(d << 1); if (p > p_max) p_max = p; rem -= d << 2; VM_NEXT(); }
                clear_stride2_right(base + p, limit); p += static_cast<int64_t>(limit << 1); if (p > p_max) p_max = p; rem -= limit << 2;
            }
            if (C < B && rem >= 4 && (C - p) < 2) {
                ws.grow_to(std::min(B, std::max<int64_t>(C * 2, C + 64)), B, p_min, p_max);
                base = ws.base_ptr(); C = std::min(ws.cap, B); continue;
            }
            break;
        }
        const OpMeta& m = meta_arr[CUR_IDX()];
        if (!run_raw_slice(m.src_lo + 1, m.src_hi, cold_v, cold_m)) return finish(cold_v, cold_m);
        VM_NEXT();
    }

    VM_LBL(L_CLEAR_SEEK_L2) {
        if (VM_UNLIKELY(rem == 0)) return finish("STEP_LIMIT_EXCEEDED", "Exceeded step limit " + std::to_string(M));
        --rem;
        if (base[p] == 0) { VM_NEXT(); }
        if (VM_LIKELY(p - 2 >= -C && rem >= 4ULL) && base[p - 2] == 0) {
            base[p] = 0; p -= 2; if (p < p_min) p_min = p; rem -= 4ULL; VM_NEXT();
        }
        while (true) {
            const uint64_t a = static_cast<uint64_t>(p + C) >> 1;
            const uint64_t limit = VM_LIKELY((a << 2) <= rem) ? a : (rem >> 2);
            if (limit > 0) {
                const uint64_t d = scan_left_zero_s2(base, p, limit);
                if (VM_LIKELY(d > 0)) { clear_stride2_right(base + p - static_cast<int64_t>((d - 1) << 1), d); p -= static_cast<int64_t>(d << 1); if (p < p_min) p_min = p; rem -= d << 2; VM_NEXT(); }
                clear_stride2_right(base + p - static_cast<int64_t>((limit - 1) << 1), limit); p -= static_cast<int64_t>(limit << 1); if (p < p_min) p_min = p; rem -= limit << 2;
            }
            if (C < B && rem >= 4 && (p + C) < 2) {
                ws.grow_to(std::min(B, std::max<int64_t>(C * 2, C + 64)), B, p_min, p_max);
                base = ws.base_ptr(); C = std::min(ws.cap, B); continue;
            }
            break;
        }
        const OpMeta& m = meta_arr[CUR_IDX()];
        if (!run_raw_slice(m.src_lo + 1, m.src_hi, cold_v, cold_m)) return finish(cold_v, cold_m);
        VM_NEXT();
    }

    VM_LBL(L_CLEAR_SEEK_R) {
        if (VM_UNLIKELY(rem == 0)) return finish("STEP_LIMIT_EXCEEDED", "Exceeded step limit " + std::to_string(M));
        --rem;
        if (base[p] == 0) { VM_NEXT(); }
        const uint64_t s = static_cast<uint64_t>(ip->arg);
        while (true) {
            const uint64_t a = static_cast<uint64_t>(C - p) / s;
            const uint64_t limit = VM_LIKELY(a * (s + 2) <= rem) ? a : (rem / (s + 2));
            int64_t q = p;
            for (uint64_t d = 1; d <= limit; ++d) {
                base[q] = 0; q += static_cast<int64_t>(s);
                if (base[q] == 0) { p = q; if (p > p_max) p_max = p; rem -= d * (s + 2); VM_NEXT(); }
            }
            p = q; if (p > p_max) p_max = p; rem -= limit * (s + 2);
            if (C < B && rem >= (s + 2) && static_cast<uint64_t>(C - p) < s) {
                ws.grow_to(std::min(B, std::max<int64_t>(C * 2, C + static_cast<int64_t>(s))), B, p_min, p_max);
                base = ws.base_ptr(); C = std::min(ws.cap, B); continue;
            }
            break;
        }
        const OpMeta& m = meta_arr[CUR_IDX()];
        if (!run_raw_slice(m.src_lo + 1, m.src_hi, cold_v, cold_m)) return finish(cold_v, cold_m);
        VM_NEXT();
    }

    VM_LBL(L_CLEAR_SEEK_L) {
        if (VM_UNLIKELY(rem == 0)) return finish("STEP_LIMIT_EXCEEDED", "Exceeded step limit " + std::to_string(M));
        --rem;
        if (base[p] == 0) { VM_NEXT(); }
        const uint64_t s = static_cast<uint64_t>(ip->arg);
        while (true) {
            const uint64_t a = static_cast<uint64_t>(p + C) / s;
            const uint64_t limit = VM_LIKELY(a * (s + 2) <= rem) ? a : (rem / (s + 2));
            int64_t q = p;
            for (uint64_t d = 1; d <= limit; ++d) {
                base[q] = 0; q -= static_cast<int64_t>(s);
                if (base[q] == 0) { p = q; if (p < p_min) p_min = p; rem -= d * (s + 2); VM_NEXT(); }
            }
            p = q; if (p < p_min) p_min = p; rem -= limit * (s + 2);
            if (C < B && rem >= (s + 2) && static_cast<uint64_t>(p + C) < s) {
                ws.grow_to(std::min(B, std::max<int64_t>(C * 2, C + static_cast<int64_t>(s))), B, p_min, p_max);
                base = ws.base_ptr(); C = std::min(ws.cap, B); continue;
            }
            break;
        }
        const OpMeta& m = meta_arr[CUR_IDX()];
        if (!run_raw_slice(m.src_lo + 1, m.src_hi, cold_v, cold_m)) return finish(cold_v, cold_m);
        VM_NEXT();
    }

    VM_LBL(L_HALT) {
        if (out_pos != L) {
            return finish("OUTPUT_MISMATCH", "Output too short: got " + std::to_string(out_pos) + ", expected " + std::to_string(L));
        }
        return finish("AC", "OK");
    }

#if !(defined(__GNUC__) || defined(__clang__))
        }
    }
#endif
}

}  // namespace

int main(int argc, char* argv[]) {
    setName("The Compression Singularity SPJ (Bitfuck 6-op VM + NormScore quitp)");
    registerTestlibCmd(argc, argv);

    auto t_start = std::chrono::steady_clock::now();

    // 1. Read test input (L and binary string S)
    long long L_ll = inf.readLong(1LL, 20000000LL, "L");
    size_t L = static_cast<size_t>(L_ll);
    std::string target = inf.readToken();
    if (target.size() != L) {
        quitf(_fail, "Input target length mismatch: header L=%llu, actual=%llu",
              static_cast<unsigned long long>(L), static_cast<unsigned long long>(target.size()));
    }
    for (size_t i = 0; i < L; ++i) {
        if (target[i] != '0' && target[i] != '1') {
            quitf(_fail, "Input target contains non-binary character at index %llu", static_cast<unsigned long long>(i));
        }
    }

    // 2. Read reference answer (precomputed B_ref in bits; optional custom step_limit)
    long long b_ref_ll = ans.readLong(1LL, 1000000000LL, "B_ref");
    uint64_t b_ref = static_cast<uint64_t>(b_ref_ll);
    uint64_t step_limit = DEFAULT_STEP_LIMIT;
    if (!ans.seekEof()) {
        long long custom_m = ans.readLong(1LL, 3000000000000LL, "step_limit");
        step_limit = static_cast<uint64_t>(custom_m);
    }

    // 3. Read raw contestant output strictly (allow at most ONE trailing \n or \r\n, zero internal spaces)
    const size_t max_prog_len = 2 * L + 100;
    std::string prog;
    prog.reserve(std::min<size_t>(max_prog_len + 4, 262144));
    while (!ouf.eof()) {
        char c = ouf.readChar();
        prog.push_back(c);
        if (prog.size() > max_prog_len + 4) {
            quitf(_wa, "PROGRAM_TOO_LONG: output exceeds limit 2*L+100=%llu",
                  static_cast<unsigned long long>(max_prog_len));
        }
    }
    if (!prog.empty() && prog.back() == '\n') {
        prog.pop_back();
        if (!prog.empty() && prog.back() == '\r') prog.pop_back();
    }

    // 4. Validate syntax, length, brackets, and compile IR
    TapeWorkspace ws;
    std::string v, msg;
    if (!validate_and_compile(prog, max_prog_len, ws, v, msg)) {
        quitf(_wa, "%s: %s (|P|=%llu)", v.c_str(), msg.c_str(), static_cast<unsigned long long>(prog.size()));
    }

    // 5. Execute deterministic Bitfuck VM
    VerifyResult r = ws.ops.empty()
        ? run_flat_stream(target, prog, step_limit, DEFAULT_TAPE_BOUND, ws)
        : run_vm_core(target, prog, step_limit, DEFAULT_TAPE_BOUND, DEFAULT_WALL_LIMIT_S, ws, t_start);

    if (r.verdict != "AC") {
        quitf(_wa, "%s: %s (|P|=%llu, steps=%llu, out=%llu/%llu)",
              r.verdict.c_str(), r.message.c_str(),
              static_cast<unsigned long long>(prog.size()),
              static_cast<unsigned long long>(r.steps),
              static_cast<unsigned long long>(r.out_len),
              static_cast<unsigned long long>(L));
    }

    // 6. Compute RawScore (unbounded) & NormScore ratio in [0, 1] for Luogu quitp
    const double LOG2_6 = std::log2(6.0);
    const double HALF_POW = std::pow(1000.0, 0.75);
    double raw_score = 100.0 * static_cast<double>(b_ref) / (static_cast<double>(prog.size()) * LOG2_6);
    double rp = (raw_score > 0.0) ? std::pow(raw_score, 0.75) : 0.0;
    double ratio = (rp > 0.0) ? (rp / (rp + HALF_POW)) : 0.0;
    if (ratio > 1.0) ratio = 1.0;
    if (ratio < 0.0) ratio = 0.0;
    double norm_score = 100.0 * ratio;

    if (ratio >= 1.0) {
        quitf(_ok, "AC | |P|=%llu B_ref=%llu Raw=%.2f Norm=%.2f steps=%llu p=[%lld,%lld]",
              static_cast<unsigned long long>(prog.size()),
              static_cast<unsigned long long>(b_ref),
              raw_score, norm_score,
              static_cast<unsigned long long>(r.steps),
              static_cast<long long>(r.p_min),
              static_cast<long long>(r.p_max));
    } else {
        quitp(ratio, "AC | |P|=%llu B_ref=%llu Raw=%.2f Norm=%.2f steps=%llu p=[%lld,%lld]",
              static_cast<unsigned long long>(prog.size()),
              static_cast<unsigned long long>(b_ref),
              raw_score, norm_score,
              static_cast<unsigned long long>(r.steps),
              static_cast<long long>(r.p_min),
              static_cast<long long>(r.p_max));
    }
    return 0;
}
