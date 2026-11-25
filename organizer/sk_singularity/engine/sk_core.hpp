#pragma once
// White-box C++20 deterministic engine for "The SK Singularity" (sk_singularity).
//
// Core mathematical guarantee:
//   For any closed 2-symbol combinator system (S of arity a_S >= 1, K of arity a_K >= 1)
//   under leftmost-outermost (normal-order) reduction, exact step count T(E) is strictly
//   compositional on Weak Head Normal Form (WHNF) and Normal Form (NF):
//     - Every closed term has a unique spine h u_0 ... u_{k-1} (h in {S, K}).
//     - A root redex fires iff k == arity[h]; WHNF holds iff k < arity[h].
//     - Memoizing (whnf, nf) on a hash-consed immutable DAG computes the exact same
//       normal-order step count as pure-tree reduction (no call-by-need undercounting:
//       duplicated shared subterms contribute their exact step count once per occurrence),
//       while detecting cycles in O(1) and resolving super-exponential shared reductions
//       (T(E) >> 2^10000) within milliseconds under a deterministic unique-DAG-node guard.

#include <algorithm>
#include <array>
#include <cmath>
#include <cstdint>
#include <cstring>
#include <iomanip>
#include <memory>
#include <sstream>
#include <string>
#include <type_traits>
#include <unordered_map>
#include <utility>
#include <vector>

namespace sk_singularity {

static constexpr int MAX_EXPR_BYTES = 4096;
static constexpr int MIN_ARITY = 1;
static constexpr int MAX_ARITY = 4;
static constexpr int DEFAULT_MAX_NODES = 10000000;
static constexpr int DEFAULT_MAX_STACK = 40000000;
static constexpr double SCORE_POWER = 0.85;
static constexpr double SINGULARITY_LOG2_THRESHOLD = 64.0;

// ============================================================================
// Pure-integer deterministic StepCount:
//   - Exact uint64_t when T < 2^62 (exp2 == -1, mant == T).
//   - Normalized 63-bit integer mantissa * 2^exp2 when T >= 2^62,
//     with mant in [2^62, 2^63) and round-to-nearest-even addition.
//   Zero platform floating-point drift during reduction!
// ============================================================================
struct StepCount {
    static constexpr uint64_t EXACT_LIMIT = (1ULL << 62);
    static constexpr int64_t EXACT_EXP = (-9223372036854775807LL - 1);

    uint64_t mant = 0;
    int64_t exp2 = EXACT_EXP;

    static StepCount zero() { return {0ULL, EXACT_EXP}; }
    static StepCount one()  { return {1ULL, EXACT_EXP}; }

    bool is_exact() const { return exp2 == EXACT_EXP; }
    bool is_zero()  const { return exp2 == EXACT_EXP && mant == 0ULL; }

    void promote() {
        if (exp2 != EXACT_EXP || mant == 0ULL) return;
        int lz = __builtin_clzll(mant); // mant in [1, 2^62 - 1] => lz >= 2
        int shift = lz - 1;             // places top bit at bit 62
        mant <<= shift;
        exp2 = -static_cast<int64_t>(shift);
    }

    static StepCount add(StepCount a, StepCount b) {
        if (a.is_zero()) return b;
        if (b.is_zero()) return a;
        if (a.is_exact() && b.is_exact()) {
            uint64_t s = a.mant + b.mant;
            if (s < EXACT_LIMIT) return {s, EXACT_EXP};
            return {s, 0}; // bit 62 is set, bit 63 is 0
        }
        a.promote();
        b.promote();
        if (a.exp2 < b.exp2) std::swap(a, b);
        int64_t diff = a.exp2 - b.exp2;
        if (diff >= 64) return a;
        uint64_t b_aligned = b.mant >> diff;
        if (diff > 0) {
            uint64_t rem = b.mant & ((1ULL << diff) - 1ULL);
            uint64_t half = 1ULL << (diff - 1);
            if (rem > half || (rem == half && (b_aligned & 1ULL))) {
                b_aligned++;
            }
        }
        uint64_t sum = a.mant + b_aligned;
        int64_t e = a.exp2;
        if (sum >= (1ULL << 63)) {
            uint64_t odd = sum & 1ULL;
            sum >>= 1;
            if (odd && (sum & 1ULL)) {
                sum++;
                if (sum >= (1ULL << 63)) {
                    sum >>= 1;
                    e++;
                }
            }
            e++;
        }
        return {sum, e};
    }

    double log2_plus1() const {
        if (is_exact()) {
            return std::log2(static_cast<double>(mant) + 1.0);
        }
        double frac = static_cast<double>(mant) / static_cast<double>(1ULL << 62);
        return static_cast<double>(exp2 + 62) + std::log2(frac);
    }

