// Ultra-High-Performance Deterministic Single-Tape Bitfuck VM (v4)
// for "The Compression Singularity".
//
// Microarchitectural optimizations (100% raw-step-exact & OOB-exact):
//   - 64 KiB L2-cache-resident dynamically-growing tape (grows on demand up to |p| <= B)
//     with 128-byte zero guard padding on both sides for safe unaligned AVX-512/AVX2 loads
//   - 32-byte HotOp (2 ops per L1 cache line) with direct-threaded `goto *ip->handler`
//     and HALT sentinel (zero pc bounds check, zero jump-table indirection)
//   - Cold OpMeta split out of hot stream (touched only on cold fallback or profiling)
//   - Template-specialized hot loop <DO_TRACE, DO_PROF, RUN_ONLY>
//   - Countdown remaining-step register `rem`; zero-division `a * k <= rem` seek bounds;
//     watchdog checked ONLY on backward jumps (JNZ)
//   - AVX-512BW (64 B/cycle) -> AVX2 (32 B/cycle) -> 64-bit SWAR SIMD kernels:
//       * EMIT                : 64 bits/cycle target comparison
//       * SEEK_R1 / SEEK_L1   : 64 cells/cycle [>] and [<]
//       * SEEK_R2 / SEEK_L2   : 32 stride-2 cells/cycle [>>] and [<<]
//       * SEEK_R4 / SEEK_L4   : 16 stride-4 cells/cycle [>>>>] and [<<<<]
//       * CLEAR_SEEK_R1/L1/R2/L2 : d==1 carry-chain fast-path + SIMD scan + inline SIMD clear
//   - Persistent --server mode with zero-copy cached target & O(p_max - p_min) dirty-range reset

#include <algorithm>
#include <chrono>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <fstream>
#include <iostream>
#include <string>
#include <utility>
#include <vector>

#if defined(__AVX512BW__) || defined(__AVX2__)
#include <immintrin.h>
#endif

namespace {

static constexpr int64_t GUARD_PAD = 128;

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
    HALT = 25,
};

// 32-byte hot instruction (exact half cache line; 2 HotOps per 64B L1 cache line)
struct HotOp {
    void* handler;       // direct-threaded label address (GCC/Clang)
    int64_t arg;         // move delta / flip parity / stride / emit count / JZ-JNZ target ptr or index
    uint32_t raw_steps;  // exact raw steps for op
    int16_t min_off;     // min tape offset in linear/balanced block (|off| <= 4096)
    int16_t max_off;     // max tape offset in linear/balanced block (|off| <= 4096)
    uint32_t aux_begin;  // flip_offsets range begin
    uint32_t aux_end;    // flip_offsets range end
};
static_assert(sizeof(HotOp) == 32, "HotOp must stay 32 bytes");

struct OpMeta {
    uint32_t src_lo;
    uint32_t src_hi;
    OpKind kind;
};

struct TraceSample {
    uint64_t step;
    int64_t p;
    int64_t p_min;
    int64_t p_max;
    size_t out_pos;
    size_t ones_approx;
    std::string window;
};

struct ProfileEntry {
    uint32_t src_lo;
    uint32_t src_hi;
    std::string kind;
    uint64_t hits;
    uint64_t raw_steps;
};

struct VerifyResult {
    std::string verdict;
    std::string message;
    size_t prog_len = 0;
    size_t ir_ops = 0;
    uint64_t steps = 0;
    size_t out_len = 0;
    size_t target_len = 0;
    int64_t p_final = 0;
    int64_t p_min = 0;
    int64_t p_max = 0;
    double elapsed_ms = 0.0;
    uint64_t op_counts[6] = {0, 0, 0, 0, 0, 0};
    std::string out_bits;
    std::vector<int64_t> nonzero_sample;
    size_t nonzero_total = 0;
    std::vector<TraceSample> trace;
    std::vector<ProfileEntry> hotspots;
};

// Dynamically-growing L2-cache-friendly tape + reusable compiler scratch buffers.
struct TapeWorkspace {
    std::vector<uint8_t> buf;
    int64_t cap = 0;
    int64_t dirty_lo = 0;
    int64_t dirty_hi = -1;

    // Reusable compiler buffers across --server requests
    std::vector<HotOp> ops;
    std::vector<OpMeta> meta;
    std::vector<int32_t> flip_offsets;
    std::vector<size_t> match;
    std::vector<size_t> bstack;
    std::vector<int32_t> scratch_flips;

