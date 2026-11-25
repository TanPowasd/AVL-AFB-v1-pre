// Hidden Strong Reference Solver for "The SK Singularity" (sk_singularity): algebra_synth.cpp
// Standalone C++20: symbolic probe gadget discovery + behavioral deduplication +
// rule-symmetric fanout analysis + multi-island compositional evolution +
// 3-stage DAG-node cliff promotion & local boundary refinement (~80% TL budget).

#include <algorithm>
#include <array>
#include <chrono>
#include <cmath>
#include <cstdint>
#include <iostream>
#include <random>
#include <sstream>
#include <string>
#include <unordered_map>
#include <unordered_set>
#include <utility>
#include <vector>

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
        int shift = __builtin_clzll(mant) - 1;
        mant <<= shift;
        exp2 = -static_cast<int64_t>(shift);
    }
    static StepCount add(StepCount a, StepCount b) {
        if (a.is_zero()) return b;
        if (b.is_zero()) return a;
        if (a.is_exact() && b.is_exact()) {
            uint64_t s = a.mant + b.mant;
            if (s < EXACT_LIMIT) return {s, EXACT_EXP};
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
    double sym_prob_S = 0.5;
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

enum class Status : uint8_t { HALT = 0, CYC = 1, NL = 2 };

struct EvalRes {
    Status st = Status::NL;
    double log2t = 0.0;
    int nodes = 0;
    int nf_root = -1;
};

struct MemoEval {
    const Rule& rule;
    int max_nodes;
    int num_atoms;
    std::vector<int> L, R, wn, nn, hv;
    std::vector<StepCount> ws, ns;
    std::vector<uint8_t> wst, nst;
    std::vector<uint64_t> hk;
    std::vector<uint32_t> used;
    uint32_t mask = 4095;
    static constexpr uint64_t EMPTY = ~0ULL;

    MemoEval(const Rule& r, int max_n, int probes = 0)
        : rule(r), max_nodes(max_n), num_atoms(2 + probes) {
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
        for (int i = 0; i < num_atoms; ++i) {
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

    inline int spine(int w, std::vector<int>& args) const {
        args.clear();
        int cur = w;
        while (cur >= num_atoms) { args.push_back(R[cur]); cur = L[cur]; }
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

    Status whnf(int root) {
        if (wst[root] == 2) return Status::HALT;
        struct F { int u; uint8_t ph; int aux; StepCount acc; };
        std::vector<F> st{{root, 0, -1, StepCount::zero()}};
        std::vector<int> args;
        args.reserve(16);
        while (!st.empty()) {
            if (st.size() > 2500000) return Status::NL;
            F f = st.back(); st.pop_back();
            int u = f.u;
            if (f.ph == 0) {
                if (wst[u] == 2) continue;
                if (wst[u] == 1) return Status::CYC;
                wst[u] = 1;
                st.push_back({u, 1, -1, StepCount::zero()});
                if (wst[L[u]] != 2) st.push_back({L[u], 0, -1, StepCount::zero()});
            } else if (f.ph == 1) {
                int wa = wn[L[u]];
                StepCount t1 = ws[L[u]];
                int h = spine(wa, args);
                int k = static_cast<int>(args.size());
                if (h >= 2 || k + 1 < rule.arity[h]) {
                    int w = app(wa, R[u]); if (w < 0) return Status::NL;
                    wn[u] = w; ws[u] = t1; wst[u] = 2;
                } else {
                    args.push_back(R[u]);
                    int nxt = inst(h, rule.root[h], args.data()); if (nxt < 0) return Status::NL;
                    StepCount t1p = StepCount::add(t1, StepCount::one());
                    if (wst[nxt] == 2) {
                        wn[u] = wn[nxt]; ws[u] = StepCount::add(t1p, ws[nxt]); wst[u] = 2;
                    } else if (wst[nxt] == 1) {
                        return Status::CYC;
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
        return Status::HALT;
    }

    EvalRes eval_node(int root) {
        struct NF { int u; uint8_t ph; };
        std::vector<NF> st{{root, 0}};
        std::vector<int> args;
        args.reserve(16);
        while (!st.empty()) {
            if (st.size() > 2500000) return {Status::NL, 0.0, static_cast<int>(L.size()), -1};
            NF f = st.back(); st.pop_back();
            int u = f.u;
            if (f.ph == 0) {
                if (nst[u] == 2) continue;
                if (nst[u] == 1) return {Status::CYC, 0.0, static_cast<int>(L.size()), -1};
                nst[u] = 1;
                Status ws_st = whnf(u);
                if (ws_st != Status::HALT) return {ws_st, 0.0, static_cast<int>(L.size()), -1};
                int w = wn[u];
                spine(w, args);
                int k = static_cast<int>(args.size());
                if (k == 0) { nn[u] = w; ns[u] = ws[u]; nst[u] = 2; }
                else {
                    st.push_back({u, 1});
                    for (int i = k - 1; i >= 0; --i)
                        if (nst[args[i]] != 2) st.push_back({args[i], 0});
                }
            } else {
                int w = wn[u];
                int h = spine(w, args);
                int k = static_cast<int>(args.size());
                int cur = h; StepCount tot = ws[u];
                for (int i = 0; i < k; ++i) {
                    tot = StepCount::add(tot, ns[args[i]]);
                    cur = app(cur, nn[args[i]]);
                    if (cur < 0) return {Status::NL, 0.0, static_cast<int>(L.size()), -1};
                }
                nn[u] = cur; ns[u] = tot; nst[u] = 2;
            }
        }
        return {Status::HALT, ns[root].log2_plus1(), static_cast<int>(L.size()), nn[root]};
    }

    EvalRes eval_pre(const std::string& pre) {
        reset();
        size_t pos = 0;
        int root = build_pre(pre, pos);
        if (root < 0) return {Status::NL, 0.0, static_cast<int>(L.size()), -1};
        return eval_node(root);
    }

    uint64_t shash(int u, std::unordered_map<int, uint64_t>& memo) const {
        if (u < num_atoms) return mix64(0x9e3779b97f4a7c15ULL * static_cast<uint64_t>(u + 1));
        auto it = memo.find(u);
        if (it != memo.end()) return it->second;
        uint64_t hl = shash(L[u], memo);
        uint64_t hr = shash(R[u], memo);
        uint64_t h = mix64(hl ^ (hr * 0x517cc1b727220a95ULL + 0x9e3779b97f4a7c15ULL));
        memo[u] = h;
        return h;
    }
};

static std::string rand_tree(int n, std::mt19937_64& rng, double pS) {
    if (n == 1) {
        return (std::uniform_real_distribution<double>(0.0, 1.0)(rng) < pS) ? "S" : "K";
    }
    int l = 1 + static_cast<int>(rng() % (n - 1));
    return "." + rand_tree(l, rng, pS) + rand_tree(n - l, rng, pS);
}

static std::string rand_with_req(int n, const std::string& req_pre, int req_sz, std::mt19937_64& rng, double pS) {
    if (req_pre.empty()) return rand_tree(n, rng, pS);
    if (n <= req_sz) return req_pre;
    std::string cur = req_pre;
    int rem = n - req_sz;
    while (rem > 0) {
        int add = 1 + static_cast<int>(rng() % rem);
        std::string g = rand_tree(add, rng, pS);
        cur = (rng() & 1ULL) ? ("." + g + cur) : ("." + cur + g);
        rem -= add;
    }
    return cur;
}

static void parse_spans(const std::string& p, std::vector<int>& ep, std::vector<int>& lc) {
    int m = static_cast<int>(p.size());
    ep.resize(m); lc.resize(m);
    auto dfs = [&](auto&& self, int i) -> int {
        if (p[i] != '.') { ep[i] = i + 1; lc[i] = 1; return i + 1; }
        int mid = self(self, i + 1);
        int ed = self(self, mid);
        ep[i] = ed; lc[i] = lc[i + 1] + lc[mid];
        return ed;
    };
    dfs(dfs, 0);
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

    // Symmetric fanout analysis: give slightly higher sampling weight to whichever symbol has larger RHS!
    int szS = static_cast<int>(rule.rhs[0].size());
    int szK = static_cast<int>(rule.rhs[1].size());
    if (szS > szK) rule.sym_prob_S = 0.58;
    else if (szK > szS) rule.sym_prob_S = 0.42;
    else rule.sym_prob_S = 0.50;

    std::string req_pre;
    int req_sz = 0;
    if (l4 != "-" && !l4.empty()) {
        size_t pr = 0;
        req_pre = sexp_to_pre(l4, pr);
        for (char c : req_pre) if (c != '.') req_sz++;
    }
    auto has_req = [&](const std::string& p) -> bool {
        return req_pre.empty() || p.find(req_pre) != std::string::npos;
    };

    auto t0 = std::chrono::steady_clock::now();
    auto elapsed = [&]() -> int {
        return static_cast<int>(std::chrono::duration_cast<std::chrono::milliseconds>(
            std::chrono::steady_clock::now() - t0).count());
    };
    // Safe anytime schedule (~80% of TL_ms)
    int budget_ms = std::max(300, tl_ms * 78 / 100);

    // Deterministic seed invariant to irrelevant formatting, depends on rule + N + req
    uint64_t seed = 0xA16EB7A9ULL ^ (static_cast<uint64_t>(N) * 0x9e3779b97f4a7c15ULL);
    for (char c : l2) seed = seed * 131ULL + static_cast<unsigned char>(c);
    for (char c : l3) seed = seed * 131ULL + static_cast<unsigned char>(c);
    for (char c : l4) seed = seed * 131ULL + static_cast<unsigned char>(c);
    std::mt19937_64 rng(seed);

    // Phase 1: Symbolic Probe Gadget Discovery (sizes 1..5, ~8% budget)
    MemoEval probe_ev(rule, 1500, 3);
    std::vector<std::vector<std::string>> gadgets_by_sz(6);
    std::unordered_set<uint64_t> seen_beh;
    std::vector<std::pair<std::string, int>> power_gadgets = {{"S", 1}, {"K", 1}};

    auto classify_gadget = [&](const std::string& pre, uint64_t& sig, double& psc) -> bool {
        probe_ev.reset();
        size_t pos = 0;
        int u = probe_ev.build_pre(pre, pos);
        if (u < 0) return false;
        auto r0 = probe_ev.eval_node(u);
        if (r0.st != Status::HALT) return false;
        std::unordered_map<int, uint64_t> hm;
        uint64_t h0 = probe_ev.shash(r0.nf_root, hm);
        auto run_probe = [&](int arity_probes, uint64_t fallback_h, EvalRes& out_r) -> uint64_t {
            probe_ev.reset();
            size_t p = 0;
            int cur = probe_ev.build_pre(pre, p);
            for (int a = 0; a < arity_probes && cur >= 0; ++a) {
                cur = probe_ev.app(cur, 2 + a);
            }
            out_r = (cur >= 0) ? probe_ev.eval_node(cur) : EvalRes{};
            if (out_r.st != Status::HALT) return fallback_h;
            std::unordered_map<int, uint64_t> pm;
            return probe_ev.shash(out_r.nf_root, pm);
        };
        EvalRes r1{}, r2{}, r3{};
        uint64_t h1 = run_probe(1, 0x1111ULL, r1);
        uint64_t h2 = run_probe(2, 0x2222ULL, r2);
        uint64_t h3 = run_probe(3, 0x3333ULL, r3);

        sig = MemoEval::mix64(h0 ^ MemoEval::mix64(h1 + 17) ^ MemoEval::mix64(h2 + 31) ^ MemoEval::mix64(h3 + 47));
        psc = r0.log2t;
        if (r1.st == Status::HALT) psc += r1.log2t * 1.5;
        if (r2.st == Status::HALT) psc += r2.log2t * 2.0;
        if (r3.st == Status::HALT) psc += r3.log2t * 2.5;
        return true;
    };

    int probe_deadline = std::min(budget_ms / 10, 320);
    for (int n = 1; n <= std::min(N, 5) && elapsed() < probe_deadline; ++n) {
        std::vector<std::string> cands;
        if (n == 1) cands = {"S", "K"};
        else {
            for (int l = 1; l < n; ++l) {
                for (const auto& a : gadgets_by_sz[l]) {
                    for (const auto& b : gadgets_by_sz[n - l]) {
                        cands.push_back("." + a + b);
                        if (cands.size() > 700) break;
                    }
                    if (cands.size() > 700) break;
                }
            }
        }
        struct SG { std::string pre; double psc; };
        std::vector<SG> kept;
        for (const auto& pre : cands) {
            if (elapsed() >= probe_deadline) break;
            uint64_t sig = 0; double psc = 0.0;
            if (!classify_gadget(pre, sig, psc)) continue;
            if (seen_beh.insert(sig).second) kept.push_back({pre, psc});
        }
        std::sort(kept.begin(), kept.end(), [](const SG& a, const SG& b) { return a.psc > b.psc; });
        int cap = (n <= 4) ? 24 : 20;
        for (int i = 0; i < std::min(static_cast<int>(kept.size()), cap); ++i) {
            gadgets_by_sz[n].push_back(kept[i].pre);
            if (i < 12) power_gadgets.push_back({kept[i].pre, n});
        }
    }
    if (!req_pre.empty()) power_gadgets.push_back({req_pre, req_sz});

    // Phase 2: Fast staged evaluators (cap internal uncompacted MemoEval to stay well under memory/time limits)
    MemoEval ev_fast(rule, std::min(vmax, 4500));
    MemoEval ev_mid(rule, std::min(vmax, 60000));
    int full_cap = (tl_ms < 1500) ? std::min(vmax, 350000) : std::min(vmax, 1000000);
    MemoEval ev_full(rule, full_cap);

    struct Item { std::string pre; int leaves; double sc; };
    static constexpr int NUM_ISLANDS = 4;
    std::array<std::vector<Item>, NUM_ISLANDS> islands;
    std::vector<std::vector<Item>> beam(N + 1);
    std::unordered_set<std::string> seen;
    std::vector<std::pair<std::string, int>> promoted;

    Item best{req_pre.empty() ? "S" : req_pre, std::max(1, req_sz), -1.0};
    {
        auto r0 = ev_fast.eval_pre(best.pre);
        if (r0.st == Status::HALT) best.sc = r0.log2t;
    }

    int min_sz = std::max(1, req_sz);
    auto insert_island = [&](int isl, const Item& it) {
        if (!has_req(it.pre)) return;
        auto& pop = islands[isl];
        if (pop.size() < 28) {
            pop.push_back(it);
            return;
        }
        int w = 0;
        for (int i = 1; i < static_cast<int>(pop.size()); ++i)
            if (pop[i].sc < pop[w].sc) w = i;
        if (it.sc > pop[w].sc) {
            for (const auto& x : pop) if (std::abs(x.sc - it.sc) < 1e-9 && x.leaves == it.leaves) return;
            pop[w] = it;
        }
    };

    auto eval_and_record = [&](const std::string& pre, int lv, int isl, bool req_only) {
        if (lv < 1 || lv > N) return;
        if (req_only && !has_req(pre)) return;
        if (!seen.insert(pre).second) return;
        auto res = ev_fast.eval_pre(pre);
        if (res.st == Status::NL) {
            if (has_req(pre) && promoted.size() < 1200) promoted.push_back({pre, lv});
            return;
        }
        if (res.st != Status::HALT) return;
        Item it{pre, lv, res.log2t};
        if (has_req(pre)) {
            if (it.sc > best.sc) best = it;
            insert_island(isl, it);
        }
        auto& b = beam[lv];
        int cap = 24;
        if (static_cast<int>(b.size()) < cap) b.push_back(it);
        else {
            int w = 0;
            for (int i = 1; i < static_cast<int>(b.size()); ++i)
                if (b[i].sc < b[w].sc) w = i;
            if (it.sc > b[w].sc) b[w] = it;
        }
    };

    // Seed bottom-up beam from discovered gadgets up to N (~15% budget)
    for (int n = 1; n <= std::min(N, 5); ++n) {
        for (const auto& g : gadgets_by_sz[n]) eval_and_record(g, n, 0, false);
    }
    int bu_deadline = budget_ms * 22 / 100;
    for (int n = 2; n <= N && elapsed() < bu_deadline; ++n) {
        for (int l = 1; l < n && elapsed() < bu_deadline; ++l) {
            int r = n - l;
            if (beam[l].empty() || beam[r].empty()) continue;
            int lim = std::min(static_cast<int>(beam[l].size() * beam[r].size()), 24);
            for (int t = 0; t < lim; ++t) {
                const auto& a = beam[l][rng() % beam[l].size()];
                const auto& b = beam[r][rng() % beam[r].size()];
                eval_and_record("." + a.pre + b.pre, n, t % NUM_ISLANDS, false);
            }
        }
        if (!req_pre.empty() && n > req_sz) {
            int rem = n - req_sz;
            if (!beam[rem].empty()) {
                for (int t = 0; t < std::min(static_cast<int>(beam[rem].size()), 14); ++t) {
                    const auto& a = beam[rem][t];
                    eval_and_record("." + a.pre + req_pre, n, t % NUM_ISLANDS, true);
                    eval_and_record("." + req_pre + a.pre, n, t % NUM_ISLANDS, true);
                }
            }
        }
    }

    // Explicit remnant seeding (both direct and symmetric eraser-wrapped for both S and K!)
    if (!req_pre.empty()) {
        for (int sz = 1; sz <= N - req_sz - 1; ++sz) {
            for (const auto& it : beam[sz]) {
                eval_and_record("..K" + it.pre + req_pre, sz + req_sz + 1, 0, true);
                eval_and_record("..K" + req_pre + it.pre, sz + req_sz + 1, 1, true);
                eval_and_record("..S" + it.pre + req_pre, sz + req_sz + 1, 2, true);
                eval_and_record("..S" + req_pre + it.pre, sz + req_sz + 1, 3, true);
            }
        }
        for (int i = 0; i < 400 && elapsed() < budget_ms * 30 / 100; ++i) {
            int sz = min_sz + static_cast<int>(rng() % (N - min_sz + 1));
            eval_and_record(rand_with_req(sz, req_pre, req_sz, rng, rule.sym_prob_S), sz, i % NUM_ISLANDS, true);
        }
    } else {
        for (int i = 0; i < 400 && elapsed() < budget_ms * 30 / 100; ++i) {
            int sz = std::max(4, N - static_cast<int>(rng() % std::min(N - 3, 10)));
            eval_and_record(rand_tree(sz, rng, rule.sym_prob_S), sz, i % NUM_ISLANDS, true);
        }
    }

    // Phase 3: Multi-Island Compositional Evolution (until 68% budget)
    int evo_deadline = budget_ms * 68 / 100;
    std::vector<int> ep, lc;
    int iter = 0;
    while (elapsed() < evo_deadline) {
        int isl = iter % NUM_ISLANDS;
        iter++;
        // Periodic ring migration
        if ((iter & 255) == 0 && !islands[isl].empty()) {
            const auto& em = islands[isl][rng() % islands[isl].size()];
            insert_island((isl + 1) % NUM_ISLANDS, em);
        }
        // Periodic diversity injection on island 3 to escape local optima
        if ((iter & 1023) == 0) {
            islands[NUM_ISLANDS - 1].clear();
            for (int k = 0; k < 24; ++k) {
                int sz = min_sz + static_cast<int>(rng() % (N - min_sz + 1));
                eval_and_record(rand_with_req(sz, req_pre, req_sz, rng, rule.sym_prob_S), sz, NUM_ISLANDS - 1, true);
            }
        }
        if (islands[isl].empty()) {
            int sz = min_sz + static_cast<int>(rng() % (N - min_sz + 1));
            eval_and_record(rand_with_req(sz, req_pre, req_sz, rng, rule.sym_prob_S), sz, isl, true);
            continue;
        }
        // Tournament selection (size 3) inside island, or pick global best 15% of time
        const Item* par_ptr = nullptr;
        if (rng() % 7 == 0 && best.sc >= 0.0) {
            par_ptr = &best;
        } else {
            const auto& pop = islands[isl];
            int a = static_cast<int>(rng() % pop.size());
            int b = static_cast<int>(rng() % pop.size());
            par_ptr = (pop[a].sc >= pop[b].sc) ? &pop[a] : &pop[b];
        }
        const Item par = *par_ptr;
        std::string p = par.pre;
        parse_spans(p, ep, lc);
        int idx = static_cast<int>(rng() % p.size());
        int sub = lc[idx];
        int rem = N - (par.leaves - sub);
        std::string rep;
        int rsz = sub;
        int op = static_cast<int>(rng() % 7);
        if (op == 0 && p[idx] != '.') {
            rep = (p[idx] == 'S') ? "K" : "S"; rsz = 1;
        } else if (op == 1 && !power_gadgets.empty()) {
            const auto& g = power_gadgets[rng() % power_gadgets.size()];
            if (g.second <= rem) { rep = g.first; rsz = g.second; }
            else continue;
        } else if (op == 2 && p[idx] == '.') {
            int l1 = idx + 1, r1 = ep[l1], r2 = ep[idx];
            rep = "." + p.substr(r1, r2 - r1) + p.substr(l1, r1 - l1);
        } else if (op == 3 && rem > sub && !power_gadgets.empty()) {
            const auto& g = power_gadgets[rng() % power_gadgets.size()];
            if (sub + g.second <= rem) {
                std::string sstr = p.substr(idx, ep[idx] - idx);
                rep = (rng() & 1ULL) ? ("." + g.first + sstr) : ("." + sstr + g.first);
                rsz = sub + g.second;
            } else continue;
        } else if (op == 4 && rem >= 2 * sub) {
            std::string sstr = p.substr(idx, ep[idx] - idx);
            rep = "." + sstr + sstr; rsz = 2 * sub;
        } else if (op == 5 && p[idx] == '.') {
            // Tree rotation: ((a b) c) <-> (a (b c))
            int l1 = idx + 1, r1 = ep[l1], r2 = ep[idx];
            if (p[l1] == '.') {
                int a1 = l1 + 1, b1 = ep[a1];
                rep = "." + p.substr(a1, b1 - a1) + "." + p.substr(b1, r1 - b1) + p.substr(r1, r2 - r1);
            } else if (p[r1] == '.') {
                int b1 = r1 + 1, c1 = ep[b1];
                rep = ".." + p.substr(l1, r1 - l1) + p.substr(b1, c1 - b1) + p.substr(c1, r2 - c1);
            } else continue;
        } else {
            int gsz = 1 + static_cast<int>(rng() % std::min(rem, 12));
            if (!beam[gsz].empty() && (rng() & 1ULL)) {
                rep = beam[gsz][rng() % beam[gsz].size()].pre;
                rsz = gsz;
            } else {
                rep = rand_tree(gsz, rng, rule.sym_prob_S);
                rsz = gsz;
            }
        }
        std::string child = p.substr(0, idx) + rep + p.substr(ep[idx]);
        eval_and_record(child, par.leaves - sub + rsz, isl, true);
    }

    // Phase 4: Staged Cliff Promotion (4.5k -> 60k -> V_max) + Local Boundary Refinement!
    std::shuffle(promoted.begin(), promoted.end(), rng);
    std::vector<std::pair<std::string, int>> super_promoted;
    int mid_deadline = budget_ms * 86 / 100;
    for (size_t i = 0; i < promoted.size() && elapsed() < mid_deadline; ++i) {
        const auto& cand = promoted[i];
        auto r2 = ev_mid.eval_pre(cand.first);
        if (r2.st == Status::HALT) {
            if (r2.log2t > best.sc) best = {cand.first, cand.second, r2.log2t};
        } else if (r2.st == Status::NL && super_promoted.size() < 40) {
            super_promoted.push_back(cand);
        }
    }
    for (size_t i = 0; i < super_promoted.size() && elapsed() < budget_ms; ++i) {
        const auto& cand = super_promoted[i];
        auto r3 = ev_full.eval_pre(cand.first);
        if (r3.st == Status::HALT && r3.log2t > best.sc) {
            best = {cand.first, cand.second, r3.log2t};
        }
    }

    // Final local polish around global best with remaining time!
    while (elapsed() < budget_ms && best.sc >= 0.0) {
        std::string p = best.pre;
        parse_spans(p, ep, lc);
        int idx = static_cast<int>(rng() % p.size());
        int sub = lc[idx];
        int rem = N - (best.leaves - sub);
        std::string rep;
        int rsz = sub;
        if (p[idx] != '.' && (rng() & 1ULL)) {
            rep = (p[idx] == 'S') ? "K" : "S"; rsz = 1;
        } else if (rem > sub && !power_gadgets.empty()) {
            const auto& g = power_gadgets[rng() % power_gadgets.size()];
            if (sub + g.second <= rem) {
                std::string sstr = p.substr(idx, ep[idx] - idx);
                rep = (rng() & 1ULL) ? ("." + g.first + sstr) : ("." + sstr + g.first);
                rsz = sub + g.second;
            } else continue;
        } else {
            rsz = 1 + static_cast<int>(rng() % std::min(rem, 6));
            rep = rand_tree(rsz, rng, rule.sym_prob_S);
        }
        std::string child = p.substr(0, idx) + rep + p.substr(ep[idx]);
        int clv = best.leaves - sub + rsz;
        if (clv > N || !has_req(child)) continue;
        if (!seen.insert(child).second) continue;
        auto r2 = ev_mid.eval_pre(child);
        if (r2.st == Status::HALT && r2.log2t > best.sc) {
            best = {child, clv, r2.log2t};
        }
    }

    size_t out_pos = 0;
    std::cout << pre_to_sexp(best.pre, out_pos) << "\n";
    return 0;
}