    std::string display_string() const {
        if (is_exact()) {
            return std::to_string(mant);
        }
        double l2 = log2_plus1();
        double l10 = l2 * 0.30102999566398119521;
        long long p10 = static_cast<long long>(std::floor(l10));
        double m10 = std::pow(10.0, l10 - static_cast<double>(p10));
        if (m10 >= 9.9995) {
            m10 = 1.0;
            p10++;
        }
        std::ostringstream oss;
        oss << std::fixed << std::setprecision(3) << m10 << "e+" << p10;
        return oss.str();
    }
};

inline double compute_norm_score(double log2_steps, double l_ref) {
    if (!(log2_steps > 0.0) || !(l_ref > 0.0)) return 0.0;
    double a = std::pow(log2_steps, SCORE_POWER);
    double b = std::pow(l_ref, SCORE_POWER);
    if (!(a > 0.0) || !std::isfinite(a)) return 100.0;
    double val = 100.0 * a / (a + b);
    return std::max(0.0, std::min(100.0, val));
}

// ============================================================================
// AST for closed SK expressions (used for parsing, size, remnant containment)
// ============================================================================
struct AstNode {
    int sym; // 0 = S, 1 = K, -1 = app(l, r)
    int l = -1;
    int r = -1;
    int leaves = 1;
};

struct ExprAst {
    std::vector<AstNode> nodes;
    int root = -1;
    int leaves = 0;
    std::string preorder; // '.' for app, 'S', 'K'
};

inline bool parse_expr_strict(const std::string& raw, ExprAst& out, std::string& err) {
    out.nodes.clear();
    out.root = -1;
    out.leaves = 0;
    out.preorder.clear();

    if (static_cast<int>(raw.size()) > MAX_EXPR_BYTES) {
        err = "Expression exceeds maximum byte length (" + std::to_string(MAX_EXPR_BYTES) + ")";
        return false;
    }

    // Strip optional single trailing \n or \r\n, reject any embedded newlines or illegal chars
    std::string s = raw;
    while (!s.empty() && (s.back() == '\n' || s.back() == '\r')) s.pop_back();
    for (unsigned char ch : s) {
        if (ch != 'S' && ch != 'K' && ch != '(' && ch != ')' && ch != ' ') {
            err = "Illegal character in expression (only S, K, (, ), and ASCII space allowed)";
            return false;
        }
    }

    size_t pos = 0;
    auto skip_sp = [&]() {
        while (pos < s.size() && s[pos] == ' ') pos++;
    };

    auto parse_rec = [&](auto&& self, int depth) -> int {
        if (depth > 256) {
            err = "Parenthesis nesting depth exceeds limit";
            return -1;
        }
        skip_sp();
        if (pos >= s.size()) {
            err = "Unexpected end of expression";
            return -1;
        }
        char c = s[pos];
        if (c == 'S' || c == 'K') {
            pos++;
            int id = static_cast<int>(out.nodes.size());
            out.nodes.push_back({c == 'S' ? 0 : 1, -1, -1, 1});
            out.preorder.push_back(c);
            return id;
        }
        if (c == '(') {
            pos++;
            out.preorder.push_back('.');
            int a = self(self, depth + 1);
            if (a < 0) return -1;
            int b = self(self, depth + 1);
            if (b < 0) return -1;
            skip_sp();
            if (pos >= s.size() || s[pos] != ')') {
                err = "Expected ')' in binary application";
                return -1;
            }
            pos++;
            int id = static_cast<int>(out.nodes.size());
            int lv = out.nodes[a].leaves + out.nodes[b].leaves;
            out.nodes.push_back({-1, a, b, lv});
            return id;
        }
        err = std::string("Unexpected token '") + c + "'";
        return -1;
    };

    int rt = parse_rec(parse_rec, 0);
    if (rt < 0) return false;
    skip_sp();
    if (pos != s.size()) {
        err = "Trailing tokens after root expression";
        return false;
    }
    out.root = rt;
    out.leaves = out.nodes[rt].leaves;
    return true;
}

inline std::string ast_to_sexp(const ExprAst& ast, int u) {
    const AstNode& nd = ast.nodes[u];
    if (nd.sym == 0) return "S";
    if (nd.sym == 1) return "K";
    return "(" + ast_to_sexp(ast, nd.l) + " " + ast_to_sexp(ast, nd.r) + ")";
}

inline bool ast_equal(const ExprAst& a, int ua, const ExprAst& b, int ub) {
    const AstNode& na = a.nodes[ua];
    const AstNode& nb = b.nodes[ub];
    if (na.sym != nb.sym || na.leaves != nb.leaves) return false;
    if (na.sym >= 0) return true;
    return ast_equal(a, na.l, b, nb.l) && ast_equal(a, na.r, b, nb.r);
}

inline bool ast_contains_subtree(const ExprAst& hay, int u, const ExprAst& needle) {
    if (ast_equal(hay, u, needle, needle.root)) return true;
    const AstNode& nd = hay.nodes[u];
    if (nd.sym >= 0) return false;
    if (nd.leaves < needle.leaves) return false;
    return ast_contains_subtree(hay, nd.l, needle) || ast_contains_subtree(hay, nd.r, needle);
}

// ============================================================================
// Generalized 2-Symbol Combinator Rule Specification
// ============================================================================
struct RhsNode {
    int tag = -1; // >=0 arg index, -1 literal S, -2 literal K, -3 app(l, r)
    int l = -1;
    int r = -1;
};

struct RuleSpec {
    int arity[2] = {3, 2};
    std::string rhs_str[2];
    std::vector<RhsNode> rhs[2];
    int root[2] = {-1, -1};
};

inline bool parse_rhs_strict(const std::string& raw, int arity, std::vector<RhsNode>& out, int& root_id, std::string& err) {
    out.clear();
    root_id = -1;
    if (arity < MIN_ARITY || arity > MAX_ARITY) {
        err = "Arity out of range [1, 4]";
        return false;
    }
    std::string s = raw;
    while (!s.empty() && (s.back() == '\n' || s.back() == '\r')) s.pop_back();
    size_t pos = 0;
    auto skip_sp = [&]() {
        while (pos < s.size() && (s[pos] == ' ' || s[pos] == '\t')) pos++;
    };
    auto parse_rec = [&](auto&& self, int depth) -> int {
        if (depth > 64) { err = "RHS nesting too deep"; return -1; }
        skip_sp();
        if (pos >= s.size()) { err = "Unexpected end of RHS"; return -1; }
        char c = s[pos];
        if (c == '(') {
            pos++;
            int a = self(self, depth + 1);
            if (a < 0) return -1;
            int b = self(self, depth + 1);
            if (b < 0) return -1;
            skip_sp();
            if (pos >= s.size() || s[pos] != ')') { err = "Expected ')' in RHS"; return -1; }
            pos++;
            int id = static_cast<int>(out.size());
            out.push_back({-3, a, b});
            return id;
        }
        pos++;
        int id = static_cast<int>(out.size());
        if (c == 'S') {
            out.push_back({-1, -1, -1});
            return id;
        }
        if (c == 'K') {
            out.push_back({-2, -1, -1});
            return id;
        }
        if (c >= '0' && c <= '9') {
            int idx = c - '0';
            if (idx >= arity) {
                err = "RHS variable index out of range for arity";
                return -1;
            }
            out.push_back({idx, -1, -1});
            return id;
        }
        err = std::string("Illegal RHS token '") + c + "'";
        return -1;
    };
    int rt = parse_rec(parse_rec, 0);
    if (rt < 0) return false;
    skip_sp();
    if (pos != s.size()) { err = "Trailing tokens in RHS"; return false; }
    root_id = rt;
    return true;
}

inline bool make_rule_spec(int aS, const std::string& rS, int aK, const std::string& rK, RuleSpec& out, std::string& err) {
    out.arity[0] = aS;
    out.arity[1] = aK;
    out.rhs_str[0] = rS;
    out.rhs_str[1] = rK;
    if (!parse_rhs_strict(rS, aS, out.rhs[0], out.root[0], err)) return false;
    if (!parse_rhs_strict(rK, aK, out.rhs[1], out.root[1], err)) return false;
    return true;
}

inline std::string swap_sk_string(const std::string& s) {
    std::string out = s;
    for (char& c : out) {
        if (c == 'S') c = 'K';
        else if (c == 'K') c = 'S';
    }
    return out;
}

inline RuleSpec flip_sk_rule(const RuleSpec& r) {
    RuleSpec out;
    std::string err;
    make_rule_spec(r.arity[1], swap_sk_string(r.rhs_str[1]),
                   r.arity[0], swap_sk_string(r.rhs_str[0]), out, err);
    return out;
}

// ============================================================================
// Hash-Consed Memoized Exact Normal-Order Engine
// ============================================================================
enum class EvalStatus : uint8_t {
    HALTED = 0,
    CYCLE_WHNF = 1,
    CYCLE_NF = 2,
    NODE_LIMIT = 3,
    STACK_LIMIT = 4
};

inline const char* status_name(EvalStatus st) {
    switch (st) {
        case EvalStatus::HALTED:      return "HALTED";
        case EvalStatus::CYCLE_WHNF:  return "CYCLE_WHNF";
        case EvalStatus::CYCLE_NF:    return "CYCLE_NF";
        case EvalStatus::NODE_LIMIT:  return "NODE_LIMIT";
        case EvalStatus::STACK_LIMIT: return "STACK_LIMIT";
    }
    return "UNKNOWN";
}

struct EvalResult {
    EvalStatus status = EvalStatus::NODE_LIMIT;
    StepCount steps = StepCount::zero();
    int dag_nodes = 0;
    int nf_root = -1;
    int whnf_hits = 0;
    int nf_hits = 0;
    int redex_fires = 0;
    int peak_stack = 0;
};

struct MemoEngine {
    RuleSpec rule;
    int max_nodes;
    int max_stack;
    int num_atoms; // 2 (S, K) + optional symbolic probes

    // 32-byte cache-packed hot node (exact half cache line; only array touched in app/WHNF)
    struct HotNode {
        int l;
        int r;
        int wn;
        uint8_t wst; // 0=unvisited, 1=visiting, 2=done
        uint8_t nst; // 0=unvisited, 1=visiting, 2=done
        uint16_t sp; // top 2 bits: min(spine_head, 2); bottom 14 bits: min(spine_k, 16383)
        StepCount ws;
    };
    static_assert(sizeof(HotNode) == 32, "HotNode must pack to 32 bytes");