    void prepare(int64_t B) {
        const int64_t init_cap = std::min<int64_t>(B, 32768LL);
        if (buf.empty() || cap < init_cap) {
            cap = init_cap;
            buf.assign(static_cast<size_t>(2 * cap + 1 + 2 * GUARD_PAD), 0);
            dirty_lo = 0;
            dirty_hi = -1;
        } else if (dirty_lo <= dirty_hi) {
            int64_t lo = std::max(-cap, dirty_lo);
            int64_t hi = std::min(cap, dirty_hi);
            if (lo <= hi) {
                std::memset(buf.data() + GUARD_PAD + cap + lo, 0, static_cast<size_t>(hi - lo + 1));
            }
            dirty_lo = 0;
            dirty_hi = -1;
        }
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

    void mark_dirty(int64_t lo, int64_t hi) {
        dirty_lo = lo;
        dirty_hi = hi;
    }
};

static const char* kind_name(OpKind k) {
    switch (k) {
        case OpKind::RIGHT: return "RIGHT";
        case OpKind::LEFT: return "LEFT";
        case OpKind::FLIP: return "FLIP";
        case OpKind::LINEAR: return "LINEAR";
        case OpKind::EMIT: return "EMIT";
        case OpKind::JZ: return "JZ";
        case OpKind::JNZ: return "JNZ";
        case OpKind::CLEAR: return "CLEAR";
        case OpKind::INF_LOOP: return "INF_LOOP";
        case OpKind::SEEK_R1: return "SEEK_R1";
        case OpKind::SEEK_L1: return "SEEK_L1";
        case OpKind::SEEK_R2: return "SEEK_R2";
        case OpKind::SEEK_L2: return "SEEK_L2";
        case OpKind::SEEK_R4: return "SEEK_R4";
        case OpKind::SEEK_L4: return "SEEK_L4";
        case OpKind::SEEK_R: return "SEEK_R";
        case OpKind::SEEK_L: return "SEEK_L";
        case OpKind::CLEAR_SEEK_R1: return "CLEAR_SEEK_R1";
        case OpKind::CLEAR_SEEK_L1: return "CLEAR_SEEK_L1";
        case OpKind::CLEAR_SEEK_R2: return "CLEAR_SEEK_R2";
        case OpKind::CLEAR_SEEK_L2: return "CLEAR_SEEK_L2";
        case OpKind::CLEAR_SEEK_R: return "CLEAR_SEEK_R";
        case OpKind::CLEAR_SEEK_L: return "CLEAR_SEEK_L";
        case OpKind::IF_BALANCED: return "IF_BALANCED";
        case OpKind::INF_BALANCED: return "INF_BALANCED";
        case OpKind::HALT: return "HALT";
    }
    return "UNKNOWN";
}

// ---------------------------------------------------------------------------
// AVX-512BW (64 B/cycle) -> AVX2 (32 B/cycle) -> SWAR SIMD Kernels
// All unaligned loads are 100% bounds-safe via GUARD_PAD = 128 on both ends.
// ---------------------------------------------------------------------------

static inline uint64_t scan_right_zero_s1(const uint8_t* base, int64_t p, uint64_t limit) {
    uint64_t d = 0;
    const uint8_t* ptr = base + p + 1;
#if defined(__AVX512BW__)
    const __m512i z = _mm512_setzero_si512();
    while (d + 64 <= limit) {
        uint64_t m = static_cast<uint64_t>(_mm512_cmpeq_epi8_mask(_mm512_loadu_si512(ptr + d), z));
        if (m) return d + static_cast<uint64_t>(__builtin_ctzll(m)) + 1;
        d += 64;
    }
    if (d < limit) {
        uint32_t rem = static_cast<uint32_t>(limit - d);
        uint64_t m = static_cast<uint64_t>(_mm512_cmpeq_epi8_mask(_mm512_loadu_si512(ptr + d), z));
        m &= (1ULL << rem) - 1ULL;
        if (m) return d + static_cast<uint64_t>(__builtin_ctzll(m)) + 1;
    }
    return 0;
#elif defined(__AVX2__)
    const __m256i z = _mm256_setzero_si256();
    while (d + 32 <= limit) {
        __m256i v = _mm256_loadu_si256(reinterpret_cast<const __m256i*>(ptr + d));
        uint32_t m = static_cast<uint32_t>(_mm256_movemask_epi8(_mm256_cmpeq_epi8(v, z)));
        if (m) return d + static_cast<uint64_t>(__builtin_ctz(m)) + 1;
        d += 32;
    }
    if (d < limit) {
        uint32_t rem = static_cast<uint32_t>(limit - d);
        __m256i v = _mm256_loadu_si256(reinterpret_cast<const __m256i*>(ptr + d));
        uint32_t m = static_cast<uint32_t>(_mm256_movemask_epi8(_mm256_cmpeq_epi8(v, z)));
        m &= (1U << rem) - 1U;
        if (m) return d + static_cast<uint64_t>(__builtin_ctz(m)) + 1;
    }
    return 0;
#else
    if (limit > 0) {
        const void* f = std::memchr(ptr, 0, static_cast<size_t>(limit));
        if (f) return static_cast<uint64_t>(static_cast<const uint8_t*>(f) - ptr) + 1;
    }
    return 0;
#endif
}

static inline uint64_t scan_left_zero_s1(const uint8_t* base, int64_t p, uint64_t limit) {
    uint64_t d = 0;
#if defined(__AVX512BW__)
    const __m512i z = _mm512_setzero_si512();
    while (d + 64 <= limit) {
        uint64_t m = static_cast<uint64_t>(_mm512_cmpeq_epi8_mask(_mm512_loadu_si512(base + p - static_cast<int64_t>(d) - 64), z));
        if (m) return d + static_cast<uint64_t>(__builtin_clzll(m)) + 1;
        d += 64;
    }
    if (d < limit) {
        uint32_t rem = static_cast<uint32_t>(limit - d);
        uint64_t m = static_cast<uint64_t>(_mm512_cmpeq_epi8_mask(_mm512_loadu_si512(base + p - static_cast<int64_t>(d) - 64), z));
        m &= ~((1ULL << (64U - rem)) - 1ULL);
        if (m) return d + static_cast<uint64_t>(__builtin_clzll(m)) + 1;
    }
    return 0;
#elif defined(__AVX2__)
    const __m256i z = _mm256_setzero_si256();
    while (d + 32 <= limit) {
        __m256i v = _mm256_loadu_si256(reinterpret_cast<const __m256i*>(base + p - static_cast<int64_t>(d) - 32));
        uint32_t m = static_cast<uint32_t>(_mm256_movemask_epi8(_mm256_cmpeq_epi8(v, z)));
        if (m) return d + static_cast<uint64_t>(__builtin_clz(m)) + 1;
        d += 32;
    }
    if (d < limit) {
        uint32_t rem = static_cast<uint32_t>(limit - d);
        __m256i v = _mm256_loadu_si256(reinterpret_cast<const __m256i*>(base + p - static_cast<int64_t>(d) - 32));
        uint32_t m = static_cast<uint32_t>(_mm256_movemask_epi8(_mm256_cmpeq_epi8(v, z)));
        m &= ~((1U << (32U - rem)) - 1U);
        if (m) return d + static_cast<uint64_t>(__builtin_clz(m)) + 1;
    }
    return 0;
#else
    for (uint64_t k = 1; k <= limit; ++k) if (base[p - static_cast<int64_t>(k)] == 0) return k;
    return 0;
#endif
}

static inline uint64_t scan_right_zero_s2(const uint8_t* base, int64_t p, uint64_t limit) {
    uint64_t d = 0;
    const uint8_t* ptr = base + p + 2;
#if defined(__AVX512BW__)
    const __m512i z = _mm512_setzero_si512();
    while (d + 32 <= limit) {
        uint64_t m = static_cast<uint64_t>(_mm512_cmpeq_epi8_mask(_mm512_loadu_si512(ptr + (d << 1)), z)) & 0x5555555555555555ULL;
        if (m) return d + static_cast<uint64_t>(__builtin_ctzll(m) >> 1) + 1;
        d += 32;
    }
    if (d < limit) {
        uint32_t rem = static_cast<uint32_t>(limit - d);
        uint64_t m = static_cast<uint64_t>(_mm512_cmpeq_epi8_mask(_mm512_loadu_si512(ptr + (d << 1)), z)) & 0x5555555555555555ULL;
        m &= (1ULL << (rem << 1)) - 1ULL;
        if (m) return d + static_cast<uint64_t>(__builtin_ctzll(m) >> 1) + 1;
    }
    return 0;
#elif defined(__AVX2__)
    const __m256i z = _mm256_setzero_si256();
    while (d + 16 <= limit) {
        __m256i v = _mm256_loadu_si256(reinterpret_cast<const __m256i*>(ptr + (d << 1)));
        uint32_t m = static_cast<uint32_t>(_mm256_movemask_epi8(_mm256_cmpeq_epi8(v, z))) & 0x55555555U;
        if (m) return d + static_cast<uint64_t>(__builtin_ctz(m) >> 1) + 1;
        d += 16;
    }
    if (d < limit) {
        uint32_t rem = static_cast<uint32_t>(limit - d);
        __m256i v = _mm256_loadu_si256(reinterpret_cast<const __m256i*>(ptr + (d << 1)));
        uint32_t m = static_cast<uint32_t>(_mm256_movemask_epi8(_mm256_cmpeq_epi8(v, z))) & 0x55555555U;
        m &= (1U << (rem << 1)) - 1U;
        if (m) return d + static_cast<uint64_t>(__builtin_ctz(m) >> 1) + 1;
    }
    return 0;
#else
    for (uint64_t k = 1; k <= limit; ++k) if (base[p + static_cast<int64_t>(k << 1)] == 0) return k;
    return 0;
#endif
}

static inline uint64_t scan_left_zero_s2(const uint8_t* base, int64_t p, uint64_t limit) {
    uint64_t d = 0;
#if defined(__AVX512BW__)
    const __m512i z = _mm512_setzero_si512();
    while (d + 32 <= limit) {
        uint64_t m = static_cast<uint64_t>(_mm512_cmpeq_epi8_mask(_mm512_loadu_si512(base + p - static_cast<int64_t>(d << 1) - 64), z)) & 0x5555555555555555ULL;
        if (m) return d + static_cast<uint64_t>(__builtin_clzll(m) >> 1) + 1;
        d += 32;
    }
    if (d < limit) {
        uint32_t rem = static_cast<uint32_t>(limit - d);
        uint64_t m = static_cast<uint64_t>(_mm512_cmpeq_epi8_mask(_mm512_loadu_si512(base + p - static_cast<int64_t>(d << 1) - 64), z)) & 0x5555555555555555ULL;
        m &= ~((1ULL << (64U - (rem << 1))) - 1ULL);
        if (m) return d + static_cast<uint64_t>(__builtin_clzll(m) >> 1) + 1;
    }
    return 0;
#elif defined(__AVX2__)
    const __m256i z = _mm256_setzero_si256();
    while (d + 16 <= limit) {
        __m256i v = _mm256_loadu_si256(reinterpret_cast<const __m256i*>(base + p - static_cast<int64_t>(d << 1) - 32));
        uint32_t m = static_cast<uint32_t>(_mm256_movemask_epi8(_mm256_cmpeq_epi8(v, z))) & 0x55555555U;
        if (m) return d + static_cast<uint64_t>(__builtin_clz(m) >> 1) + 1;
        d += 16;
    }
    if (d < limit) {
        uint32_t rem = static_cast<uint32_t>(limit - d);
        __m256i v = _mm256_loadu_si256(reinterpret_cast<const __m256i*>(base + p - static_cast<int64_t>(d << 1) - 32));
        uint32_t m = static_cast<uint32_t>(_mm256_movemask_epi8(_mm256_cmpeq_epi8(v, z))) & 0x55555555U;
        m &= ~((1U << (32U - (rem << 1))) - 1U);
        if (m) return d + static_cast<uint64_t>(__builtin_clz(m) >> 1) + 1;
    }
    return 0;
#else
    for (uint64_t k = 1; k <= limit; ++k) if (base[p - static_cast<int64_t>(k << 1)] == 0) return k;
    return 0;
#endif
}

static inline uint64_t scan_right_zero_s4(const uint8_t* base, int64_t p, uint64_t limit) {
    uint64_t d = 0;
    const uint8_t* ptr = base + p + 4;
#if defined(__AVX512BW__)
    const __m512i z = _mm512_setzero_si512();
    while (d + 16 <= limit) {
        uint64_t m = static_cast<uint64_t>(_mm512_cmpeq_epi8_mask(_mm512_loadu_si512(ptr + (d << 2)), z)) & 0x1111111111111111ULL;
        if (m) return d + static_cast<uint64_t>(__builtin_ctzll(m) >> 2) + 1;
        d += 16;
    }
    if (d < limit) {
        uint32_t rem = static_cast<uint32_t>(limit - d);
        uint64_t m = static_cast<uint64_t>(_mm512_cmpeq_epi8_mask(_mm512_loadu_si512(ptr + (d << 2)), z)) & 0x1111111111111111ULL;
        m &= (1ULL << (rem << 2)) - 1ULL;
        if (m) return d + static_cast<uint64_t>(__builtin_ctzll(m) >> 2) + 1;
    }
    return 0;
#elif defined(__AVX2__)
    const __m256i z = _mm256_setzero_si256();
    while (d + 8 <= limit) {
        __m256i v = _mm256_loadu_si256(reinterpret_cast<const __m256i*>(ptr + (d << 2)));
        uint32_t m = static_cast<uint32_t>(_mm256_movemask_epi8(_mm256_cmpeq_epi8(v, z))) & 0x11111111U;
        if (m) return d + static_cast<uint64_t>(__builtin_ctz(m) >> 2) + 1;
        d += 8;
    }
    if (d < limit) {
        uint32_t rem = static_cast<uint32_t>(limit - d);
        __m256i v = _mm256_loadu_si256(reinterpret_cast<const __m256i*>(ptr + (d << 2)));
        uint32_t m = static_cast<uint32_t>(_mm256_movemask_epi8(_mm256_cmpeq_epi8(v, z))) & 0x11111111U;
        m &= (1U << (rem << 2)) - 1U;
        if (m) return d + static_cast<uint64_t>(__builtin_ctz(m) >> 2) + 1;
    }
    return 0;
#else
    for (uint64_t k = 1; k <= limit; ++k) if (base[p + static_cast<int64_t>(k << 2)] == 0) return k;
    return 0;
#endif
}

static inline uint64_t scan_left_zero_s4(const uint8_t* base, int64_t p, uint64_t limit) {
    uint64_t d = 0;
#if defined(__AVX512BW__)
    const __m512i z = _mm512_setzero_si512();
    while (d + 16 <= limit) {
        uint64_t m = static_cast<uint64_t>(_mm512_cmpeq_epi8_mask(_mm512_loadu_si512(base + p - static_cast<int64_t>(d << 2) - 64), z)) & 0x1111111111111111ULL;
        if (m) return d + static_cast<uint64_t>(__builtin_clzll(m) >> 2) + 1;
        d += 16;
    }
    if (d < limit) {
        uint32_t rem = static_cast<uint32_t>(limit - d);
        uint64_t m = static_cast<uint64_t>(_mm512_cmpeq_epi8_mask(_mm512_loadu_si512(base + p - static_cast<int64_t>(d << 2) - 64), z)) & 0x1111111111111111ULL;
        m &= ~((1ULL << (64U - (rem << 2))) - 1ULL);
        if (m) return d + static_cast<uint64_t>(__builtin_clzll(m) >> 2) + 1;
    }
    return 0;
#elif defined(__AVX2__)
    const __m256i z = _mm256_setzero_si256();
    while (d + 8 <= limit) {
        __m256i v = _mm256_loadu_si256(reinterpret_cast<const __m256i*>(base + p - static_cast<int64_t>(d << 2) - 32));
        uint32_t m = static_cast<uint32_t>(_mm256_movemask_epi8(_mm256_cmpeq_epi8(v, z))) & 0x11111111U;
        if (m) return d + static_cast<uint64_t>(__builtin_clz(m) >> 2) + 1;
        d += 8;
    }
    if (d < limit) {
        uint32_t rem = static_cast<uint32_t>(limit - d);
        __m256i v = _mm256_loadu_si256(reinterpret_cast<const __m256i*>(base + p - static_cast<int64_t>(d << 2) - 32));
        uint32_t m = static_cast<uint32_t>(_mm256_movemask_epi8(_mm256_cmpeq_epi8(v, z))) & 0x11111111U;
        m &= ~((1U << (32U - (rem << 2))) - 1U);
        if (m) return d + static_cast<uint64_t>(__builtin_clz(m) >> 2) + 1;
    }
    return 0;
#else
    for (uint64_t k = 1; k <= limit; ++k) if (base[p - static_cast<int64_t>(k << 2)] == 0) return k;
    return 0;
#endif
}

static inline void clear_s1_fast(uint8_t* ptr, uint64_t count) {
    if (VM_LIKELY(count == 1)) { ptr[0] = 0; return; }
    uint64_t i = 0;
#if defined(__AVX512BW__)
    if (count >= 64) {
        const __m512i z = _mm512_setzero_si512();
        while (i + 64 <= count) {
            _mm512_storeu_si512(ptr + i, z);
            i += 64;
        }
        if (i < count) _mm512_storeu_si512(ptr + count - 64, z);
        return;
    }
#endif
#if defined(__AVX2__)
    if (count >= 32) {
        const __m256i z = _mm256_setzero_si256();
        while (i + 32 <= count) {
            _mm256_storeu_si256(reinterpret_cast<__m256i*>(ptr + i), z);
            i += 32;
        }
        if (i < count) _mm256_storeu_si256(reinterpret_cast<__m256i*>(ptr + count - 32), z);
        return;
    }
#endif
    while (i + 8 <= count) {
        uint64_t z64 = 0;
        std::memcpy(ptr + i, &z64, 8);
        i += 8;
    }
    while (i < count) { ptr[i++] = 0; }
}

static inline void clear_stride2_right(uint8_t* ptr, uint64_t count) {
    if (VM_LIKELY(count == 1)) { ptr[0] = 0; return; }
    uint64_t i = 0;
#if defined(__AVX512BW__)
    const __m512i keep_odd512 = _mm512_set1_epi16(static_cast<short>(0xFF00));
    while (i + 32 <= count) {
        void* p512 = ptr + (i << 1);
        _mm512_storeu_si512(p512, _mm512_and_si512(_mm512_loadu_si512(p512), keep_odd512));
        i += 32;
    }
#endif
#if defined(__AVX2__)
    const __m256i keep_odd = _mm256_set1_epi16(static_cast<short>(0xFF00));
    while (i + 16 <= count) {
        __m256i* p256 = reinterpret_cast<__m256i*>(ptr + (i << 1));
        _mm256_storeu_si256(p256, _mm256_and_si256(_mm256_loadu_si256(p256), keep_odd));
        i += 16;
    }
#endif
    while (i < count) {
        ptr[i << 1] = 0;
        ++i;
    }
}

static inline uint64_t find_first_mismatch_fast(const char* ptr, uint64_t n, char bit) {
    uint64_t i = 0;
#if defined(__AVX512BW__)
    const __m512i pat512 = _mm512_set1_epi8(bit);
    while (i + 64 <= n) {
        uint64_t m = static_cast<uint64_t>(_mm512_cmpneq_epi8_mask(_mm512_loadu_si512(ptr + i), pat512));
        if (m) return i + static_cast<uint64_t>(__builtin_ctzll(m));
        i += 64;
    }
#endif
#if defined(__AVX2__)
    const __m256i pat = _mm256_set1_epi8(bit);
    while (i + 32 <= n) {
        __m256i v = _mm256_loadu_si256(reinterpret_cast<const __m256i*>(ptr + i));
        uint32_t m = static_cast<uint32_t>(_mm256_movemask_epi8(_mm256_cmpeq_epi8(v, pat)));
        if (m != 0xFFFFFFFFU) return i + static_cast<uint64_t>(__builtin_ctz(~m));
        i += 32;
    }
#endif
    const uint64_t pat64 = (bit == '1') ? 0x3131313131313131ULL : 0x3030303030303030ULL;
    while (i + 8 <= n) {
        uint64_t w;
        std::memcpy(&w, ptr + i, 8);
        if (w != pat64) {
            for (uint64_t k = 0; k < 8; ++k) if (ptr[i + k] != bit) return i + k;
        }
        i += 8;
    }
    while (i < n) {
        if (ptr[i] != bit) return i;
        ++i;
    }
    return n;
}

static std::string json_escape(const std::string& s) {
    std::string out;
    out.reserve(s.size() + 8);
    for (unsigned char c : s) {
        switch (c) {
            case '"': out += "\\\""; break;
            case '\\': out += "\\\\"; break;
            case '\n': out += "\\n"; break;
            case '\r': out += "\\r"; break;
            case '\t': out += "\\t"; break;
            default:
                if (c < 0x20) {
                    char buf[8];
                    std::snprintf(buf, sizeof(buf), "\\u%04x", c);
                    out += buf;
                } else {
                    out.push_back(static_cast<char>(c));
                }
        }
    }
    return out;
}

static bool read_file_bytes(const std::string& path, std::string& out) {
    std::ifstream ifs(path, std::ios::binary);
    if (!ifs) return false;
    ifs.seekg(0, std::ios::end);
    std::streamoff sz = ifs.tellg();
    if (sz < 0) return false;
    out.resize(static_cast<size_t>(sz));
    ifs.seekg(0, std::ios::beg);
    if (sz > 0) {
        ifs.read(&out[0], sz);
        if (!ifs) return false;
    }
    return true;
}

static bool parse_target_file(const std::string& path, size_t& L, std::string& S, std::string& err) {
    std::ifstream ifs(path, std::ios::binary);
    if (!ifs) { err = "Failed to open target file"; return false; }
    std::string line1, line2;
    if (!std::getline(ifs, line1)) { err = "Empty target file"; return false; }
    if (!line1.empty() && line1.back() == '\r') line1.pop_back();
    try {
        size_t pos = 0;
        unsigned long long parsed = std::stoull(line1, &pos);
        while (pos < line1.size() && (line1[pos] == ' ' || line1[pos] == '\t')) ++pos;
        if (pos != line1.size()) { err = "Invalid L header in target file"; return false; }
        L = static_cast<size_t>(parsed);
    } catch (...) {
        err = "Invalid integer L in target file";
        return false;
    }
    if (!std::getline(ifs, line2)) { err = "Missing binary string S in target file"; return false; }
    if (!line2.empty() && line2.back() == '\r') line2.pop_back();
    if (line2.size() != L) { err = "Target string length mismatch with L"; return false; }
    for (char c : line2) {
        if (c != '0' && c != '1') { err = "Target string contains non-binary character"; return false; }
    }
    S.swap(line2);
    return true;
}

static void strip_single_trailing_newline(std::string& prog) {
    if (!prog.empty() && prog.back() == '\n') {
        prog.pop_back();
        if (!prog.empty() && prog.back() == '\r') prog.pop_back();
    }
}

static bool validate_and_compile(
    const std::string& prog,
    size_t max_prog_len,
    bool skip_ir_if_flat,
    TapeWorkspace& ws,
    uint64_t static_counts[6],
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
        message = "Program length " + std::to_string(prog.size()) + " exceeds limit " + std::to_string(max_prog_len);
        return false;
    }
    const size_t n = prog.size();
    const char* pstr = prog.data();
    ws.bstack.clear();

