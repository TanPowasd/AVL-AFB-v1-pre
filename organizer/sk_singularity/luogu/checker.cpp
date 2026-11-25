// Luogu Special Judge (testlib.h, C++14 compatible, zero external deps)
// for "The SK Singularity" (sk_singularity).
//
// Input (.in):
//   Line 1: N a_S a_K V_max TL_ms
//   Line 2: RHS_S
//   Line 3: RHS_K
//   Line 4: E_req (or '-')
// Answer (.ans):
//   Single positive floating-point number L_ref (reference log2(T+1) anchor)
// Contestant Output (.out):
//   Single valid SK full binary tree expression E (<= 4096 bytes)
//
// Partial scoring:
//   NormScore = 100 * (L^0.85) / (L^0.85 + L_ref^0.85), where L = log2(T(E) + 1).
//   Reports quitp(NormScore / 100.0) with exact/approx step count and DAG node stats.

#include "testlib.h"

#include <algorithm>
#include <chrono>
#include <cmath>
#include <cstdint>
#include <cstdio>
#include <iomanip>
#include <sstream>
#include <string>
#include <vector>

namespace {

static const int MAX_EXPR_BYTES = 4096;
static const int MIN_ARITY = 1;
static const int MAX_ARITY = 4;
static const int MAX_STACK = 40000000;
static const double SCORE_POWER = 0.85;
static const double SOFT_WATCHDOG_S = 8.0;

struct StepCount {
    static const uint64_t EXACT_LIMIT = (1ULL << 62);
    static const int64_t EXACT_EXP = (-9223372036854775807LL - 1);

    uint64_t mant;
    int64_t exp2;

    StepCount() : mant(0ULL), exp2(EXACT_EXP) {}
    StepCount(uint64_t m, int64_t e) : mant(m), exp2(e) {}

    static StepCount zero() { return StepCount(0ULL, EXACT_EXP); }
    static StepCount one()  { return StepCount(1ULL, EXACT_EXP); }

    bool is_exact() const { return exp2 == EXACT_EXP; }
    bool is_zero()  const { return exp2 == EXACT_EXP && mant == 0ULL; }

    void promote() {
        if (exp2 != EXACT_EXP || mant == 0ULL) return;
        int lz = __builtin_clzll(mant);
        int shift = lz - 1;
        mant <<= shift;
        exp2 = -static_cast<int64_t>(shift);
    }

    static StepCount add(StepCount a, StepCount b) {
        if (a.is_zero()) return b;
        if (b.is_zero()) return a;
        if (a.is_exact() && b.is_exact()) {
            uint64_t s = a.mant + b.mant;
            if (s < EXACT_LIMIT) return StepCount(s, EXACT_EXP);
            return StepCount(s, 0);
        }
        a.promote();
        b.promote();
        if (a.exp2 < b.exp2) std::swap(a, b);
        int64_t diff = a.exp2 - b.exp2;
        if (diff >= 64) return a;
        uint64_t b_al = b.mant >> diff;
        if (diff > 0) {
            uint64_t rem = b.mant & ((1ULL << diff) - 1ULL);
            uint64_t half = 1ULL << (diff - 1);
            if (rem > half || (rem == half && (b_al & 1ULL))) {
                b_al++;
            }
        }
        uint64_t sum = a.mant + b_al;
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
        return StepCount(sum, e);
    }

    double log2_plus1() const {
        if (is_exact()) return std::log2(static_cast<double>(mant) + 1.0);
        double frac = static_cast<double>(mant) / static_cast<double>(1ULL << 62);
        return static_cast<double>(exp2 + 62) + std::log2(frac);
    }