    // Cold NF result storage: allocated lazily in 4096-entry uninitialized chunks
    // so WHNF-only nodes pay zero memory/bandwidth overhead in app().
    struct NfCold {
        int node;
        uint64_t mant;
        int64_t exp2;
    };
    static_assert(std::is_trivially_default_constructible_v<NfCold>, "NfCold must be trivially default constructible");
    static constexpr int NF_CHUNK_BITS = 12;
    static constexpr int NF_CHUNK_SIZE = 1 << NF_CHUNK_BITS;
    static constexpr int NF_CHUNK_MASK = NF_CHUNK_SIZE - 1;

    // Compact open-addressing hash slot for arbitrary 32-bit node IDs (> 2^21 - 1)
    struct HtSlot {
        uint64_t key;
        int val;
    };
    static_assert(std::is_trivially_copyable_v<HtSlot>, "HtSlot must be trivially copyable");

    static constexpr uint64_t EMPTY_KEY = ~0ULL;
    static constexpr int PACKED_MAX_NODES = (1 << 21) - 1; // 2,097,151: fits (a<<42)|(b<<21)|id in 63 bits

    std::vector<HotNode> nodes;
    std::vector<std::unique_ptr<NfCold[]>> nf_chunks;

    bool use_packed_ht = true;
    std::vector<uint64_t> ht_packed;
    std::vector<HtSlot> ht_wide;
    std::vector<uint32_t> used_slots;
    uint32_t ht_mask = 0;
    uint32_t ht_grow_at = 0;
    uint8_t ht_shift = 52;
    bool track_used = true;
    size_t used_limit = 512;

    // Precompiled linear post-order bytecode for rule.rhs[0/1]
    std::vector<int8_t> bc[2];
    int bc_max_stack[2] = {0, 0};

    // Reusable evaluation stacks & scratch buffers (zero heap allocation across evals)
    struct WhnfFrame {
        uint32_t u_ph; // bit 0: 0=phase1, 1=phase2; bits 1..31: node id u
        int aux;
        StepCount acc;
    };
    static_assert(sizeof(WhnfFrame) == 24, "WhnfFrame must pack to 24 bytes");

    struct NfFrame {
        int u;
        uint8_t phase;
    };

    std::vector<WhnfFrame> wst_vec;
    std::vector<NfFrame> nst_vec;
    std::vector<int> nf_args_buf;

    // On-demand 100%-sound WHNF-congruence divergence prover scratch (strictly read-only on main DAG)
    static constexpr uint32_t DIV_TAB_SIZE = 1024;
    static constexpr uint32_t DIV_TAB_MASK = DIV_TAB_SIZE - 1;
    std::array<int, DIV_TAB_SIZE> div_key{};
    std::array<int, DIV_TAB_SIZE> div_val{};
    std::array<uint32_t, DIV_TAB_SIZE> div_gen_arr{};
    uint32_t div_gen = 0;

    explicit MemoEngine(const RuleSpec& r, int max_n = DEFAULT_MAX_NODES, int probes = 0, int max_s = DEFAULT_MAX_STACK)
        : rule(r), max_nodes(max_n), max_stack(max_s), num_atoms(2 + probes) {
        use_packed_ht = (std::max(max_nodes, num_atoms) <= PACKED_MAX_NODES);
        init_hash_table(4096);
        nodes.reserve(1024);
        wst_vec.reserve(512);
        nst_vec.reserve(512);
        compile_bytecode();
        reset();
    }

    void init_hash_table(uint32_t cap) {
        ht_mask = cap - 1;
        ht_shift = static_cast<uint8_t>(64 - __builtin_ctz(cap));
        ht_grow_at = static_cast<uint32_t>((static_cast<uint64_t>(cap) * 3ULL) / 5ULL);
        used_slots.clear();
        track_used = true;
        used_limit = static_cast<size_t>(cap) >> 3;
        if (use_packed_ht) {
            std::vector<HtSlot>().swap(ht_wide);
            ht_packed.resize(cap);
            std::memset(ht_packed.data(), 0xFF, static_cast<size_t>(cap) * sizeof(uint64_t));
        } else {
            std::vector<uint64_t>().swap(ht_packed);
            ht_wide.resize(cap);
            std::memset(ht_wide.data(), 0xFF, static_cast<size_t>(cap) * sizeof(HtSlot));
        }
    }

    void compile_bytecode() {
        for (int c = 0; c < 2; ++c) {
            bc[c].clear();
            int max_depth = 0;
            int cur_depth = 0;
            auto emit = [&](auto&& self, int u) -> void {
                if (u < 0 || u >= static_cast<int>(rule.rhs[c].size())) return;
                const RhsNode& nd = rule.rhs[c][u];
                if (nd.tag == -3) {
                    self(self, nd.l);
                    self(self, nd.r);
                    bc[c].push_back(-3);
                    cur_depth--;
                } else {
                    bc[c].push_back(static_cast<int8_t>(nd.tag));
                    if (++cur_depth > max_depth) max_depth = cur_depth;
                }
            };
            if (rule.root[c] >= 0) emit(emit, rule.root[c]);
            bc_max_stack[c] = max_depth;
        }
    }

    void reconfigure(const RuleSpec& r, int max_n = DEFAULT_MAX_NODES, int probes = 0, int max_s = DEFAULT_MAX_STACK) {
        rule = r;
        max_nodes = max_n;
        max_stack = max_s;
        num_atoms = 2 + probes;
        bool want_packed = (std::max(max_nodes, num_atoms) <= PACKED_MAX_NODES);
        if (want_packed != use_packed_ht) {
            use_packed_ht = want_packed;
            init_hash_table(4096);
        }
        compile_bytecode();
        reset();
    }

    // Steady-state buffer retention ceilings for persistent --batch workers:
    // keep warm L3-friendly buffers for normal cases (<= 262k nodes, ~20 MiB RSS),
    // but immediately release multi-hundred-MiB allocations after huge evaluations
    // so idle workers stay tiny even on 24 GiB RAM servers.
    static constexpr size_t RETAIN_MAX_NODES_CAP = 262144;
    static constexpr size_t RETAIN_MAX_HT_CAP = 524288;
    static constexpr size_t RETAIN_MAX_NF_CHUNKS = 64;
    static constexpr size_t RETAIN_MAX_STACK_CAP = 65536;

    void trim_memory_if_oversized() {
        if (nodes.capacity() > RETAIN_MAX_NODES_CAP) {
            std::vector<HotNode>().swap(nodes);
            nodes.reserve(1024);
        }
        if (ht_wide.capacity() > RETAIN_MAX_HT_CAP || ht_packed.capacity() > RETAIN_MAX_HT_CAP) {
            std::vector<HtSlot>().swap(ht_wide);
            std::vector<uint64_t>().swap(ht_packed);
            std::vector<uint32_t>().swap(used_slots);
            init_hash_table(4096);
        }
        if (nf_chunks.size() > RETAIN_MAX_NF_CHUNKS) {
            std::vector<std::unique_ptr<NfCold[]>>().swap(nf_chunks);
        }
        if (wst_vec.capacity() > RETAIN_MAX_STACK_CAP) {
            std::vector<WhnfFrame>().swap(wst_vec);
            wst_vec.reserve(512);
        }
        if (nst_vec.capacity() > RETAIN_MAX_STACK_CAP) {
            std::vector<NfFrame>().swap(nst_vec);
            nst_vec.reserve(512);
        }
        if (nf_args_buf.capacity() > 4096) {
            std::vector<int>().swap(nf_args_buf);
        }
    }