    size_t n_open = 0;
    for (size_t i = 0; i < n; ++i) {
        switch (pstr[i]) {
            case '>': static_counts[0]++; break;
            case '<': static_counts[1]++; break;
            case '+': static_counts[2]++; break;
            case '.': static_counts[3]++; break;
            case '[': static_counts[4]++; n_open++; break;
            case ']': static_counts[5]++; break;
            default:
                verdict = "SYNTAX_ERROR";
                message = "Illegal character (byte " + std::to_string(static_cast<unsigned char>(pstr[i])) +
                          ") at index " + std::to_string(i);
                return false;
        }
    }
    if (static_counts[4] != static_counts[5]) {
        ws.match.assign(n, 0);
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

    if (n_open > 0) {
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
    } else {
        ws.match.clear();
        if (skip_ir_if_flat) {
            ws.ops.clear();
            ws.meta.clear();
            return true;
        }
    }

    auto& ops = ws.ops;
    auto& meta = ws.meta;
    auto& flip_offsets = ws.flip_offsets;
    ops.clear();
    meta.clear();
    flip_offsets.clear();
    if (ops.capacity() < n + 16) {
        ops.reserve(n + 16);
        meta.reserve(n + 16);
    }

    auto emit_op = [&](OpKind kind, uint32_t slo, uint32_t shi, int64_t arg,
                       int16_t mn, int16_t mx, uint32_t ab, uint32_t ae, uint32_t rs) {
        ops.push_back({nullptr, arg, rs, mn, mx, ab, ae});
        meta.push_back({slo, shi, kind});
    };

    ws.bstack.clear();

    auto analyze_linear = [&](size_t lo, size_t hi, int64_t& delta, int16_t& min_off, int16_t& max_off,
                              uint32_t& aux_b, uint32_t& aux_e, bool& flips_zero) {
        auto& sf = ws.scratch_flips;
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
    // Sentinel HALT instruction: removes pc < n_ops check on EVERY dispatch!
    emit_op(OpKind::HALT, static_cast<uint32_t>(n), static_cast<uint32_t>(n), 0, 0, 0, 0, 0, 0);
    return true;
}

static void record_trace_sample(
    std::vector<TraceSample>& trace,
    uint64_t step,
    int64_t p,
    int64_t p_min,
    int64_t p_max,
    size_t out_pos,
    const uint8_t* base,
    int64_t cap
) {
    const int64_t half = 32;
    std::string win;
    win.reserve(65);
    for (int64_t dx = -half; dx <= half; ++dx) {
        int64_t q = p + dx;
        if (q < -cap || q > cap) win.push_back('.');
        else win.push_back(base[q] ? '1' : '0');
    }
    size_t ones = 0;
    int64_t lo = std::max(-cap, p_min);
    int64_t hi = std::min(cap, p_max);
    if (hi - lo <= 8192) {
        for (int64_t q = lo; q <= hi; ++q) ones += base[q];
    } else {
        int64_t stride = (hi - lo) / 4096 + 1;
        size_t hits = 0, total = 0;
        for (int64_t q = lo; q <= hi; q += stride) { hits += base[q]; ++total; }
        ones = total ? static_cast<size_t>((static_cast<double>(hits) / total) * (hi - lo + 1)) : 0;
    }
    trace.push_back({step, p, p_min, p_max, out_pos, ones, std::move(win)});
}

struct RunOptions {
    uint64_t step_limit = 3000000000000ULL;
    int64_t tape_bound = 10000000LL;
    size_t max_prog_len = 0;
    double wall_limit_s = 600.0;
    int trace_points = 0;
    bool profile = false;
    bool run_only = false;
    size_t max_out_bits = 2000000;
};

template <bool DO_TRACE, bool DO_PROF, bool RUN_ONLY>
static VerifyResult run_vm_core(
    const std::string& target,
    const std::string& prog,
    const RunOptions& opt,
    TapeWorkspace& ws,
    VerifyResult& res,
    std::chrono::steady_clock::time_point t_start
) {
    const int64_t B = opt.tape_bound;
    ws.prepare(B);
    uint8_t* base = ws.base_ptr();
    int64_t C = std::min(ws.cap, B);

    const size_t L = target.size();
    const char* const target_ptr = target.data();
    const uint64_t M = opt.step_limit;
    uint64_t rem = M;

    if constexpr (RUN_ONLY) {
        res.out_bits.reserve(std::min<size_t>(opt.max_out_bits, 65536));
    }

    int64_t p = 0, p_min = 0, p_max = 0;
    size_t out_pos = 0;
    const size_t n_real_ops = ws.ops.size() - 1;  // exclude trailing HALT sentinel
    HotOp* const op_arr = ws.ops.data();
    const OpMeta* const meta_arr = ws.meta.data();
    const int32_t* const flips_arr = ws.flip_offsets.data();
    const char* const prog_ptr = prog.data();

    std::vector<uint64_t> prof_hits(DO_PROF ? n_real_ops : 0, 0);
    std::vector<uint64_t> prof_steps(DO_PROF ? n_real_ops : 0, 0);

    uint64_t wd = 0;
    const uint64_t WD_INTERVAL = 32000000ULL;

    size_t next_trace_out = 0;
    size_t trace_out_stride = 0;
    uint64_t next_trace_step = 0;
    uint64_t trace_step_stride = 0;
    if constexpr (DO_TRACE) {
        trace_out_stride = std::max<size_t>(1, (RUN_ONLY ? opt.max_out_bits : L) / static_cast<size_t>(opt.trace_points));
        trace_step_stride = std::max<uint64_t>(1, std::min<uint64_t>(M, 10000000ULL) / static_cast<size_t>(opt.trace_points));
        record_trace_sample(res.trace, 0, 0, 0, 0, 0, base, C);
        next_trace_out = trace_out_stride;
        next_trace_step = trace_step_stride;
    }

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
        const uint64_t t_done = M - rem;
        if constexpr (DO_TRACE) {
            if (res.trace.empty() || res.trace.back().step != t_done || res.trace.back().out_pos != out_pos) {
                record_trace_sample(res.trace, t_done, std::clamp(p, -C, C), p_min, p_max, out_pos, base, C);
            }
        }
        if constexpr (RUN_ONLY) {
            int64_t lo = std::max(-C, p_min);
            int64_t hi = std::min(C, p_max);
            for (int64_t q = lo; q <= hi; ++q) {
                if (base[q]) {
                    res.nonzero_total++;
                    if (res.nonzero_sample.size() < 32) res.nonzero_sample.push_back(q);
                }
            }
        }
        ws.mark_dirty(p_min, p_max);
        res.verdict = v;
        res.message = msg;
        res.steps = t_done;
        res.out_len = out_pos;
        res.p_final = p;
        res.p_min = p_min;
        res.p_max = p_max;
        res.elapsed_ms = std::chrono::duration<double, std::milli>(std::chrono::steady_clock::now() - t_start).count();
        if constexpr (DO_PROF) {
            std::vector<size_t> idx(n_real_ops);
            for (size_t i = 0; i < n_real_ops; ++i) idx[i] = i;
            std::sort(idx.begin(), idx.end(), [&](size_t a, size_t b) { return prof_steps[a] > prof_steps[b]; });
            const size_t top_k = std::min<size_t>(16, n_real_ops);
            for (size_t k = 0; k < top_k; ++k) {
                size_t id = idx[k];
                if (prof_steps[id] == 0) break;
                res.hotspots.push_back({meta_arr[id].src_lo, meta_arr[id].src_hi, kind_name(meta_arr[id].kind), prof_hits[id], prof_steps[id]});
            }
        }
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
                    if constexpr (RUN_ONLY) {
                        if (res.out_bits.size() < opt.max_out_bits) res.out_bits.push_back(bit);
                        ++out_pos; ++s_ip;
                    } else {
                        if (out_pos >= L) { out_v = "OUTPUT_MISMATCH"; out_m = "Output length exceeded target length " + std::to_string(L); return false; }
                        if (target_ptr[out_pos] != bit) {
                            out_v = "OUTPUT_MISMATCH";
                            out_m = "Bit mismatch at index " + std::to_string(out_pos) + ": expected '" + target_ptr[out_pos] + "', got '" + bit + "'";
                            return false;
                        }
                        ++out_pos; ++s_ip;
                    }
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
    // Pre-bind direct-threaded handler pointer AND jump-target pointer into each 32-byte HotOp!
    for (size_t i = 0; i <= n_real_ops; ++i) {
        OpKind k = meta_arr[i].kind;
        op_arr[i].handler = dispatch_tbl[static_cast<size_t>(k)];
        if (k == OpKind::JZ || k == OpKind::JNZ) {
            op_arr[i].arg = reinterpret_cast<int64_t>(op_arr + static_cast<size_t>(op_arr[i].arg));
        }
    }
    const HotOp* ip = op_arr;

#define VM_NEXT() do { \
    ++ip; \
    if constexpr (DO_TRACE) { \
        const uint64_t t_now = M - rem; \
        if (res.trace.size() < static_cast<size_t>(opt.trace_points + 2) && (out_pos >= next_trace_out || t_now >= next_trace_step)) { \
            record_trace_sample(res.trace, t_now, p, p_min, p_max, out_pos, base, C); \
            while (next_trace_out <= out_pos) next_trace_out += trace_out_stride; \
            while (next_trace_step <= t_now) next_trace_step += trace_step_stride; \
        } \
    } \
    goto *ip->handler; \
} while (0)

#define VM_JUMP(tgt_ptr) do { \
    ip = (tgt_ptr); \
    if constexpr (DO_TRACE) { \
        const uint64_t t_now = M - rem; \
        if (res.trace.size() < static_cast<size_t>(opt.trace_points + 2) && (out_pos >= next_trace_out || t_now >= next_trace_step)) { \
            record_trace_sample(res.trace, t_now, p, p_min, p_max, out_pos, base, C); \
            while (next_trace_out <= out_pos) next_trace_out += trace_out_stride; \
            while (next_trace_step <= t_now) next_trace_step += trace_step_stride; \
        } \
    } \
    goto *ip->handler; \
} while (0)

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
        if constexpr (DO_TRACE) {
            const uint64_t t_now = M - rem;
            if (res.trace.size() < static_cast<size_t>(opt.trace_points + 2) && (out_pos >= next_trace_out || t_now >= next_trace_step)) {
                record_trace_sample(res.trace, t_now, p, p_min, p_max, out_pos, base, C);
                while (next_trace_out <= out_pos) next_trace_out += trace_out_stride;
                while (next_trace_step <= t_now) next_trace_step += trace_step_stride;
            }
        }
        switch (meta_arr[CUR_IDX()].kind) {
#endif

    VM_LBL(L_RIGHT) {
        const int64_t np = p + ip->arg;
        const uint64_t rs = ip->raw_steps;
        if (VM_LIKELY(np <= C && rs <= rem)) {
            p = np;
            if (p > p_max) p_max = p;
            rem -= rs;
            if constexpr (DO_PROF) { size_t id = CUR_IDX(); prof_hits[id]++; prof_steps[id] += rs; }
            VM_NEXT();
        }
        if (rs <= rem && ensure_cap(p, np)) {
            p = np;
            if (p > p_max) p_max = p;
            rem -= rs;
            if constexpr (DO_PROF) { size_t id = CUR_IDX(); prof_hits[id]++; prof_steps[id] += rs; }
            VM_NEXT();
        }
        const OpMeta& m = meta_arr[CUR_IDX()];
        if (!run_raw_slice(m.src_lo, m.src_hi, cold_v, cold_m)) return finish(cold_v, cold_m);
        VM_NEXT();
    }

    VM_LBL(L_LEFT) {
        const int64_t np = p - ip->arg;
        const uint64_t rs = ip->raw_steps;
        if (VM_LIKELY(np >= -C && rs <= rem)) {
            p = np;
            if (p < p_min) p_min = p;
            rem -= rs;
            if constexpr (DO_PROF) { size_t id = CUR_IDX(); prof_hits[id]++; prof_steps[id] += rs; }
            VM_NEXT();
        }
        if (rs <= rem && ensure_cap(np, p)) {
            p = np;
            if (p < p_min) p_min = p;
            rem -= rs;
            if constexpr (DO_PROF) { size_t id = CUR_IDX(); prof_hits[id]++; prof_steps[id] += rs; }
            VM_NEXT();
        }
        const OpMeta& m = meta_arr[CUR_IDX()];
        if (!run_raw_slice(m.src_lo, m.src_hi, cold_v, cold_m)) return finish(cold_v, cold_m);
        VM_NEXT();
    }

    VM_LBL(L_FLIP) {
        const uint64_t rs = ip->raw_steps;
        if (VM_LIKELY(rs <= rem)) {
            base[p] ^= static_cast<uint8_t>(ip->arg);
            rem -= rs;
            if constexpr (DO_PROF) { size_t id = CUR_IDX(); prof_hits[id]++; prof_steps[id] += rs; }
            VM_NEXT();
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
            p += ip->arg;
            rem -= rs;
            if constexpr (DO_PROF) { size_t id = CUR_IDX(); prof_hits[id]++; prof_steps[id] += rs; }
            VM_NEXT();
        }
        if (rs <= rem && ensure_cap(lo, hi)) {
            for (uint32_t k = ip->aux_begin; k < ip->aux_end; ++k) base[p + flips_arr[k]] ^= 1;
            if (lo < p_min) p_min = lo;
            if (hi > p_max) p_max = hi;
            p += ip->arg;
            rem -= rs;
            if constexpr (DO_PROF) { size_t id = CUR_IDX(); prof_hits[id]++; prof_steps[id] += rs; }
            VM_NEXT();
        }
        const OpMeta& m = meta_arr[CUR_IDX()];
        if (!run_raw_slice(m.src_lo, m.src_hi, cold_v, cold_m)) return finish(cold_v, cold_m);
        VM_NEXT();
    }

    VM_LBL(L_EMIT) {
        const uint64_t k = static_cast<uint64_t>(ip->arg);
        if (VM_UNLIKELY(rem == 0)) return finish("STEP_LIMIT_EXCEEDED", "Exceeded step limit " + std::to_string(M));
        const char bit = base[p] ? '1' : '0';
        if constexpr (RUN_ONLY) {
            if (VM_LIKELY(k <= rem)) {
                if (res.out_bits.size() < opt.max_out_bits) {
                    res.out_bits.append(std::min<size_t>(static_cast<size_t>(k), opt.max_out_bits - res.out_bits.size()), bit);
                }
                out_pos += static_cast<size_t>(k);
                rem -= k;
                if constexpr (DO_PROF) { size_t id = CUR_IDX(); prof_hits[id]++; prof_steps[id] += k; }
                VM_NEXT();
            }
            if (res.out_bits.size() < opt.max_out_bits) {
                res.out_bits.append(std::min<size_t>(static_cast<size_t>(rem), opt.max_out_bits - res.out_bits.size()), bit);
            }
            out_pos += static_cast<size_t>(rem);
            rem = 0;
            return finish("STEP_LIMIT_EXCEEDED", "Exceeded step limit " + std::to_string(M));
        } else {
            if (VM_LIKELY(k == 1 && out_pos < L && target_ptr[out_pos] == bit)) {
                ++out_pos;
                --rem;
                if constexpr (DO_PROF) { size_t id = CUR_IDX(); prof_hits[id]++; prof_steps[id]++; }
                VM_NEXT();
            }
            const uint64_t avail = (out_pos < L) ? static_cast<uint64_t>(L - out_pos) : 0ULL;
            const uint64_t check_n = std::min(k, avail);
            const uint64_t mm = (check_n == 1) ? (target_ptr[out_pos] == bit ? 1ULL : 0ULL)
                                               : find_first_mismatch_fast(target_ptr + out_pos, check_n, bit);
            if (VM_LIKELY(mm == check_n && k <= avail)) {
                if (VM_LIKELY(k <= rem)) {
                    out_pos += static_cast<size_t>(k);
                    rem -= k;
                    if constexpr (DO_PROF) { size_t id = CUR_IDX(); prof_hits[id]++; prof_steps[id] += k; }
                    VM_NEXT();
                }
                out_pos += static_cast<size_t>(rem);
                rem = 0;
                return finish("STEP_LIMIT_EXCEEDED", "Exceeded step limit " + std::to_string(M));
            }
            const uint64_t need = mm + 1;
            if (need <= rem) {
                out_pos += static_cast<size_t>(mm);
                rem -= need;
                if (out_pos >= L) return finish("OUTPUT_MISMATCH", "Output length exceeded target length " + std::to_string(L));
                return finish("OUTPUT_MISMATCH", "Bit mismatch at index " + std::to_string(out_pos) +
                                                 ": expected '" + target_ptr[out_pos] + "', got '" + bit + "'");
            }
            out_pos += static_cast<size_t>(rem);
            rem = 0;
            return finish("STEP_LIMIT_EXCEEDED", "Exceeded step limit " + std::to_string(M));
        }
    }

    VM_LBL(L_JZ) {
        if (VM_UNLIKELY(rem == 0)) return finish("STEP_LIMIT_EXCEEDED", "Exceeded step limit " + std::to_string(M));
        --rem;
        if constexpr (DO_PROF) { size_t id = CUR_IDX(); prof_hits[id]++; prof_steps[id]++; }
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
            if (sec > opt.wall_limit_s) {
                return finish("VM_TIMEOUT", "VM verification exceeded physical time limit (" + std::to_string(opt.wall_limit_s) + "s)");
            }
        }
        if (VM_UNLIKELY(rem == 0)) return finish("STEP_LIMIT_EXCEEDED", "Exceeded step limit " + std::to_string(M));
        --rem;
        if constexpr (DO_PROF) { size_t id = CUR_IDX(); prof_hits[id]++; prof_steps[id]++; }
#if defined(__GNUC__) || defined(__clang__)
        VM_JUMP((base[p] != 0) ? reinterpret_cast<const HotOp*>(ip->arg) : (ip + 1));
#else
        VM_JUMP((base[p] != 0) ? (op_arr + static_cast<size_t>(ip->arg)) : (ip + 1));
#endif
    }