    std::string display() const {
        if (is_exact()) return std::to_string(mant);
        double l2 = log2_plus1();
        double l10 = l2 * 0.30102999566398119521;
        long long p10 = static_cast<long long>(std::floor(l10));
        double m10 = std::pow(10.0, l10 - static_cast<double>(p10));
        if (m10 >= 9.9995) {
            m10 = 1.0;
            p10++;
        }
        char buf[64];
        std::snprintf(buf, sizeof(buf), "%.3fe+%lld", m10, p10);
        return std::string(buf);
    }
};

struct AstNode {
    int sym;
    int l;
    int r;
    int leaves;
};

struct ExprAst {
    std::vector<AstNode> nodes;
    int root;
    int leaves;
    ExprAst() : root(-1), leaves(0) {}
};

int parse_expr_rec(const std::string& s, size_t& pos, int depth, ExprAst& out, std::string& err) {
    if (depth > 256) { err = "Parenthesis nesting too deep"; return -1; }
    while (pos < s.size() && s[pos] == ' ') pos++;
    if (pos >= s.size()) { err = "Unexpected end of expression"; return -1; }
    char c = s[pos];
    if (c == 'S' || c == 'K') {
        pos++;
        int id = static_cast<int>(out.nodes.size());
        AstNode nd = {c == 'S' ? 0 : 1, -1, -1, 1};
        out.nodes.push_back(nd);
        return id;
    }
    if (c == '(') {
        pos++;
        int a = parse_expr_rec(s, pos, depth + 1, out, err);
        if (a < 0) return -1;
        int b = parse_expr_rec(s, pos, depth + 1, out, err);
        if (b < 0) return -1;
        while (pos < s.size() && s[pos] == ' ') pos++;
        if (pos >= s.size() || s[pos] != ')') { err = "Expected ')'"; return -1; }
        pos++;
        int id = static_cast<int>(out.nodes.size());
        AstNode nd = {-1, a, b, out.nodes[a].leaves + out.nodes[b].leaves};
        out.nodes.push_back(nd);
        return id;
    }
    err = std::string("Unexpected char '") + c + "'";
    return -1;
}

bool parse_expr_strict(const std::string& raw, ExprAst& out, std::string& err) {
    out.nodes.clear();
    out.root = -1;
    out.leaves = 0;
    if (static_cast<int>(raw.size()) > MAX_EXPR_BYTES) {
        err = "Output exceeds 4096 bytes";
        return false;
    }
    std::string s = raw;
    while (!s.empty() && (s.back() == '\n' || s.back() == '\r')) s.pop_back();
    for (size_t i = 0; i < s.size(); ++i) {
        unsigned char ch = static_cast<unsigned char>(s[i]);
        if (ch != 'S' && ch != 'K' && ch != '(' && ch != ')' && ch != ' ') {
            err = "Illegal character in output";
            return false;
        }
    }
    size_t pos = 0;
    int rt = parse_expr_rec(s, pos, 0, out, err);
    if (rt < 0) return false;
    while (pos < s.size() && s[pos] == ' ') pos++;
    if (pos != s.size()) { err = "Trailing tokens after root expression"; return false; }
    out.root = rt;
    out.leaves = out.nodes[rt].leaves;
    return true;
}

bool ast_equal(const ExprAst& a, int ua, const ExprAst& b, int ub) {
    const AstNode& na = a.nodes[ua];
    const AstNode& nb = b.nodes[ub];
    if (na.sym != nb.sym || na.leaves != nb.leaves) return false;
    if (na.sym >= 0) return true;
    return ast_equal(a, na.l, b, nb.l) && ast_equal(a, na.r, b, nb.r);
}

bool ast_contains(const ExprAst& hay, int u, const ExprAst& needle) {
    if (ast_equal(hay, u, needle, needle.root)) return true;
    const AstNode& nd = hay.nodes[u];
    if (nd.sym >= 0 || nd.leaves < needle.leaves) return false;
    return ast_contains(hay, nd.l, needle) || ast_contains(hay, nd.r, needle);
}

struct RhsNode { int tag, l, r; };
struct RuleSpec {
    int arity[2];
    std::vector<RhsNode> rhs[2];
    int root[2];
};

int parse_rhs_rec(const std::string& s, size_t& pos, int arity, std::vector<RhsNode>& out) {
    while (pos < s.size() && (s[pos] == ' ' || s[pos] == '\t')) pos++;
    if (pos >= s.size()) return -1;
    char c = s[pos];
    if (c == '(') {
        pos++;
        int a = parse_rhs_rec(s, pos, arity, out);
        if (a < 0) return -1;
        int b = parse_rhs_rec(s, pos, arity, out);
        if (b < 0) return -1;
        while (pos < s.size() && (s[pos] == ' ' || s[pos] == '\t')) pos++;
        if (pos >= s.size() || s[pos] != ')') return -1;
        pos++;
        int id = static_cast<int>(out.size());
        RhsNode nd = {-3, a, b};
        out.push_back(nd);
        return id;
    }
    pos++;
    int id = static_cast<int>(out.size());
    if (c == 'S') { RhsNode nd = {-1, -1, -1}; out.push_back(nd); return id; }
    if (c == 'K') { RhsNode nd = {-2, -1, -1}; out.push_back(nd); return id; }
    if (c >= '0' && c <= '9' && (c - '0') < arity) {
        RhsNode nd = {c - '0', -1, -1};
        out.push_back(nd);
        return id;
    }
    return -1;
}

enum EvalStatus { ST_HALT = 0, ST_CYCLE_WHNF = 1, ST_CYCLE_NF = 2, ST_NODE_LIMIT = 3, ST_STACK_LIMIT = 4, ST_WATCHDOG = 5 };

struct MemoEngine {
    RuleSpec rule;
    int max_nodes;
    std::vector<int> L, R, wn, nn, hv;
    std::vector<StepCount> ws, ns;
    std::vector<uint8_t> wst, nst;
    std::vector<uint64_t> hk;
    uint32_t mask;
    std::chrono::steady_clock::time_point t0;
    uint64_t ticks;
    static const uint64_t EMPTY = ~0ULL;