    inline void reserve_next_node() {
        if (__builtin_expect(nodes.size() == nodes.capacity(), 0)) {
            size_t cur = nodes.capacity();
            size_t next = (cur < 1024) ? 1024 : (cur * 2);
            size_t cap_lim = static_cast<size_t>(std::max(max_nodes, num_atoms));
            if (next > cap_lim) next = cap_lim;
            if (next > cur) nodes.reserve(next);
        }
    }

    inline int node_count() const { return static_cast<int>(nodes.size()); }
    inline int left(int u) const { return nodes[u].l; }
    inline int right(int u) const { return nodes[u].r; }
    inline uint8_t whnf_st(int u) const { return nodes[u].wst; }
    inline uint8_t nf_st(int u) const { return nodes[u].nst; }
    inline StepCount whnf_sc(int u) const { return nodes[u].ws; }
    inline StepCount nf_sc(int u) const {
        const NfCold& c = nf_chunks[static_cast<size_t>(u) >> NF_CHUNK_BITS][u & NF_CHUNK_MASK];
        return {c.mant, c.exp2};
    }
    inline int nf_nd(int u) const {
        return nf_chunks[static_cast<size_t>(u) >> NF_CHUNK_BITS][u & NF_CHUNK_MASK].node;
    }

    inline void set_nf(int u, int node, StepCount sc) {
        size_t ch = static_cast<size_t>(u) >> NF_CHUNK_BITS;
        if (__builtin_expect(ch >= nf_chunks.size(), 0)) nf_chunks.resize(ch + 1);
        if (__builtin_expect(!nf_chunks[ch], 0)) {
            nf_chunks[ch].reset(new NfCold[NF_CHUNK_SIZE]);
        }
        nf_chunks[ch][u & NF_CHUNK_MASK] = {node, sc.mant, sc.exp2};
    }

    inline const NfCold& get_nf(int u) const {
        return nf_chunks[static_cast<size_t>(u) >> NF_CHUNK_BITS][u & NF_CHUNK_MASK];
    }

    void reset() {
        size_t hsz = static_cast<size_t>(ht_mask) + 1;
        if (use_packed_ht) {
            if (track_used && used_slots.size() * 8 < hsz) {
                uint64_t* hp = ht_packed.data();
                for (uint32_t idx : used_slots) hp[idx] = EMPTY_KEY;
            } else {
                std::memset(ht_packed.data(), 0xFF, hsz * sizeof(uint64_t));
            }
        } else {
            if (track_used && used_slots.size() * 8 < hsz) {
                HtSlot* hp = ht_wide.data();
                for (uint32_t idx : used_slots) hp[idx].key = EMPTY_KEY;
            } else {
                std::memset(ht_wide.data(), 0xFF, hsz * sizeof(HtSlot));
            }
        }
        used_slots.clear();
        track_used = true;
        used_limit = hsz >> 3;
        nodes.clear();

        for (int i = 0; i < num_atoms; ++i) {
            uint16_t sp = static_cast<uint16_t>((i < 2 ? i : 2) << 14);
            nodes.push_back({-1, -1, i, 2, 2, sp, StepCount::zero()});
            set_nf(i, i, StepCount::zero());
        }
    }

    inline uint32_t hash_packed(uint64_t key) const {
        return static_cast<uint32_t>(((key ^ (key >> 21)) * 0x9e3779b97f4a7c15ULL) >> ht_shift);
    }

    inline uint32_t hash_wide(uint64_t key) const {
        return static_cast<uint32_t>(((key ^ (key >> 27)) * 0x9e3779b97f4a7c15ULL) >> ht_shift);
    }

    void rehash_packed() {
        uint32_t nc = (ht_mask + 1) * 2;
        uint32_t nm = nc - 1;
        uint8_t nsh = static_cast<uint8_t>(64 - __builtin_ctz(nc));
        track_used = false;
        used_slots.clear();
        std::vector<uint64_t> nh(nc);
        std::memset(nh.data(), 0xFF, static_cast<size_t>(nc) * sizeof(uint64_t));
        uint64_t* np = nh.data();
        const uint64_t* op = ht_packed.data();
        const size_t osz = ht_packed.size();
        for (size_t i = 0; i < osz; ++i) {
            uint64_t sl = op[i];
            if (sl != EMPTY_KEY) {
                uint64_t k = sl >> 21;
                uint32_t idx = static_cast<uint32_t>(((k ^ (k >> 21)) * 0x9e3779b97f4a7c15ULL) >> nsh);
                while (np[idx] != EMPTY_KEY) idx = (idx + 1) & nm;
                np[idx] = sl;
            }
        }
        ht_packed.swap(nh);
        ht_mask = nm;
        ht_shift = nsh;
        ht_grow_at = static_cast<uint32_t>((static_cast<uint64_t>(nc) * 3ULL) / 5ULL);
    }

    void rehash_wide() {
        uint32_t nc = (ht_mask + 1) * 2;
        uint32_t nm = nc - 1;
        uint8_t nsh = static_cast<uint8_t>(64 - __builtin_ctz(nc));
        track_used = false;
        used_slots.clear();
        std::vector<HtSlot> nh(nc);
        std::memset(nh.data(), 0xFF, static_cast<size_t>(nc) * sizeof(HtSlot));
        HtSlot* np = nh.data();
        const HtSlot* op = ht_wide.data();
        const size_t osz = ht_wide.size();
        for (size_t i = 0; i < osz; ++i) {
            uint64_t k = op[i].key;
            if (k != EMPTY_KEY) {
                uint32_t idx = static_cast<uint32_t>(((k ^ (k >> 27)) * 0x9e3779b97f4a7c15ULL) >> nsh);
                while (np[idx].key != EMPTY_KEY) idx = (idx + 1) & nm;
                np[idx] = op[i];
            }
        }
        ht_wide.swap(nh);
        ht_mask = nm;
        ht_shift = nsh;
        ht_grow_at = static_cast<uint32_t>((static_cast<uint64_t>(nc) * 3ULL) / 5ULL);
    }

    int app_wide(int a, int b) {
        const uint64_t key = (static_cast<uint64_t>(static_cast<uint32_t>(a)) << 32) | static_cast<uint32_t>(b);
        HtSlot* hp = ht_wide.data();
        uint32_t idx = hash_wide(key);
        while (hp[idx].key != EMPTY_KEY) {
            if (hp[idx].key == key) return hp[idx].val;
            idx = (idx + 1) & ht_mask;
        }
        int id = static_cast<int>(nodes.size());
        if (__builtin_expect(id >= max_nodes, 0)) return -1;
        if (__builtin_expect(static_cast<uint32_t>(id) >= ht_grow_at, 0)) {
            rehash_wide();
            hp = ht_wide.data();
            idx = hash_wide(key);
            while (hp[idx].key != EMPTY_KEY) idx = (idx + 1) & ht_mask;
        }
        hp[idx] = {key, id};
        if (__builtin_expect(track_used, 0)) {
            if (used_slots.size() < used_limit) used_slots.push_back(idx);
            else track_used = false;
        }
        uint16_t sa = nodes[a].sp;
        uint16_t sp_new = ((sa & 0x3FFFU) < 0x3FFFU) ? static_cast<uint16_t>(sa + 1U) : sa;
        reserve_next_node();
        nodes.push_back({a, b, -1, 0, 0, sp_new, StepCount::zero()});
        return id;
    }