    VM_LBL(L_CLEAR) {
        if (VM_UNLIKELY(rem == 0)) return finish("STEP_LIMIT_EXCEEDED", "Exceeded step limit " + std::to_string(M));
        if (base[p] == 0) {
            --rem;
            if constexpr (DO_PROF) { size_t id = CUR_IDX(); prof_hits[id]++; prof_steps[id]++; }
            VM_NEXT();
        }
        if (VM_LIKELY(rem >= 3)) {
            base[p] = 0;
            rem -= 3;
            if constexpr (DO_PROF) { size_t id = CUR_IDX(); prof_hits[id]++; prof_steps[id] += 3; }
            VM_NEXT();
        }
        rem = 0;
        return finish("STEP_LIMIT_EXCEEDED", "Exceeded step limit " + std::to_string(M));
    }

    VM_LBL(L_INF_LOOP) {
        if (VM_UNLIKELY(rem == 0)) return finish("STEP_LIMIT_EXCEEDED", "Exceeded step limit " + std::to_string(M));
        --rem;
        if (VM_LIKELY(base[p] == 0)) {
            if constexpr (DO_PROF) { size_t id = CUR_IDX(); prof_hits[id]++; prof_steps[id]++; }
            VM_NEXT();
        }
        rem = 0;
        return finish("STEP_LIMIT_EXCEEDED", "Infinite empty loop '[]' on cell=1");
    }