    MemoEngine(const RuleSpec& r, int mn) : rule(r), max_nodes(mn), mask(4095), ticks(0) {
        t0 = std::chrono::steady_clock::now();
        hk.assign(4096, EMPTY);
        hv.assign(4096, -1);
        for (int i = 0; i < 2; ++i) {
            L.push_back(-1); R.push_back(-1);
            wn.push_back(i); ws.push_back(StepCount::zero()); wst.push_back(2);
            nn.push_back(i); ns.push_back(StepCount::zero()); nst.push_back(2);
        }
    }

    bool expired() {
        if ((++ticks & 16383ULL) != 0ULL) return false;
        double s = std::chrono::duration<double>(std::chrono::steady_clock::now() - t0).count();
        return s >= SOFT_WATCHDOG_S;
    }

    static inline uint64_t mix64(uint64_t x) {
        x ^= x >> 30; x *= 0xbf58476d1ce4e5b9ULL;
        x ^= x >> 27; x *= 0x94d049bb133111ebULL;
        return x ^ (x >> 31);
    }

    void rehash() {
        uint32_t nc = (mask + 1) * 2;
        std::vector<uint64_t> nk(nc, EMPTY);
        std::vector<int> nv(nc, -1);
        uint32_t nm = nc - 1;
        for (size_t i = 0; i < hk.size(); ++i) {
            if (hk[i] != EMPTY) {
                uint32_t idx = static_cast<uint32_t>(mix64(hk[i])) & nm;
                while (nk[idx] != EMPTY) idx = (idx + 1) & nm;
                nk[idx] = hk[i]; nv[idx] = hv[i];
            }
        }
        hk.swap(nk); hv.swap(nv); mask = nm;
    }

    int app(int a, int b) {
        uint64_t key = (static_cast<uint64_t>(static_cast<uint32_t>(a)) << 32) | static_cast<uint32_t>(b);
        uint32_t idx = static_cast<uint32_t>(mix64(key)) & mask;
        while (hk[idx] != EMPTY) {
            if (hk[idx] == key) return hv[idx];
            idx = (idx + 1) & mask;
        }
        int id = static_cast<int>(L.size());
        if (id >= max_nodes) return -1;
        if (static_cast<size_t>(id) * 10 >= hk.size() * 7) {
            rehash();
            idx = static_cast<uint32_t>(mix64(key)) & mask;
            while (hk[idx] != EMPTY) idx = (idx + 1) & mask;
        }
        hk[idx] = key; hv[idx] = id;
        L.push_back(a); R.push_back(b);
        wn.push_back(-1); ws.push_back(StepCount::zero()); wst.push_back(0);
        nn.push_back(-1); ns.push_back(StepCount::zero()); nst.push_back(0);
        return id;
    }

    int build_ast(const ExprAst& ast, int u) {
        const AstNode& nd = ast.nodes[u];
        if (nd.sym >= 0) return nd.sym;
        int a = build_ast(ast, nd.l); if (a < 0) return -1;
        int b = build_ast(ast, nd.r); if (b < 0) return -1;
        return app(a, b);
    }

    int spine(int w, std::vector<int>& args) const {
        args.clear();
        int cur = w;
        while (cur >= 2) { args.push_back(R[cur]); cur = L[cur]; }
        std::reverse(args.begin(), args.end());
        return cur;
    }

    int inst(int comb, int rid, const int* args) {
        const RhsNode& nd = rule.rhs[comb][rid];
        if (nd.tag == -1) return 0;
        if (nd.tag == -2) return 1;
        if (nd.tag >= 0) return args[nd.tag];
        int a = inst(comb, nd.l, args); if (a < 0) return -1;
        int b = inst(comb, nd.r, args); if (b < 0) return -1;
        return app(a, b);
    }