    inline int app(int a, int b) {
        if (__builtin_expect(!use_packed_ht, 0)) return app_wide(a, b);
        const uint64_t key = (static_cast<uint64_t>(static_cast<uint32_t>(a)) << 21) | static_cast<uint32_t>(b);
        uint64_t* hp = ht_packed.data();
        uint32_t idx = hash_packed(key);
        uint64_t sl;
        while ((sl = hp[idx]) != EMPTY_KEY) {
            if ((sl >> 21) == key) return static_cast<int>(sl & 0x1FFFFFULL);
            idx = (idx + 1) & ht_mask;
        }
        int id = static_cast<int>(nodes.size());
        if (__builtin_expect(id >= max_nodes, 0)) return -1;
        if (__builtin_expect(static_cast<uint32_t>(id) >= ht_grow_at, 0)) {
            rehash_packed();
            hp = ht_packed.data();
            idx = hash_packed(key);
            while (hp[idx] != EMPTY_KEY) idx = (idx + 1) & ht_mask;
        }
        hp[idx] = (key << 21) | static_cast<uint64_t>(id);
        if (__builtin_expect(track_used, 0)) {
            if (used_slots.size() < used_limit) used_slots.push_back(idx);
            else track_used = false;
        }
        uint16_t sa = nodes[a].sp;
        uint16_t sp_new = ((sa & 0x3FFFU) < 0x3FFFU) ? static_cast<uint16_t>(sa + 1U) : sa;
        reserve_next_node();
        nodes.push_back({a, b, -1, 0, 0, sp_new, StepCount::zero()});
        return id;
    }

    int build_ast(const ExprAst& ast, int u) {
        const AstNode& nd = ast.nodes[u];
        if (nd.sym >= 0) return nd.sym;
        int a = build_ast(ast, nd.l);
        if (a < 0) return -1;
        int b = build_ast(ast, nd.r);
        if (b < 0) return -1;
        return app(a, b);
    }

    inline int spine_of_whnf(int w, std::vector<int>& args) const {
        args.clear();
        int cur = w;
        const HotNode* hp = nodes.data();
        while (cur >= num_atoms) {
            args.push_back(hp[cur].r);
            cur = hp[cur].l;
        }
        std::reverse(args.begin(), args.end());
        return cur;
    }

    int inst_rhs(int comb, int rhs_id, const int* args) {
        const RhsNode& nd = rule.rhs[comb][rhs_id];
        if (nd.tag == -1) return 0;
        if (nd.tag == -2) return 1;
        if (nd.tag >= 0) return args[nd.tag];
        int a = inst_rhs(comb, nd.l, args);
        if (a < 0) return -1;
        int b = inst_rhs(comb, nd.r, args);
        if (b < 0) return -1;
        return app(a, b);
    }

    inline int inst_rhs_fast(int comb, const int* args) {
        const auto& code = bc[comb];
        const int len = static_cast<int>(code.size());
        if (__builtin_expect(len == 1, 0)) {
            int8_t op = code[0];
            return (op >= 0) ? args[op] : ((op == -1) ? 0 : 1);
        }
        if (__builtin_expect(bc_max_stack[comb] <= 64, 1)) {
            int stk[64];
            int top = 0;
            const int8_t* p = code.data();
            for (int i = 0; i < len; ++i) {
                int8_t op = p[i];
                if (op >= 0) {
                    stk[top++] = args[op];
                } else if (op == -3) {
                    int b = stk[--top];
                    int a = stk[--top];
                    int r = app(a, b);
                    if (__builtin_expect(r < 0, 0)) return -1;
                    stk[top++] = r;
                } else {
                    stk[top++] = (op == -1) ? 0 : 1;
                }
            }
            return stk[0];
        }
        return inst_rhs(comb, rule.root[comb], args);
    }

    inline int lookup_app_readonly(int a, int b) const {
        if (__builtin_expect(use_packed_ht, 1)) {
            const uint64_t key = (static_cast<uint64_t>(static_cast<uint32_t>(a)) << 21) | static_cast<uint32_t>(b);
            const uint64_t* hp = ht_packed.data();
            uint32_t idx = hash_packed(key);
            uint64_t sl;
            while ((sl = hp[idx]) != EMPTY_KEY) {
                if ((sl >> 21) == key) return static_cast<int>(sl & 0x1FFFFFULL);
                idx = (idx + 1) & ht_mask;
            }
            return -1;
        }
        const uint64_t key = (static_cast<uint64_t>(static_cast<uint32_t>(a)) << 32) | static_cast<uint32_t>(b);
        const HtSlot* hp = ht_wide.data();
        uint32_t idx = hash_wide(key);
        while (hp[idx].key != EMPTY_KEY) {
            if (hp[idx].key == key) return hp[idx].val;
            idx = (idx + 1) & ht_mask;
        }
        return -1;
    }

    inline void div_cache_reset() {
        if (++div_gen == 0U) {
            div_gen_arr.fill(0U);
            div_gen = 1U;
        }
    }

    inline bool div_cache_get(int k, int& out) const {
        uint32_t h = (static_cast<uint32_t>(k) * 2654435761U) & DIV_TAB_MASK;
        for (int probe = 0; probe < 16; ++probe) {
            if (div_gen_arr[h] != div_gen) return false;
            if (div_key[h] == k) {
                out = div_val[h];
                return true;
            }
            h = (h + 1U) & DIV_TAB_MASK;
        }
        return false;
    }

    inline void div_cache_put(int k, int v) {
        uint32_t h = (static_cast<uint32_t>(k) * 2654435761U) & DIV_TAB_MASK;
        for (int probe = 0; probe < 16; ++probe) {
            if (div_gen_arr[h] != div_gen || div_key[h] == k) {
                div_gen_arr[h] = div_gen;
                div_key[h] = k;
                div_val[h] = v;
                return;
            }
            h = (h + 1U) & DIV_TAB_MASK;
        }
    }

    int inst_rhs_readonly(int comb, const int* args) const {
        const auto& code = bc[comb];
        const int len = static_cast<int>(code.size());
        if (len == 0 || bc_max_stack[comb] > 64) return -1;
        if (len == 1) {
            int8_t op = code[0];
            return (op >= 0) ? args[op] : ((op == -1) ? 0 : 1);
        }
        int stk[64];
        int top = 0;
        const int8_t* p = code.data();
        for (int i = 0; i < len; ++i) {
            int8_t op = p[i];
            if (op >= 0) {
                stk[top++] = args[op];
            } else if (op == -3) {
                int b = stk[--top];
                int a = stk[--top];
                int r = lookup_app_readonly(a, b);
                if (r < 0) return -1;
                stk[top++] = r;
            } else {
                stk[top++] = (op == -1) ? 0 : 1;
            }
        }
        return stk[0];
    }

    // Read-only bounded WHNF micro-stepper: returns y with x ->_whnf^{1..max_steps} y
    // using only existing DAG nodes (no mutation, 100% sound for x |> y).
    int step_whnf_readonly_once(int x) const {
        if (x < num_atoms) return -1;
        int a = nodes[x].l;
        int b = nodes[x].r;
        int wa = (nodes[a].wst == 2) ? nodes[a].wn : a;
        uint16_t sp = nodes[wa].sp;
        int h = sp >> 14;
        int k = sp & 0x3FFF;
        if (h < 2 && k + 1 == rule.arity[h]) {
            int args[MAX_ARITY];
            int cur = wa;
            const HotNode* hp = nodes.data();
            for (int i = k - 1; i >= 0; --i) {
                args[i] = hp[cur].r;
                cur = hp[cur].l;
            }
            args[k] = b;
            return inst_rhs_readonly(h, args);
        }
        if (wa != a) {
            return lookup_app_readonly(wa, b);
        }
        return -1;
    }