    VM_LBL(L_IF_BALANCED) {
        if (VM_UNLIKELY(rem == 0)) return finish("STEP_LIMIT_EXCEEDED", "Exceeded step limit " + std::to_string(M));
        if (base[p] == 0) {
            --rem;
            if constexpr (DO_PROF) { size_t id = CUR_IDX(); prof_hits[id]++; prof_steps[id]++; }
            VM_NEXT();
        }
        const int64_t lo = p + ip->min_off;
        const int64_t hi = p + ip->max_off;
        const uint64_t rs = ip->raw_steps;
        if (VM_LIKELY(lo >= -C && hi <= C && rs <= rem)) {
            for (uint32_t k = ip->aux_begin; k < ip->aux_end; ++k) base[p + flips_arr[k]] ^= 1;
            if (lo < p_min) p_min = lo;
            if (hi > p_max) p_max = hi;
            rem -= rs;
            if constexpr (DO_PROF) { size_t id = CUR_IDX(); prof_hits[id]++; prof_steps[id] += rs; }
            VM_NEXT();
        }
        if (rs <= rem && ensure_cap(lo, hi)) {
            for (uint32_t k = ip->aux_begin; k < ip->aux_end; ++k) base[p + flips_arr[k]] ^= 1;
            if (lo < p_min) p_min = lo;
            if (hi > p_max) p_max = hi;
            rem -= rs;
            if constexpr (DO_PROF) { size_t id = CUR_IDX(); prof_hits[id]++; prof_steps[id] += rs; }
            VM_NEXT();
        }
        const OpMeta& m = meta_arr[CUR_IDX()];
        if (!run_raw_slice(m.src_lo, m.src_hi, cold_v, cold_m)) return finish(cold_v, cold_m);
        VM_NEXT();
    }