    EvalStatus whnf(int root) {
        if (wst[root] == 2) return ST_HALT;
        struct Frame { int u; uint8_t ph; int aux; StepCount acc; };
        std::vector<Frame> st;
        st.reserve(256);
        Frame f0 = {root, 0, -1, StepCount::zero()};
        st.push_back(f0);
        std::vector<int> args;
        args.reserve(16);
        while (!st.empty()) {
            if (expired()) return ST_WATCHDOG;
            if (static_cast<int>(st.size()) > MAX_STACK) return ST_STACK_LIMIT;
            Frame f = st.back(); st.pop_back();
            int u = f.u;
            if (f.ph == 0) {
                if (wst[u] == 2) continue;
                if (wst[u] == 1) return ST_CYCLE_WHNF;
                wst[u] = 1;
                Frame f1 = {u, 1, -1, StepCount::zero()};
                st.push_back(f1);
                if (wst[L[u]] != 2) {
                    Frame fl = {L[u], 0, -1, StepCount::zero()};
                    st.push_back(fl);
                }
            } else if (f.ph == 1) {
                int wa = wn[L[u]];
                StepCount t1 = ws[L[u]];
                int h = spine(wa, args);
                int k = static_cast<int>(args.size());
                if (k + 1 < rule.arity[h]) {
                    int w = app(wa, R[u]); if (w < 0) return ST_NODE_LIMIT;
                    wn[u] = w; ws[u] = t1; wst[u] = 2;
                } else {
                    args.push_back(R[u]);
                    int nxt = inst(h, rule.root[h], &args[0]); if (nxt < 0) return ST_NODE_LIMIT;
                    StepCount t1p = StepCount::add(t1, StepCount::one());
                    if (wst[nxt] == 2) {
                        wn[u] = wn[nxt]; ws[u] = StepCount::add(t1p, ws[nxt]); wst[u] = 2;
                    } else if (wst[nxt] == 1) {
                        return ST_CYCLE_WHNF;
                    } else {
                        Frame f2 = {u, 2, nxt, t1p};
                        Frame fn = {nxt, 0, -1, StepCount::zero()};
                        st.push_back(f2);
                        st.push_back(fn);
                    }
                }
            } else {
                wn[u] = wn[f.aux];
                ws[u] = StepCount::add(f.acc, ws[f.aux]);
                wst[u] = 2;
            }
        }
        return ST_HALT;
    }

    EvalStatus eval_nf(int root, StepCount& out_steps) {
        struct Frame { int u; uint8_t ph; };
        std::vector<Frame> st;
        st.reserve(256);
        Frame f0 = {root, 0};
        st.push_back(f0);
        std::vector<int> args;
        args.reserve(16);
        while (!st.empty()) {
            if (expired()) return ST_WATCHDOG;
            if (static_cast<int>(st.size()) > MAX_STACK) return ST_STACK_LIMIT;
            Frame f = st.back(); st.pop_back();
            int u = f.u;
            if (f.ph == 0) {
                if (nst[u] == 2) continue;
                if (nst[u] == 1) return ST_CYCLE_NF;
                nst[u] = 1;
                EvalStatus ws_st = whnf(u);
                if (ws_st != ST_HALT) return ws_st;
                int w = wn[u];
                spine(w, args);
                int k = static_cast<int>(args.size());
                if (k == 0) { nn[u] = w; ns[u] = ws[u]; nst[u] = 2; }
                else {
                    Frame f1 = {u, 1};
                    st.push_back(f1);
                    for (int i = k - 1; i >= 0; --i) {
                        if (nst[args[i]] != 2) {
                            Frame fa = {args[i], 0};
                            st.push_back(fa);
                        }
                    }
                }
            } else {
                int w = wn[u];
                int h = spine(w, args);
                int k = static_cast<int>(args.size());
                int cur = h; StepCount tot = ws[u];
                for (int i = 0; i < k; ++i) {
                    tot = StepCount::add(tot, ns[args[i]]);
                    cur = app(cur, nn[args[i]]);
                    if (cur < 0) return ST_NODE_LIMIT;
                }
                nn[u] = cur; ns[u] = tot; nst[u] = 2;
            }
        }
        out_steps = ns[root];
        return ST_HALT;
    }
};

std::string trim_ws(std::string s) {
    while (!s.empty() && (s.back() == '\r' || s.back() == '\n' || s.back() == ' ' || s.back() == '\t')) s.pop_back();
    size_t i = 0;
    while (i < s.size() && (s[i] == ' ' || s[i] == '\t')) i++;
    return s.substr(i);
}

} // namespace