    // Bottom-up 1-layer WHNF peel (strictly read-only): returns y with x |> y
    // using only already-completed WHNF reductions (nodes[t].wst == 2) or valid
    // read-only intermediate WHNF steps (t ->_whnf+ t').
    int peel_one_whnf(int x, int depth, int& budget, bool intermediate_mode) {
        if (x < num_atoms || depth >= 96 || budget <= 0) return x;
        int cached = x;
        if (div_cache_get(x, cached)) return cached;
        --budget;
        const int l = nodes[x].l;
        const int r = nodes[x].r;
        const int l2 = peel_one_whnf(l, depth + 1, budget, intermediate_mode);
        const int r2 = peel_one_whnf(r, depth + 1, budget, intermediate_mode);
        int res = x;
        bool child_rebuilt = false;
        if (l2 != l || r2 != r) {
            int cand = lookup_app_readonly(l2, r2);
            if (cand < 0 && l2 != l) cand = lookup_app_readonly(l2, r);
            if (cand < 0 && r2 != r) cand = lookup_app_readonly(l, r2);
            if (cand >= 0) {
                res = cand;
                child_rebuilt = true;
            }
        }
        if (!child_rebuilt) {
            if (intermediate_mode) {
                // Take 1..4 valid read-only WHNF steps (e.g. I (G K) -> K (G K) (K (G K)) -> G K)
                int cur = x;
                for (int s = 0; s < 4; ++s) {
                    int nxt = step_whnf_readonly_once(cur);
                    if (nxt < 0 || nxt == cur) break;
                    cur = nxt;
                    // Stop as soon as cur is an application/atom that isn't a transient K-pair
                    if (s >= 1 && (nodes[cur].wst != 0 || nodes[cur].nst != 0)) break;
                }
                if (cur != x) {
                    res = cur;
                } else if (nodes[x].wst == 2 && nodes[x].wn != x) {
                    res = nodes[x].wn;
                }
            } else if (nodes[x].wst == 2 && nodes[x].wn != x) {
                res = nodes[x].wn;
            }
        }
        div_cache_put(x, res);
        return res;
    }

    // Full known-WHNF collapse (strictly read-only): returns y with x |>* y
    // using only already-completed WHNF reductions (nodes[t].wst == 2).
    int reduce_known_whnf(int x, int depth, int& budget) {
        if (x < num_atoms) return x;
        if (nodes[x].wst == 2) return nodes[x].wn;
        if (depth >= 96 || budget <= 0) return x;
        int cached = x;
        if (div_cache_get(x, cached)) return cached;
        --budget;
        const int l = nodes[x].l;
        const int r = nodes[x].r;
        const int l2 = reduce_known_whnf(l, depth + 1, budget);
        const int r2 = reduce_known_whnf(r, depth + 1, budget);
        int res = x;
        if (l2 != l || r2 != r) {
            int cand = lookup_app_readonly(l2, r2);
            if (cand < 0 && l2 != l) cand = lookup_app_readonly(l2, r);
            if (cand < 0 && r2 != r) cand = lookup_app_readonly(l, r2);
            if (cand >= 0) {
                res = (nodes[cand].wst == 2) ? nodes[cand].wn : cand;
            }
        }
        div_cache_put(x, res);
        return res;
    }

    // 100%-sound WHNF divergence certificate:
    // Called immediately after pushing u's phase-2 frame onto wst_vec for child nxt.
    // Every active node c (nodes[c].wst == 1) satisfies k_c >= 1 + k_nxt.
    // Any read-only WHNF-reduced image y of nxt satisfies nxt |>* y => k_nxt >= k_y.
    // Hence nodes[y].wst == 1 implies 1 + k_nxt <= k_y <= k_nxt (contradiction -> DIV).
    bool prove_whnf_congruence_div(int nxt) {
        div_cache_reset();
        int b1 = 384;
        int c1 = peel_one_whnf(nxt, 0, b1, false);
        if (c1 >= 0 && nodes[c1].wst == 1) return true;

        div_cache_reset();
        int b2 = 384;
        int c2 = reduce_known_whnf(nxt, 0, b2);
        if (c2 >= 0 && nodes[c2].wst == 1) return true;

        div_cache_reset();
        int b3 = 384;
        int c3 = peel_one_whnf(nxt, 0, b3, true);
        if (c3 >= 0 && nodes[c3].wst == 1) return true;
        return false;
    }

    // 100%-sound NF divergence certificate:
    // Checks whether u reduces via known WHNFs (u |>* c) to an active NF ancestor c
    // with at least one committed non-zero WHNF step (phase == 2) between c and u.
    bool prove_nf_congruence_div(int u) {
        auto is_strict_nf_ancestor = [&](int c) -> bool {
            if (c < 0 || nodes[c].nst != 1) return false;
            bool seen_step = false;
            for (int i = static_cast<int>(nst_vec.size()) - 1; i >= 0; --i) {
                const NfFrame& fr = nst_vec[static_cast<size_t>(i)];
                if (fr.phase == 2) seen_step = true;
                if (fr.u == c && fr.phase != 0) return seen_step;
            }
            return false;
        };
        div_cache_reset();
        int b1 = 384;
        int c1 = peel_one_whnf(u, 0, b1, false);
        if (is_strict_nf_ancestor(c1)) return true;

        div_cache_reset();
        int b2 = 384;
        int c2 = reduce_known_whnf(u, 0, b2);
        if (is_strict_nf_ancestor(c2)) return true;

        div_cache_reset();
        int b3 = 384;
        int c3 = peel_one_whnf(u, 0, b3, true);
        if (is_strict_nf_ancestor(c3)) return true;
        return false;
    }