    VM_LBL(L_INF_BALANCED) {
        if (VM_UNLIKELY(rem == 0)) return finish("STEP_LIMIT_EXCEEDED", "Exceeded step limit " + std::to_string(M));
        if (base[p] == 0) {
            --rem;
            if constexpr (DO_PROF) { size_t id = CUR_IDX(); prof_hits[id]++; prof_steps[id]++; }
            VM_NEXT();
        }
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
            if constexpr (DO_PROF) { size_t id = CUR_IDX(); prof_hits[id] += full; prof_steps[id] += full * cycle + 1; }
        }
        if (!run_raw_slice(m.src_lo + 1, m.src_hi, cold_v, cold_m)) return finish(cold_v, cold_m);
        return finish("STEP_LIMIT_EXCEEDED", "Exceeded step limit " + std::to_string(M));
    }

    VM_LBL(L_SEEK_R1) {
        if (VM_UNLIKELY(rem == 0)) return finish("STEP_LIMIT_EXCEEDED", "Exceeded step limit " + std::to_string(M));
        --rem;
        if (base[p] == 0) { if constexpr (DO_PROF) { size_t id = CUR_IDX(); prof_hits[id]++; prof_steps[id]++; } VM_NEXT(); }
        uint64_t total_d = 0;
        while (true) {
            const uint64_t a = static_cast<uint64_t>(C - p);
            const uint64_t limit = VM_LIKELY((a << 1) <= rem) ? a : (rem >> 1);
            if (limit > 0) {
                const uint64_t d = scan_right_zero_s1(base, p, limit);
                if (VM_LIKELY(d > 0)) {
                    p += static_cast<int64_t>(d);
                    if (p > p_max) p_max = p;
                    rem -= d << 1;
                    total_d += d;
                    if constexpr (DO_PROF) { size_t id = CUR_IDX(); prof_hits[id]++; prof_steps[id] += 1 + (total_d << 1); }
                    VM_NEXT();
                }
                p += static_cast<int64_t>(limit);
                if (p > p_max) p_max = p;
                rem -= limit << 1;
                total_d += limit;
            }
            if (C < B && rem >= 2 && (C - p) < 1) {
                ws.grow_to(std::min(B, std::max<int64_t>(C * 2, C + 64)), B, p_min, p_max);
                base = ws.base_ptr();
                C = std::min(ws.cap, B);
                continue;
            }
            break;
        }
        if constexpr (DO_PROF) { size_t id = CUR_IDX(); prof_hits[id]++; prof_steps[id] += 1 + (total_d << 1); }
        const OpMeta& m = meta_arr[CUR_IDX()];
        if (!run_raw_slice(m.src_lo + 1, m.src_hi, cold_v, cold_m)) return finish(cold_v, cold_m);
        VM_NEXT();
    }

    VM_LBL(L_SEEK_L1) {
        if (VM_UNLIKELY(rem == 0)) return finish("STEP_LIMIT_EXCEEDED", "Exceeded step limit " + std::to_string(M));
        --rem;
        if (base[p] == 0) { if constexpr (DO_PROF) { size_t id = CUR_IDX(); prof_hits[id]++; prof_steps[id]++; } VM_NEXT(); }
        uint64_t total_d = 0;
        while (true) {
            const uint64_t a = static_cast<uint64_t>(p + C);
            const uint64_t limit = VM_LIKELY((a << 1) <= rem) ? a : (rem >> 1);
            if (limit > 0) {
                const uint64_t d = scan_left_zero_s1(base, p, limit);
                if (VM_LIKELY(d > 0)) {
                    p -= static_cast<int64_t>(d);
                    if (p < p_min) p_min = p;
                    rem -= d << 1;
                    total_d += d;
                    if constexpr (DO_PROF) { size_t id = CUR_IDX(); prof_hits[id]++; prof_steps[id] += 1 + (total_d << 1); }
                    VM_NEXT();
                }
                p -= static_cast<int64_t>(limit);
                if (p < p_min) p_min = p;
                rem -= limit << 1;
                total_d += limit;
            }
            if (C < B && rem >= 2 && (p + C) < 1) {
                ws.grow_to(std::min(B, std::max<int64_t>(C * 2, C + 64)), B, p_min, p_max);
                base = ws.base_ptr();
                C = std::min(ws.cap, B);
                continue;
            }
            break;
        }
        if constexpr (DO_PROF) { size_t id = CUR_IDX(); prof_hits[id]++; prof_steps[id] += 1 + (total_d << 1); }
        const OpMeta& m = meta_arr[CUR_IDX()];
        if (!run_raw_slice(m.src_lo + 1, m.src_hi, cold_v, cold_m)) return finish(cold_v, cold_m);
        VM_NEXT();
    }

    VM_LBL(L_SEEK_R2) {
        if (VM_UNLIKELY(rem == 0)) return finish("STEP_LIMIT_EXCEEDED", "Exceeded step limit " + std::to_string(M));
        --rem;
        if (base[p] == 0) { if constexpr (DO_PROF) { size_t id = CUR_IDX(); prof_hits[id]++; prof_steps[id]++; } VM_NEXT(); }
        uint64_t total_d = 0;
        while (true) {
            const uint64_t a = static_cast<uint64_t>(C - p) >> 1;
            const uint64_t limit = VM_LIKELY(a * 3ULL <= rem) ? a : (rem / 3ULL);
            if (limit > 0) {
                const uint64_t d = scan_right_zero_s2(base, p, limit);
                if (VM_LIKELY(d > 0)) {
                    p += static_cast<int64_t>(d << 1);
                    if (p > p_max) p_max = p;
                    rem -= d * 3ULL;
                    total_d += d;
                    if constexpr (DO_PROF) { size_t id = CUR_IDX(); prof_hits[id]++; prof_steps[id] += 1 + total_d * 3ULL; }
                    VM_NEXT();
                }
                p += static_cast<int64_t>(limit << 1);
                if (p > p_max) p_max = p;
                rem -= limit * 3ULL;
                total_d += limit;
            }
            if (C < B && rem >= 3 && (C - p) < 2) {
                ws.grow_to(std::min(B, std::max<int64_t>(C * 2, C + 64)), B, p_min, p_max);
                base = ws.base_ptr();
                C = std::min(ws.cap, B);
                continue;
            }
            break;
        }
        if constexpr (DO_PROF) { size_t id = CUR_IDX(); prof_hits[id]++; prof_steps[id] += 1 + total_d * 3ULL; }
        const OpMeta& m = meta_arr[CUR_IDX()];
        if (!run_raw_slice(m.src_lo + 1, m.src_hi, cold_v, cold_m)) return finish(cold_v, cold_m);
        VM_NEXT();
    }

    VM_LBL(L_SEEK_L2) {
        if (VM_UNLIKELY(rem == 0)) return finish("STEP_LIMIT_EXCEEDED", "Exceeded step limit " + std::to_string(M));
        --rem;
        if (base[p] == 0) { if constexpr (DO_PROF) { size_t id = CUR_IDX(); prof_hits[id]++; prof_steps[id]++; } VM_NEXT(); }
        uint64_t total_d = 0;
        while (true) {
            const uint64_t a = static_cast<uint64_t>(p + C) >> 1;
            const uint64_t limit = VM_LIKELY(a * 3ULL <= rem) ? a : (rem / 3ULL);
            if (limit > 0) {
                const uint64_t d = scan_left_zero_s2(base, p, limit);
                if (VM_LIKELY(d > 0)) {
                    p -= static_cast<int64_t>(d << 1);
                    if (p < p_min) p_min = p;
                    rem -= d * 3ULL;
                    total_d += d;
                    if constexpr (DO_PROF) { size_t id = CUR_IDX(); prof_hits[id]++; prof_steps[id] += 1 + total_d * 3ULL; }
                    VM_NEXT();
                }
                p -= static_cast<int64_t>(limit << 1);
                if (p < p_min) p_min = p;
                rem -= limit * 3ULL;
                total_d += limit;
            }
            if (C < B && rem >= 3 && (p + C) < 2) {
                ws.grow_to(std::min(B, std::max<int64_t>(C * 2, C + 64)), B, p_min, p_max);
                base = ws.base_ptr();
                C = std::min(ws.cap, B);
                continue;
            }
            break;
        }
        if constexpr (DO_PROF) { size_t id = CUR_IDX(); prof_hits[id]++; prof_steps[id] += 1 + total_d * 3ULL; }
        const OpMeta& m = meta_arr[CUR_IDX()];
        if (!run_raw_slice(m.src_lo + 1, m.src_hi, cold_v, cold_m)) return finish(cold_v, cold_m);
        VM_NEXT();
    }

    VM_LBL(L_SEEK_R4) {
        if (VM_UNLIKELY(rem == 0)) return finish("STEP_LIMIT_EXCEEDED", "Exceeded step limit " + std::to_string(M));
        --rem;
        if (base[p] == 0) { if constexpr (DO_PROF) { size_t id = CUR_IDX(); prof_hits[id]++; prof_steps[id]++; } VM_NEXT(); }
        uint64_t total_d = 0;
        while (true) {
            const uint64_t a = static_cast<uint64_t>(C - p) >> 2;
            const uint64_t limit = VM_LIKELY(a * 5ULL <= rem) ? a : (rem / 5ULL);
            if (limit > 0) {
                const uint64_t d = scan_right_zero_s4(base, p, limit);
                if (VM_LIKELY(d > 0)) {
                    p += static_cast<int64_t>(d << 2);
                    if (p > p_max) p_max = p;
                    rem -= d * 5ULL;
                    total_d += d;
                    if constexpr (DO_PROF) { size_t id = CUR_IDX(); prof_hits[id]++; prof_steps[id] += 1 + total_d * 5ULL; }
                    VM_NEXT();
                }
                p += static_cast<int64_t>(limit << 2);
                if (p > p_max) p_max = p;
                rem -= limit * 5ULL;
                total_d += limit;
            }
            if (C < B && rem >= 5 && (C - p) < 4) {
                ws.grow_to(std::min(B, std::max<int64_t>(C * 2, C + 64)), B, p_min, p_max);
                base = ws.base_ptr();
                C = std::min(ws.cap, B);
                continue;
            }
            break;
        }
        if constexpr (DO_PROF) { size_t id = CUR_IDX(); prof_hits[id]++; prof_steps[id] += 1 + total_d * 5ULL; }
        const OpMeta& m = meta_arr[CUR_IDX()];
        if (!run_raw_slice(m.src_lo + 1, m.src_hi, cold_v, cold_m)) return finish(cold_v, cold_m);
        VM_NEXT();
    }

    VM_LBL(L_SEEK_L4) {
        if (VM_UNLIKELY(rem == 0)) return finish("STEP_LIMIT_EXCEEDED", "Exceeded step limit " + std::to_string(M));
        --rem;
        if (base[p] == 0) { if constexpr (DO_PROF) { size_t id = CUR_IDX(); prof_hits[id]++; prof_steps[id]++; } VM_NEXT(); }
        uint64_t total_d = 0;
        while (true) {
            const uint64_t a = static_cast<uint64_t>(p + C) >> 2;
            const uint64_t limit = VM_LIKELY(a * 5ULL <= rem) ? a : (rem / 5ULL);
            if (limit > 0) {
                const uint64_t d = scan_left_zero_s4(base, p, limit);
                if (VM_LIKELY(d > 0)) {
                    p -= static_cast<int64_t>(d << 2);
                    if (p < p_min) p_min = p;
                    rem -= d * 5ULL;
                    total_d += d;
                    if constexpr (DO_PROF) { size_t id = CUR_IDX(); prof_hits[id]++; prof_steps[id] += 1 + total_d * 5ULL; }
                    VM_NEXT();
                }
                p -= static_cast<int64_t>(limit << 2);
                if (p < p_min) p_min = p;
                rem -= limit * 5ULL;
                total_d += limit;
            }
            if (C < B && rem >= 5 && (p + C) < 4) {
                ws.grow_to(std::min(B, std::max<int64_t>(C * 2, C + 64)), B, p_min, p_max);
                base = ws.base_ptr();
                C = std::min(ws.cap, B);
                continue;
            }
            break;
        }
        if constexpr (DO_PROF) { size_t id = CUR_IDX(); prof_hits[id]++; prof_steps[id] += 1 + total_d * 5ULL; }
        const OpMeta& m = meta_arr[CUR_IDX()];
        if (!run_raw_slice(m.src_lo + 1, m.src_hi, cold_v, cold_m)) return finish(cold_v, cold_m);
        VM_NEXT();
    }

    VM_LBL(L_SEEK_R) {
        if (VM_UNLIKELY(rem == 0)) return finish("STEP_LIMIT_EXCEEDED", "Exceeded step limit " + std::to_string(M));
        --rem;
        if (base[p] == 0) { if constexpr (DO_PROF) { size_t id = CUR_IDX(); prof_hits[id]++; prof_steps[id]++; } VM_NEXT(); }
        const uint64_t s = static_cast<uint64_t>(ip->arg);
        uint64_t total_d = 0;
        while (true) {
            const uint64_t a = static_cast<uint64_t>(C - p) / s;
            const uint64_t limit = VM_LIKELY(a * (s + 1) <= rem) ? a : (rem / (s + 1));
            int64_t q = p;
            for (uint64_t d = 1; d <= limit; ++d) {
                q += static_cast<int64_t>(s);
                if (base[q] == 0) {
                    p = q; if (p > p_max) p_max = p;
                    rem -= d * (s + 1);
                    total_d += d;
                    if constexpr (DO_PROF) { size_t id = CUR_IDX(); prof_hits[id]++; prof_steps[id] += 1 + total_d * (s + 1); }
                    VM_NEXT();
                }
            }
            p = q; if (p > p_max) p_max = p;
            rem -= limit * (s + 1);
            total_d += limit;
            if (C < B && rem >= (s + 1) && static_cast<uint64_t>(C - p) < s) {
                ws.grow_to(std::min(B, std::max<int64_t>(C * 2, C + static_cast<int64_t>(s))), B, p_min, p_max);
                base = ws.base_ptr();
                C = std::min(ws.cap, B);
                continue;
            }
            break;
        }
        if constexpr (DO_PROF) { size_t id = CUR_IDX(); prof_hits[id]++; prof_steps[id] += 1 + total_d * (s + 1); }
        const OpMeta& m = meta_arr[CUR_IDX()];
        if (!run_raw_slice(m.src_lo + 1, m.src_hi, cold_v, cold_m)) return finish(cold_v, cold_m);
        VM_NEXT();
    }

    VM_LBL(L_SEEK_L) {
        if (VM_UNLIKELY(rem == 0)) return finish("STEP_LIMIT_EXCEEDED", "Exceeded step limit " + std::to_string(M));
        --rem;
        if (base[p] == 0) { if constexpr (DO_PROF) { size_t id = CUR_IDX(); prof_hits[id]++; prof_steps[id]++; } VM_NEXT(); }
        const uint64_t s = static_cast<uint64_t>(ip->arg);
        uint64_t total_d = 0;
        while (true) {
            const uint64_t a = static_cast<uint64_t>(p + C) / s;
            const uint64_t limit = VM_LIKELY(a * (s + 1) <= rem) ? a : (rem / (s + 1));
            int64_t q = p;
            for (uint64_t d = 1; d <= limit; ++d) {
                q -= static_cast<int64_t>(s);
                if (base[q] == 0) {
                    p = q; if (p < p_min) p_min = p;
                    rem -= d * (s + 1);
                    total_d += d;
                    if constexpr (DO_PROF) { size_t id = CUR_IDX(); prof_hits[id]++; prof_steps[id] += 1 + total_d * (s + 1); }
                    VM_NEXT();
                }
            }
            p = q; if (p < p_min) p_min = p;
            rem -= limit * (s + 1);
            total_d += limit;
            if (C < B && rem >= (s + 1) && static_cast<uint64_t>(p + C) < s) {
                ws.grow_to(std::min(B, std::max<int64_t>(C * 2, C + static_cast<int64_t>(s))), B, p_min, p_max);
                base = ws.base_ptr();
                C = std::min(ws.cap, B);
                continue;
            }
            break;
        }
        if constexpr (DO_PROF) { size_t id = CUR_IDX(); prof_hits[id]++; prof_steps[id] += 1 + total_d * (s + 1); }
        const OpMeta& m = meta_arr[CUR_IDX()];
        if (!run_raw_slice(m.src_lo + 1, m.src_hi, cold_v, cold_m)) return finish(cold_v, cold_m);
        VM_NEXT();
    }

    VM_LBL(L_CLEAR_SEEK_R1) {
        if (VM_UNLIKELY(rem == 0)) return finish("STEP_LIMIT_EXCEEDED", "Exceeded step limit " + std::to_string(M));
        --rem;
        if (base[p] == 0) { if constexpr (DO_PROF) { size_t id = CUR_IDX(); prof_hits[id]++; prof_steps[id]++; } VM_NEXT(); }
        if (VM_LIKELY(p + 1 <= C && rem >= 3ULL) && base[p + 1] == 0) {
            base[p] = 0; ++p; if (p > p_max) p_max = p; rem -= 3ULL;
            if constexpr (DO_PROF) { size_t id = CUR_IDX(); prof_hits[id]++; prof_steps[id] += 4; }
            VM_NEXT();
        }
        uint64_t total_d = 0;
        while (true) {
            const uint64_t a = static_cast<uint64_t>(C - p);
            const uint64_t limit = VM_LIKELY(a * 3ULL <= rem) ? a : (rem / 3ULL);
            if (limit > 0) {
                const uint64_t d = scan_right_zero_s1(base, p, limit);
                if (VM_LIKELY(d > 0)) {
                    clear_s1_fast(base + p, d);
                    p += static_cast<int64_t>(d);
                    if (p > p_max) p_max = p;
                    rem -= d * 3ULL;
                    total_d += d;
                    if constexpr (DO_PROF) { size_t id = CUR_IDX(); prof_hits[id]++; prof_steps[id] += 1 + total_d * 3ULL; }
                    VM_NEXT();
                }
                clear_s1_fast(base + p, limit);
                p += static_cast<int64_t>(limit);
                if (p > p_max) p_max = p;
                rem -= limit * 3ULL;
                total_d += limit;
            }
            if (C < B && rem >= 3 && (C - p) < 1) {
                ws.grow_to(std::min(B, std::max<int64_t>(C * 2, C + 64)), B, p_min, p_max);
                base = ws.base_ptr();
                C = std::min(ws.cap, B);
                continue;
            }
            break;
        }
        if constexpr (DO_PROF) { size_t id = CUR_IDX(); prof_hits[id]++; prof_steps[id] += 1 + total_d * 3ULL; }
        const OpMeta& m = meta_arr[CUR_IDX()];
        if (!run_raw_slice(m.src_lo + 1, m.src_hi, cold_v, cold_m)) return finish(cold_v, cold_m);
        VM_NEXT();
    }

    VM_LBL(L_CLEAR_SEEK_L1) {
        if (VM_UNLIKELY(rem == 0)) return finish("STEP_LIMIT_EXCEEDED", "Exceeded step limit " + std::to_string(M));
        --rem;
        if (base[p] == 0) { if constexpr (DO_PROF) { size_t id = CUR_IDX(); prof_hits[id]++; prof_steps[id]++; } VM_NEXT(); }
        if (VM_LIKELY(p - 1 >= -C && rem >= 3ULL) && base[p - 1] == 0) {
            base[p] = 0; --p; if (p < p_min) p_min = p; rem -= 3ULL;
            if constexpr (DO_PROF) { size_t id = CUR_IDX(); prof_hits[id]++; prof_steps[id] += 4; }
            VM_NEXT();
        }
        uint64_t total_d = 0;
        while (true) {
            const uint64_t a = static_cast<uint64_t>(p + C);
            const uint64_t limit = VM_LIKELY(a * 3ULL <= rem) ? a : (rem / 3ULL);
            if (limit > 0) {
                const uint64_t d = scan_left_zero_s1(base, p, limit);
                if (VM_LIKELY(d > 0)) {
                    clear_s1_fast(base + p - static_cast<int64_t>(d) + 1, d);
                    p -= static_cast<int64_t>(d);
                    if (p < p_min) p_min = p;
                    rem -= d * 3ULL;
                    total_d += d;
                    if constexpr (DO_PROF) { size_t id = CUR_IDX(); prof_hits[id]++; prof_steps[id] += 1 + total_d * 3ULL; }
                    VM_NEXT();
                }
                clear_s1_fast(base + p - static_cast<int64_t>(limit) + 1, limit);
                p -= static_cast<int64_t>(limit);
                if (p < p_min) p_min = p;
                rem -= limit * 3ULL;
                total_d += limit;
            }
            if (C < B && rem >= 3 && (p + C) < 1) {
                ws.grow_to(std::min(B, std::max<int64_t>(C * 2, C + 64)), B, p_min, p_max);
                base = ws.base_ptr();
                C = std::min(ws.cap, B);
                continue;
            }
            break;
        }
        if constexpr (DO_PROF) { size_t id = CUR_IDX(); prof_hits[id]++; prof_steps[id] += 1 + total_d * 3ULL; }
        const OpMeta& m = meta_arr[CUR_IDX()];
        if (!run_raw_slice(m.src_lo + 1, m.src_hi, cold_v, cold_m)) return finish(cold_v, cold_m);
        VM_NEXT();
    }

    VM_LBL(L_CLEAR_SEEK_R2) {
        if (VM_UNLIKELY(rem == 0)) return finish("STEP_LIMIT_EXCEEDED", "Exceeded step limit " + std::to_string(M));
        --rem;
        if (base[p] == 0) { if constexpr (DO_PROF) { size_t id = CUR_IDX(); prof_hits[id]++; prof_steps[id]++; } VM_NEXT(); }
        if (VM_LIKELY(p + 2 <= C && rem >= 4ULL) && base[p + 2] == 0) {
            base[p] = 0; p += 2; if (p > p_max) p_max = p; rem -= 4ULL;
            if constexpr (DO_PROF) { size_t id = CUR_IDX(); prof_hits[id]++; prof_steps[id] += 5; }
            VM_NEXT();
        }
        uint64_t total_d = 0;
        while (true) {
            const uint64_t a = static_cast<uint64_t>(C - p) >> 1;
            const uint64_t limit = VM_LIKELY((a << 2) <= rem) ? a : (rem >> 2);
            if (limit > 0) {
                const uint64_t d = scan_right_zero_s2(base, p, limit);
                if (VM_LIKELY(d > 0)) {
                    clear_stride2_right(base + p, d);
                    p += static_cast<int64_t>(d << 1);
                    if (p > p_max) p_max = p;
                    rem -= d << 2;
                    total_d += d;
                    if constexpr (DO_PROF) { size_t id = CUR_IDX(); prof_hits[id]++; prof_steps[id] += 1 + (total_d << 2); }
                    VM_NEXT();
                }
                clear_stride2_right(base + p, limit);
                p += static_cast<int64_t>(limit << 1);
                if (p > p_max) p_max = p;
                rem -= limit << 2;
                total_d += limit;
            }
            if (C < B && rem >= 4 && (C - p) < 2) {
                ws.grow_to(std::min(B, std::max<int64_t>(C * 2, C + 64)), B, p_min, p_max);
                base = ws.base_ptr();
                C = std::min(ws.cap, B);
                continue;
            }
            break;
        }
        if constexpr (DO_PROF) { size_t id = CUR_IDX(); prof_hits[id]++; prof_steps[id] += 1 + (total_d << 2); }
        const OpMeta& m = meta_arr[CUR_IDX()];
        if (!run_raw_slice(m.src_lo + 1, m.src_hi, cold_v, cold_m)) return finish(cold_v, cold_m);
        VM_NEXT();
    }

    VM_LBL(L_CLEAR_SEEK_L2) {
        if (VM_UNLIKELY(rem == 0)) return finish("STEP_LIMIT_EXCEEDED", "Exceeded step limit " + std::to_string(M));
        --rem;
        if (base[p] == 0) { if constexpr (DO_PROF) { size_t id = CUR_IDX(); prof_hits[id]++; prof_steps[id]++; } VM_NEXT(); }
        if (VM_LIKELY(p - 2 >= -C && rem >= 4ULL) && base[p - 2] == 0) {
            base[p] = 0; p -= 2; if (p < p_min) p_min = p; rem -= 4ULL;
            if constexpr (DO_PROF) { size_t id = CUR_IDX(); prof_hits[id]++; prof_steps[id] += 5; }
            VM_NEXT();
        }
        uint64_t total_d = 0;
        while (true) {
            const uint64_t a = static_cast<uint64_t>(p + C) >> 1;
            const uint64_t limit = VM_LIKELY((a << 2) <= rem) ? a : (rem >> 2);
            if (limit > 0) {
                const uint64_t d = scan_left_zero_s2(base, p, limit);
                if (VM_LIKELY(d > 0)) {
                    clear_stride2_right(base + p - static_cast<int64_t>((d - 1) << 1), d);
                    p -= static_cast<int64_t>(d << 1);
                    if (p < p_min) p_min = p;
                    rem -= d << 2;
                    total_d += d;
                    if constexpr (DO_PROF) { size_t id = CUR_IDX(); prof_hits[id]++; prof_steps[id] += 1 + (total_d << 2); }
                    VM_NEXT();
                }
                clear_stride2_right(base + p - static_cast<int64_t>((limit - 1) << 1), limit);
                p -= static_cast<int64_t>(limit << 1);
                if (p < p_min) p_min = p;
                rem -= limit << 2;
                total_d += limit;
            }
            if (C < B && rem >= 4 && (p + C) < 2) {
                ws.grow_to(std::min(B, std::max<int64_t>(C * 2, C + 64)), B, p_min, p_max);
                base = ws.base_ptr();
                C = std::min(ws.cap, B);
                continue;
            }
            break;
        }
        if constexpr (DO_PROF) { size_t id = CUR_IDX(); prof_hits[id]++; prof_steps[id] += 1 + (total_d << 2); }
        const OpMeta& m = meta_arr[CUR_IDX()];
        if (!run_raw_slice(m.src_lo + 1, m.src_hi, cold_v, cold_m)) return finish(cold_v, cold_m);
        VM_NEXT();
    }

    VM_LBL(L_CLEAR_SEEK_R) {
        if (VM_UNLIKELY(rem == 0)) return finish("STEP_LIMIT_EXCEEDED", "Exceeded step limit " + std::to_string(M));
        --rem;
        if (base[p] == 0) { if constexpr (DO_PROF) { size_t id = CUR_IDX(); prof_hits[id]++; prof_steps[id]++; } VM_NEXT(); }
        const uint64_t s = static_cast<uint64_t>(ip->arg);
        uint64_t total_d = 0;
        while (true) {
            const uint64_t a = static_cast<uint64_t>(C - p) / s;
            const uint64_t limit = VM_LIKELY(a * (s + 2) <= rem) ? a : (rem / (s + 2));
            int64_t q = p;
            for (uint64_t d = 1; d <= limit; ++d) {
                base[q] = 0;
                q += static_cast<int64_t>(s);
                if (base[q] == 0) {
                    p = q; if (p > p_max) p_max = p;
                    rem -= d * (s + 2);
                    total_d += d;
                    if constexpr (DO_PROF) { size_t id = CUR_IDX(); prof_hits[id]++; prof_steps[id] += 1 + total_d * (s + 2); }
                    VM_NEXT();
                }
            }
            p = q; if (p > p_max) p_max = p;
            rem -= limit * (s + 2);
            total_d += limit;
            if (C < B && rem >= (s + 2) && static_cast<uint64_t>(C - p) < s) {
                ws.grow_to(std::min(B, std::max<int64_t>(C * 2, C + static_cast<int64_t>(s))), B, p_min, p_max);
                base = ws.base_ptr();
                C = std::min(ws.cap, B);
                continue;
            }
            break;
        }
        if constexpr (DO_PROF) { size_t id = CUR_IDX(); prof_hits[id]++; prof_steps[id] += 1 + total_d * (s + 2); }
        const OpMeta& m = meta_arr[CUR_IDX()];
        if (!run_raw_slice(m.src_lo + 1, m.src_hi, cold_v, cold_m)) return finish(cold_v, cold_m);
        VM_NEXT();
    }

    VM_LBL(L_CLEAR_SEEK_L) {
        if (VM_UNLIKELY(rem == 0)) return finish("STEP_LIMIT_EXCEEDED", "Exceeded step limit " + std::to_string(M));
        --rem;
        if (base[p] == 0) { if constexpr (DO_PROF) { size_t id = CUR_IDX(); prof_hits[id]++; prof_steps[id]++; } VM_NEXT(); }
        const uint64_t s = static_cast<uint64_t>(ip->arg);
        uint64_t total_d = 0;
        while (true) {
            const uint64_t a = static_cast<uint64_t>(p + C) / s;
            const uint64_t limit = VM_LIKELY(a * (s + 2) <= rem) ? a : (rem / (s + 2));
            int64_t q = p;
            for (uint64_t d = 1; d <= limit; ++d) {
                base[q] = 0;
                q -= static_cast<int64_t>(s);
                if (base[q] == 0) {
                    p = q; if (p < p_min) p_min = p;
                    rem -= d * (s + 2);
                    total_d += d;
                    if constexpr (DO_PROF) { size_t id = CUR_IDX(); prof_hits[id]++; prof_steps[id] += 1 + total_d * (s + 2); }
                    VM_NEXT();
                }
            }
            p = q; if (p < p_min) p_min = p;
            rem -= limit * (s + 2);
            total_d += limit;
            if (C < B && rem >= (s + 2) && static_cast<uint64_t>(p + C) < s) {
                ws.grow_to(std::min(B, std::max<int64_t>(C * 2, C + static_cast<int64_t>(s))), B, p_min, p_max);
                base = ws.base_ptr();
                C = std::min(ws.cap, B);
                continue;
            }
            break;
        }
        if constexpr (DO_PROF) { size_t id = CUR_IDX(); prof_hits[id]++; prof_steps[id] += 1 + total_d * (s + 2); }
        const OpMeta& m = meta_arr[CUR_IDX()];
        if (!run_raw_slice(m.src_lo + 1, m.src_hi, cold_v, cold_m)) return finish(cold_v, cold_m);
        VM_NEXT();
    }

    VM_LBL(L_HALT) {
        if constexpr (!RUN_ONLY) {
            if (out_pos != L) {
                return finish("OUTPUT_MISMATCH", "Output too short: got " + std::to_string(out_pos) + ", expected " + std::to_string(L));
            }
        }
        return finish("AC", "OK");
    }

