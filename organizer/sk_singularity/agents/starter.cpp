// Starter template for "The SK Singularity" (sk_singularity).
// Standalone C++20: parses the 4-line combinator rule specification, runs a quick
// bounded hash-consed normal-order evaluator over random valid trees (<= N leaves,
// containing E_req if required), and prints the best halting expression found.

#include <algorithm>
#include <chrono>
#include <cmath>
#include <cstdint>
#include <iostream>
#include <random>
#include <sstream>
#include <string>
#include <vector>

struct StepCount {
    uint64_t mant = 0;
    int64_t exp2 = -1;
    static constexpr uint64_t EXACT_LIMIT = (1ULL << 62);
    static StepCount zero() { return {0ULL, -1}; }
    static StepCount one()  { return {1ULL, -1}; }
    bool is_exact() const { return exp2 == -1; }
    bool is_zero()  const { return exp2 == -1 && mant == 0ULL; }
    void promote() {
        if (exp2 != -1 || mant == 0ULL) return;
        int shift = __builtin_clzll(mant) - 1;
        mant <<= shift;
        exp2 = -shift;
    }
    static StepCount add(StepCount a, StepCount b) {
        if (a.is_zero()) return b;
        if (b.is_zero()) return a;
        if (a.is_exact() && b.is_exact()) {
            uint64_t s = a.mant + b.mant;
            if (s < EXACT_LIMIT) return {s, -1};
            return {s, 0};
        }
        a.promote(); b.promote();
        if (a.exp2 < b.exp2) std::swap(a, b);
        int64_t diff = a.exp2 - b.exp2;
        if (diff >= 64) return a;
        uint64_t b_al = b.mant >> diff;
        if (diff > 0) {
            uint64_t rem = b.mant & ((1ULL << diff) - 1ULL);
            uint64_t half = 1ULL << (diff - 1);
            if (rem > half || (rem == half && (b_al & 1ULL))) b_al++;
        }
        uint64_t sum = a.mant + b_al;
        int64_t e = a.exp2;
        if (sum >= (1ULL << 63)) {
            uint64_t odd = sum & 1ULL;
            sum >>= 1;
            if (odd && (sum & 1ULL)) { sum++; if (sum >= (1ULL << 63)) { sum >>= 1; e++; } }
            e++;
        }
        return {sum, e};
    }
    double log2_plus1() const {
        if (is_exact()) return std::log2(static_cast<double>(mant) + 1.0);
        return static_cast<double>(exp2 + 62) + std::log2(static_cast<double>(mant) / static_cast<double>(1ULL << 62));
    }
};

struct RhsNode { int tag, l, r; };
struct Rule {
    int arity[2] = {3, 2};
    std::vector<RhsNode> rhs[2];
    int root[2] = {-1, -1};
};

static int parse_rhs(const std::string& s, size_t& pos, std::vector<RhsNode>& out) {
    while (pos < s.size() && (s[pos] == ' ' || s[pos] == '\t')) pos++;
    char c = s[pos];
    if (c == '(') {
        pos++;
        int a = parse_rhs(s, pos, out);
        int b = parse_rhs(s, pos, out);
        while (pos < s.size() && (s[pos] == ' ' || s[pos] == '\t')) pos++;
        if (pos < s.size() && s[pos] == ')') pos++;
        int id = static_cast<int>(out.size());
        out.push_back({-3, a, b});
        return id;
    }
    pos++;
    int id = static_cast<int>(out.size());
    if (c == 'S') out.push_back({-1, -1, -1});
    else if (c == 'K') out.push_back({-2, -1, -1});
    else out.push_back({c - '0', -1, -1});
    return id;
}

static std::string sexp_to_pre(const std::string& s, size_t& pos) {
    while (pos < s.size() && (s[pos] == ' ' || s[pos] == '\t')) pos++;
    if (s[pos] == '(') {
        pos++;
        std::string a = sexp_to_pre(s, pos);
        std::string b = sexp_to_pre(s, pos);
        while (pos < s.size() && (s[pos] == ' ' || s[pos] == '\t')) pos++;
        if (pos < s.size() && s[pos] == ')') pos++;
        return "." + a + b;
    }
    return std::string(1, s[pos++]);
}

static std::string pre_to_sexp(const std::string& p, size_t& pos) {
    char c = p[pos++];
    if (c == 'S') return "S";
    if (c == 'K') return "K";
    std::string a = pre_to_sexp(p, pos);
    std::string b = pre_to_sexp(p, pos);
    return "(" + a + " " + b + ")";
}