    EvalStatus compute_whnf(int root, int& whnf_hits, int& redex_fires, int& peak_stack) {
        if (nodes[root].wst == 2) {
            whnf_hits++;
            return EvalStatus::HALTED;
        }
        wst_vec.clear();
        int args[MAX_ARITY];
        int u = root;
        int phase2_depth = 0;
        int next_div_check = 16;

    enter_phase0:
        {
            int cur_sz = static_cast<int>(wst_vec.size()) + 1;
            if (cur_sz > peak_stack) peak_stack = cur_sz;
            if (__builtin_expect(cur_sz > max_stack, 0)) return EvalStatus::STACK_LIMIT;
            uint8_t s = nodes[u].wst;
            if (s == 2) {
                whnf_hits++;
                goto pop_next;
            }
            if (__builtin_expect(s == 1, 0)) return EvalStatus::CYCLE_WHNF;
            nodes[u].wst = 1;
            int a = nodes[u].l;
            if (nodes[a].wst != 2) {
                wst_vec.push_back({static_cast<uint32_t>(u) << 1, -1, StepCount::zero()});
                u = a;
                goto enter_phase0;
            }
            whnf_hits++;
        }

    do_phase1:
        {
            const HotNode& nu = nodes[u];
            int a = nu.l;
            int b = nu.r;
            const HotNode& na = nodes[a];
            int wa = na.wn;
            StepCount t1 = na.ws;
            uint16_t sp = nodes[wa].sp;
            int h = sp >> 14;
            int k = sp & 0x3FFF;
            if (h >= 2 || k + 1 < rule.arity[h]) {
                int w = (wa == a) ? u : app(wa, b);
                if (__builtin_expect(w < 0, 0)) return EvalStatus::NODE_LIMIT;
                nodes[u].wn = w;
                nodes[u].ws = t1;
                nodes[u].wst = 2;
            } else {
                int cur = wa;
                const HotNode* hp = nodes.data();
                for (int i = k - 1; i >= 0; --i) {
                    args[i] = hp[cur].r;
                    cur = hp[cur].l;
                }
                args[k] = b;
                int nxt = inst_rhs_fast(h, args);
                if (__builtin_expect(nxt < 0, 0)) return EvalStatus::NODE_LIMIT;
                redex_fires++;
                StepCount t1p = StepCount::add(t1, StepCount::one());
                uint8_t ns = nodes[nxt].wst;
                if (ns == 2) {
                    whnf_hits++;
                    nodes[u].wn = nodes[nxt].wn;
                    nodes[u].ws = StepCount::add(t1p, nodes[nxt].ws);
                    nodes[u].wst = 2;
                } else if (__builtin_expect(ns == 1, 0)) {
                    return EvalStatus::CYCLE_WHNF;
                } else {
                    wst_vec.push_back({(static_cast<uint32_t>(u) << 1) | 1U, nxt, t1p});
                    ++phase2_depth;
                    if (__builtin_expect(phase2_depth >= next_div_check, 0)) {
                        if (prove_whnf_congruence_div(nxt)) return EvalStatus::CYCLE_WHNF;
                        if (phase2_depth >= next_div_check + 7) {
                            next_div_check = (next_div_check < (1 << 28)) ? (next_div_check * 2) : (1 << 29);
                        }
                    }
                    u = nxt;
                    goto enter_phase0;
                }
            }
        }

    pop_next:
        while (!wst_vec.empty()) {
            WhnfFrame f = wst_vec.back();
            wst_vec.pop_back();
            u = static_cast<int>(f.u_ph >> 1);
            if ((f.u_ph & 1U) == 0U) {
                goto do_phase1;
            }
            --phase2_depth;
            int nxt = f.aux;
            nodes[u].wn = nodes[nxt].wn;
            nodes[u].ws = StepCount::add(f.acc, nodes[nxt].ws);
            nodes[u].wst = 2;
        }
        return EvalStatus::HALTED;
    }

    EvalResult eval_nf(int root) {
        EvalResult out;
        nst_vec.clear();
        nst_vec.push_back({root, 0});
        int sargs[8];
        int nf_step_depth = 0;
        int next_nf_div_check = 16;
        while (!nst_vec.empty()) {
            int sz = static_cast<int>(nst_vec.size());
            if (sz > out.peak_stack) out.peak_stack = sz;
            if (__builtin_expect(sz > max_stack, 0)) {
                out.status = EvalStatus::STACK_LIMIT;
                out.dag_nodes = static_cast<int>(nodes.size());
                return out;
            }
            NfFrame f = nst_vec.back();
            nst_vec.pop_back();
            int u = f.u;
            if (f.phase == 0) {
                uint8_t s = nodes[u].nst;
                if (s == 2) { out.nf_hits++; continue; }
                if (__builtin_expect(s == 1, 0)) {
                    out.status = EvalStatus::CYCLE_NF;
                    out.dag_nodes = static_cast<int>(nodes.size());
                    return out;
                }
                if (__builtin_expect(nf_step_depth >= next_nf_div_check, 0)) {
                    if (prove_nf_congruence_div(u)) {
                        out.status = EvalStatus::CYCLE_NF;
                        out.dag_nodes = static_cast<int>(nodes.size());
                        return out;
                    }
                    if (nf_step_depth >= next_nf_div_check + 7) {
                        next_nf_div_check = (next_nf_div_check < (1 << 28)) ? (next_nf_div_check * 2) : (1 << 29);
                    }
                }
                nodes[u].nst = 1;
                EvalStatus ws = compute_whnf(u, out.whnf_hits, out.redex_fires, out.peak_stack);
                if (ws != EvalStatus::HALTED) {
                    out.status = ws;
                    out.dag_nodes = static_cast<int>(nodes.size());
                    return out;
                }
                int w = nodes[u].wn;
                if (w < num_atoms) {
                    set_nf(u, w, nodes[u].ws);
                    nodes[u].nst = 2;
                } else {
                    const uint8_t done_ph = nodes[u].ws.is_zero() ? 1 : 2;
                    if (done_ph == 2) ++nf_step_depth;
                    nst_vec.push_back({u, done_ph});
                    int cur = w;
                    const HotNode* hp = nodes.data();
                    while (cur >= num_atoms) {
                        int arg = hp[cur].r;
                        if (hp[arg].nst == 2) {
                            out.nf_hits++;
                        } else {
                            nst_vec.push_back({arg, 0});
                        }
                        cur = hp[cur].l;
                    }
                }
            } else {
                if (f.phase == 2) --nf_step_depth;
                int w = nodes[u].wn;
                uint16_t spk = nodes[w].sp & 0x3FFFU;
                const HotNode* hp = nodes.data();
                StepCount tot = nodes[u].ws;
                bool changed = false;
                if (__builtin_expect(spk <= 8U, 1)) {
                    int k = static_cast<int>(spk);
                    int cur = w;
                    for (int i = k - 1; i >= 0; --i) {
                        sargs[i] = hp[cur].r;
                        cur = hp[cur].l;
                    }
                    int h = cur;
                    for (int i = 0; i < k; ++i) {
                        const NfCold& c = get_nf(sargs[i]);
                        tot = StepCount::add(tot, {c.mant, c.exp2});
                        if (c.node != sargs[i]) changed = true;
                    }
                    int res_node = w;
                    if (changed) {
                        res_node = h;
                        for (int i = 0; i < k; ++i) {
                            res_node = app(res_node, get_nf(sargs[i]).node);
                            if (__builtin_expect(res_node < 0, 0)) {
                                out.status = EvalStatus::NODE_LIMIT;
                                out.dag_nodes = static_cast<int>(nodes.size());
                                return out;
                            }
                        }
                    }
                    set_nf(u, res_node, tot);
                    nodes[u].nst = 2;
                } else {
                    int h = spine_of_whnf(w, nf_args_buf);
                    int k = static_cast<int>(nf_args_buf.size());
                    for (int i = 0; i < k; ++i) {
                        int ai = nf_args_buf[i];
                        const NfCold& c = get_nf(ai);
                        tot = StepCount::add(tot, {c.mant, c.exp2});
                        if (c.node != ai) changed = true;
                    }
                    int res_node = w;
                    if (changed) {
                        res_node = h;
                        for (int i = 0; i < k; ++i) {
                            res_node = app(res_node, get_nf(nf_args_buf[i]).node);
                            if (__builtin_expect(res_node < 0, 0)) {
                                out.status = EvalStatus::NODE_LIMIT;
                                out.dag_nodes = static_cast<int>(nodes.size());
                                return out;
                            }
                        }
                    }
                    set_nf(u, res_node, tot);
                    nodes[u].nst = 2;
                }
            }
        }
        out.status = EvalStatus::HALTED;
        const NfCold& rf = get_nf(root);
        out.steps = {rf.mant, rf.exp2};
        out.dag_nodes = static_cast<int>(nodes.size());
        out.nf_root = rf.node;
        return out;
    }