int main(int argc, char* argv[]) {
    registerTestlibCmd(argc, argv);

    int N = inf.readInt(1, 64, "N");
    int aS = inf.readInt(MIN_ARITY, MAX_ARITY, "a_S");
    int aK = inf.readInt(MIN_ARITY, MAX_ARITY, "a_K");
    int vmax = inf.readInt(16, 20000000, "V_max");
    inf.readInt(100, 600000, "TL_ms");
    inf.readEoln();

    std::string rS = trim_ws(inf.readLine());
    std::string rK = trim_ws(inf.readLine());
    std::string req_str = trim_ws(inf.readLine());

    double l_ref = ans.readDouble(1e-6, 1e9, "L_ref");

    RuleSpec rule;
    rule.arity[0] = aS;
    rule.arity[1] = aK;
    size_t p0 = 0, p1 = 0;
    rule.root[0] = parse_rhs_rec(rS, p0, aS, rule.rhs[0]);
    rule.root[1] = parse_rhs_rec(rK, p1, aK, rule.rhs[1]);
    if (rule.root[0] < 0 || rule.root[1] < 0) {
        quitf(_fail, "Invalid rule specification in input file");
    }

    bool has_req = (req_str != "-" && !req_str.empty());
    ExprAst req_ast;
    if (has_req) {
        std::string rerr;
        if (!parse_expr_strict(req_str, req_ast, rerr)) {
            quitf(_fail, "Invalid E_req in input file: %s", rerr.c_str());
        }
    }

    // Read raw contestant output (capped safely beyond 4096 bytes)
    std::string raw_out;
    while (!ouf.eof()) {
        char ch = ouf.readChar();
        raw_out.push_back(ch);
        if (static_cast<int>(raw_out.size()) > MAX_EXPR_BYTES + 16) {
            quitf(_wa, "Output exceeds maximum allowed size (%d bytes)", MAX_EXPR_BYTES);
        }
    }
    if (static_cast<int>(raw_out.size()) > MAX_EXPR_BYTES) {
        quitf(_wa, "Output exceeds maximum allowed size (%d bytes)", MAX_EXPR_BYTES);
    }

    ExprAst ast;
    std::string perr;
    if (!parse_expr_strict(raw_out, ast, perr)) {
        quitf(_wa, "Invalid SK expression syntax: %s", perr.c_str());
    }
    if (ast.leaves > N) {
        quitf(_wa, "Leaf count |E|=%d exceeds budget N=%d", ast.leaves, N);
    }
    if (has_req && !ast_contains(ast, ast.root, req_ast)) {
        quitf(_wa, "Required subtree remnant E_req not found in expression");
    }

    MemoEngine eng(rule, vmax);
    int root = eng.build_ast(ast, ast.root);
    if (root < 0) {
        quitf(_wa, "Expression exceeded DAG node guard V_max=%d", vmax);
    }
    StepCount steps;
    EvalStatus st = eng.eval_nf(root, steps);
    if (st != ST_HALT) {
        const char* reason = (st == ST_CYCLE_WHNF || st == ST_CYCLE_NF) ? "DIVERGENT_CYCLE"
                           : (st == ST_WATCHDOG) ? "JUDGE_WATCHDOG"
                           : "DAG_NODE_OR_STACK_LIMIT";
        quitf(_wa, "Expression failed to halt within guard (%s, dag_nodes=%d)", reason, static_cast<int>(eng.L.size()));
    }

    double log2t = steps.log2_plus1();
    double norm = 0.0;
    if (log2t > 0.0) {
        double a = std::pow(log2t, SCORE_POWER);
        double b = std::pow(l_ref, SCORE_POWER);
        norm = std::max(0.0, std::min(100.0, 100.0 * a / (a + b)));
    }
    double ratio = norm / 100.0;
    const char* sing_tag = (log2t >= 64.0) ? " [SINGULARITY]" : "";
    quitp(
        ratio,
        "AC |E|=%d/%d T=%s (log2=%.4f, L_ref=%.2f) DAG=%d Norm=%.2f/100%s",
        ast.leaves, N, steps.display().c_str(), log2t, l_ref,
        static_cast<int>(eng.L.size()), norm, sing_tag
    );
    return 0;
}