struct MiniEval {
    const Rule& rule;
    int max_nodes;
    std::vector<int> L, R, wn, nn, hv;
    std::vector<StepCount> ws, ns;
    std::vector<uint8_t> wst, nst;
    std::vector<uint64_t> hk;
    std::vector<uint32_t> used;
    uint32_t mask = 4095;
    static constexpr uint64_t EMPTY = ~0ULL;

    MiniEval(const Rule& r, int max_n = 4000) : rule(r), max_nodes(max_n) {
        hk.assign(4096, EMPTY);
        hv.assign(4096, -1);
        reset();
    }

    void reset() {
        if (used.size() * 4 < hk.size()) {
            for (uint32_t idx : used) hk[idx] = EMPTY;
        } else {
            std::fill(hk.begin(), hk.end(), EMPTY);
        }
        used.clear();
        L.clear(); R.clear(); wn.clear(); ws.clear(); wst.clear(); nn.clear(); ns.clear(); nst.clear();
        for (int i = 0; i < 2; ++i) {
            L.push_back(-1); R.push_back(-1);
            wn.push_back(i); ws.push_back(StepCount::zero()); wst.push_back(2);
            nn.push_back(i); ns.push_back(StepCount::zero()); nst.push_back(2);
        }
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
        used.clear();
        for (size_t i = 0; i < hk.size(); ++i) {
            if (hk[i] != EMPTY) {
                uint32_t idx = static_cast<uint32_t>(mix64(hk[i])) & nm;
                while (nk[idx] != EMPTY) idx = (idx + 1) & nm;
                nk[idx] = hk[i]; nv[idx] = hv[i];
                used.push_back(idx);
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
        hk[idx] = key; hv[idx] = id; used.push_back(idx);
        L.push_back(a); R.push_back(b);
        wn.push_back(-1); ws.push_back(StepCount::zero()); wst.push_back(0);
        nn.push_back(-1); ns.push_back(StepCount::zero()); nst.push_back(0);
        return id;
    }

    int build_pre(const std::string& p, size_t& pos) {
        char c = p[pos++];
        if (c == 'S') return 0;
        if (c == 'K') return 1;
        int a = build_pre(p, pos); if (a < 0) return -1;
        int b = build_pre(p, pos); if (b < 0) return -1;
        return app(a, b);
    }

    inline int spine(int w, int* args, int& k) const {
        k = 0; int cur = w;
        while (cur >= 2) { args[k++] = R[cur]; cur = L[cur]; }
        for (int i = 0; i < k / 2; ++i) std::swap(args[i], args[k - 1 - i]);
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

    bool whnf(int root) {
        if (wst[root] == 2) return true;
        struct F { int u; uint8_t ph; int aux; StepCount acc; };
        std::vector<F> st{{root, 0, -1, StepCount::zero()}};
        int args[8];
        while (!st.empty()) {
            if (st.size() > 200000) return false;
            F f = st.back(); st.pop_back();
            int u = f.u;
            if (f.ph == 0) {
                if (wst[u] == 2) continue;
                if (wst[u] == 1) return false;
                wst[u] = 1;
                st.push_back({u, 1, -1, StepCount::zero()});
                if (wst[L[u]] != 2) st.push_back({L[u], 0, -1, StepCount::zero()});
            } else if (f.ph == 1) {
                int wa = wn[L[u]];
                StepCount t1 = ws[L[u]];
                int k = 0; int h = spine(wa, args, k);
                if (k + 1 < rule.arity[h]) {
                    int w = app(wa, R[u]); if (w < 0) return false;
                    wn[u] = w; ws[u] = t1; wst[u] = 2;
                } else {
                    args[k] = R[u];
                    int nxt = inst(h, rule.root[h], args); if (nxt < 0) return false;
                    StepCount t1p = StepCount::add(t1, StepCount::one());
                    if (wst[nxt] == 2) {
                        wn[u] = wn[nxt]; ws[u] = StepCount::add(t1p, ws[nxt]); wst[u] = 2;
                    } else if (wst[nxt] == 1) {
                        return false;
                    } else {
                        st.push_back({u, 2, nxt, t1p});
                        st.push_back({nxt, 0, -1, StepCount::zero()});
                    }
                }
            } else {
                wn[u] = wn[f.aux];
                ws[u] = StepCount::add(f.acc, ws[f.aux]);
                wst[u] = 2;
            }
        }
        return true;
    }

    bool eval(const std::string& pre, double& log2t) {
        reset();
        size_t pos = 0;
        int root = build_pre(pre, pos);
        if (root < 0) return false;
        struct NF { int u; uint8_t ph; };
        std::vector<NF> st{{root, 0}};
        int args[8];
        while (!st.empty()) {
            if (st.size() > 200000) return false;
            NF f = st.back(); st.pop_back();
            int u = f.u;
            if (f.ph == 0) {
                if (nst[u] == 2) continue;
                if (nst[u] == 1) return false;
                nst[u] = 1;
                if (!whnf(u)) return false;
                int w = wn[u]; int k = 0; spine(w, args, k);
                if (k == 0) { nn[u] = w; ns[u] = ws[u]; nst[u] = 2; }
                else {
                    st.push_back({u, 1});
                    for (int i = k - 1; i >= 0; --i)
                        if (nst[args[i]] != 2) st.push_back({args[i], 0});
                }
            } else {
                int w = wn[u]; int k = 0; int h = spine(w, args, k);
                int cur = h; StepCount tot = ws[u];
                for (int i = 0; i < k; ++i) {
                    tot = StepCount::add(tot, ns[args[i]]);
                    cur = app(cur, nn[args[i]]);
                    if (cur < 0) return false;
                }
                nn[u] = cur; ns[u] = tot; nst[u] = 2;
            }
        }
        log2t = ns[root].log2_plus1();
        return true;
    }
};

static std::string rand_tree(int n, std::mt19937_64& rng) {
    if (n == 1) return (rng() & 1ULL) ? "S" : "K";
    int l = 1 + static_cast<int>(rng() % (n - 1));
    return "." + rand_tree(l, rng) + rand_tree(n - l, rng);
}

static std::string rand_with_req(int n, const std::string& req_pre, int req_sz, std::mt19937_64& rng) {
    if (req_pre.empty()) return rand_tree(n, rng);
    if (n <= req_sz) return req_pre;
    std::string cur = req_pre;
    int rem = n - req_sz;
    while (rem > 0) {
        int add = 1 + static_cast<int>(rng() % rem);
        std::string g = rand_tree(add, rng);
        cur = (rng() & 1ULL) ? ("." + g + cur) : ("." + cur + g);
        rem -= add;
    }
    return cur;
}

int main() {
    std::ios::sync_with_stdio(false);
    std::cin.tie(nullptr);

    int N = 8, aS = 3, aK = 2, vmax = 10000000, tl_ms = 5000;
    std::string l1, l2, l3, l4;
    if (!std::getline(std::cin, l1) || !std::getline(std::cin, l2) ||
        !std::getline(std::cin, l3) || !std::getline(std::cin, l4)) {
        std::cout << "S\n";
        return 0;
    }
    {
        std::istringstream iss(l1);
        iss >> N >> aS >> aK >> vmax >> tl_ms;
    }
    auto trim = [](std::string s) {
        while (!s.empty() && (s.back() == '\r' || s.back() == '\n' || s.back() == ' ' || s.back() == '\t')) s.pop_back();
        size_t i = 0;
        while (i < s.size() && (s[i] == ' ' || s[i] == '\t')) i++;
        return s.substr(i);
    };
    l2 = trim(l2); l3 = trim(l3); l4 = trim(l4);

    Rule rule;
    rule.arity[0] = aS;
    rule.arity[1] = aK;
    size_t p0 = 0, p1 = 0;
    rule.root[0] = parse_rhs(l2, p0, rule.rhs[0]);
    rule.root[1] = parse_rhs(l3, p1, rule.rhs[1]);

    std::string req_pre;
    int req_sz = 0;
    if (l4 != "-" && !l4.empty()) {
        size_t pr = 0;
        req_pre = sexp_to_pre(l4, pr);
        for (char c : req_pre) if (c != '.') req_sz++;
    }

    MiniEval ev(rule, std::min(vmax, 4000));
    std::mt19937_64 rng(0x5A17C0DEULL ^ (static_cast<uint64_t>(N) << 16) ^ (aS * 131ULL + aK));

    std::string best_pre = req_pre.empty() ? "S" : req_pre;
    double best_log2 = -1.0;
    {
        double lt = 0.0;
        if (ev.eval(best_pre, lt)) best_log2 = lt;
    }

    auto t0 = std::chrono::steady_clock::now();
    int budget_ms = std::max(50, std::min(tl_ms / 4, 250));
    int min_sz = std::max(1, req_sz);

    for (int trial = 0; trial < 320; ++trial) {
        if ((trial & 31) == 0) {
            auto ms = std::chrono::duration_cast<std::chrono::milliseconds>(
                std::chrono::steady_clock::now() - t0).count();
            if (ms >= budget_ms) break;
        }
        int sz = min_sz + static_cast<int>(rng() % (N - min_sz + 1));
        std::string cand = rand_with_req(sz, req_pre, req_sz, rng);
        double lt = 0.0;
        if (ev.eval(cand, lt) && lt > best_log2) {
            best_log2 = lt;
            best_pre = cand;
        }
    }

    size_t out_pos = 0;
    std::cout << pre_to_sexp(best_pre, out_pos) << "\n";
    return 0;
}