    // Bounded pretty-printer of a DAG node (truncates safely if expanded tree is huge)
    std::string dag_preview(int u, int max_chars = 160) const {
        std::string out;
        auto dfs = [&](auto&& self, int cur, int depth) -> void {
            if (static_cast<int>(out.size()) >= max_chars) return;
            if (cur == 0) { out.push_back('S'); return; }
            if (cur == 1) { out.push_back('K'); return; }
            if (cur < num_atoms) { out += "v" + std::to_string(cur - 2); return; }
            if (depth > 48) { out += "..."; return; }
            out.push_back('(');
            self(self, nodes[cur].l, depth + 1);
            if (static_cast<int>(out.size()) < max_chars) out.push_back(' ');
            self(self, nodes[cur].r, depth + 1);
            if (static_cast<int>(out.size()) < max_chars) out.push_back(')');
        };
        dfs(dfs, u, 0);
        if (static_cast<int>(out.size()) >= max_chars) {
            out.resize(max_chars);
            out += "...";
        }
        return out;
    }
};

// ============================================================================
// Pure-Tree Brute-Force Reference Reducer + Step-by-Step Replay Tracer
// ============================================================================
struct BruteTreeNode {
    int sym; // 0=S, 1=K, -1=app
    int l = -1;
    int r = -1;
};

struct TraceFrame {
    int step = 0;
    std::string expr;
    std::string rule_fired; // "S", "K", "NF", or "TRUNC"
    std::string redex_path; // e.g. "", "L", "LR"
    int tree_leaves = 0;
};

struct BruteTracer {
    RuleSpec rule;
    std::vector<BruteTreeNode> pool;
    int max_pool = 300000;

    explicit BruteTracer(const RuleSpec& r, int max_p = 300000) : rule(r), max_pool(max_p) {
        pool.push_back({0, -1, -1});
        pool.push_back({1, -1, -1});
    }

    int mk_app(int a, int b) {
        if (static_cast<int>(pool.size()) >= max_pool) return -1;
        int id = static_cast<int>(pool.size());
        pool.push_back({-1, a, b});
        return id;
    }

    int build_ast(const ExprAst& ast, int u) {
        const AstNode& nd = ast.nodes[u];
        if (nd.sym >= 0) return nd.sym;
        int a = build_ast(ast, nd.l);
        if (a < 0) return -1;
        int b = build_ast(ast, nd.r);
        if (b < 0) return -1;
        return mk_app(a, b);
    }

    int inst_rhs(int comb, int rhs_id, const int* args) {
        const RhsNode& nd = rule.rhs[comb][rhs_id];
        if (nd.tag == -1) return 0;
        if (nd.tag == -2) return 1;
        if (nd.tag >= 0) return args[nd.tag];
        int a = inst_rhs(comb, nd.l, args);
        if (a < 0) return -1;
        int b = inst_rhs(comb, nd.r, args);
        if (b < 0) return -1;
        return mk_app(a, b);
    }

    int check_root_redex(int u, int& fired_comb) {
        int args[8];
        int k = 0;
        int cur = u;
        while (pool[cur].sym == -1) {
            if (k >= MAX_ARITY + 1) return -1;
            args[k++] = pool[cur].r;
            cur = pool[cur].l;
        }
        int h = pool[cur].sym;
        if (k != rule.arity[h]) return -1;
        for (int i = 0; i < k / 2; ++i) std::swap(args[i], args[k - 1 - i]);
        fired_comb = h;
        return inst_rhs(h, rule.root[h], args);
    }

    int step_one(int u, std::string& path, int& fired_comb, int depth = 0) {
        if (depth > 1024) return -1;
        if (pool[u].sym != -1) return -2;
        int rr = check_root_redex(u, fired_comb);
        if (rr >= 0) return rr;
        if (static_cast<int>(pool.size()) >= max_pool) return -1;
        int nl = step_one(pool[u].l, path, fired_comb, depth + 1);
        if (nl == -1) return -1;
        if (nl != -2) {
            path.push_back('L');
            return mk_app(nl, pool[u].r);
        }
        int nr = step_one(pool[u].r, path, fired_comb, depth + 1);
        if (nr == -1) return -1;
        if (nr != -2) {
            path.push_back('R');
            return mk_app(pool[u].l, nr);
        }
        return -2;
    }

    int count_leaves_bounded(int u, int cap = 50000) const {
        if (pool[u].sym >= 0) return 1;
        int total = 0;
        std::vector<int> st{u};
        while (!st.empty()) {
            int cur = st.back();
            st.pop_back();
            if (pool[cur].sym >= 0) {
                total++;
                if (total >= cap) return cap;
            } else {
                st.push_back(pool[cur].r);
                st.push_back(pool[cur].l);
            }
        }
        return total;
    }

    std::string tree_preview(int u, int max_chars = 600) const {
        std::string out;
        auto dfs = [&](auto&& self, int cur, int depth) -> void {
            if (static_cast<int>(out.size()) >= max_chars) return;
            if (pool[cur].sym == 0) { out.push_back('S'); return; }
            if (pool[cur].sym == 1) { out.push_back('K'); return; }
            if (depth > 80) { out += "..."; return; }
            out.push_back('(');
            self(self, pool[cur].l, depth + 1);
            if (static_cast<int>(out.size()) < max_chars) out.push_back(' ');
            self(self, pool[cur].r, depth + 1);
            if (static_cast<int>(out.size()) < max_chars) out.push_back(')');
        };
        dfs(dfs, u, 0);
        if (static_cast<int>(out.size()) >= max_chars) {
            out.resize(max_chars);
            out += "...";
        }
        return out;
    }

    std::vector<TraceFrame> record_trace(const ExprAst& ast, int max_steps = 80, int max_chars = 600) {
        std::vector<TraceFrame> frames;
        int cur = build_ast(ast, ast.root);
        if (cur < 0) return frames;
        for (int t = 0; t <= max_steps; ++t) {
            int lv = count_leaves_bounded(cur, 100000);
            std::string expr_s = tree_preview(cur, max_chars);
            if (t == max_steps) {
                frames.push_back({t, expr_s, "TRUNC", "", lv});
                break;
            }
            std::string path;
            int fired = -1;
            int nxt = step_one(cur, path, fired);
            std::reverse(path.begin(), path.end());
            if (nxt == -2) {
                frames.push_back({t, expr_s, "NF", "", lv});
                break;
            }
            if (nxt == -1) {
                frames.push_back({t, expr_s, "TRUNC", "", lv});
                break;
            }
            frames.push_back({t, expr_s, fired == 0 ? "S" : "K", path, lv});
            cur = nxt;
        }
        return frames;
    }
};

inline std::string json_escape(const std::string& s) {
    std::string out;
    out.reserve(s.size() + 8);
    for (unsigned char c : s) {
        switch (c) {
            case '"':  out += "\\\""; break;
            case '\\': out += "\\\\"; break;
            case '\b': out += "\\b"; break;
            case '\f': out += "\\f"; break;
            case '\n': out += "\\n"; break;
            case '\r': out += "\\r"; break;
            case '\t': out += "\\t"; break;
            default:
                if (c < 0x20) {
                    char buf[8];
                    std::snprintf(buf, sizeof(buf), "\\u%04x", static_cast<unsigned>(c));
                    out += buf;
                } else {
                    out.push_back(static_cast<char>(c));
                }
        }
    }
    return out;
}

} // namespace sk_singularity