#if !(defined(__GNUC__) || defined(__clang__))
        }
    }
#endif
}

static VerifyResult run_flat_stream(
    const std::string& target,
    const std::string& prog,
    const RunOptions& opt,
    TapeWorkspace& ws,
    VerifyResult& res,
    std::chrono::steady_clock::time_point t_start
) {
    // Zero-allocation single-pass streaming executor for validated bracket-free programs!
    const int64_t B = opt.tape_bound;
    ws.prepare(B);
    uint8_t* base = ws.base_ptr();
    const size_t L = target.size();
    const char* const target_ptr = target.data();
    const char* const pstr = prog.data();
    const size_t n = prog.size();
    const uint64_t M = opt.step_limit;
    uint64_t rem = M;
    int64_t p = 0, p_min = 0, p_max = 0;
    size_t out_pos = 0;

    auto done = [&](const std::string& v, const std::string& msg) -> VerifyResult {
        ws.mark_dirty(p_min, p_max);
        res.verdict = v;
        res.message = msg;
        res.steps = M - rem;
        res.out_len = out_pos;
        res.p_final = p;
        res.p_min = p_min;
        res.p_max = p_max;
        res.elapsed_ms = std::chrono::duration<double, std::milli>(std::chrono::steady_clock::now() - t_start).count();
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
                return done("OUTPUT_MISMATCH", "Output length exceeded target length " + std::to_string(L));
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

static VerifyResult run_vm(
    const std::string& target,
    const std::string& prog,
    const RunOptions& opt,
    TapeWorkspace& ws
) {
    auto t_start = std::chrono::steady_clock::now();
    VerifyResult res;
    res.prog_len = prog.size();
    res.target_len = target.size();

    const size_t max_prog_len = opt.max_prog_len
        ? opt.max_prog_len
        : (opt.run_only ? std::max<size_t>(prog.size(), 10000000ULL) : (2 * target.size() + 100));

    const bool tr = opt.trace_points > 0;
    const bool pr = opt.profile;
    const bool ro = opt.run_only;

    if (!validate_and_compile(prog, max_prog_len, (!tr && !pr && !ro), ws, res.op_counts, res.verdict, res.message)) {
        res.elapsed_ms = std::chrono::duration<double, std::milli>(std::chrono::steady_clock::now() - t_start).count();
        return res;
    }
    if (ws.ops.empty()) {
        res.ir_ops = 0;
        return run_flat_stream(target, prog, opt, ws, res, t_start);
    }
    res.ir_ops = ws.ops.size() - 1;

    if (!tr && !pr && !ro) return run_vm_core<false, false, false>(target, prog, opt, ws, res, t_start);
    if (!tr && !pr &&  ro) return run_vm_core<false, false, true >(target, prog, opt, ws, res, t_start);
    if (!tr &&  pr && !ro) return run_vm_core<false, true,  false>(target, prog, opt, ws, res, t_start);
    if (!tr &&  pr &&  ro) return run_vm_core<false, true,  true >(target, prog, opt, ws, res, t_start);
    if ( tr && !pr && !ro) return run_vm_core<true,  false, false>(target, prog, opt, ws, res, t_start);
    if ( tr && !pr &&  ro) return run_vm_core<true,  false, true >(target, prog, opt, ws, res, t_start);
    if ( tr &&  pr && !ro) return run_vm_core<true,  true,  false>(target, prog, opt, ws, res, t_start);
    return run_vm_core<true, true, true>(target, prog, opt, ws, res, t_start);
}

static void write_result_json(std::ostream& os, const VerifyResult& r, bool include_out_bits, const std::string& trace_out_path) {
    if (!trace_out_path.empty()) {
        std::ofstream ofs(trace_out_path, std::ios::binary);
        ofs << "[\n";
        for (size_t i = 0; i < r.trace.size(); ++i) {
            const auto& s = r.trace[i];
            ofs << "  {\"step\":" << s.step
                << ",\"p\":" << s.p
                << ",\"p_min\":" << s.p_min
                << ",\"p_max\":" << s.p_max
                << ",\"out_pos\":" << s.out_pos
                << ",\"ones\":" << s.ones_approx
                << ",\"win\":\"" << s.window << "\"}"
                << (i + 1 < r.trace.size() ? ",\n" : "\n");
        }
        ofs << "]\n";
    }

    os << "{"
       << "\"verdict\":\"" << json_escape(r.verdict) << "\","
       << "\"message\":\"" << json_escape(r.message) << "\","
       << "\"prog_len\":" << r.prog_len << ","
       << "\"ir_ops\":" << r.ir_ops << ","
       << "\"steps\":" << r.steps << ","
       << "\"out_len\":" << r.out_len << ","
       << "\"target_len\":" << r.target_len << ","
       << "\"p_final\":" << r.p_final << ","
       << "\"p_min\":" << r.p_min << ","
       << "\"p_max\":" << r.p_max << ","
       << "\"elapsed_ms\":" << r.elapsed_ms << ","
#if defined(__AVX512BW__)
       << "\"engine\":\"fast_vm4_avx512\","
#elif defined(__AVX2__)
       << "\"engine\":\"fast_vm4_avx2\","
#else
       << "\"engine\":\"fast_vm4\","
#endif
       << "\"op_counts\":{"
       << "\">\":" << r.op_counts[0] << ","
       << "\"<\":" << r.op_counts[1] << ","
       << "\"+\":" << r.op_counts[2] << ","
       << "\".\":" << r.op_counts[3] << ","
       << "\"[\":" << r.op_counts[4] << ","
       << "\"]\":" << r.op_counts[5]
       << "}";
    if (include_out_bits) {
        os << ",\"out_bits\":\"" << r.out_bits << "\""
           << ",\"nonzero_total\":" << r.nonzero_total
           << ",\"nonzero_sample\":[";
        for (size_t i = 0; i < r.nonzero_sample.size(); ++i) {
            if (i) os << ",";
            os << r.nonzero_sample[i];
        }
        os << "]";
    }
    if (!r.hotspots.empty()) {
        os << ",\"hotspots\":[";
        for (size_t i = 0; i < r.hotspots.size(); ++i) {
            const auto& h = r.hotspots[i];
            if (i) os << ",";
            os << "{\"src_lo\":" << h.src_lo
               << ",\"src_hi\":" << h.src_hi
               << ",\"kind\":\"" << h.kind << "\""
               << ",\"hits\":" << h.hits
               << ",\"raw_steps\":" << h.raw_steps << "}";
        }
        os << "]";
    }
    os << "}\n";
}

static std::string hex_decode(const std::string& hex) {
    if (hex == "-") return "";
    std::string out;
    out.reserve(hex.size() / 2);
    auto hv = [](char c) -> int {
        if (c >= '0' && c <= '9') return c - '0';
        if (c >= 'a' && c <= 'f') return c - 'a' + 10;
        if (c >= 'A' && c <= 'F') return c - 'A' + 10;
        return 0;
    };
    for (size_t i = 0; i + 1 < hex.size(); i += 2) {
        out.push_back(static_cast<char>((hv(hex[i]) << 4) | hv(hex[i + 1])));
    }
    return out;
}

static int run_server_loop() {
    std::ios::sync_with_stdio(false);
    std::cin.tie(nullptr);
    TapeWorkspace ws;
    std::string cached_target_path;
    std::string cached_target_str;
    static const std::string kEmptyTarget;

    std::string line;
    while (std::getline(std::cin, line)) {
        if (!line.empty() && line.back() == '\r') line.pop_back();
        if (line.empty()) continue;
        if (line == "QUIT") break;

        std::vector<std::string> tok;
        size_t pos = 0;
        while (pos < line.size()) {
            while (pos < line.size() && line[pos] == ' ') ++pos;
            if (pos >= line.size()) break;
            size_t end = line.find(' ', pos);
            if (end == std::string::npos) end = line.size();
            tok.push_back(line.substr(pos, end - pos));
            pos = end;
        }
        if (tok.size() < 11 || tok[0] != "REQ") {
            std::cout << "{\"verdict\":\"INTERNAL_ERROR\",\"message\":\"Malformed server request\"}\n" << std::flush;
            continue;
        }
        RunOptions opt;
        opt.run_only = (tok[1] == "R");
        opt.step_limit = std::stoull(tok[2]);
        opt.tape_bound = std::stoll(tok[3]);
        opt.max_prog_len = static_cast<size_t>(std::stoull(tok[4]));
        opt.wall_limit_s = std::stod(tok[5]);
        opt.trace_points = std::stoi(tok[6]);
        opt.profile = (tok[7] == "1");
        std::string target_path = hex_decode(tok[8]);
        std::string prog_path = hex_decode(tok[9]);
        std::string trace_out = hex_decode(tok[10]);

        if (!opt.run_only) {
            if (target_path != cached_target_path || cached_target_str.empty()) {
                size_t L = 0;
                std::string new_target, err;
                if (!parse_target_file(target_path, L, new_target, err)) {
                    std::cout << "{\"verdict\":\"INTERNAL_ERROR\",\"message\":\"" << json_escape(err) << "\"}\n" << std::flush;
                    continue;
                }
                cached_target_path = target_path;
                cached_target_str.swap(new_target);
            }
        }
        const std::string& target_ref = opt.run_only ? kEmptyTarget : cached_target_str;
        std::string prog;
        if (!read_file_bytes(prog_path, prog)) {
            std::cout << "{\"verdict\":\"INTERNAL_ERROR\",\"message\":\"Failed to open program file\"}\n" << std::flush;
            continue;
        }
        strip_single_trailing_newline(prog);

        VerifyResult r = run_vm(target_ref, prog, opt, ws);
        write_result_json(std::cout, r, opt.run_only, trace_out);
        std::cout.flush();
    }
    return 0;
}

}  // namespace

int main(int argc, char** argv) {
    if (argc >= 2 && std::string(argv[1]) == "--server") {
        return run_server_loop();
    }
    if (argc < 3) {
        std::cerr << "Usage:\n"
                  << "  vm --server\n"
                  << "  vm <target_file|-> <program_file> [--run-only] [--step-limit M] [--tape-bound B] "
                     "[--max-prog-len K] [--cpu-limit-s S] [--trace-points N] [--trace-out PATH] [--profile]\n";
        return 2;
    }

    std::string target_path = argv[1];
    std::string prog_path = argv[2];
    RunOptions opt;
    std::string trace_out_path;
    std::string out_bits_path;

    if (target_path == "-") opt.run_only = true;

    for (int i = 3; i < argc; ++i) {
        std::string a = argv[i];
        if (a == "--step-limit" && i + 1 < argc) opt.step_limit = std::stoull(argv[++i]);
        else if (a == "--tape-bound" && i + 1 < argc) opt.tape_bound = std::stoll(argv[++i]);
        else if (a == "--max-prog-len" && i + 1 < argc) opt.max_prog_len = static_cast<size_t>(std::stoull(argv[++i]));
        else if (a == "--cpu-limit-s" && i + 1 < argc) opt.wall_limit_s = std::stod(argv[++i]);
        else if (a == "--trace-points" && i + 1 < argc) opt.trace_points = std::stoi(argv[++i]);
        else if (a == "--trace-out" && i + 1 < argc) trace_out_path = argv[++i];
        else if (a == "--profile") opt.profile = true;
        else if (a == "--run-only") opt.run_only = true;
        else if (a == "--max-out-bits" && i + 1 < argc) opt.max_out_bits = static_cast<size_t>(std::stoull(argv[++i]));
        else if (a == "--out-bits-file" && i + 1 < argc) out_bits_path = argv[++i];
        else {
            std::cerr << "Unknown argument: " << a << "\n";
            return 2;
        }
    }

    size_t L = 0;
    std::string target, err;
    if (!opt.run_only) {
        if (!parse_target_file(target_path, L, target, err)) {
            std::cerr << "Target parse error: " << err << "\n";
            return 3;
        }
    }

    std::string prog;
    if (!read_file_bytes(prog_path, prog)) {
        std::cerr << "Failed to open program file\n";
        return 3;
    }
    strip_single_trailing_newline(prog);

    TapeWorkspace ws;
    VerifyResult r = run_vm(target, prog, opt, ws);

    if (!out_bits_path.empty() && opt.run_only) {
        std::ofstream ofs(out_bits_path, std::ios::binary);
        ofs << r.out_bits.size() << "\n" << r.out_bits << "\n";
    }
    write_result_json(std::cout, r, opt.run_only && out_bits_path.empty(), trace_out_path);
    return 0;
}
